from __future__ import annotations

import hashlib
import uuid

import pytest
from conftest import DATABASE_SKIP_REASON, postgres_dsn
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from conjectures_subnet.db.engine import create_db_engine, session_factory
from conjectures_subnet.db.models import (
    Base,
    ManualReviewState,
    PayoutDiscordDelivery,
    Proof,
    ReviewDecision,
    ReviewOutcome,
    ReviewerKind,
    RewardEvent,
    RewardState,
    Submission,
    TaskMode,
    VerificationState,
)
from payout_notifier.settings import NotifierSettings, SettingsError
from payout_notifier.pricing import DefectAwardQuote, quote_formalization_defect_award
from payout_notifier.worker import PayoutNotifier

HOTKEY = "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"
COLDKEY = "5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy"


def test_settings_require_the_webhook_and_bound_polling():
    with pytest.raises(SettingsError, match="PAYOUT_DISCORD_WEBHOOK_URL"):
        NotifierSettings.from_env({"DATABASE_URL": "postgresql://unused"})
    with pytest.raises(SettingsError, match="discord.com"):
        NotifierSettings.from_env(
            {
                "DATABASE_URL": "postgresql://unused",
                "PAYOUT_DISCORD_WEBHOOK_URL": "https://example.com/api/webhooks/1/token",
            }
        )

    settings = NotifierSettings.from_env(
        {
            "DATABASE_URL": "postgresql://unused",
            "PAYOUT_DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/1/token",
            "PAYOUT_NOTIFIER_POLL_SECONDS": "7",
            "PAYOUT_NOTIFIER_RETRY_SECONDS": "45",
            "PAYOUT_NOTIFIER_LEASE_SECONDS": "90",
            "PAYOUT_NOTIFIER_ID": "test-worker",
            "TAOSTATS_API_KEY": "test-taostats-key",
            "BOUNTY_NETUID": "66",
            "PAYOUT_TAOSTATS_TIMEOUT_SECONDS": "8",
        }
    )
    assert settings.poll_seconds == 7
    assert settings.retry_seconds == 45
    assert settings.lease_seconds == 90
    assert settings.worker_id == "test-worker"
    assert settings.taostats_api_key == "test-taostats-key"
    assert settings.bounty_netuid == 66
    assert settings.taostats_timeout_seconds == 8


def test_defect_award_quote_uses_decimal_prices_and_records_inputs(monkeypatch):
    records = iter(({"price": "200.00"}, {"price": "0.003"}))
    monkeypatch.setattr(
        "payout_notifier.pricing._get_one_record", lambda *_args, **_kwargs: next(records)
    )

    quote = quote_formalization_defect_award(
        api_key="test-key", netuid=66
    )

    assert quote.amount_rao == 1_250_000_000_000
    assert quote.pricing_inputs["award_usd"] == "750.00"
    assert quote.pricing_inputs["alpha_usd"] == "0.60000"
    assert quote.pricing_inputs["netuid"] == 66


@pytest.mark.skipif(postgres_dsn() is None, reason=DATABASE_SKIP_REASON)
def test_eligible_decision_creates_locked_reward_and_delivers_once_per_signer():
    engine = create_db_engine(postgres_dsn())
    try:
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        sessions = session_factory(engine)
        content = b"theorem payout_notification_fixture : True := trivial"
        digest = hashlib.sha256(content).digest()
        submission_id = uuid.uuid4()
        with sessions.begin() as session:
            session.add(
                Proof(digest=digest, content=content, byte_length=len(content))
            )
            session.flush()
            session.add(
                Submission(
                    id=submission_id,
                    hotkey=HOTKEY,
                    idempotency_key=uuid.uuid4(),
                    request_digest=hashlib.sha256(b"request").digest(),
                    task_id="fixture-task",
                    task_bundle_sha256=hashlib.sha256(b"task").digest(),
                    problem_id="fixture-problem",
                    reward_target_id="fixture-target",
                    task_mode=TaskMode.FORMALIZED,
                    proof_digest=digest,
                    payment_reference="fixture-payment",
                    payment_sender=COLDKEY,
                    payment_amount_rao=500_000_000,
                    payment_block=1,
                    hotkey_signature=b"x" * 64,
                    verification_status=VerificationState.VERIFIED,
                    manual_review_status=ManualReviewState.APPROVED,
                    reward_status=RewardState.ELIGIBLE,
                    review_policy_version="v1",
                    bounty_amount_rao=1_000,
                    bounty_policy_version="dynamic-age-v1",
                    bounty_inputs={"fixture": True},
                )
            )
            session.flush()
            session.add(
                ReviewDecision(
                    submission_id=submission_id,
                    decision=ReviewOutcome.APPROVED,
                    kind=ReviewerKind.HUMAN,
                    reviewer="test-reviewer",
                    policy_version="v1",
                    reason_code="REVIEW_APPROVED",
                )
            )

        sent: list[dict[str, object]] = []

        def sender(_url: str, payloads: list[dict[str, object]]) -> int:
            sent.extend(payloads)
            return len(payloads)

        notifier = PayoutNotifier(
            sessions=sessions,
            webhook_url="https://discord.com/api/webhooks/1/token",
            worker_id="test-worker",
            sender=sender,
        )

        first = notifier.process_once()
        second = notifier.process_once()

        assert first.payouts_seeded == 1
        assert first.seeded == 2
        assert first.delivered == 2
        assert first.failed == 0
        assert second.payouts_seeded == 0
        assert second.seeded == 0
        assert second.delivered == 0
        assert len(sent) == 2
        assert {
            str(payload["content"]).split(" ", maxsplit=1)[0] for payload in sent
        } == {"<@1103995314299490425>", "<@213454129819942912>"}
        with sessions() as session:
            reward = session.scalar(select(RewardEvent))
            assert reward is not None
            assert reward.amount_rao == 1_000
            assert reward.pricing_policy_version == "dynamic-age-v1"
            assert reward.pricing_inputs == {"fixture": True}
            assert reward.destination_coldkey == COLDKEY
            assert reward.destination_hotkey == HOTKEY
            assert reward.eligibility_reason == "REVIEW_APPROVED"
            assert reward.generation_key == f"submission:{submission_id}"
            deliveries = session.scalars(
                select(PayoutDiscordDelivery).order_by(
                    PayoutDiscordDelivery.signer_wallet
                )
            ).all()
            assert len(deliveries) == 2
            assert {item.status for item in deliveries} == {"SENT"}
            assert all(item.attempt_count == 1 for item in deliveries)
            assert all(item.delivered_at is not None for item in deliveries)
    finally:
        engine.dispose()


@pytest.mark.skipif(postgres_dsn() is None, reason=DATABASE_SKIP_REASON)
def test_defect_decision_uses_fixed_usd_quote_instead_of_full_bounty():
    engine = create_db_engine(postgres_dsn())
    try:
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        sessions = session_factory(engine)
        content = b"theorem defect_award_notification_fixture : True := trivial"
        digest = hashlib.sha256(content).digest()
        submission_id = uuid.uuid4()
        with sessions.begin() as session:
            session.add(Proof(digest=digest, content=content, byte_length=len(content)))
            session.flush()
            session.add(
                Submission(
                    id=submission_id,
                    hotkey=HOTKEY,
                    idempotency_key=uuid.uuid4(),
                    request_digest=hashlib.sha256(b"defect-request").digest(),
                    task_id="defect-fixture-task",
                    task_bundle_sha256=hashlib.sha256(b"defect-task").digest(),
                    problem_id="defect-fixture-problem",
                    reward_target_id="defect-fixture-target",
                    task_mode=TaskMode.FORMALIZED,
                    proof_digest=digest,
                    payment_reference="defect-fixture-payment",
                    payment_sender=COLDKEY,
                    payment_amount_rao=500_000_000,
                    payment_block=1,
                    hotkey_signature=b"x" * 64,
                    verification_status=VerificationState.VERIFIED,
                    manual_review_status=ManualReviewState.APPROVED,
                    reward_status=RewardState.ELIGIBLE,
                    review_policy_version="v2",
                    bounty_amount_rao=9_000_000_000_000,
                    bounty_policy_version="dynamic-age-v2-locked",
                    bounty_inputs={"displayed_full_bounty": True},
                )
            )
            session.flush()
            session.add(
                ReviewDecision(
                    submission_id=submission_id,
                    decision=ReviewOutcome.APPROVED,
                    kind=ReviewerKind.HUMAN,
                    reviewer="test-reviewer",
                    policy_version="v2",
                    reason_code="FORMALIZATION_DEFECT_AWARD",
                )
            )

        quote_count = 0

        def quote() -> DefectAwardQuote:
            nonlocal quote_count
            quote_count += 1
            return DefectAwardQuote(
                amount_rao=1_250_000_000_000,
                pricing_inputs={
                    "award_usd": "750.00",
                    "alpha_usd": "0.6",
                    "price_source": "TaoStats fixture",
                    "price_source_urls": ["https://api.taostats.io/fixture"],
                    "price_observed_at": "2026-08-07T20:00:00+00:00",
                    "netuid": 66,
                    "calculation": "750 * 1000000000 / alpha_usd",
                    "rounding": "ROUND_HALF_UP to nearest integer Alpha rao",
                },
            )

        sent: list[dict[str, object]] = []
        notifier = PayoutNotifier(
            sessions=sessions,
            webhook_url="https://discord.com/api/webhooks/1/token",
            worker_id="test-worker",
            sender=lambda _url, payloads: sent.extend(payloads) or len(payloads),
            defect_award_quoter=quote,
        )

        first = notifier.process_once()
        second = notifier.process_once()

        assert first.payouts_seeded == 1
        assert first.seeded == 2
        assert first.delivered == 2
        assert second.payouts_seeded == 0
        assert quote_count == 1
        assert len(sent) == 2
        with sessions() as session:
            reward = session.scalar(
                select(RewardEvent).where(RewardEvent.submission_id == submission_id)
            )
            assert reward is not None
            assert reward.amount_rao == 1_250_000_000_000
            assert reward.amount_rao != 9_000_000_000_000
            assert reward.pricing_policy_version == "formalization-defect-usd-v1"
            assert reward.eligibility_reason == "FORMALIZATION_DEFECT_AWARD"
            assert reward.pricing_inputs["award_code"] == "FORMALIZATION_DEFECT_AWARD"
            assert isinstance(reward.pricing_inputs["review_decision_id"], int)
        with pytest.raises(IntegrityError, match="required pricing audit inputs"):
            with sessions.begin() as session:
                reward = session.scalar(
                    select(RewardEvent).where(RewardEvent.submission_id == submission_id)
                )
                assert reward is not None
                malformed = dict(reward.pricing_inputs)
                malformed.pop("price_source_urls")
                reward.pricing_inputs = malformed
                session.flush()
    finally:
        engine.dispose()
