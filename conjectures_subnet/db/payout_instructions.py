"""Audited cancellation/restoration of unsigned payout instructions; never signs money.

The reward event remains the immutable amount/destination and chain history. Actions are
append-only, so restoring a mistaken cancellation does not erase it. Callers own the transaction.
"""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from conjectures_subnet.db.errors import RecordConflict, RecordNotFound
from conjectures_subnet.db.models import (
    Account, REVIEWER_ROLE, ADMIN_ROLE, ManualReviewState, PayoutInstructionAction,
    PayoutState, PayoutDiscordDelivery, RewardEvent, RewardState, Submission, SubmissionEvent, TreasuryPayout,
)


def instruction_enabled():
    """SQL predicate shared by every command-producing path and chain matcher."""
    latest = (select(PayoutInstructionAction.action)
              .where(PayoutInstructionAction.reward_event_id == RewardEvent.id)
              .order_by(PayoutInstructionAction.id.desc()).limit(1)
              .correlate(RewardEvent).scalar_subquery())
    return func.coalesce(latest, "RESTORE") != "CANCEL"


def validate_chain_evidence(evidence: dict, *, now: dt.datetime) -> None:
    """Require a recent explicit operator check; this is not an on-chain revocation."""
    try:
        checked = dt.datetime.fromisoformat(evidence["checked_at"])
        age = (now - checked).total_seconds()
    except (KeyError, TypeError, ValueError):
        raise RecordConflict("missing dated chain check", reason_code="PAYOUT_CHAIN_CHECK_REQUIRED")
    if not 0 <= age <= 120:
        raise RecordConflict("chain check is stale or future-dated", reason_code="PAYOUT_CHAIN_CHECK_STALE")
    if (evidence.get("network") != "finney"
            or not evidence.get("multisig_address")
            or evidence.get("pending_multisig_count") != 0
            or type(evidence.get("pending_multisig_count")) is not int
            or evidence.get("no_matching_chain_transfer") is not True
            or not evidence.get("finalized_block_hash")):
        raise RecordConflict("a clear multisig and chain check is required", reason_code="PAYOUT_CHAIN_CHECK_REQUIRED")


def unsigned(event: RewardEvent) -> bool:
    return (event.status == PayoutState.PENDING and not event.chain_observed
            and all(getattr(event, field) is None for field in (
                "extrinsic_reference", "submitted_block", "finalized_block",
                "submitted_at", "confirmed_at", "failure_reason")))


async def change_instruction(
    session: AsyncSession, *, reward_event_id: int, submission_id: uuid.UUID,
    expected_review_id: int, expected_action_id: int | None, action: str,
    reviewer: str, reason: str, chain_evidence: dict,
) -> PayoutInstructionAction:
    from conjectures_subnet.db.submissions import latest_binding_decision

    if action not in {"CANCEL", "RESTORE"} or not reason.strip():
        raise ValueError("CANCEL/RESTORE and a nonempty reason are required")
    validate_chain_evidence(chain_evidence, now=dt.datetime.now(dt.UTC))
    account = await session.get(Account, uuid.UUID(reviewer))
    if account is None or not ({REVIEWER_ROLE, ADMIN_ROLE} & set(account.roles)):
        raise RecordConflict("reviewer role required", reason_code="PAYOUT_REVIEWER_REQUIRED")
    # Same lock order as chain reconciliation: event, then submission.
    event = await session.get(RewardEvent, reward_event_id, with_for_update=True)
    if event is None or event.submission_id != submission_id:
        raise RecordNotFound("payout does not belong to this submission")
    submission = await session.get(Submission, submission_id, with_for_update=True)
    decision = await latest_binding_decision(session, submission_id)
    if decision is None or decision.id != expected_review_id:
        raise RecordConflict("review changed", reason_code="REVIEW_CORRECTION_STALE")
    if not unsigned(event):
        raise RecordConflict("payout has transaction or failure history", reason_code="PAYOUT_NOT_UNSIGNED")
    # A watcher-observed transfer must block even if its binding has not yet caught up.
    observed = await session.scalar(select(TreasuryPayout.id).where(
        TreasuryPayout.destination_coldkey == event.destination_coldkey,
        TreasuryPayout.amount_rao == event.amount_rao,
        TreasuryPayout.block_timestamp >= event.created_at - dt.timedelta(minutes=5),
    ).limit(1))
    if observed is not None:
        raise RecordConflict("matching treasury transfer exists", reason_code="PAYOUT_CHAIN_TRANSFER_EXISTS")
    deliveries = (await session.scalars(select(PayoutDiscordDelivery).where(
        PayoutDiscordDelivery.reward_event_id == event.id).with_for_update())).all()
    if any(delivery.status == "SENDING" for delivery in deliveries):
        raise RecordConflict("a notification is in flight", reason_code="PAYOUT_NOTIFICATION_IN_FLIGHT")
    latest = await session.scalar(select(PayoutInstructionAction).where(
        PayoutInstructionAction.reward_event_id == event.id
    ).order_by(PayoutInstructionAction.id.desc()).limit(1))
    if (latest.id if latest else None) != expected_action_id:
        raise RecordConflict("instruction changed", reason_code="PAYOUT_INSTRUCTION_STALE")
    if latest is not None and latest.action == action:
        raise RecordConflict("instruction already has this disposition", reason_code="PAYOUT_INSTRUCTION_UNCHANGED")
    if action == "RESTORE" and (latest is None or latest.action != "CANCEL"):
        raise RecordConflict("only a cancellation can be restored", reason_code="PAYOUT_NOT_CANCELLED")
    if (submission.manual_review_status != ManualReviewState.APPROVED
            or submission.reward_status != RewardState.ELIGIBLE):
        raise RecordConflict("instruction changes require an approved eligible review", reason_code="PAYOUT_NOT_ELIGIBLE")
    record = PayoutInstructionAction(
        reward_event_id=event.id, action=action, actor=reviewer, reason=reason.strip(),
        review_decision_id=decision.id, chain_evidence=chain_evidence,
    )
    session.add(record)
    await session.flush()
    session.add(SubmissionEvent(submission_id=submission.id,
        kind="PAYOUT_INSTRUCTION_" + ("CANCELLED" if action == "CANCEL" else "RESTORED"),
        detail=reason.strip(), actor=reviewer,
        context={"reward_event_id":event.id, "instruction_action_id":record.id,
                 "review_decision_id":decision.id}))
    return record


async def cancel_and_withdraw(session: AsyncSession, *, reason_code: str,
                              notes: str, notes_public: str, **kwargs):
    """One transaction: suppress the instruction and append a rejection correction."""
    from conjectures_subnet.db.submissions import correct_human_decision
    from conjectures_subnet.db.models import ReviewOutcome
    from submission_api.credits import DISQUALIFICATION_CODES
    if reason_code not in DISQUALIFICATION_CODES or not notes.strip() or not notes_public.strip():
        raise ValueError("published rejection code and internal/public rationale required")
    action = await change_instruction(session, action="CANCEL", **kwargs)
    decision = await correct_human_decision(session, kwargs["submission_id"],
        supersedes_id=kwargs["expected_review_id"], decision=ReviewOutcome.REJECTED,
        reason_code=reason_code, reviewer=kwargs["reviewer"], notes=notes, notes_public=notes_public)
    return action, decision
