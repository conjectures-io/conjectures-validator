"""The submission seam: creating a submission and moving it through its lifecycle.

Used by the submission API to record intake and read state, and by the verification and
review components to record verdicts. Callers pass in a session, so the unit of work is
theirs to scope; nothing here opens its own connection.

Two properties of the schema shape everything below:

* **Intake is funded up front.** A submission row exists only once money has been
  confirmed. Since V003 there are two ways for that to be true, and `submissions` carries
  a CHECK that exactly one of them holds per row: an extrinsic-funded submission names the
  finalized transfer that paid for it, and a credit-funded one names the ledger entry it was
  debited from. `create_submission` below writes the first kind;
  `conjectures_subnet.db.intents.confirm` writes the second. Neither admits an unfunded row.
  A refused request creates no submission and is recorded in `api_rejection_log` instead.
* **The four statuses are independent axes, not one lifecycle.** A submission always has a
  verification status AND a review status AND a reward status. Each moves on its own, so
  reading one says nothing about the others.

Concurrency safety comes from the unique constraints in the migration — `(hotkey,
idempotency_key)`, `payment_reference`, and `proof_digest` — not from read-then-write checks,
so two simultaneous requests cannot both succeed.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from conjectures_subnet.db import digests
from conjectures_subnet.db.errors import (
    DuplicatePayment,
    DuplicateProof,
    IdempotencyConflict,
    RecordNotFound,
)
from conjectures_subnet.db.models import (
    ApiRejectionLog,
    ManualReviewState,
    Proof,
    ReviewDecision,
    ReviewerKind,
    ReviewOutcome,
    RewardEvent,
    RewardState,
    Submission,
    VerificationRun,
    VerificationState,
)
from verifier.hashing import canonical_json_bytes, sha256_bytes

# Constraint names from deploy/migrate/sql/V001__initial_schema.sql. Matching on the name is
# what lets one IntegrityError be reported as the specific conflict the miner caused.
IDEMPOTENCY_CONSTRAINT = "submissions_idempotency_unique"
PAYMENT_CONSTRAINT = "submissions_payment_reference_unique"
PROOF_CONSTRAINT = "submissions_proof_digest_key"


@dataclass(frozen=True)
class NewSubmission:
    """One confirmed-paid submission, ready to record."""

    hotkey: str
    idempotency_key: uuid.UUID
    request_digest: str  # sha256:<hex>; converted at the column
    task_id: str
    task_bundle_sha256: str  # sha256:<hex>
    proof_content: bytes  # the miner's Main.lean, exactly as admitted
    proof_sha256: str  # sha256:<hex>
    payment_reference: str
    payment_sender: str  # coldkey that paid, proven to own the hotkey
    payment_amount_rao: int
    payment_block: int
    hotkey_signature: bytes  # 64 bytes over request_digest
    manual_review_required: bool
    review_policy_version: str
    bounty_amount_rao: int
    bounty_policy_version: str
    bounty_inputs: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class SubmissionView:
    submission: Submission
    verification: VerificationRun | None
    replayed: bool = False


def canonical_request_digest(
    *,
    hotkey: str,
    task_id: str,
    task_bundle_sha256: str,
    proof_sha256: str,
    payment_reference: str,
    idempotency_key: str,
) -> str:
    """The identity of a request, and the message the miner signs.

    Reusing an idempotency key with any of these values changed is a conflict rather than a
    replay, so every one of them is part of the digest. It binds the proof digest too, so a
    signature cannot be reused for different proof bytes.
    """
    return sha256_bytes(
        canonical_json_bytes(
            {
                "hotkey": hotkey,
                "idempotency_key": idempotency_key,
                "payment_reference": payment_reference,
                "proof_sha256": proof_sha256,
                "task_bundle_sha256": task_bundle_sha256,
                "task_id": task_id,
            }
        )
    )


def _violates(exc: IntegrityError, constraint: str) -> bool:
    return constraint in str(getattr(exc, "orig", exc))



async def find_by_idempotency_key(
    session: AsyncSession, hotkey: str, idempotency_key: uuid.UUID
) -> Submission | None:
    result = await session.execute(
        select(Submission).where(
            Submission.hotkey == hotkey,
            Submission.idempotency_key == idempotency_key,
        )
    )
    return result.scalar_one_or_none()


async def latest_verification_run(
    session: AsyncSession, submission_id: uuid.UUID
) -> VerificationRun | None:
    result = await session.execute(
        select(VerificationRun)
        .where(VerificationRun.submission_id == submission_id)
        .order_by(VerificationRun.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def load_view(session: AsyncSession, submission: Submission) -> SubmissionView:
    return SubmissionView(
        submission=submission,
        verification=await latest_verification_run(session, submission.id),
    )


async def get_for_miner(
    session: AsyncSession, submission_id: uuid.UUID, hotkey: str
) -> SubmissionView:
    submission = await session.get(Submission, submission_id)
    # Another miner's submission is reported as absent rather than forbidden, so identifiers
    # cannot be probed for existence.
    if submission is None or submission.hotkey != hotkey:
        raise RecordNotFound("submission not found")
    return await load_view(session, submission)


async def ensure_proof(session: AsyncSession, content: bytes, digest: str) -> None:
    """Store the proof bytes, or do nothing if these exact bytes are already stored.

    `proofs` is content-addressed and the digest is verified by a CHECK constraint, so a
    matching row is by definition the same bytes.
    """
    await session.execute(
        pg_insert(Proof)
        .values(
            digest=digests.to_bytes(digest),
            content=content,
            byte_length=len(content),
        )
        .on_conflict_do_nothing(index_elements=[Proof.digest])
    )


async def create_submission(
    session: AsyncSession, request: NewSubmission
) -> SubmissionView:
    """Record one confirmed-paid submission, or return the original for an exact replay."""
    existing = await find_by_idempotency_key(
        session, request.hotkey, request.idempotency_key
    )
    if existing is not None:
        if bytes(existing.request_digest) != digests.to_bytes(request.request_digest):
            raise IdempotencyConflict(
                "idempotency key was already used with different submission data",
                idempotency_key=str(request.idempotency_key),
            )
        view = await load_view(session, existing)
        return SubmissionView(
            submission=view.submission, verification=view.verification, replayed=True
        )

    await ensure_proof(session, request.proof_content, request.proof_sha256)

    submission = Submission(
        hotkey=request.hotkey,
        idempotency_key=request.idempotency_key,
        request_digest=digests.to_bytes(request.request_digest),
        task_id=request.task_id,
        task_bundle_sha256=digests.to_bytes(request.task_bundle_sha256),
        proof_digest=digests.to_bytes(request.proof_sha256),
        payment_reference=request.payment_reference,
        payment_sender=request.payment_sender,
        payment_amount_rao=request.payment_amount_rao,
        payment_block=request.payment_block,
        hotkey_signature=request.hotkey_signature,
        verification_status=VerificationState.UNVERIFIED,
        manual_review_status=ManualReviewState.UNREVIEWED,
        reward_status=RewardState.INELIGIBLE,
        manual_review_required=request.manual_review_required,
        review_policy_version=request.review_policy_version,
        bounty_amount_rao=request.bounty_amount_rao,
        bounty_policy_version=request.bounty_policy_version,
        bounty_inputs=dict(request.bounty_inputs)
        if request.bounty_inputs is not None
        else None,
    )
    session.add(submission)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        if _violates(exc, PROOF_CONSTRAINT) or "proof_digest" in str(
            getattr(exc, "orig", exc)
        ):
            raise DuplicateProof(
                "these proof bytes have already been submitted",
                proof_sha256=request.proof_sha256,
            ) from exc
        if _violates(exc, PAYMENT_CONSTRAINT):
            raise DuplicatePayment(
                "payment reference already backs another submission",
                payment_reference=request.payment_reference,
            ) from exc
        if _violates(exc, IDEMPOTENCY_CONSTRAINT):
            raise IdempotencyConflict(
                "a submission for this idempotency key is already being created",
                idempotency_key=str(request.idempotency_key),
            ) from exc
        raise

    await session.flush()
    return SubmissionView(submission=submission, verification=None)


async def record_verification_result(
    session: AsyncSession,
    submission: Submission,
    *,
    accepted: bool,
    reason_code: str,
    stage: str,
    verifier_version: str,
    container_digest: str,
    sandbox_mode: str,
    checks: Mapping[str, bool] | None,
    report: bytes | None,
    started_at: datetime,
    finished_at: datetime,
) -> VerificationRun:
    """Record one completed verifier run and advance the affected status axes.

    The run row is inserted once, on completion: every column the schema requires is only
    known after the verifier has finished. A Lean-invalid proof can never become
    reward-eligible, and the review gate uses the flag captured on the submission rather than
    the current live setting.
    """
    run = VerificationRun(
        submission_id=submission.id,
        task_bundle_sha256=bytes(submission.task_bundle_sha256),
        proof_digest=bytes(submission.proof_digest),
        verifier_version=verifier_version,
        container_digest=digests.to_bytes(container_digest),
        sandbox_mode=sandbox_mode,
        accepted=accepted,
        reason_code=reason_code,
        stage=stage,
        checks=dict(checks) if checks is not None else None,
        report=report,
        report_digest=None
        if report is None
        else digests.to_bytes(sha256_bytes(report)),
        started_at=started_at,
        finished_at=finished_at,
    )
    session.add(run)
    await session.flush()

    verdict = VerificationState.VERIFIED if accepted else VerificationState.REJECTED
    submission.verification_status = verdict
    if not accepted:
        submission.failure_reason = reason_code

    if accepted and not submission.manual_review_required:
        # Manual review is disabled for this submission, so eligibility is automatic — but it
        # is still recorded as a policy decision rather than left implicit.
        await approve_automatically(session, submission)
    await session.flush()
    return run


async def approve_automatically(
    session: AsyncSession, submission: Submission
) -> ReviewDecision:
    """Record the AUTOMATIC review decision and make the submission reward-eligible."""
    decision = ReviewDecision(
        submission_id=submission.id,
        decision=ReviewOutcome.APPROVED,
        kind=ReviewerKind.AUTOMATIC,
        reviewer="system",
        policy_version=submission.review_policy_version,
        reason_code="AUTO_REVIEW_DISABLED",
    )
    session.add(decision)
    await session.flush()

    submission.manual_review_status = ManualReviewState.APPROVED
    submission.reward_status = RewardState.ELIGIBLE

    await session.flush()
    return decision


async def log_rejection(
    session: AsyncSession,
    *,
    reason_code: str,
    http_status: int | None = None,
    hotkey_claimed: str | None = None,
    idempotency_key: str | None = None,
    task_id: str | None = None,
    task_bundle_sha256: str | None = None,
    proof_digest: str | None = None,
    proof_byte_length: int | None = None,
    request_digest: str | None = None,
    payment_reference: str | None = None,
    source_ip: str | None = None,
    user_agent: str | None = None,
    detail: Mapping[str, Any] | None = None,
) -> None:
    """Record a refused request.

    Intake is payment-gated, so a refusal creates no submission and would otherwise leave no
    trace. This is the only record of a miner who paid and was turned away. Every field is
    unvalidated client input and the table has no domains, so a malformed value is logged
    rather than rejected.
    """
    session.add(
        ApiRejectionLog(
            reason_code=reason_code,
            http_status=http_status,
            hotkey_claimed=hotkey_claimed,
            idempotency_key=idempotency_key,
            task_id=task_id,
            task_bundle_sha256=task_bundle_sha256,
            proof_digest=proof_digest,
            proof_byte_length=proof_byte_length,
            request_digest=request_digest,
            payment_reference=payment_reference,
            source_ip=source_ip,
            user_agent=user_agent,
            detail=dict(detail) if detail else None,
        )
    )
    await session.flush()


async def proof_bytes(
    session: AsyncSession, digest: bytes | memoryview
) -> bytes | None:
    """The stored proof for a digest, for the verification worker."""
    proof = await session.get(Proof, bytes(digest))
    return None if proof is None else bytes(proof.content)


# --- The miner panel -----------------------------------------------------------------
# Reads scoped to one account, behind /v1/me/submissions and /v1/me/rewards. Distinct
# from `get_for_miner` above, which scopes to a hotkey signature and predates accounts:
# a signed-in miner may have several linked hotkeys and should see all of their work.


async def for_account(
    session: AsyncSession,
    account_id: uuid.UUID,
    *,
    limit: int,
    after: tuple[datetime, uuid.UUID] | None = None,
) -> list[Submission]:
    """One page of an account's own submissions, newest first.

    Keyset-paginated on `(created_at, id)` over `submissions_account_idx`, the same
    shape the public feeds use and for the same reason: an offset would both scan and
    silently skip a row when a new submission lands mid-page.
    """
    from sqlalchemy import tuple_

    statement = select(Submission).where(Submission.account_id == account_id)
    if after is not None:
        statement = statement.where(
            tuple_(Submission.created_at, Submission.id) < tuple_(after[0], after[1])
        )
    statement = statement.order_by(
        Submission.created_at.desc(), Submission.id.desc()
    ).limit(limit)
    return list((await session.execute(statement)).scalars())


async def get_for_account(
    session: AsyncSession, submission_id: uuid.UUID, account_id: uuid.UUID
) -> SubmissionView:
    """One of the account's own submissions, with its latest verification run.

    Another account's submission is reported as absent rather than forbidden, so a
    submission id cannot be probed for existence.
    """
    submission = await session.get(Submission, submission_id)
    if submission is None or submission.account_id != account_id:
        raise RecordNotFound("submission not found")
    return await load_view(session, submission)


async def rewards_for_account(
    session: AsyncSession,
    account_id: uuid.UUID,
    *,
    limit: int,
    after_id: int | None = None,
) -> list[tuple[RewardEvent, str]]:
    """An account's payouts, newest first, each with the task it was earned on.

    Joined through `submissions` because `reward_events` has no account column: a
    payout belongs to a submission, and the submission belongs to the account. Keyset
    on the reward event's own identity column, which is monotonic and unique.
    """
    statement = (
        select(RewardEvent, Submission.task_id)
        .join(Submission, Submission.id == RewardEvent.submission_id)
        .where(Submission.account_id == account_id)
        .order_by(RewardEvent.id.desc())
        .limit(limit)
    )
    if after_id is not None:
        statement = statement.where(RewardEvent.id < after_id)
    return [(row[0], row[1]) for row in (await session.execute(statement)).all()]
