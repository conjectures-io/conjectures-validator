from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Callable

from sqlalchemy import and_, exists, func, literal, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from conjectures_subnet.db.models import (
    Account,
    PayoutDiscordDelivery,
    PayoutState,
    ReviewDecision,
    ReviewerKind,
    RewardEvent,
    RewardState,
    Submission,
)
from payout_notifier.discord import (
    DEFAULT_DISCORD_MENTIONS,
    DEFAULT_MULTISIG,
    DEFAULT_NETUID,
    DEFAULT_NETWORK,
    DEFAULT_ORIGIN_HOTKEY,
    DEFAULT_PROXY_FOR,
    discord_notifications,
    send_discord_notifications,
)
from payout_notifier.pricing import (
    DefectAwardQuote,
    FORMALIZATION_DEFECT_POLICY_VERSION,
)

logger = logging.getLogger("payout_notifier")


@dataclass(frozen=True)
class ClaimedDelivery:
    reward_event_id: int
    submission_id: str
    destination_coldkey: str
    destination_hotkey: str
    amount_rao: int
    signer_wallet: str
    discord_user_id: str


@dataclass(frozen=True)
class Processed:
    payouts_seeded: int = 0
    seeded: int = 0
    delivered: int = 0
    failed: int = 0


class PayoutNotifier:
    """Turn eligible decisions into locked payout events, then notify every signer."""

    def __init__(
        self,
        *,
        sessions: sessionmaker[Session],
        webhook_url: str,
        worker_id: str,
        retry_seconds: float = 30.0,
        lease_seconds: float = 60.0,
        sender: Callable[[str, list[dict[str, object]]], int] = send_discord_notifications,
        defect_award_quoter: Callable[[], DefectAwardQuote] | None = None,
    ) -> None:
        self.sessions = sessions
        self.webhook_url = webhook_url
        self.worker_id = worker_id
        self.retry_seconds = retry_seconds
        self.lease_seconds = lease_seconds
        self.sender = sender
        self.defect_award_quoter = defect_award_quoter

    def seed_reward_events(self) -> int:
        """Create one idempotent payout instruction for each newly eligible submission.

        Full bounties copy the submission-time lock byte-for-byte. A binding
        ``FORMALIZATION_DEFECT_AWARD`` instead creates the policy's fixed $750 payout using one
        current authoritative quote shared by every defect award in this pass. An
        account-configured payout pair takes precedence; legacy direct submissions fall back to
        the coldkey that funded the attempt and the submitting hotkey. Credit submissions with no
        configured payout coldkey are deliberately skipped rather than guessed.
        """
        with self.sessions() as session:
            latest_decision_id = (
                select(ReviewDecision.id)
                .where(
                    ReviewDecision.submission_id == Submission.id,
                    ReviewDecision.kind != ReviewerKind.ADVISORY,
                )
                .order_by(ReviewDecision.id.desc())
                .limit(1)
                .correlate(Submission)
                .scalar_subquery()
            )
            latest_reason = (
                select(ReviewDecision.reason_code)
                .where(
                    ReviewDecision.submission_id == Submission.id,
                    ReviewDecision.kind != ReviewerKind.ADVISORY,
                )
                .order_by(ReviewDecision.id.desc())
                .limit(1)
                .correlate(Submission)
                .scalar_subquery()
            )
            destination_coldkey = func.coalesce(
                Account.payout_coldkey, Submission.payment_sender
            )
            destination_hotkey = func.coalesce(
                Account.payout_hotkey, Submission.hotkey
            )
            existing_reward = exists(
                select(RewardEvent.id).where(
                    RewardEvent.submission_id == Submission.id
                )
            ).correlate(Submission)
            candidates = session.execute(
                select(
                    Submission.id,
                    latest_decision_id.label("review_decision_id"),
                    func.coalesce(
                        latest_reason, literal("REWARD_ELIGIBLE")
                    ).label("eligibility_reason"),
                    Submission.bounty_amount_rao.label("locked_amount_rao"),
                    Submission.bounty_policy_version.label("locked_policy_version"),
                    Submission.bounty_inputs.label("locked_pricing_inputs"),
                    destination_coldkey.label("destination_coldkey"),
                    destination_hotkey.label("destination_hotkey"),
                )
                .outerjoin(Account, Account.id == Submission.account_id)
                .where(
                    Submission.reward_status == RewardState.ELIGIBLE,
                    destination_coldkey.is_not(None),
                    destination_hotkey.is_not(None),
                    ~existing_reward,
                    or_(
                        latest_reason == "FORMALIZATION_DEFECT_AWARD",
                        Submission.bounty_locked_at.is_not(None),
                    ),
                )
            ).all()

        defect_quote: DefectAwardQuote | None = None
        if any(row.eligibility_reason == "FORMALIZATION_DEFECT_AWARD" for row in candidates):
            if self.defect_award_quoter is None:
                raise RuntimeError(
                    "a FORMALIZATION_DEFECT_AWARD is eligible but no TaoStats quoter is configured"
                )
            defect_quote = self.defect_award_quoter()
            if defect_quote.amount_rao <= 0:
                raise ValueError("the defect-award quote must contain a positive rao amount")

        created = 0
        with self.sessions.begin() as session:
            for candidate in candidates:
                if candidate.eligibility_reason == "FORMALIZATION_DEFECT_AWARD":
                    assert defect_quote is not None
                    pricing_inputs = dict(defect_quote.pricing_inputs)
                    pricing_inputs.update(
                        {
                            "award_code": "FORMALIZATION_DEFECT_AWARD",
                            "review_decision_id": candidate.review_decision_id,
                        }
                    )
                    amount_rao = defect_quote.amount_rao
                    pricing_policy_version = FORMALIZATION_DEFECT_POLICY_VERSION
                else:
                    amount_rao = candidate.locked_amount_rao
                    pricing_policy_version = candidate.locked_policy_version
                    pricing_inputs = candidate.locked_pricing_inputs

                statement = (
                    insert(RewardEvent)
                    .values(
                        submission_id=candidate.id,
                        eligibility_reason=candidate.eligibility_reason,
                        amount_rao=amount_rao,
                        pricing_policy_version=pricing_policy_version,
                        pricing_inputs=pricing_inputs,
                        destination_coldkey=candidate.destination_coldkey,
                        destination_hotkey=candidate.destination_hotkey,
                        initiated_by="payout-notifier:auto",
                        generation_key=f"submission:{candidate.id}",
                    )
                    .on_conflict_do_nothing(
                        index_elements=[RewardEvent.generation_key],
                        index_where=RewardEvent.generation_key.is_not(None),
                    )
                    .returning(RewardEvent.id)
                )
                if session.scalar(statement) is not None:
                    created += 1
        return created

    def seed(self) -> int:
        """Create missing per-signer outbox rows for every currently pending payout."""
        created = 0
        with self.sessions.begin() as session:
            for wallet, user_id in DEFAULT_DISCORD_MENTIONS.items():
                candidate = select(
                    RewardEvent.id,
                    literal(wallet),
                    literal(user_id),
                ).where(
                    RewardEvent.status == PayoutState.PENDING,
                    RewardEvent.extrinsic_reference.is_(None),
                )
                statement = (
                    insert(PayoutDiscordDelivery)
                    .from_select(
                        ["reward_event_id", "signer_wallet", "discord_user_id"],
                        candidate,
                    )
                    .on_conflict_do_nothing(
                        index_elements=["reward_event_id", "signer_wallet"]
                    )
                    .returning(PayoutDiscordDelivery.reward_event_id)
                )
                result = session.execute(statement)
                created += len(result.scalars().all())
        return created

    def claim(self) -> ClaimedDelivery | None:
        now = dt.datetime.now(dt.UTC)
        with self.sessions.begin() as session:
            due = or_(
                and_(
                    PayoutDiscordDelivery.status.in_(("PENDING", "FAILED")),
                    PayoutDiscordDelivery.next_attempt_at <= now,
                ),
                and_(
                    PayoutDiscordDelivery.status == "SENDING",
                    PayoutDiscordDelivery.lease_until <= now,
                ),
            )
            statement = (
                select(
                    PayoutDiscordDelivery,
                    RewardEvent.id.label("claimed_reward_event_id"),
                    RewardEvent.submission_id.label("claimed_submission_id"),
                    RewardEvent.destination_coldkey.label("claimed_destination_coldkey"),
                    RewardEvent.destination_hotkey.label("claimed_destination_hotkey"),
                    RewardEvent.amount_rao.label("claimed_amount_rao"),
                )
                .join(
                    RewardEvent,
                    RewardEvent.id == PayoutDiscordDelivery.reward_event_id,
                )
                .where(
                    due,
                    RewardEvent.status == PayoutState.PENDING,
                    RewardEvent.extrinsic_reference.is_(None),
                )
                .order_by(
                    PayoutDiscordDelivery.next_attempt_at,
                    PayoutDiscordDelivery.reward_event_id,
                    PayoutDiscordDelivery.signer_wallet,
                )
                .with_for_update(of=PayoutDiscordDelivery, skip_locked=True)
                .limit(1)
            )
            row = session.execute(statement).one_or_none()
            if row is None:
                return None
            delivery = row[0]
            delivery.status = "SENDING"
            delivery.attempt_count += 1
            delivery.lease_owner = self.worker_id
            delivery.lease_until = now + dt.timedelta(seconds=self.lease_seconds)
            delivery.updated_at = now
            return ClaimedDelivery(
                reward_event_id=row.claimed_reward_event_id,
                submission_id=str(row.claimed_submission_id),
                destination_coldkey=row.claimed_destination_coldkey,
                destination_hotkey=row.claimed_destination_hotkey,
                amount_rao=row.claimed_amount_rao,
                signer_wallet=delivery.signer_wallet,
                discord_user_id=delivery.discord_user_id,
            )

    def _finish(self, claimed: ClaimedDelivery, *, error: str | None) -> None:
        now = dt.datetime.now(dt.UTC)
        with self.sessions.begin() as session:
            delivery = session.get(
                PayoutDiscordDelivery,
                (claimed.reward_event_id, claimed.signer_wallet),
                with_for_update=True,
            )
            if (
                delivery is None
                or delivery.status != "SENDING"
                or delivery.lease_owner != self.worker_id
            ):
                logger.warning(
                    "lost delivery lease reward_event=%s signer=%s",
                    claimed.reward_event_id,
                    claimed.signer_wallet,
                )
                return
            delivery.lease_owner = None
            delivery.lease_until = None
            delivery.updated_at = now
            if error is None:
                delivery.status = "SENT"
                delivery.delivered_at = now
                delivery.last_error = None
            else:
                delivery.status = "FAILED"
                delivery.next_attempt_at = now + dt.timedelta(seconds=self.retry_seconds)
                delivery.last_error = error[:2_000]

    def process_once(self) -> Processed:
        payouts_seeded = self.seed_reward_events()
        seeded = self.seed()
        delivered = 0
        failed = 0
        while claimed := self.claim():
            try:
                payloads = discord_notifications(
                    [
                        (
                            claimed.reward_event_id,
                            claimed.submission_id,
                            claimed.destination_coldkey,
                            claimed.destination_hotkey,
                            claimed.amount_rao,
                        )
                    ],
                    wallets=(claimed.signer_wallet,),
                    mentions={claimed.signer_wallet: claimed.discord_user_id},
                    origin_hotkey=DEFAULT_ORIGIN_HOTKEY,
                    origin_netuid=DEFAULT_NETUID,
                    destination_netuid=DEFAULT_NETUID,
                    proxy_for=DEFAULT_PROXY_FOR,
                    multisig=DEFAULT_MULTISIG,
                    network=DEFAULT_NETWORK,
                )
                accepted = self.sender(self.webhook_url, payloads)
                if accepted != len(payloads):
                    raise RuntimeError(
                        f"Discord accepted {accepted} of {len(payloads)} messages"
                    )
            except Exception as exc:
                failed += 1
                self._finish(claimed, error=f"{type(exc).__name__}: {exc}")
                logger.exception(
                    "Discord payout delivery failed reward_event=%s signer=%s",
                    claimed.reward_event_id,
                    claimed.signer_wallet,
                )
            else:
                delivered += 1
                self._finish(claimed, error=None)
                logger.info(
                    "Discord payout delivery sent reward_event=%s signer=%s user=%s",
                    claimed.reward_event_id,
                    claimed.signer_wallet,
                    claimed.discord_user_id,
                )
        return Processed(
            payouts_seeded=payouts_seeded,
            seeded=seeded,
            delivered=delivered,
            failed=failed,
        )


__all__ = ["ClaimedDelivery", "PayoutNotifier", "Processed"]
