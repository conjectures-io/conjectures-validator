from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import uuid
from dataclasses import replace

import pytest
from conftest import DATABASE_SKIP_REASON, postgres_dsn
from sqlalchemy import select

from conjectures_subnet.db.engine import (
    async_session_factory,
    create_async_db_engine,
    create_db_engine,
    session_factory,
)
from conjectures_subnet.db import async_session_scope
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
    TreasuryPayout,
    TreasuryPayoutState,
    VerificationState,
)
from conjectures_subnet.transfers import ObservedBlock, ObservedPayout
from payout_notifier.discord import (
    DEFAULT_NETUID,
    DEFAULT_ORIGIN_HOTKEY,
    DEFAULT_PROXY_FOR,
)
from payout_watcher.settings import PayoutWatcherSettings, SettingsError
from payout_watcher.watcher import MAX_BACKOFF_SECONDS, PayoutWatcher
from conjectures_subnet.db import payouts as store
from conjectures_subnet.db.payouts import _same_payout
from submission_api.routers._account import latest_reward

DESTINATION_COLDKEY = "5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy"
# The delegated hotkey a PRE-V035 payout was staked to. This file deliberately keeps exercising
# that shape: `reward_events.destination_hotkey` is retained for exactly these rows, and
# `db/payouts.py` still matches a historical `StakeAndHotkeyTransferred` event on it. The
# current shape is covered by `observed_stake_transfer` and the test that uses it.
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


def observed_stake_transfer() -> ObservedPayout:
    """The current shape: `transfer_stake`, so the stake never leaves the origin hotkey.

    `payouts_in_events` fills both hotkey fields with the single one the event carries, which
    is what lets everything downstream read one shape — so here they are equal, and
    `moved_hotkey` is False.
    """
    return replace(
        observed_payout(),
        destination_hotkey=DEFAULT_ORIGIN_HOTKEY,
        extrinsic_index=9,
        event_index=21,
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


def seed_pending(
    sessions,
    *,
    destination_hotkey=DESTINATION_HOTKEY,
    marker: str = "",
    created_at: dt.datetime = REWARD_CREATED,
) -> tuple[uuid.UUID, int]:
    """Seed one PENDING reward event.

    `destination_hotkey=None` is the current shape — `transfer_stake` records none — and the
    default is the legacy one, so both eras of row are reconciled by this file.

    `marker` varies the three values a second row would otherwise collide on -- the proof
    text behind `submissions.proof_digest`, the payment reference, and `reward_target_id`,
    which carries a one-payout-per-target UNIQUE.  A test that needs a second outstanding
    obligation passes one; the default keeps every existing caller's fixture unchanged.

    `created_at` is set at insert because `enforce_locked_reward_event` fires on UPDATE and
    refuses a reward row that stops copying its submission's bounty lock. A test that needs an
    obligation dated after the payment it settles has to say so here.
    """
    content = b"theorem payout_chain_fixture : True := trivial" + marker.encode()
    digest = hashlib.sha256(content).digest()
    submission_id = uuid.uuid4()
    with sessions.begin() as session:
        session.add(Proof(digest=digest, content=content, byte_length=len(content)))
        session.flush()
        session.add(
            Submission(
                id=submission_id,
                signer_coldkey=DESTINATION_COLDKEY,
                idempotency_key=uuid.uuid4(),
                request_digest=hashlib.sha256(b"request").digest(),
                task_id="fixture-task",
                task_bundle_sha256=hashlib.sha256(b"task").digest(),
                problem_id="fixture-problem",
                reward_target_id=f"fixture-target{marker}",
                task_mode=TaskMode.FORMALIZED,
                proof_digest=digest,
                payment_reference=f"fixture-payment{marker}",
                payment_sender=DESTINATION_COLDKEY,
                payment_amount_rao=500_000_000,
                payment_block=1,
                signer_signature=b"x" * 64,
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
            destination_hotkey=destination_hotkey,
            status=PayoutState.PENDING,
            initiated_by="test",
            created_at=created_at,
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


@pytest.mark.skipif(postgres_dsn() is None, reason=DATABASE_SKIP_REASON)
def test_a_transfer_stake_payout_reconciles_without_a_destination_hotkey():
    """The current shape, end to end: a reward event with no destination hotkey, settled by a
    `StakeTransferred` event whose only hotkey is the validator's own.

    This is the case V035 created and the one every future payout takes. It is worth a test of
    its own rather than a parametrisation of the legacy one, because the thing being checked is
    that a NULL `destination_hotkey` still matches: `_oldest_match` compares that column only
    when the stored row has a value, and getting that wrong would leave every new payout
    permanently unmatched while the money had actually moved.
    """
    dsn = postgres_dsn()
    assert dsn is not None
    sync_engine = create_db_engine(dsn)
    async_engine = create_async_db_engine(dsn)
    try:
        Base.metadata.drop_all(sync_engine)
        Base.metadata.create_all(sync_engine)
        sync_sessions = session_factory(sync_engine)
        submission_id, reward_id = seed_pending(sync_sessions, destination_hotkey=None)
        async_sessions = async_session_factory(async_engine)
        observed = observed_stake_transfer()
        # The stake did not move off the origin hotkey, which is what distinguishes this event
        # from the legacy one the rest of the file uses.
        assert observed.moved_hotkey is False
        source = FakePayoutSource(finalized=102, best=102, payouts={102: (observed,)})
        watcher = PayoutWatcher(
            settings=settings(dsn), sessions=async_sessions, source=source
        )

        async def scenario():
            scanned = await watcher.scan_once()
            assert scanned is not None
            assert scanned.confirmed == 1
            async with async_sessions() as session:
                reward = await session.get(RewardEvent, reward_id)
                submission = await session.get(Submission, submission_id)
                assert reward is not None
                assert reward.status == PayoutState.CONFIRMED
                assert reward.chain_observed is True
                assert reward.extrinsic_reference == "102-9-21"
                # Still NULL after settlement: reconciling a payout must not backfill a
                # destination hotkey that the call never had.
                assert reward.destination_hotkey is None
                assert submission is not None
                assert submission.reward_status == RewardState.REWARDED

        asyncio.run(scenario())
    finally:
        asyncio.run(async_engine.dispose())
        sync_engine.dispose()


@pytest.mark.skipif(postgres_dsn() is None, reason=DATABASE_SKIP_REASON)
def test_a_nominated_hotkey_does_not_block_a_transfer_stake_payout():
    """A row that names a destination hotkey, paid by the call that moves no stake.

    This is the straddle the V035 changeover left behind, and it was found stranding two real
    obligations. The notifier stopped filling `destination_hotkey` only when the new code
    deployed, so rows written in between carry a hotkey the miner *nominated* while the payout
    that settles them is a `transfer_stake` whose only hotkey is the validator's own.

    Matching those two against each other compares a nomination with a constant. They can never
    be equal, so before this fix the money moved, the event was decoded correctly, and the
    obligation stayed PENDING while the watcher logged `payout_unmatched` forever. The hotkey is
    now consulted only when the event actually moved the stake, which the legacy test above
    still covers.
    """
    dsn = postgres_dsn()
    assert dsn is not None
    sync_engine = create_db_engine(dsn)
    async_engine = create_async_db_engine(dsn)
    try:
        Base.metadata.drop_all(sync_engine)
        Base.metadata.create_all(sync_engine)
        sync_sessions = session_factory(sync_engine)
        # The row carries a nominated hotkey, exactly as the pre-V035 notifier wrote it.
        submission_id, reward_id = seed_pending(sync_sessions)
        async_sessions = async_session_factory(async_engine)
        observed = observed_stake_transfer()
        assert observed.moved_hotkey is False
        assert observed.destination_hotkey != DESTINATION_HOTKEY
        source = FakePayoutSource(finalized=102, best=102, payouts={102: (observed,)})
        watcher = PayoutWatcher(
            settings=settings(dsn), sessions=async_sessions, source=source
        )

        async def scenario():
            scanned = await watcher.scan_once()
            assert scanned is not None
            assert scanned.confirmed == 1
            assert scanned.unmatched == 0
            async with async_sessions() as session:
                reward = await session.get(RewardEvent, reward_id)
                submission = await session.get(Submission, submission_id)
                assert reward is not None
                assert reward.status == PayoutState.CONFIRMED
                assert reward.chain_observed is True
                # The nomination is retained rather than overwritten with the validator's own
                # hotkey: it is what the row recorded, and settling it proves nothing about it.
                assert reward.destination_hotkey == DESTINATION_HOTKEY
                assert submission is not None
                assert submission.reward_status == RewardState.REWARDED

        asyncio.run(scenario())
    finally:
        asyncio.run(async_engine.dispose())
        sync_engine.dispose()


def test_a_legacy_payout_still_requires_its_nominated_hotkey():
    """The other side of the same rule, and the reason it is not simply dropped.

    A `StakeAndHotkeyTransferred` event genuinely moved the stake to a new position, so both
    sides name the same thing and the comparison is meaningful. Two pre-V035 obligations to the
    same coldkey for the same amount but different delegated hotkeys are different payouts, and
    one must not settle the other.
    """
    moved = observed_payout()
    assert moved.moved_hotkey is True

    class Row:
        destination_coldkey = DESTINATION_COLDKEY
        amount_rao = AMOUNT
        destination_hotkey = "5CiQaJSuTyKAWoVXYzWSweHb2ECfFJjky8bxZabw8yyyp5cT"

    # A different delegated hotkey on a stake-moving payout is still a different payout.
    assert _same_payout(Row(), moved) is False
    # ...and the matching one still settles.
    Row.destination_hotkey = DESTINATION_HOTKEY
    assert _same_payout(Row(), moved) is True


def test_a_failing_streak_backs_off_instead_of_hammering_a_rate_limit():
    """The most common scan failure is a refused chain read, and the most common refusal is an
    archive endpoint's historical-work budget.

    Retrying that on the ordinary poll interval spends the budget on rejections and can hold it
    exhausted indefinitely, so the wait grows with the streak and is capped. One success resets
    it, because a watcher that recovered must not stay slow.
    """
    dsn = postgres_dsn()
    watcher = PayoutWatcher(
        settings=settings(dsn or "postgresql://unused"), sessions=None, source=None
    )
    poll = watcher.settings.poll_seconds

    # A healthy pass waits exactly the poll interval.
    assert watcher._delay(0) == poll
    # Then doubling, so a single hiccup costs nothing noticeable.
    assert watcher._delay(1) == poll
    assert watcher._delay(2) == poll * 2
    assert watcher._delay(3) == poll * 4
    # Capped, and the cap holds however long the outage lasts rather than growing without bound.
    assert watcher._delay(50) == MAX_BACKOFF_SECONDS
    assert watcher._delay(10_000) == MAX_BACKOFF_SECONDS


def _ledger(sessions):
    """Every recorded treasury payout, oldest first, as (reference, status, reward_event_id)."""
    with sessions.begin() as session:
        return [
            (row.extrinsic_reference, row.status, row.reward_event_id)
            for row in session.scalars(
                select(TreasuryPayout).order_by(TreasuryPayout.id)
            )
        ]


@pytest.mark.skipif(postgres_dsn() is None, reason=DATABASE_SKIP_REASON)
def test_the_watcher_reads_and_records_with_nothing_outstanding():
    """Scanning no longer waits for an obligation, and an unmatched payout survives as a row.

    This is the whole inversion. Before it the cursor only moved inside a scan that an
    outstanding obligation had authorised, so a quiet period left the watcher behind finality
    and a payout made during one was never read at all. Here there is nothing owed, and the
    payout is still read, still recorded, and still available to be claimed later.
    """
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
            finalized=102, best=102, payouts={102: (observed_payout(),)}
        )
        watcher = PayoutWatcher(
            settings=settings(dsn), sessions=async_sessions, source=source
        )

        async def scenario():
            first = await watcher.scan_once()
            assert first is not None and first.confirmed == 1
            assert _ledger(sync_sessions) == [
                ("102-7-13", TreasuryPayoutState.CLAIMED, reward_id)
            ]

            # Nothing is owed now. A payout still arrives, and is still read.
            orphan = replace(
                observed_payout(),
                block=140,
                block_timestamp=START + dt.timedelta(seconds=139 * 12),
                extrinsic_index=2,
                event_index=5,
                amount_rao=999_000_000_000,
            )
            source.finalized = source.best = 150
            source.payouts[140] = (orphan,)

            second = await watcher.scan_once()
            assert second is not None
            assert second.observed == 1
            assert second.unmatched == 1
            assert second.confirmed == 0

            async with async_sessions() as session:
                cursor = await store.cursor(session)
                assert cursor is not None
                # The cursor moved with no obligation outstanding, which is the point.
                assert cursor.last_scanned_block == 150

            assert _ledger(sync_sessions) == [
                ("102-7-13", TreasuryPayoutState.CLAIMED, reward_id),
                ("140-2-5", TreasuryPayoutState.UNCLAIMED, None),
            ]
            assert submission_id is not None

        asyncio.run(scenario())
    finally:
        asyncio.run(async_engine.dispose())
        sync_engine.dispose()


@pytest.mark.skipif(postgres_dsn() is None, reason=DATABASE_SKIP_REASON)
def test_a_payout_made_before_its_obligation_is_recorded_then_bound_by_hand():
    """The incident this table was built for, end to end.

    Somebody paid a solver by hand before the notifier had seeded the obligation. Automatic
    matching must still refuse -- `_oldest_match` will not let a new obligation eat an older
    lookalike transfer, and relaxing that would be the more expensive bug. What changes is that
    refusing is no longer the end of it: the payout is a durable row, an operator binds it to the
    obligation it actually paid, and no second payout command is ever rendered.
    """
    dsn = postgres_dsn()
    assert dsn is not None
    sync_engine = create_db_engine(dsn)
    async_engine = create_async_db_engine(dsn)
    try:
        Base.metadata.drop_all(sync_engine)
        Base.metadata.create_all(sync_engine)
        sync_sessions = session_factory(sync_engine)
        async_sessions = async_session_factory(async_engine)
        seed_pending(sync_sessions)
        source = FakePayoutSource(
            finalized=102, best=102, payouts={102: (observed_payout(),)}
        )
        watcher = PayoutWatcher(
            settings=settings(dsn), sessions=async_sessions, source=source
        )
        orphan_at = START + dt.timedelta(seconds=139 * 12)
        orphan = replace(
            observed_payout(),
            block=140,
            block_timestamp=orphan_at,
            extrinsic_index=2,
            event_index=5,
        )

        async def scenario():
            # Settle the first obligation, which is what opens the cursor.
            assert (await watcher.scan_once()) is not None

            # A hand-made payment lands while nothing is owed.
            source.finalized = source.best = 150
            source.payouts[140] = (orphan,)
            scanned = await watcher.scan_once()
            assert scanned is not None
            assert scanned.unmatched == 1 and scanned.confirmed == 0
            assert _ledger(sync_sessions)[-1] == (
                "140-2-5",
                TreasuryPayoutState.UNCLAIMED,
                None,
            )

            # Only now does the notifier seed the obligation that payment was for.
            submission_id, reward_id = seed_pending(
                sync_sessions,
                marker="-late",
                created_at=orphan_at + dt.timedelta(hours=4),
            )

            # Refused, deliberately: the obligation postdates the payment by far more than the
            # chain-clock tolerance, so nothing may claim it automatically, however many passes run.
            assert (await watcher.scan_once()) is not None
            assert (await watcher.scan_once()) is not None
            async with async_sessions() as session:
                reward = await session.get(RewardEvent, reward_id)
                assert reward is not None and reward.status == PayoutState.PENDING
                queue = await store.unclaimed_payouts(session)
                assert [row.extrinsic_reference for row in queue] == ["140-2-5"]
                payout_id = queue[0].id

            # An operator says what that money was for.
            async with async_session_scope(async_sessions) as session:
                update = await store.bind_payout(
                    session,
                    payout_id=payout_id,
                    reward_event_id=reward_id,
                    note="paid by hand before the notifier was repaired",
                )
            assert update.changed is True

            async with async_sessions() as session:
                reward = await session.get(RewardEvent, reward_id)
                submission = await session.get(Submission, submission_id)
                assert reward is not None
                assert reward.status == PayoutState.CONFIRMED
                assert reward.chain_observed is True
                assert reward.extrinsic_reference == "140-2-5"
                assert submission is not None
                assert submission.reward_status == RewardState.REWARDED
                # The queue is empty, so nothing prompts a second payment.
                assert await store.unclaimed_payouts(session) == ()
            assert _ledger(sync_sessions)[-1] == (
                "140-2-5",
                TreasuryPayoutState.CLAIMED,
                reward_id,
            )

        asyncio.run(scenario())
    finally:
        asyncio.run(async_engine.dispose())
        sync_engine.dispose()


@pytest.mark.skipif(postgres_dsn() is None, reason=DATABASE_SKIP_REASON)
def test_an_obligation_seeded_just_after_its_payment_still_settles_itself():
    """Inside the chain-clock tolerance the retry pass closes the race without an operator.

    The narrow but real case: a payout is observed, matches nothing, and the notifier seeds its
    obligation moments later. Automatic matching is still permitted there, so needing a human
    would be a worse answer than retrying.
    """
    dsn = postgres_dsn()
    assert dsn is not None
    sync_engine = create_db_engine(dsn)
    async_engine = create_async_db_engine(dsn)
    try:
        Base.metadata.drop_all(sync_engine)
        Base.metadata.create_all(sync_engine)
        sync_sessions = session_factory(sync_engine)
        async_sessions = async_session_factory(async_engine)
        seed_pending(sync_sessions)
        orphan_at = START + dt.timedelta(seconds=139 * 12)
        source = FakePayoutSource(
            finalized=102, best=102, payouts={102: (observed_payout(),)}
        )
        watcher = PayoutWatcher(
            settings=settings(dsn),
            sessions=async_sessions,
            source=source,
            # The pass runs while the payment is still inside the tolerance window.
            clock=lambda: orphan_at + dt.timedelta(seconds=30),
        )

        async def scenario():
            assert (await watcher.scan_once()) is not None

            source.finalized = source.best = 150
            source.payouts[140] = (
                replace(
                    observed_payout(),
                    block=140,
                    block_timestamp=orphan_at,
                    extrinsic_index=2,
                    event_index=5,
                ),
            )
            first = await watcher.scan_once()
            assert first is not None and first.unmatched == 1
            assert _ledger(sync_sessions)[-1] == (
                "140-2-5",
                TreasuryPayoutState.UNCLAIMED,
                None,
            )

            # The obligation lands 20 seconds after the block: inside the tolerance.
            _, reward_id = seed_pending(
                sync_sessions,
                marker="-real",
                created_at=orphan_at + dt.timedelta(seconds=20),
            )
            second = await watcher.scan_once()
            assert second is not None
            assert second.confirmed == 1
            assert _ledger(sync_sessions)[-1] == (
                "140-2-5",
                TreasuryPayoutState.CLAIMED,
                reward_id,
            )

        asyncio.run(scenario())
    finally:
        asyncio.run(async_engine.dispose())
        sync_engine.dispose()


@pytest.mark.skipif(postgres_dsn() is None, reason=DATABASE_SKIP_REASON)
def test_rescanning_a_block_records_one_row_and_disregard_empties_the_queue():
    """Recording is idempotent, so replaying history is safe; disregarding ends a row's life.

    Both halves are what make the ledger operable. Without idempotence a rescan would double-count
    the treasury's own outgoings; without a disposition the unclaimed queue could only grow, since
    treasury alpha moves for reasons that are not bounties.
    """
    dsn = postgres_dsn()
    assert dsn is not None
    sync_engine = create_db_engine(dsn)
    async_engine = create_async_db_engine(dsn)
    try:
        Base.metadata.drop_all(sync_engine)
        Base.metadata.create_all(sync_engine)
        sync_sessions = session_factory(sync_engine)
        seed_pending(sync_sessions)
        async_sessions = async_session_factory(async_engine)
        unrelated = replace(
            observed_payout(), amount_rao=5_000_000_000, extrinsic_index=1, event_index=2
        )
        source = FakePayoutSource(finalized=102, best=102, payouts={102: (unrelated,)})
        watcher = PayoutWatcher(
            settings=settings(dsn), sessions=async_sessions, source=source
        )

        async def scenario():
            assert await watcher.scan_once() is not None
            assert _ledger(sync_sessions) == [
                ("102-1-2", TreasuryPayoutState.UNCLAIMED, None)
            ]

            # Replay the same block by rewinding the cursor, as a deliberate rescan would.
            async with async_session_scope(async_sessions) as session:
                cursor = await store.cursor(session)
                assert cursor is not None
                cursor.last_scanned_block = 101
            assert await watcher.scan_once() is not None
            assert _ledger(sync_sessions) == [
                ("102-1-2", TreasuryPayoutState.UNCLAIMED, None)
            ]

            async with async_sessions() as session:
                queue = await store.unclaimed_payouts(session)
                payout_id = queue[0].id
            async with async_session_scope(async_sessions) as session:
                await store.disregard_payout(
                    session, payout_id=payout_id, note="treasury rebalance, not a bounty"
                )
            async with async_sessions() as session:
                assert await store.unclaimed_payouts(session) == ()
            assert _ledger(sync_sessions) == [
                ("102-1-2", TreasuryPayoutState.DISREGARDED, None)
            ]

        asyncio.run(scenario())
    finally:
        asyncio.run(async_engine.dispose())
        sync_engine.dispose()
