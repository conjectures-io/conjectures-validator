"""Durable chain reconciliation for outbound bounty payouts.

The Discord notifier creates an obligation and renders the call; it does not prove the call ran.
This module is the only path that advances that obligation to ``SUBMITTED`` or ``CONFIRMED``.  Its
input is a successful Subtensor event decoded from a best or finalized block, and the reward event
plus submission state change in one transaction.

Matching uses the complete economic fingerprint available on chain: treasury coldkey/hotkey,
destination coldkey, subnet, and exact Alpha amount.  The chain call has no memo field for a
reward-event id.  If two outstanding obligations have an identical fingerprint, the oldest is
settled first; those calls are byte-for-byte indistinguishable on chain, so FIFO is the only stable
accounting order rather than a guess from off-chain timing.

The destination *hotkey* is part of that fingerprint only when the observed event is one that
actually moved the stake.  V035 switched payouts from ``transfer_stake_and_hotkey`` to
``transfer_stake``: the stake no longer moves to a new position, so ``transfer_stake`` reports
the validator's own hotkey as the destination and ``reward_events.destination_hotkey`` is NULL
on every obligation written since.

Which side decides is the whole subtlety, and getting it from the *event* rather than the row is
what ``_hotkey_agrees`` exists for.  A row written before the notifier stopped filling that
column records the hotkey the stake was to be moved *to* — a nomination.  Pay it with
``transfer_stake`` and the event reports the validator's key instead, so a row-driven comparison
asks a nomination to equal a constant, fails forever, and strands a correct payout as
``payout_unmatched``.  Two such rows were already outstanding when this was found.

So the stored hotkey is consulted only for a legacy ``StakeAndHotkeyTransferred`` event, where
both sides name the position the stake ended up in — and there it is still compared, because a
pre-V035 payout to the same coldkey for the same amount but a *different* delegated hotkey is a
different payout and collapsing the fingerprint would let one settle the other.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import and_, func, or_, select, true
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from conjectures_subnet.db.errors import RecordNotFound
from conjectures_subnet.db.models import (
    PayoutState,
    PayoutWatchCursor,
    RewardEvent,
    RewardState,
    Submission,
    SubmissionEvent,
    TreasuryPayout,
    TreasuryPayoutState,
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


def _hotkey_agrees(
    event: RewardEvent, observed: ObservedPayout | TreasuryPayout
) -> bool:
    """Whether a stored destination hotkey may be compared with the observed one at all.

    Only a payout that *moved* the stake carries a destination hotkey that means the same thing
    on both sides.  `transfer_stake` leaves the stake on the validator's own hotkey, so the
    destination hotkey such an event reports is that key -- configuration, not a destination
    anybody chose.  A reward row written before the payout command changed over records the
    hotkey the stake was to be moved *to*, and comparing the two compares a nomination with a
    constant: they never agree, and the obligation stalls unmatched however correct the payout.

    So the stored value is consulted only for a legacy `StakeAndHotkeyTransferred` event, where
    both sides genuinely name the position the stake ended up in.  A row that names no hotkey
    still matches anything, which is the V035-onward case and unchanged.
    """
    if not observed.moved_hotkey:
        return True
    return (
        event.destination_hotkey is None
        or event.destination_hotkey == observed.destination_hotkey
    )


def _same_payout(
    event: RewardEvent, observed: ObservedPayout | TreasuryPayout
) -> bool:
    return (
        event.destination_coldkey == observed.destination_coldkey
        and event.amount_rao == observed.amount_rao
        and _hotkey_agrees(event, observed)
    )


async def _by_reference(
    session: AsyncSession, observed: ObservedPayout | TreasuryPayout
) -> RewardEvent | None:
    statement = (
        select(RewardEvent)
        .where(RewardEvent.extrinsic_reference == observed.reference)
        .with_for_update()
    )
    return (await session.execute(statement)).scalar_one_or_none()


async def _oldest_match(
    session: AsyncSession, observed: ObservedPayout | TreasuryPayout
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
            RewardEvent.amount_rao == observed.amount_rao,
            # The SQL half of `_hotkey_agrees`, and it has to agree with it exactly: a row this
            # predicate selects is one that function will then be asked to confirm.
            or_(
                RewardEvent.destination_hotkey.is_(None),
                RewardEvent.destination_hotkey == observed.destination_hotkey,
            )
            if observed.moved_hotkey
            else true(),
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
    observed: ObservedPayout | TreasuryPayout,
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
    """Settle one finalized chain payout and its submission atomically.

    Kept for a caller holding only a decoded event and no ledger row.  The watcher goes through
    `record_payout` then `claim_payout` instead, so that a payout which matches nothing is still
    durably recorded rather than merely logged.
    """
    event = await _by_reference(session, observed)
    if event is not None:
        if not _same_payout(event, observed):
            raise PayoutConflict(
                f"reference {observed.reference} is attached to a different payout"
            )
    else:
        event = await _oldest_match(session, observed)
        if event is None:
            return None
    return await _settle(session, event, observed)


async def _settle(
    session: AsyncSession,
    event: RewardEvent,
    observed: ObservedPayout | TreasuryPayout,
    *,
    claims: TreasuryPayout | None = None,
) -> PayoutUpdate:
    """Move one obligation and its submission to paid, and bind the ledger row that paid it.

    `observed` may be a freshly decoded event or a recorded ledger row; both carry the same
    names, so there is no branch here on which it was.  `claims` is the row to mark CLAIMED, and
    is bound even when the obligation was already settled -- a reward confirmed before this table
    existed still needs its payout accounted for, or it would sit UNCLAIMED forever.
    """
    if event.status not in (
        PayoutState.PENDING,
        PayoutState.SUBMITTED,
        PayoutState.CONFIRMED,
    ):
        raise PayoutConflict(
            f"reference {observed.reference} belongs to reward event {event.id} in "
            f"{event.status}, not a reconcilable state"
        )
    submission = await _submission_for(session, event)

    def _bind() -> None:
        if claims is not None:
            claims.status = TreasuryPayoutState.CLAIMED
            claims.reward_event_id = event.id

    already_confirmed = event.status == PayoutState.CONFIRMED and event.chain_observed
    if already_confirmed and submission.reward_status == RewardState.REWARDED:
        _bind()
        await session.flush()
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
    _bind()
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


# --- The finalized payout ledger ----------------------------------------------------------
#
# Recording is unconditional and matching is a separate, repeatable step.  That split is the
# point: before it, a payout the watcher could not match was a log line over a block the cursor
# would never revisit, so a payment made before its obligation existed was gone.  Now it is a row
# that stays `UNCLAIMED` until something claims it or an operator rules it out.


async def record_payout(
    session: AsyncSession, observed: ObservedPayout
) -> TreasuryPayout:
    """Record one finalized payout, returning the existing row if it is already known.

    Idempotent on `extrinsic_reference`, which is what lets the watcher re-read a block freely:
    a restart between recording a payout and advancing the cursor, or a deliberate rescan of
    history, lands on the unique index instead of recording one chain event twice.
    """
    statement = (
        insert(TreasuryPayout)
        .values(
            extrinsic_reference=observed.reference,
            block=observed.block,
            block_timestamp=observed.block_timestamp,
            extrinsic_index=observed.extrinsic_index,
            event_index=observed.event_index,
            origin_coldkey=observed.origin_coldkey,
            origin_hotkey=observed.origin_hotkey,
            destination_coldkey=observed.destination_coldkey,
            destination_hotkey=observed.destination_hotkey,
            origin_netuid=observed.origin_netuid,
            destination_netuid=observed.destination_netuid,
            amount_rao=observed.amount_rao,
        )
        .on_conflict_do_nothing(index_elements=["extrinsic_reference"])
    )
    await session.execute(statement)
    # Re-read rather than use RETURNING: on the conflict path RETURNING yields nothing, and the
    # caller needs the stored row either way to decide what to do with it.
    existing = await session.execute(
        select(TreasuryPayout)
        .where(TreasuryPayout.extrinsic_reference == observed.reference)
        .with_for_update()
    )
    return existing.scalar_one()


async def claim_payout(
    session: AsyncSession, payout_id: int
) -> PayoutUpdate | None:
    """Settle one recorded payout against the obligation it pays, if one can be found.

    Taken by id and in its own transaction, so that a conflict raised here cannot roll back the
    recording that preceded it.  That ordering is the guarantee the ledger rests on: what the
    chain did is committed before anything tries to interpret it.

    None means the payout stays `UNCLAIMED`: either nothing matches its fingerprint, or the only
    candidates were created after it was paid.  That second case is not a failure to be retried
    into success -- it is the out-of-order payment this whole table exists to make visible, and
    it is resolved by `bind_payout` rather than by waiting.
    """
    payout = await session.get(TreasuryPayout, payout_id, with_for_update=True)
    if payout is None:
        raise RecordNotFound(f"no treasury payout {payout_id}")
    if payout.status is not TreasuryPayoutState.UNCLAIMED:
        return None
    event = await _by_reference(session, payout)
    if event is not None:
        if not _same_payout(event, payout):
            raise PayoutConflict(
                f"reference {payout.reference} is attached to a different payout"
            )
    else:
        event = await _oldest_match(session, payout)
        if event is None:
            return None
    return await _settle(session, event, payout, claims=payout)


async def unclaimed_payouts(
    session: AsyncSession,
    *,
    claimable_since: dt.datetime | None = None,
    limit: int = 100,
) -> tuple[TreasuryPayout, ...]:
    """Finalized treasury money that settles nothing: the operator's queue.

    `claimable_since` narrows it to payouts an *automatic* match could still reach, and the bound
    the reconciler passes is exactly `now - CHAIN_CLOCK_TOLERANCE`.  Anything older cannot be
    claimed automatically however many times it is retried: every obligation written from here on
    carries `created_at >= now`, and `_oldest_match` requires `created_at <= block_timestamp +
    CHAIN_CLOCK_TOLERANCE`.  Omit it for the operator's view, which wants precisely the rows
    automatic matching has given up on.
    """
    statement = select(TreasuryPayout).where(
        TreasuryPayout.status == TreasuryPayoutState.UNCLAIMED
    )
    if claimable_since is not None:
        statement = statement.where(TreasuryPayout.block_timestamp >= claimable_since)
    statement = statement.order_by(TreasuryPayout.block, TreasuryPayout.id).limit(limit)
    return tuple((await session.execute(statement)).scalars().all())


async def bind_payout(
    session: AsyncSession,
    *,
    payout_id: int,
    reward_event_id: int,
    note: str,
) -> PayoutUpdate:
    """Bind a payout to an obligation by hand, on an operator's authority.

    The deliberate escape from the clock rule in `_oldest_match`.  That rule refuses to let a new
    obligation eat an older lookalike transfer, which is right as an automatic policy and wrong as
    a final answer -- somebody paid by hand before the system knew to expect it, and only a person
    can say which submission that money was for.  `note` is required because this is the one path
    that settles money on an assertion rather than on a fingerprint.
    """
    payout = await session.get(TreasuryPayout, payout_id, with_for_update=True)
    if payout is None:
        raise RecordNotFound(f"no treasury payout {payout_id}")
    if payout.status is not TreasuryPayoutState.UNCLAIMED:
        raise PayoutConflict(
            f"treasury payout {payout_id} is {payout.status}, not UNCLAIMED"
        )
    event = await session.get(RewardEvent, reward_event_id, with_for_update=True)
    if event is None:
        raise RecordNotFound(f"no reward event {reward_event_id}")
    if event.amount_rao != payout.amount_rao:
        raise PayoutConflict(
            f"reward event {reward_event_id} is for {event.amount_rao} rao but treasury "
            f"payout {payout_id} moved {payout.amount_rao}"
        )
    if event.destination_coldkey != payout.destination_coldkey:
        raise PayoutConflict(
            f"reward event {reward_event_id} pays {event.destination_coldkey} but treasury "
            f"payout {payout_id} went to {payout.destination_coldkey}"
        )
    payout.note = note
    return await _settle(session, event, payout, claims=payout)


async def disregard_payout(
    session: AsyncSession, *, payout_id: int, note: str
) -> TreasuryPayout:
    """Rule one payout out of reconciliation, with a recorded reason.

    What keeps `UNCLAIMED` a queue somebody can empty.  Treasury alpha moves for reasons that are
    not bounties, and without this every one of them would sit in the operator's queue forever.
    """
    payout = await session.get(TreasuryPayout, payout_id, with_for_update=True)
    if payout is None:
        raise RecordNotFound(f"no treasury payout {payout_id}")
    if payout.status is not TreasuryPayoutState.UNCLAIMED:
        raise PayoutConflict(
            f"treasury payout {payout_id} is {payout.status}, not UNCLAIMED"
        )
    payout.status = TreasuryPayoutState.DISREGARDED
    payout.note = note
    await session.flush()
    return payout


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
    "bind_payout",
    "claim_payout",
    "confirm",
    "cursor",
    "disregard_payout",
    "mark_submitted",
    "oldest_unresolved_at",
    "open_cursor",
    "record_payout",
    "revert_submitted",
    "submitted_after",
    "unclaimed_payouts",
]
