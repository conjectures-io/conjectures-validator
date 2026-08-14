from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import uuid

import pytest
from conftest import DATABASE_SKIP_REASON, postgres_dsn
from sqlalchemy import select

from conjectures_subnet.db.engine import (
    async_session_factory,
    create_async_db_engine,
    create_db_engine,
    session_factory,
)
from conjectures_subnet.db.models import (
    Base,
    ManualReviewState,
    PayoutState,
    Proof,
    RewardEvent,
    RewardState,
    Submission,
    SubmissionEvent,
    TaskMode,
    VerificationState,
)
from conjectures_subnet.transfers import ObservedBlock, ObservedPayout
from payout_notifier.discord import (
    DEFAULT_NETUID,
    DEFAULT_ORIGIN_HOTKEY,
    DEFAULT_PROXY_FOR,
)
from payout_watcher.settings import PayoutWatcherSettings, SettingsError
from payout_watcher.watcher import PayoutWatcher
from submission_api.routers._account import latest_reward

DESTINATION_COLDKEY = "5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy"
DESTINATION_HOTKEY = "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"
START = dt.datetime(2026, 8, 6, 12, 0, tzinfo=dt.UTC)
REWARD_CREATED = START + dt.timedelta(seconds=99 * 12)
PAYOUT_TIME = START + dt.timedelta(seconds=101 * 12)
AMOUNT = 1_044_286_814_577


class FakePayoutSource:
    def __init__(self, *, finalized: int, best: int, payouts=None):
        self.finalized = finalized
        self.best = best
        self.payouts = dict(payouts or {})

    async def finalized_head(self) -> int:
        return self.finalized

    async def best_head(self) -> int:
        return self.best

    async def block(self, number: int) -> ObservedBlock:
        return ObservedBlock(
            number=number,
            hash=f"0x{number:064x}",
            timestamp=START + dt.timedelta(seconds=(number - 1) * 12),
        )

    async def payouts_in(self, *, block: int):
        return tuple(self.payouts.get(block, ()))


def observed_payout() -> ObservedPayout:
    return ObservedPayout(
        block=102,
        block_timestamp=PAYOUT_TIME,
        extrinsic_index=7,
        event_index=13,
        origin_coldkey=DEFAULT_PROXY_FOR,
        destination_coldkey=DESTINATION_COLDKEY,
        origin_hotkey=DEFAULT_ORIGIN_HOTKEY,
        destination_hotkey=DESTINATION_HOTKEY,
        origin_netuid=DEFAULT_NETUID,
        destination_netuid=DEFAULT_NETUID,
        amount_rao=AMOUNT,
    )


def settings(dsn: str) -> PayoutWatcherSettings:
    return PayoutWatcherSettings.from_env(
        {
            "DATABASE_URL": dsn,
            "PAYOUT_WATCH_BATCH_BLOCKS": "200",
            "PAYOUT_WATCH_POLL_SECONDS": "1",
            "PAYOUT_WATCHER_ID": "test-watcher",
        }
    )


def seed_pending(sessions) -> tuple[uuid.UUID, int]:
    content = b"theorem payout_chain_fixture : True := trivial"
    digest = hashlib.sha256(content).digest()
    submission_id = uuid.uuid4()
    with sessions.begin() as session:
        session.add(Proof(digest=digest, content=content, byte_length=len(content)))
        session.flush()
        session.add(
            Submission(
                id=submission_id,
                hotkey=DESTINATION_HOTKEY,
                idempotency_key=uuid.uuid4(),
                request_digest=hashlib.sha256(b"request").digest(),
                task_id="fixture-task",
                task_bundle_sha256=hashlib.sha256(b"task").digest(),
                problem_id="fixture-problem",
                reward_target_id="fixture-target",
                task_mode=TaskMode.FORMALIZED,
                proof_digest=digest,
                payment_reference="fixture-payment",
                payment_sender=DESTINATION_COLDKEY,
                payment_amount_rao=500_000_000,
                payment_block=1,
                hotkey_signature=b"x" * 64,
                verification_status=VerificationState.VERIFIED,
                manual_review_status=ManualReviewState.APPROVED,
                reward_status=RewardState.ELIGIBLE,
                review_policy_version="v1",
                bounty_amount_rao=AMOUNT,
                bounty_policy_version="dynamic-age-v1",
                bounty_inputs={"fixture": True},
            )
        )
        session.flush()
        reward = RewardEvent(
            submission_id=submission_id,
            eligibility_reason="REVIEW_APPROVED",
            amount_rao=AMOUNT,
            pricing_policy_version="dynamic-age-v1",
            pricing_inputs={"fixture": True},
            generation_key=f"submission:{submission_id}",
            destination_coldkey=DESTINATION_COLDKEY,
            destination_hotkey=DESTINATION_HOTKEY,
            status=PayoutState.PENDING,
            initiated_by="test",
            created_at=REWARD_CREATED,
        )
        session.add(reward)
        session.flush()
        return submission_id, reward.id


def test_settings_are_bounded_and_share_the_command_renderer_identity():
    value = settings("postgresql+psycopg://unused")
    assert value.origin_coldkey == DEFAULT_PROXY_FOR
    assert value.origin_hotkey == DEFAULT_ORIGIN_HOTKEY
    assert value.netuid == DEFAULT_NETUID
    with pytest.raises(SettingsError, match="PAYOUT_WATCH_BATCH_BLOCKS"):
        PayoutWatcherSettings.from_env(
            {
                "DATABASE_URL": "postgresql+psycopg://unused",
                "PAYOUT_WATCH_BATCH_BLOCKS": "0",
            }
        )


@pytest.mark.skipif(postgres_dsn() is None, reason=DATABASE_SKIP_REASON)
def test_best_chain_means_paying_and_finalized_chain_means_paid():
    dsn = postgres_dsn()
    assert dsn is not None
    sync_engine = create_db_engine(dsn)
    async_engine = create_async_db_engine(dsn)
    try:
        Base.metadata.drop_all(sync_engine)
        Base.metadata.create_all(sync_engine)
        sync_sessions = session_factory(sync_engine)
        submission_id, reward_id = seed_pending(sync_sessions)
        async_sessions = async_session_factory(async_engine)
        source = FakePayoutSource(
            finalized=101, best=102, payouts={102: (observed_payout(),)}
        )
        watcher = PayoutWatcher(
            settings=settings(dsn), sessions=async_sessions, source=source
        )

        async def scenario():
            # A prepared command is internal state, not yet "Paying" on the website.
            async with async_sessions() as session:
                assert await latest_reward(session, submission_id) is None

            first = await watcher.scan_once()
            assert first is not None
            assert first.submitted == 1
            assert first.confirmed == 0
            async with async_sessions() as session:
                reward = await session.get(RewardEvent, reward_id)
                submission = await session.get(Submission, submission_id)
                visible = await latest_reward(session, submission_id)
                assert reward is not None and reward.status == PayoutState.SUBMITTED
                assert reward.chain_observed is True
                assert reward.extrinsic_reference == "102-7-13"
                assert submission is not None
                assert submission.reward_status == RewardState.ELIGIBLE
                assert visible is not None and visible.status == "SUBMITTED"

            source.finalized = 102
            second = await watcher.scan_once()
            assert second is not None
            assert second.confirmed == 1
            async with async_sessions() as session:
                reward = await session.get(RewardEvent, reward_id)
                submission = await session.get(Submission, submission_id)
                assert reward is not None and reward.status == PayoutState.CONFIRMED
                assert reward.chain_observed is True
                assert reward.finalized_block == 102
                assert reward.confirmed_at == PAYOUT_TIME
                assert submission is not None
                assert submission.reward_status == RewardState.REWARDED
                kinds = tuple(
                    await session.scalars(
                        select(SubmissionEvent.kind)
                        .where(SubmissionEvent.submission_id == submission_id)
                        .order_by(SubmissionEvent.id)
                    )
                )
                assert kinds == ("PAYOUT_SUBMITTED", "PAYOUT_CONFIRMED")

        asyncio.run(scenario())
    finally:
        asyncio.run(async_engine.dispose())
        sync_engine.dispose()


@pytest.mark.skipif(postgres_dsn() is None, reason=DATABASE_SKIP_REASON)
def test_a_best_chain_reorg_returns_the_tracker_to_pending():
    dsn = postgres_dsn()
    assert dsn is not None
    sync_engine = create_db_engine(dsn)
    async_engine = create_async_db_engine(dsn)
    try:
        Base.metadata.drop_all(sync_engine)
        Base.metadata.create_all(sync_engine)
        sync_sessions = session_factory(sync_engine)
        submission_id, reward_id = seed_pending(sync_sessions)
        async_sessions = async_session_factory(async_engine)
        source = FakePayoutSource(
            finalized=101, best=102, payouts={102: (observed_payout(),)}
        )
        watcher = PayoutWatcher(
            settings=settings(dsn), sessions=async_sessions, source=source
        )

        async def scenario():
            first = await watcher.scan_once()
            assert first is not None and first.submitted == 1

            # A different best-chain tail no longer contains the event from block 102.
            source.best = 103
            source.payouts.clear()
            second = await watcher.scan_once()
            assert second is not None and second.reorged == 1
            async with async_sessions() as session:
                reward = await session.get(RewardEvent, reward_id)
                assert reward is not None and reward.status == PayoutState.PENDING
                assert reward.chain_observed is False
                assert reward.extrinsic_reference is None
                assert reward.submitted_block is None
                assert await latest_reward(session, submission_id) is None
                kinds = tuple(
                    await session.scalars(
                        select(SubmissionEvent.kind)
                        .where(SubmissionEvent.submission_id == submission_id)
                        .order_by(SubmissionEvent.id)
                    )
                )
                assert kinds == ("PAYOUT_SUBMITTED", "PAYOUT_REORGED")

        asyncio.run(scenario())
    finally:
        asyncio.run(async_engine.dispose())
        sync_engine.dispose()


@pytest.mark.skipif(postgres_dsn() is None, reason=DATABASE_SKIP_REASON)
def test_legacy_paid_state_is_hidden_until_its_finalized_event_is_reobserved():
    dsn = postgres_dsn()
    assert dsn is not None
    sync_engine = create_db_engine(dsn)
    async_engine = create_async_db_engine(dsn)
    try:
        Base.metadata.drop_all(sync_engine)
        Base.metadata.create_all(sync_engine)
        sync_sessions = session_factory(sync_engine)
        submission_id, reward_id = seed_pending(sync_sessions)
        with sync_sessions.begin() as session:
            reward = session.get(RewardEvent, reward_id)
            submission = session.get(Submission, submission_id)
            assert reward is not None and submission is not None
            reward.status = PayoutState.CONFIRMED
            reward.extrinsic_reference = "legacy-operator-reference"
            reward.submitted_block = 102
            reward.finalized_block = 102
            reward.submitted_at = PAYOUT_TIME
            reward.confirmed_at = PAYOUT_TIME
            submission.reward_status = RewardState.REWARDED

        async_sessions = async_session_factory(async_engine)
        source = FakePayoutSource(
            finalized=102, best=102, payouts={102: (observed_payout(),)}
        )
        watcher = PayoutWatcher(
            settings=settings(dsn), sessions=async_sessions, source=source
        )

        async def scenario():
            # A database assertion alone is neither Paying nor Paid.
            async with async_sessions() as session:
                assert await latest_reward(session, submission_id) is None

            scanned = await watcher.scan_once()
            assert scanned is not None and scanned.confirmed == 1
            async with async_sessions() as session:
                reward = await session.get(RewardEvent, reward_id)
                submission = await session.get(Submission, submission_id)
                visible = await latest_reward(session, submission_id)
                assert reward is not None and reward.chain_observed is True
                assert reward.extrinsic_reference == "102-7-13"
                assert reward.status == PayoutState.CONFIRMED
                assert submission is not None
                assert submission.reward_status == RewardState.REWARDED
                assert visible is not None and visible.status == "CONFIRMED"
                timeline = (
                    await session.scalars(
                        select(SubmissionEvent)
                        .where(SubmissionEvent.submission_id == submission_id)
                        .order_by(SubmissionEvent.id.desc())
                    )
                ).first()
                assert timeline is not None
                assert timeline.context["replaced_extrinsic_reference"] == (
                    "legacy-operator-reference"
                )

        asyncio.run(scenario())
    finally:
        asyncio.run(async_engine.dispose())
        sync_engine.dispose()
