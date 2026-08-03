"""The submission seam: creating a submission and moving it through its lifecycle.

Used by the submission API to record intake and read state, and by the verification and
review components to record verdicts. Callers pass in a session, so the unit of work is
theirs to scope; nothing here opens its own connection.

Two properties of the schema shape everything below:

* **Intake is payment-gated.** Every payment column on `submissions` is NOT NULL and there
  is no payment state, so a row exists only once a finalized transfer has been confirmed.
  A refused request creates no submission and is recorded in `api_rejection_log` instead.
* **The four statuses are independent axes, not one lifecycle.** A submission always has a
  verification status AND a review status AND a reward status. Every status write inserts a
  `submission_events` row in the same transaction, or the history develops holes.

Concurrency safety comes from the unique constraints in the migration — `(hotkey,
idempotency_key)`, `payment_reference`, and `proof_digest` — not from read-then-write checks,
so two simultaneous requests cannot both succeed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from conjectures_subnet.db import digests
from conjectures_subnet.db.errors import (
    DuplicatePayment,
    DuplicateProof,
    IdempotencyConflict,
    RecordConflict,
    RecordNotFound,
)
from conjectures_subnet.db.models import (
    ApiRejectionLog,
    ManualReviewState,
    PayoutState,
    Proof,
    ProblemWinner,
    ReviewDecision,
    ReviewerKind,
    ReviewOutcome,
    RewardState,
    RewardEvent,
    Submission,
    SubmissionEvent,
    SubmissionStatusField,
    VerificationRun,
    VerificationState,
)
from verifier.hashing import canonical_json_bytes, sha256_bytes


ACTOR_API = "api"
ACTOR_VERIFIER = "verification-worker"
ACTOR_REWARD = "payout-operator"
CREATED_STATUS = "CREATED"

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
    request_digest: str          # sha256:<hex>; converted at the column
    task_id: str
    problem_id: str
    task_mode: str
    task_bundle_sha256: str      # sha256:<hex>
    proof_content: bytes         # the miner's Main.lean, exactly as admitted
    proof_sha256: str            # sha256:<hex>
    payment_reference: str
    payment_sender: str          # coldkey that paid, proven to own the hotkey
    payment_amount_rao: int
    payment_block: int
    request_timestamp_ms: int
    hotkey_signature: bytes      # 64 bytes over the signed request envelope
    manual_review_required: bool
    review_policy_version: str
    bounty_amount_rao: int
    bounty_policy_version: str
    bounty_inputs: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class SubmissionView:
    submission: Submission
    verification: VerificationRun | None
    review: ReviewDecision | None = None
    reward: RewardEvent | None = None
    winner: ProblemWinner | None = None
    replayed: bool = False


@dataclass(frozen=True)
class VerificationLease:
    submission: Submission
    proof_content: bytes


@dataclass(frozen=True)
class WinnerClaim:
    won: bool
    winner: ProblemWinner


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


async def _record_event(
    session: AsyncSession,
    submission_id: uuid.UUID,
    *,
    status_field: SubmissionStatusField,
    to_status: str,
    from_status: str | None = None,
    causation_id: uuid.UUID | None = None,
    verification_run_id: int | None = None,
    review_decision_id: int | None = None,
    reward_event_id: int | None = None,
    detail: Mapping[str, Any] | None = None,
    actor: str = ACTOR_API,
) -> None:
    session.add(
        SubmissionEvent(
            submission_id=submission_id,
            status_field=status_field,
            from_status=from_status,
            to_status=to_status,
            actor=actor,
            causation_id=causation_id,
            verification_run_id=verification_run_id,
            review_decision_id=review_decision_id,
            reward_event_id=reward_event_id,
            detail=dict(detail) if detail else None,
        )
    )


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


async def latest_review_decision(
    session: AsyncSession, submission_id: uuid.UUID
) -> ReviewDecision | None:
    result = await session.execute(
        select(ReviewDecision)
        .where(ReviewDecision.submission_id == submission_id)
        .order_by(ReviewDecision.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def reward_event(
    session: AsyncSession, submission_id: uuid.UUID
) -> RewardEvent | None:
    result = await session.execute(
        select(RewardEvent).where(RewardEvent.submission_id == submission_id)
    )
    return result.scalar_one_or_none()


async def problem_winner(
    session: AsyncSession, problem_id: str
) -> ProblemWinner | None:
    return await session.get(ProblemWinner, problem_id)


async def load_view(session: AsyncSession, submission: Submission) -> SubmissionView:
    return SubmissionView(
        submission=submission,
        verification=await latest_verification_run(session, submission.id),
        review=await latest_review_decision(session, submission.id),
        reward=await reward_event(session, submission.id),
        winner=await problem_winner(session, submission.problem_id),
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


async def _ensure_proof(session: AsyncSession, content: bytes, digest: str) -> None:
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


async def create_submission(session: AsyncSession, request: NewSubmission) -> SubmissionView:
    """Record one confirmed-paid submission, or return the original for an exact replay."""
    existing = await find_by_idempotency_key(session, request.hotkey, request.idempotency_key)
    if existing is not None:
        if bytes(existing.request_digest) != digests.to_bytes(request.request_digest):
            raise IdempotencyConflict(
                "idempotency key was already used with different submission data",
                idempotency_key=str(request.idempotency_key),
            )
        view = await load_view(session, existing)
        return SubmissionView(
            submission=view.submission,
            verification=view.verification,
            review=view.review,
            reward=view.reward,
            winner=view.winner,
            replayed=True,
        )

    await _ensure_proof(session, request.proof_content, request.proof_sha256)

    submission = Submission(
        hotkey=request.hotkey,
        idempotency_key=request.idempotency_key,
        request_digest=digests.to_bytes(request.request_digest),
        task_id=request.task_id,
        problem_id=request.problem_id,
        task_mode=request.task_mode,
        task_bundle_sha256=digests.to_bytes(request.task_bundle_sha256),
        proof_digest=digests.to_bytes(request.proof_sha256),
        payment_reference=request.payment_reference,
        payment_sender=request.payment_sender,
        payment_amount_rao=request.payment_amount_rao,
        payment_block=request.payment_block,
        request_timestamp_ms=request.request_timestamp_ms,
        hotkey_signature=request.hotkey_signature,
        verification_status=VerificationState.UNVERIFIED,
        manual_review_status=ManualReviewState.UNREVIEWED,
        reward_status=RewardState.INELIGIBLE,
        manual_review_required=request.manual_review_required,
        review_policy_version=request.review_policy_version,
        bounty_amount_rao=request.bounty_amount_rao,
        bounty_policy_version=request.bounty_policy_version,
        bounty_inputs=(
            dict(request.bounty_inputs) if request.bounty_inputs is not None else None
        ),
    )
    session.add(submission)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        if _violates(exc, PROOF_CONSTRAINT) or "proof_digest" in str(getattr(exc, "orig", exc)):
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

    await _record_event(
        session,
        submission.id,
        status_field=SubmissionStatusField.CREATED,
        to_status=CREATED_STATUS,
        causation_id=request.idempotency_key,
        detail={
            "task_id": request.task_id,
            "problem_id": request.problem_id,
            "task_mode": request.task_mode,
            "proof_sha256": request.proof_sha256,
            "payment_reference": request.payment_reference,
            "payment_amount_rao": request.payment_amount_rao,
            "payment_block": request.payment_block,
            "request_timestamp_ms": request.request_timestamp_ms,
            "bounty_amount_rao": request.bounty_amount_rao,
            "bounty_policy_version": request.bounty_policy_version,
        },
    )
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
    actor: str = ACTOR_VERIFIER,
    lease_owner: str | None = None,
) -> VerificationRun:
    """Record one completed verifier run and advance the affected status axes.

    The run row is inserted once, on completion: every column the schema requires is only
    known after the verifier has finished. A Lean-invalid proof can never become
    reward-eligible, and the review gate uses the flag captured on the submission rather than
    the current live setting.
    """
    if VerificationState(submission.verification_status) is not VerificationState.UNVERIFIED:
        raise RecordConflict(
            "submission already has a terminal verification verdict",
            reason_code="VERIFICATION_ALREADY_RECORDED",
            submission_id=str(submission.id),
        )
    if lease_owner is not None and submission.verification_lease_owner != lease_owner:
        raise RecordConflict(
            "verification lease is not owned by this worker",
            reason_code="VERIFICATION_LEASE_LOST",
            submission_id=str(submission.id),
        )

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
        report_digest=None if report is None else digests.to_bytes(sha256_bytes(report)),
        started_at=started_at,
        finished_at=finished_at,
    )
    session.add(run)
    await session.flush()

    previous = VerificationState(submission.verification_status).value
    verdict = VerificationState.VERIFIED if accepted else VerificationState.REJECTED
    submission.verification_status = verdict
    submission.verification_lease_owner = None
    submission.verification_lease_expires_at = None
    if not accepted:
        submission.failure_reason = reason_code
    await _record_event(
        session,
        submission.id,
        status_field=SubmissionStatusField.VERIFICATION,
        from_status=previous,
        to_status=verdict.value,
        verification_run_id=run.id,
        detail={"reason_code": reason_code, "stage": stage, "accepted": accepted},
        actor=actor,
    )

    if accepted and not submission.manual_review_required:
        # Manual review is disabled for this submission, so eligibility is automatic — but it
        # is still recorded as a policy decision rather than left implicit.
        await approve_automatically(session, submission, actor=actor)
    await session.flush()
    return run


async def _claim_problem_winner(
    session: AsyncSession,
    submission: Submission,
    *,
    claim_reason: str,
) -> WinnerClaim:
    """Atomically claim the paired problem for this submission."""
    result = await session.execute(
        pg_insert(ProblemWinner)
        .values(
            problem_id=submission.problem_id,
            submission_id=submission.id,
            claim_reason=claim_reason,
        )
        .on_conflict_do_nothing(index_elements=[ProblemWinner.problem_id])
        .returning(ProblemWinner.problem_id)
    )
    _ = result.scalar_one_or_none()
    winner = await problem_winner(session, submission.problem_id)
    if winner is None:  # defensive: INSERT or the competing row must be visible here
        raise RuntimeError("problem winner claim disappeared")
    return WinnerClaim(won=winner.submission_id == submission.id, winner=winner)


async def _make_reward_eligible(
    session: AsyncSession,
    submission: Submission,
    *,
    eligibility_reason: str,
    actor: str,
) -> WinnerClaim:
    claim = await _claim_problem_winner(
        session, submission, claim_reason=eligibility_reason
    )
    if not claim.won:
        # The Lean verdict remains valid. Only the bounty is unavailable because
        # the opposite (or same) mode already won this mathematical problem.
        submission.failure_reason = "PROBLEM_ALREADY_WON"
        return claim

    previous = RewardState(submission.reward_status).value
    submission.reward_status = RewardState.ELIGIBLE
    await _record_event(
        session,
        submission.id,
        status_field=SubmissionStatusField.REWARD,
        from_status=previous,
        to_status=RewardState.ELIGIBLE.value,
        detail={
            "eligibility_reason": eligibility_reason,
            "problem_id": submission.problem_id,
            "winner_submission_id": str(submission.id),
        },
        actor=actor,
    )
    return claim


async def approve_automatically(
    session: AsyncSession, submission: Submission, *, actor: str = ACTOR_VERIFIER
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

    review_previous = ManualReviewState(submission.manual_review_status).value
    submission.manual_review_status = ManualReviewState.APPROVED
    await _record_event(
        session,
        submission.id,
        status_field=SubmissionStatusField.MANUAL_REVIEW,
        from_status=review_previous,
        to_status=ManualReviewState.APPROVED.value,
        review_decision_id=decision.id,
        detail={"automatic": True, "policy_version": submission.review_policy_version},
        actor=actor,
    )
    await _make_reward_eligible(
        session,
        submission,
        eligibility_reason="AUTO_REVIEW_DISABLED",
        actor=actor,
    )
    await session.flush()
    return decision


async def record_human_review(
    session: AsyncSession,
    submission_id: uuid.UUID,
    *,
    decision: ReviewOutcome,
    reviewer: str,
    reason_code: str,
    notes: str | None = None,
) -> SubmissionView:
    """Append an audited binding review and, on approval, claim the winner."""
    result = await session.execute(
        select(Submission).where(Submission.id == submission_id).with_for_update()
    )
    submission = result.scalar_one_or_none()
    if submission is None:
        raise RecordNotFound("submission not found")
    if VerificationState(submission.verification_status) is not VerificationState.VERIFIED:
        raise RecordConflict(
            "only a Lean-verified submission can be reviewed",
            reason_code="SUBMISSION_NOT_VERIFIED",
        )
    if ManualReviewState(submission.manual_review_status) is not ManualReviewState.UNREVIEWED:
        raise RecordConflict(
            "submission already has a binding review",
            reason_code="REVIEW_ALREADY_RECORDED",
        )

    review = ReviewDecision(
        submission_id=submission.id,
        decision=decision,
        kind=ReviewerKind.HUMAN,
        reviewer=reviewer,
        policy_version=submission.review_policy_version,
        reason_code=reason_code,
        notes=notes,
    )
    session.add(review)
    await session.flush()

    previous = ManualReviewState(submission.manual_review_status).value
    next_status = (
        ManualReviewState.APPROVED
        if decision is ReviewOutcome.APPROVED
        else ManualReviewState.REJECTED
    )
    submission.manual_review_status = next_status
    await _record_event(
        session,
        submission.id,
        status_field=SubmissionStatusField.MANUAL_REVIEW,
        from_status=previous,
        to_status=next_status.value,
        review_decision_id=review.id,
        detail={"reason_code": reason_code, "policy_version": submission.review_policy_version},
        actor=reviewer,
    )
    if decision is ReviewOutcome.APPROVED:
        await _make_reward_eligible(
            session,
            submission,
            eligibility_reason="REVIEW_APPROVED",
            actor=reviewer,
        )
    else:
        submission.failure_reason = reason_code
    await session.flush()
    await session.refresh(submission)
    return await load_view(session, submission)


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


async def proof_bytes(session: AsyncSession, digest: bytes | memoryview) -> bytes | None:
    """The stored proof for a digest, for the verification worker."""
    proof = await session.get(Proof, bytes(digest))
    return None if proof is None else bytes(proof.content)


async def claim_verification(
    session: AsyncSession,
    *,
    worker_id: str,
    lease_seconds: int,
) -> VerificationLease | None:
    """Lease the oldest due submission without blocking another worker."""
    now = await session.scalar(select(func.now()))
    if now is None:  # pragma: no cover - PostgreSQL now() is never NULL
        raise RuntimeError("database clock is unavailable")
    result = await session.execute(
        select(Submission)
        .where(
            Submission.verification_status == VerificationState.UNVERIFIED,
            Submission.verification_next_attempt_at <= now,
            or_(
                Submission.verification_lease_owner.is_(None),
                Submission.verification_lease_expires_at <= now,
            ),
        )
        .order_by(Submission.verification_next_attempt_at, Submission.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    submission = result.scalar_one_or_none()
    if submission is None:
        return None
    proof = await proof_bytes(session, submission.proof_digest)
    if proof is None:
        raise RuntimeError(f"proof bytes missing for submission {submission.id}")
    submission.verification_attempts += 1
    submission.verification_lease_owner = worker_id
    submission.verification_lease_expires_at = now + timedelta(seconds=lease_seconds)
    await session.flush()
    return VerificationLease(submission=submission, proof_content=proof)


async def release_verification(
    session: AsyncSession,
    submission_id: uuid.UUID,
    *,
    worker_id: str,
    retry_after_seconds: int,
) -> None:
    """Release a lease after an infrastructure failure, preserving no false verdict."""
    result = await session.execute(
        select(Submission).where(Submission.id == submission_id).with_for_update()
    )
    submission = result.scalar_one_or_none()
    if submission is None:
        raise RecordNotFound("submission not found")
    if submission.verification_lease_owner != worker_id:
        raise RecordConflict(
            "verification lease is not owned by this worker",
            reason_code="VERIFICATION_LEASE_LOST",
        )
    submission.verification_lease_owner = None
    submission.verification_lease_expires_at = None
    now = await session.scalar(select(func.now()))
    if now is None:  # pragma: no cover
        raise RuntimeError("database clock is unavailable")
    submission.verification_next_attempt_at = now + timedelta(seconds=retry_after_seconds)
    await session.flush()


async def record_verification_infrastructure_failure(
    session: AsyncSession,
    submission_id: uuid.UUID,
    *,
    worker_id: str,
    verifier_version: str,
    container_digest: str,
    retry_after_seconds: int,
    started_at: datetime,
    finished_at: datetime,
) -> VerificationRun:
    """Audit a retryable attempt without manufacturing a Lean verdict."""
    result = await session.execute(
        select(Submission).where(Submission.id == submission_id).with_for_update()
    )
    submission = result.scalar_one_or_none()
    if submission is None:
        raise RecordNotFound("submission not found")
    if submission.verification_lease_owner != worker_id:
        raise RecordConflict(
            "verification lease is not owned by this worker",
            reason_code="VERIFICATION_LEASE_LOST",
        )
    run = VerificationRun(
        submission_id=submission.id,
        task_bundle_sha256=bytes(submission.task_bundle_sha256),
        proof_digest=bytes(submission.proof_digest),
        verifier_version=verifier_version,
        container_digest=digests.to_bytes(container_digest),
        sandbox_mode="not-completed",
        accepted=False,
        reason_code="VERIFIER_INFRASTRUCTURE_ERROR",
        stage="WORKER",
        checks=None,
        report=None,
        report_digest=None,
        started_at=started_at,
        finished_at=finished_at,
    )
    session.add(run)
    submission.verification_lease_owner = None
    submission.verification_lease_expires_at = None
    now = await session.scalar(select(func.now()))
    if now is None:  # pragma: no cover
        raise RuntimeError("database clock is unavailable")
    submission.verification_next_attempt_at = now + timedelta(seconds=retry_after_seconds)
    await session.flush()
    return run


async def create_reward_event(
    session: AsyncSession,
    submission_id: uuid.UUID,
    *,
    treasury_account: str,
    payout_policy_commit: str,
    initiated_by: str = ACTOR_REWARD,
) -> RewardEvent:
    """Create the single manual-multisig payout instruction for an eligible winner."""
    result = await session.execute(
        select(Submission).where(Submission.id == submission_id).with_for_update()
    )
    submission = result.scalar_one_or_none()
    if submission is None:
        raise RecordNotFound("submission not found")
    if RewardState(submission.reward_status) is not RewardState.ELIGIBLE:
        raise RecordConflict(
            "submission is not reward-eligible", reason_code="REWARD_NOT_ELIGIBLE"
        )
    winner = await problem_winner(session, submission.problem_id)
    if winner is None or winner.submission_id != submission.id:
        raise RecordConflict(
            "submission does not own the problem winner claim",
            reason_code="NOT_PROBLEM_WINNER",
        )
    existing = await reward_event(session, submission.id)
    if existing is not None:
        if (
            existing.treasury_account != treasury_account
            or existing.payout_policy_commit != payout_policy_commit
        ):
            raise RecordConflict(
                "submission already has a different payout instruction",
                reason_code="PAYOUT_INTENT_CONFLICT",
            )
        return existing

    payout = RewardEvent(
        submission_id=submission.id,
        eligibility_reason=winner.claim_reason,
        bounty_amount_rao=submission.bounty_amount_rao,
        payout_policy_commit=payout_policy_commit,
        destination_coldkey=submission.payment_sender,
        destination_hotkey=submission.hotkey,
        treasury_account=treasury_account,
        status=PayoutState.AWAITING_MULTISIG,
        initiated_by=initiated_by,
    )
    session.add(payout)
    await session.flush()
    return payout


async def confirm_manual_payout(
    session: AsyncSession,
    reward_event_id: int,
    *,
    extrinsic_reference: str,
    finalized_block: int,
    confirmed_at: datetime | None = None,
    actor: str = ACTOR_REWARD,
) -> RewardEvent:
    result = await session.execute(
        select(RewardEvent).where(RewardEvent.id == reward_event_id).with_for_update()
    )
    payout = result.scalar_one_or_none()
    if payout is None:
        raise RecordNotFound("reward event not found")
    if PayoutState(payout.status) is PayoutState.CONFIRMED:
        if (
            payout.extrinsic_reference != extrinsic_reference
            or payout.finalized_block != finalized_block
        ):
            raise RecordConflict(
                "reward event is confirmed with a different transfer",
                reason_code="PAYOUT_REFERENCE_CONFLICT",
            )
        return payout
    if PayoutState(payout.status) is not PayoutState.AWAITING_MULTISIG:
        raise RecordConflict(
            "reward event is not awaiting multisig execution",
            reason_code="PAYOUT_NOT_AWAITING_MULTISIG",
        )
    submission = await session.get(Submission, payout.submission_id, with_for_update=True)
    if submission is None:
        raise RecordNotFound("submission not found")
    previous = RewardState(submission.reward_status).value
    payout.status = PayoutState.CONFIRMED
    payout.extrinsic_reference = extrinsic_reference
    payout.finalized_block = finalized_block
    payout.confirmed_at = confirmed_at or datetime.now(timezone.utc)
    submission.reward_status = RewardState.REWARDED
    await session.flush()
    await _record_event(
        session,
        submission.id,
        status_field=SubmissionStatusField.REWARD,
        from_status=previous,
        to_status=RewardState.REWARDED.value,
        reward_event_id=payout.id,
        detail={
            "extrinsic_reference": payout.extrinsic_reference,
            "finalized_block": finalized_block,
            "amount_rao": payout.bounty_amount_rao,
        },
        actor=actor,
    )
    await session.flush()
    return payout


async def mark_reward_failed(
    session: AsyncSession,
    reward_event_id: int,
    *,
    reason_code: str,
    actor: str = ACTOR_REWARD,
) -> RewardEvent:
    result = await session.execute(
        select(RewardEvent).where(RewardEvent.id == reward_event_id).with_for_update()
    )
    payout = result.scalar_one_or_none()
    if payout is None:
        raise RecordNotFound("reward event not found")
    if PayoutState(payout.status) in {PayoutState.CONFIRMED, PayoutState.FAILED}:
        raise RecordConflict(
            "reward event is already terminal", reason_code="PAYOUT_ALREADY_TERMINAL"
        )
    submission = await session.get(Submission, payout.submission_id, with_for_update=True)
    if submission is None:
        raise RecordNotFound("submission not found")
    previous = RewardState(submission.reward_status).value
    payout.status = PayoutState.FAILED
    payout.failure_reason = reason_code
    submission.reward_status = RewardState.FAILED
    submission.failure_reason = reason_code
    await session.flush()
    await _record_event(
        session,
        submission.id,
        status_field=SubmissionStatusField.REWARD,
        from_status=previous,
        to_status=RewardState.FAILED.value,
        reward_event_id=payout.id,
        detail={"reason_code": reason_code},
        actor=actor,
    )
    await session.flush()
    return payout
