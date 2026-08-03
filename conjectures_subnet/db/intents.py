"""Submission intents: hold a credit, attach a bundle, then spend and submit.

Three phases, and the split is the point. A miner should learn that their bundle is
admissible, and what digest to sign, *before* a credit leaves their balance — but the
credit has to be claimed up front or a miner could open unlimited intents against one
credit and race the confirmations.

    open      → a credit is HELD (a claim on the balance, not a movement)
    attach    → the bundle is admitted, the server computes the digest to sign
    confirm   → the credit is SPENT and the submission is written, in one transaction

The hold is state on the intent row rather than a ledger entry, because a claim that
may evaporate cannot be written to an append-only ledger. See
``conjectures_subnet.db.credits`` for the balance arithmetic that reads it.

``confirm`` is where the money moves, so its ordering is a correctness requirement:

1. lock the account, so no concurrent confirm or open sees the same balance;
2. re-read the intent under that lock and refuse anything not still attached — the
   status check has to happen after the lock, not before it;
3. write the proof bytes into ``proofs``;
4. append the SPEND entry, naming the intent;
5. insert the submission pointing at that entry;
6. mark the intent CONFIRMED with the submission id.

Steps 4 and 5 are in that order because ``submissions.credit_ledger_id`` references the
ledger. The SPEND names the *intent* rather than the submission precisely so this order
is possible; a mutual reference would leave neither row insertable first, and an
append-only ledger cannot be backfilled.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from conjectures_subnet.db import credits, digests
from conjectures_subnet.db import submissions as submission_store
from conjectures_subnet.db.errors import (
    DuplicateProof,
    RecordConflict,
    RecordNotFound,
)
from conjectures_subnet.db.models import (
    CreditEntryKind,
    IntentState,
    Submission,
    SubmissionEvent,
    SubmissionIntent,
)

PROOF_CONSTRAINT = "submissions_proof_digest_key"

# The event kinds this module writes. The timeline is what a miner reads while
# waiting, so every state change worth asking about gets one.
EVENT_ACCEPTED = "SUBMISSION_ACCEPTED"
EVENT_QUEUED = "QUEUED_FOR_VERIFICATION"


@dataclass(frozen=True)
class ConfirmedIntent:
    intent: SubmissionIntent
    submission: Submission
    balance: credits.CreditBalance


async def open_intent(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    hotkey: str,
    task_id: str,
    task_bundle_sha256: str,
    credit_price_rao: int,
    expires_at: dt.datetime,
    now: dt.datetime,
    credits_held: int = 1,
) -> tuple[SubmissionIntent, credits.CreditBalance]:
    """Hold a credit and open an intent, or refuse for insufficient credit.

    The account is locked before the balance is read, so two concurrent calls cannot
    both see the last credit and both succeed. The balance returned is the one *after*
    the hold, which is what the caller should show: it is what the account can still
    spend.
    """
    await credits.lock_account(session, account_id)
    balance = await credits.credit_balance(
        session, account_id, credit_price_rao=credit_price_rao, now=now
    )
    if balance.credits_available < credits_held:
        raise RecordConflict(
            "not enough credits for another verification attempt",
            reason_code="INSUFFICIENT_CREDITS",
            credits_available=balance.credits_available,
            credits_required=credits_held,
        )

    intent = SubmissionIntent(
        account_id=account_id,
        hotkey=hotkey,
        task_id=task_id,
        task_bundle_sha256=digests.to_bytes(task_bundle_sha256),
        credits_held=credits_held,
        credit_price_rao=credit_price_rao,
        expires_at=expires_at,
    )
    session.add(intent)
    await session.flush()

    # Recomputed after the insert so the hold just taken is included.
    after = await credits.credit_balance(
        session, account_id, credit_price_rao=credit_price_rao, now=now
    )
    return intent, after


async def get_intent(
    session: AsyncSession,
    intent_id: uuid.UUID,
    account_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> SubmissionIntent:
    """One intent, scoped to its owner.

    Another account's intent is absent rather than forbidden, so an identifier cannot
    be probed for existence.
    """
    statement = select(SubmissionIntent).where(SubmissionIntent.id == intent_id)
    if for_update:
        statement = statement.with_for_update()
    intent = (await session.execute(statement)).scalar_one_or_none()
    if intent is None or intent.account_id != account_id:
        raise RecordNotFound("intent not found")
    return intent


def assert_usable(intent: SubmissionIntent, *, now: dt.datetime) -> None:
    """Refuse an intent that has lapsed or already been used.

    Expiry is checked against the timestamp rather than the status, because the
    sweeper that flips lapsed intents to EXPIRED runs on its own schedule and a hold
    stops counting the moment it lapses.
    """
    if intent.status == IntentState.CONFIRMED:
        raise RecordConflict(
            "that intent has already been confirmed",
            reason_code="INTENT_ALREADY_CONFIRMED",
            submission_id=str(intent.submission_id),
        )
    if intent.status in (IntentState.EXPIRED, IntentState.CANCELLED):
        raise RecordConflict(
            f"that intent is {str(intent.status).lower()}",
            reason_code="INTENT_NOT_OPEN",
        )
    if intent.expires_at <= now:
        raise RecordConflict(
            "that intent has expired; open a new one",
            reason_code="INTENT_EXPIRED",
        )


async def attach_bundle(
    session: AsyncSession,
    intent: SubmissionIntent,
    *,
    proof_content: bytes,
    proof_sha256: str,
    request_digest: str,
    now: dt.datetime,
) -> SubmissionIntent:
    """Store the admitted proof and the digest the client must sign.

    The proof lives on the intent rather than in ``proofs`` because ``proofs`` is the
    record of what was verified, and an unconfirmed intent has not paid for
    verification yet. It moves across in ``confirm``.

    Re-attaching is allowed and replaces the previous bundle: a miner who uploads the
    wrong file should not have to burn the held credit to fix it. The digest is
    recomputed, so the previous signature stops being valid.
    """
    assert_usable(intent, now=now)
    intent.proof_content = proof_content
    intent.proof_sha256 = digests.to_bytes(proof_sha256)
    intent.request_digest = digests.to_bytes(request_digest)
    intent.status = IntentState.BUNDLE_ATTACHED
    await session.flush()
    return intent


async def confirm(
    session: AsyncSession,
    intent_id: uuid.UUID,
    account_id: uuid.UUID,
    *,
    hotkey_signature: bytes,
    manual_review_required: bool,
    review_policy_version: str,
    bounty_amount_rao: int,
    bounty_policy_version: str,
    bounty_inputs: dict | None,
    now: dt.datetime,
) -> ConfirmedIntent:
    """Debit the credit and write the submission, atomically.

    The caller supplies the quote and the review policy, and has already verified the
    signature against ``intent.request_digest``. What this owns is that the credit and
    the submission come into existence together: either both, or neither.

    Idempotent for an exact retry. A second confirm of a CONFIRMED intent raises
    INTENT_ALREADY_CONFIRMED carrying the original submission id, rather than charging
    again — the partial unique index on ``credit_ledger.intent_id`` would refuse the
    second SPEND anyway, but a clear conflict beats a constraint violation.
    """
    # Lock the account first, then re-read the intent under that lock. Checking the
    # status before taking the lock would be checking a value that can change before
    # the write.
    await credits.lock_account(session, account_id)
    intent = await get_intent(session, intent_id, account_id, for_update=True)
    assert_usable(intent, now=now)

    if intent.status != IntentState.BUNDLE_ATTACHED or intent.proof_content is None:
        raise RecordConflict(
            "upload a bundle before confirming",
            reason_code="INTENT_HAS_NO_BUNDLE",
        )
    if intent.request_digest is None:  # pragma: no cover - the CHECK above forbids it
        raise RecordConflict(
            "intent has no request digest", reason_code="INTENT_HAS_NO_BUNDLE"
        )

    proof_sha256 = digests.to_prefixed(intent.proof_sha256)
    await submission_store.ensure_proof(
        session, bytes(intent.proof_content), proof_sha256
    )

    # The SPEND is appended before the submission, because the submission references
    # it. It names the intent, which is what makes that order possible.
    spend = await credits.record_entry(
        session,
        account_id=account_id,
        kind=CreditEntryKind.SPEND,
        amount_rao=-(intent.credits_held * intent.credit_price_rao),
        credit_price_rao=intent.credit_price_rao,
        intent_id=intent.id,
    )

    submission = Submission(
        hotkey=intent.hotkey,
        # The intent id is the idempotency key: one intent is one attempt, so an
        # accidental double confirm has nothing new to create.
        idempotency_key=intent.id,
        request_digest=bytes(intent.request_digest),
        task_id=intent.task_id,
        task_bundle_sha256=bytes(intent.task_bundle_sha256),
        proof_digest=bytes(intent.proof_sha256),
        hotkey_signature=hotkey_signature,
        manual_review_required=manual_review_required,
        review_policy_version=review_policy_version,
        bounty_amount_rao=bounty_amount_rao,
        bounty_policy_version=bounty_policy_version,
        bounty_inputs=bounty_inputs,
        account_id=account_id,
        credit_ledger_id=spend.id,
        intent_id=intent.id,
    )
    session.add(submission)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        if PROOF_CONSTRAINT in str(getattr(exc, "orig", exc)):
            raise DuplicateProof(
                "these exact proof bytes have already been submitted",
                proof_sha256=proof_sha256,
            ) from exc
        raise RecordConflict(
            "the submission could not be recorded", reason_code="CONFLICT"
        ) from exc

    intent.status = IntentState.CONFIRMED
    intent.submission_id = submission.id
    # The bytes now live in `proofs`, which is the durable record. Keeping a second
    # copy on the intent would mean a proof deleted from one place still existed in
    # the other.
    intent.proof_content = None
    intent.proof_sha256 = None
    await session.flush()

    await record_event(
        session,
        submission.id,
        kind=EVENT_ACCEPTED,
        detail="Credit debited and submission recorded.",
        context={"intent_id": str(intent.id), "credit_ledger_id": spend.id},
    )
    await record_event(
        session,
        submission.id,
        kind=EVENT_QUEUED,
        detail="Waiting for a verification worker to claim the proof.",
    )

    balance = await credits.credit_balance(
        session, account_id, credit_price_rao=intent.credit_price_rao, now=now
    )
    return ConfirmedIntent(intent=intent, submission=submission, balance=balance)


async def cancel(
    session: AsyncSession, intent: SubmissionIntent, *, now: dt.datetime
) -> SubmissionIntent:
    """Release the hold. The credit was never spent, so nothing is refunded."""
    assert_usable(intent, now=now)
    intent.status = IntentState.CANCELLED
    intent.proof_content = None
    intent.proof_sha256 = None
    await session.flush()
    return intent


async def expire_lapsed(session: AsyncSession, *, now: dt.datetime) -> int:
    """Tidy up intents whose window closed.

    Housekeeping only. The hold already stopped counting against the balance when the
    timestamp passed — see ``credits.held_rao`` — so this never changes what an
    account can spend, it just stops the rows accumulating with proof bytes attached.
    """
    result = await session.execute(
        update(SubmissionIntent)
        .where(
            SubmissionIntent.status.in_(credits.LIVE_INTENT_STATES),
            SubmissionIntent.expires_at <= now,
        )
        .values(status=IntentState.EXPIRED, proof_content=None, proof_sha256=None)
    )
    return result.rowcount


async def submission_ids_for(
    session: AsyncSession, intent_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, uuid.UUID]:
    """Map intents to the submissions they produced.

    The ledger page needs this because a SPEND names its intent rather than its submission —
    see the note on ``credit_ledger.intent_id`` — so the submission a debit paid for is one
    hop away. One statement over the primary key, not a scan.
    """
    if not intent_ids:
        return {}
    statement = select(SubmissionIntent.id, SubmissionIntent.submission_id).where(
        SubmissionIntent.id.in_(intent_ids),
        SubmissionIntent.submission_id.is_not(None),
    )
    return {row[0]: row[1] for row in (await session.execute(statement)).all()}


async def intents_for(
    session: AsyncSession, account_id: uuid.UUID, *, limit: int
) -> Sequence[SubmissionIntent]:
    statement = (
        select(SubmissionIntent)
        .where(SubmissionIntent.account_id == account_id)
        .order_by(SubmissionIntent.created_at.desc())
        .limit(limit)
    )
    return list((await session.execute(statement)).scalars())


# --- Submission timeline -----------------------------------------------------------


async def record_event(
    session: AsyncSession,
    submission_id: uuid.UUID,
    *,
    kind: str,
    detail: str | None = None,
    context: dict | None = None,
    actor: str = "system",
) -> SubmissionEvent:
    """Append one timeline entry. Append-only, like the ledger."""
    event = SubmissionEvent(
        submission_id=submission_id,
        kind=kind,
        detail=detail,
        context=context,
        actor=actor,
    )
    session.add(event)
    await session.flush()
    return event


async def events_for(
    session: AsyncSession, submission_id: uuid.UUID
) -> Sequence[SubmissionEvent]:
    """The whole timeline for one submission, oldest first."""
    statement = (
        select(SubmissionEvent)
        .where(SubmissionEvent.submission_id == submission_id)
        .order_by(SubmissionEvent.id)
    )
    return list((await session.execute(statement)).scalars())


__all__ = [
    "EVENT_ACCEPTED",
    "EVENT_QUEUED",
    "ConfirmedIntent",
    "assert_usable",
    "attach_bundle",
    "cancel",
    "confirm",
    "events_for",
    "expire_lapsed",
    "get_intent",
    "intents_for",
    "open_intent",
    "record_event",
]
