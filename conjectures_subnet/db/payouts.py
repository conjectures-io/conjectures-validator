"""Durable chain reconciliation for outbound bounty payouts.

The Discord notifier creates an obligation and renders the call; it does not prove the call ran.
This module is the only path that advances that obligation to ``SUBMITTED`` or ``CONFIRMED``.  Its
input is a successful Subtensor event decoded from a best or finalized block, and the reward event
plus submission state change in one transaction.

Matching uses the complete economic fingerprint available on chain: treasury coldkey/hotkey,
destination coldkey/hotkey, subnet, and exact Alpha amount.  The chain call has no memo field for a
reward-event id.  If two outstanding obligations have an identical fingerprint, the oldest is
settled first; those calls are byte-for-byte indistinguishable on chain, so FIFO is the only stable
accounting order rather than a guess from off-chain timing.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from conjectures_subnet.db.errors import RecordNotFound
from conjectures_subnet.db.models import (
    PayoutState,
    PayoutWatchCursor,
    RewardEvent,
    RewardState,
    Submission,
    SubmissionEvent,
)
from conjectures_subnet.transfers import ObservedPayout

PAYOUT_WATCHER = "payouts"
# A Subtensor block timestamp is written near the beginning of its block.  This tolerance admits a
# reward row created later in that same block while still preventing a newly created obligation
# from consuming an old, lookalike transfer during the cursor's opening boundary.
CHAIN_CLOCK_TOLERANCE = dt.timedelta(seconds=60)


class PayoutConflict(RuntimeError):
    """Stored payout state contradicts the chain event being reconciled."""


@dataclass(frozen=True)
class PayoutUpdate:
    reward_event_id: int
    submission_id: uuid.UUID
    changed: bool


@dataclass(frozen=True)
class SubmittedPayout:
    reward_event_id: int
    reference: str
    submitted_block: int


# --- Cursor -------------------------------------------------------------------------------


async def cursor(
    session: AsyncSession, *, watcher: str = PAYOUT_WATCHER
) -> PayoutWatchCursor | None:
    return await session.get(PayoutWatchCursor, watcher)


async def open_cursor(
    session: AsyncSession,
    *,
    network: str,
    origin_coldkey: str,
    origin_hotkey: str,
    netuid: int,
    watch_from: dt.datetime,
    start_block: int,
    start_block_timestamp: dt.datetime,
    watcher: str = PAYOUT_WATCHER,
) -> PayoutWatchCursor:
    row = PayoutWatchCursor(
        watcher=watcher,
        network=network,
        origin_coldkey=origin_coldkey,
        origin_hotkey=origin_hotkey,
        netuid=netuid,
        watch_from=watch_from,
        start_block=start_block,
        start_block_timestamp=start_block_timestamp,
        last_scanned_block=start_block - 1,
    )
    session.add(row)
    await session.flush()
    return row


async def advance_cursor(
    session: AsyncSession,
    *,
    through_block: int,
    now: dt.datetime,
    watcher: str = PAYOUT_WATCHER,
) -> PayoutWatchCursor:
    row = await session.get(PayoutWatchCursor, watcher, with_for_update=True)
    if row is None:
        raise RecordNotFound(f"no cursor for payout watcher {watcher!r}")
    if through_block > row.last_scanned_block:
        row.last_scanned_block = through_block
        row.last_scanned_at = now
        await session.flush()
    return row


async def oldest_unresolved_at(session: AsyncSession) -> dt.datetime | None:
    # A manually reconciled historical row may have been written after its transfer.  Prefer its
    # submitted time when that is earlier so the first replay starts far enough back to verify it.
    watch_from = func.least(
        RewardEvent.created_at,
        func.coalesce(RewardEvent.submitted_at, RewardEvent.created_at),
    )
    statement = (
        select(watch_from)
        .where(
            or_(
                RewardEvent.status.in_((PayoutState.PENDING, PayoutState.SUBMITTED)),
                and_(
                    RewardEvent.status == PayoutState.CONFIRMED,
                    RewardEvent.chain_observed.is_(False),
                ),
            )
        )
        .order_by(watch_from, RewardEvent.id)
        .limit(1)
    )
    return (await session.execute(statement)).scalar_one_or_none()


# --- Event settlement ---------------------------------------------------------------------


def _same_payout(event: RewardEvent, observed: ObservedPayout) -> bool:
    return (
        event.destination_coldkey == observed.destination_coldkey
        and event.destination_hotkey == observed.destination_hotkey
        and event.amount_rao == observed.amount_rao
    )


async def _by_reference(
    session: AsyncSession, observed: ObservedPayout
) -> RewardEvent | None:
    statement = (
        select(RewardEvent)
        .where(RewardEvent.extrinsic_reference == observed.reference)
        .with_for_update()
    )
    return (await session.execute(statement)).scalar_one_or_none()


async def _oldest_match(
    session: AsyncSession, observed: ObservedPayout
) -> RewardEvent | None:
    # Unverified legacy states participate in the same FIFO as new obligations.  A legacy
    # reference may not use the canonical block-extrinsic-event form, so the complete economic
    # fingerprint is what lets a replay replace that assertion with the event actually observed.
    statement = (
        select(RewardEvent)
        .where(
            or_(
                and_(
                    RewardEvent.status == PayoutState.PENDING,
                    RewardEvent.extrinsic_reference.is_(None),
                ),
                and_(
                    RewardEvent.status.in_(
                        (PayoutState.SUBMITTED, PayoutState.CONFIRMED)
                    ),
                    RewardEvent.chain_observed.is_(False),
                ),
            ),
            RewardEvent.destination_coldkey == observed.destination_coldkey,
            RewardEvent.destination_hotkey == observed.destination_hotkey,
            RewardEvent.amount_rao == observed.amount_rao,
            RewardEvent.created_at
            <= observed.block_timestamp + CHAIN_CLOCK_TOLERANCE,
        )
        .order_by(RewardEvent.created_at, RewardEvent.id)
        .with_for_update()
        .limit(1)
    )
    return (await session.execute(statement)).scalar_one_or_none()


async def _submission_for(
    session: AsyncSession, event: RewardEvent
) -> Submission:
    submission = await session.get(Submission, event.submission_id, with_for_update=True)
    if submission is None:
        raise PayoutConflict(
            f"reward event {event.id} names missing submission {event.submission_id}"
        )
    return submission


def _timeline(
    *,
    submission_id: uuid.UUID,
    kind: str,
    detail: str,
    observed: ObservedPayout,
    previous_reference: str | None = None,
) -> SubmissionEvent:
    context: dict[str, object] = {
        "extrinsic_reference": observed.reference,
        "block": observed.block,
        "amount_rao": observed.amount_rao,
    }
    if previous_reference is not None and previous_reference != observed.reference:
        context["replaced_extrinsic_reference"] = previous_reference
    return SubmissionEvent(
        submission_id=submission_id,
        kind=kind,
        detail=detail,
        context=context,
        actor="payout-watcher",
        occurred_at=observed.block_timestamp,
    )


async def mark_submitted(
    session: AsyncSession, observed: ObservedPayout
) -> PayoutUpdate | None:
    """Record a payout event from the best head as submitted, but not yet paid.

    Best-chain state can reorganize, so this transition is reversible by ``revert_submitted``.
    It exists to make the site's "Paying" label literal: there is a successful chain event, but
    the block is not finalized yet.
    """
    event = await _by_reference(session, observed)
    if event is not None:
        if not _same_payout(event, observed):
            raise PayoutConflict(
                f"reference {observed.reference} is attached to a different payout"
            )
        if event.chain_observed and event.status in (
            PayoutState.SUBMITTED,
            PayoutState.CONFIRMED,
        ):
            return PayoutUpdate(event.id, event.submission_id, changed=False)
        if event.status not in (
            PayoutState.PENDING,
            PayoutState.SUBMITTED,
            PayoutState.CONFIRMED,
        ):
            raise PayoutConflict(
                f"reference {observed.reference} belongs to reward event {event.id} in "
                f"{event.status}"
            )
    else:
        event = await _oldest_match(session, observed)
        if event is None:
            return None
    submission = await _submission_for(session, event)
    # Before V013 a CONFIRMED row could set this cached projection without event provenance.  A
    # best-chain observation proves only Paying, so demote that cache until finality is observed.
    if submission.reward_status == RewardState.REWARDED and not event.chain_observed:
        submission.reward_status = RewardState.ELIGIBLE
    if submission.reward_status != RewardState.ELIGIBLE:
        raise PayoutConflict(
            f"reward event {event.id} matched chain but submission {submission.id} is "
            f"{submission.reward_status}, not ELIGIBLE"
        )

    previous_reference = event.extrinsic_reference
    transition_at = max(event.created_at, observed.block_timestamp)
    event.status = PayoutState.SUBMITTED
    event.chain_observed = True
    event.extrinsic_reference = observed.reference
    event.submitted_block = observed.block
    event.submitted_at = transition_at
    event.finalized_block = None
    event.confirmed_at = None
    event.failure_reason = None
    session.add(
        _timeline(
            submission_id=submission.id,
            kind="PAYOUT_SUBMITTED",
            detail="The payout appeared on chain and is waiting for finality.",
            observed=observed,
            previous_reference=previous_reference,
        )
    )
    await session.flush()
    return PayoutUpdate(event.id, submission.id, changed=True)


async def confirm(
    session: AsyncSession, observed: ObservedPayout
) -> PayoutUpdate | None:
    """Settle one finalized chain payout and its submission atomically."""
    event = await _by_reference(session, observed)
    if event is not None:
        if not _same_payout(event, observed):
            raise PayoutConflict(
                f"reference {observed.reference} is attached to a different payout"
            )
        if event.status not in (
            PayoutState.PENDING,
            PayoutState.SUBMITTED,
            PayoutState.CONFIRMED,
        ):
            raise PayoutConflict(
                f"reference {observed.reference} belongs to reward event {event.id} in "
                f"{event.status}, not a reconcilable state"
            )
    else:
        event = await _oldest_match(session, observed)
        if event is None:
            return None

    submission = await _submission_for(session, event)
    already_confirmed = (
        event.status == PayoutState.CONFIRMED and event.chain_observed
    )
    if already_confirmed and submission.reward_status == RewardState.REWARDED:
        return PayoutUpdate(event.id, event.submission_id, changed=False)
    if submission.reward_status not in (RewardState.ELIGIBLE, RewardState.REWARDED):
        raise PayoutConflict(
            f"reward event {event.id} matched finalized chain but submission {submission.id} is "
            f"{submission.reward_status}, not ELIGIBLE or REWARDED"
        )

    # A watcher that was behind finality may see the event for the first time here.  In that case
    # submitted and confirmed are the same observed chain fact, and no invented intermediate state
    # is exposed merely to make the state machine visit every label.
    previous_reference = event.extrinsic_reference
    transition_at = max(event.created_at, observed.block_timestamp)
    event.status = PayoutState.CONFIRMED
    event.chain_observed = True
    event.extrinsic_reference = observed.reference
    event.submitted_block = observed.block
    event.finalized_block = observed.block
    event.submitted_at = transition_at
    event.confirmed_at = transition_at
    event.failure_reason = None
    submission.reward_status = RewardState.REWARDED
    session.add(
        _timeline(
            submission_id=submission.id,
            kind="PAYOUT_CONFIRMED",
            detail="The payout finalized on chain.",
            observed=observed,
            previous_reference=previous_reference,
        )
    )
    await session.flush()
    return PayoutUpdate(event.id, submission.id, changed=True)


async def submitted_after(
    session: AsyncSession, finalized_block: int
) -> tuple[SubmittedPayout, ...]:
    statement = select(
        RewardEvent.id,
        RewardEvent.extrinsic_reference,
        RewardEvent.submitted_block,
    ).where(
        RewardEvent.status == PayoutState.SUBMITTED,
        RewardEvent.chain_observed.is_(True),
        RewardEvent.extrinsic_reference.is_not(None),
        RewardEvent.submitted_block.is_not(None),
        RewardEvent.submitted_block > finalized_block,
    )
    return tuple(
        SubmittedPayout(
            reward_event_id=row.id,
            reference=row.extrinsic_reference,
            submitted_block=row.submitted_block,
        )
        for row in (await session.execute(statement)).all()
    )


async def revert_submitted(
    session: AsyncSession,
    submitted: SubmittedPayout,
    *,
    now: dt.datetime,
) -> bool:
    """Return a best-chain payout to pending after its event was reorganized away."""
    event = await session.get(RewardEvent, submitted.reward_event_id, with_for_update=True)
    if (
        event is None
        or event.status != PayoutState.SUBMITTED
        or event.extrinsic_reference != submitted.reference
    ):
        return False
    event.status = PayoutState.PENDING
    event.chain_observed = False
    event.extrinsic_reference = None
    event.submitted_block = None
    event.submitted_at = None
    event.finalized_block = None
    event.confirmed_at = None
    event.failure_reason = None
    session.add(
        SubmissionEvent(
            submission_id=event.submission_id,
            kind="PAYOUT_REORGED",
            detail="The unfinalized payout event left the best chain; the payout is pending again.",
            context={"extrinsic_reference": submitted.reference},
            actor="payout-watcher",
            occurred_at=now,
        )
    )
    await session.flush()
    return True


__all__ = [
    "CHAIN_CLOCK_TOLERANCE",
    "PAYOUT_WATCHER",
    "PayoutConflict",
    "PayoutUpdate",
    "SubmittedPayout",
    "advance_cursor",
    "confirm",
    "cursor",
    "mark_submitted",
    "oldest_unresolved_at",
    "open_cursor",
    "revert_submitted",
    "submitted_after",
]
