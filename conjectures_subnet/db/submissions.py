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

Concurrency safety comes from the unique constraints in the migration — `(signer_coldkey,
idempotency_key)`, `(account_id, idempotency_key)`, `payment_reference`, and `proof_digest` —
not from read-then-write checks, so two simultaneous requests cannot both succeed.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from conjectures_subnet.attribution import PublicCredit
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
    ReviewDecision,
    ReviewerKind,
    ReviewOutcome,
    RewardEvent,
    RewardState,
    Submission,
    TaskMode,
    VerificationRun,
    VerificationState,
)
from verifier.hashing import canonical_json_bytes, sha256_bytes

# Constraint names from deploy/migrate/sql/V001__initial_schema.sql. Matching on the name is
# what lets one IntegrityError be reported as the specific conflict the miner caused.
IDEMPOTENCY_CONSTRAINT = "submissions_idempotency_unique"
PAYMENT_CONSTRAINT = "submissions_payment_reference_unique"
PROOF_CONSTRAINT = "submissions_proof_digest_key"
REWARD_TARGET_CONSTRAINT = "submissions_reward_target_reward_unique"
# V033. One correction per decision, so the append-only history is a chain rather than a fork.
SUPERSEDES_CONSTRAINT = "review_decisions_supersedes_unique"

PROBLEM_ALREADY_AWARDED = "PROBLEM_ALREADY_AWARDED"
PROBLEM_CONTRADICTED = "PROBLEM_VERIFIED_IN_BOTH_MODES"


@dataclass(frozen=True)
class NewSubmission:
    """One confirmed-paid submission, ready to record."""

    signer_coldkey: str
    idempotency_key: uuid.UUID
    request_digest: str  # sha256:<hex>; converted at the column
    task_id: str
    task_bundle_sha256: str  # sha256:<hex>
    problem_id: str  # from the allowlist, not the request: the miner does not choose it
    reward_target_id: str  # stable across source repins of one exact theorem target
    task_mode: TaskMode
    proof_content: bytes  # the miner's Main.lean, exactly as admitted
    proof_sha256: str  # sha256:<hex>
    payment_reference: str
    # The coldkey that paid. Since V035 it must equal `signer_coldkey`: the key that sent the
    # money is the key that signs for it, which is what replaced the old two-key arrangement
    # where a hotkey signed and the chain was asked whether its owner had paid.
    payment_sender: str
    payment_amount_rao: int
    payment_block: int
    signer_signature: bytes  # 64 bytes by signer_coldkey over request_digest
    manual_review_required: bool
    review_policy_version: str
    # Indicative snapshot retained for audit. It is not a payout lock.
    bounty_amount_rao: int
    bounty_policy_version: str
    bounty_inputs: Mapping[str, Any] | None = None
    public_credit_name: str | None = None
    public_credit_url: str | None = None
    public_credit_orcid: str | None = None


@dataclass(frozen=True)
class SubmissionView:
    submission: Submission
    verification: VerificationRun | None
    replayed: bool = False


@dataclass(frozen=True)
class RecordedVerdict:
    """One recorded run, and whether it decided the submission."""

    run: VerificationRun
    applied: bool


@dataclass(frozen=True)
class AccountCounts:
    """How much work one account has in each state. Every field is a count of its own rows.

    Three numbers rather than a status histogram because these are the three a signed-in page
    has a decision to make about: how much has been submitted at all, how much is still waiting
    on the reward decision, and how much has been approved but not yet paid.
    """

    submissions_total: int
    submissions_in_review: int
    rewards_unclaimed: int


def canonical_request_digest(
    *,
    signer_coldkey: str,
    task_id: str,
    task_bundle_sha256: str,
    proof_sha256: str,
    payment_reference: str,
    idempotency_key: str,
    public_credit: PublicCredit | None = None,
) -> str:
    """The identity of a request, and the message the miner signs.

    Reusing an idempotency key with any of these values changed is a conflict rather than a
    replay, so every one of them is part of the digest. It binds the proof digest too, so a
    signature cannot be reused for different proof bytes.
    """
    payload = {
        # V035. Was "hotkey" and is deliberately a different key name, not the same name
        # holding a different address: the digest IS the signed message, so a miner signing the
        # old shape must fail loudly rather than produce a signature that verifies against an
        # identity nobody proved.
        "signer_coldkey": signer_coldkey,
        "idempotency_key": idempotency_key,
        "payment_reference": payment_reference,
        "proof_sha256": proof_sha256,
        "task_bundle_sha256": task_bundle_sha256,
        "task_id": task_id,
    }
    # Omit rather than encode null, preserving the digest shape for miners who do not request
    # public name credit. When present, every published byte is covered by the signature.
    if public_credit is not None:
        payload["public_credit"] = public_credit.to_dict()
    return sha256_bytes(canonical_json_bytes(payload))


def _violates(exc: IntegrityError, constraint: str) -> bool:
    return constraint in str(getattr(exc, "orig", exc))



def session_request_digest(
    *,
    account_id: str,
    task_id: str,
    task_bundle_sha256: str,
    proof_sha256: str,
    idempotency_key: str,
    public_credit: PublicCredit | None = None,
) -> str:
    """The identity of a session-authorised request.

    `submissions.request_digest` means "the canonical request", not "the bytes someone signed" —
    the key-signed paths sign it as well, which is a second job for the same value. This path has
    no signature, so the digest keeps only the first job, and it is the one that matters for the
    column's stated purpose: telling a replay from a conflict. Reusing an idempotency key with
    any of these values changed is a conflict.

    Keyed by account rather than by a key, because on this path the session is the identity
    that authorised it and there is no signature at all.
    `payment_reference` is absent for the same reason it is on every credit-funded path: there
    is no transfer.
    """
    payload = {
        "account_id": account_id,
        "idempotency_key": idempotency_key,
        "proof_sha256": proof_sha256,
        "task_bundle_sha256": task_bundle_sha256,
        "task_id": task_id,
    }
    if public_credit is not None:
        payload["public_credit"] = public_credit.to_dict()
    return sha256_bytes(canonical_json_bytes(payload))


async def find_by_idempotency_key(
    session: AsyncSession, signer_coldkey: str, idempotency_key: uuid.UUID
) -> Submission | None:
    """The replay lookup for a coldkey-signed submission.

    Mirrors `submissions_signer_idempotency_unique`. Re-keyed by V035 from the hotkey that used
    to identify a miner to the coldkey that now does; the original index over
    `(hotkey, idempotency_key)` stopped constraining anything the moment `hotkey` became null
    on every new row, because PostgreSQL treats NULLs in a unique index as distinct.
    """
    result = await session.execute(
        select(Submission).where(
            Submission.signer_coldkey == signer_coldkey,
            Submission.idempotency_key == idempotency_key,
        )
    )
    return result.scalar_one_or_none()


async def find_session_submission_by_idempotency_key(
    session: AsyncSession, account_id: uuid.UUID, idempotency_key: uuid.UUID
) -> Submission | None:
    """The replay lookup for a session-authorised submission.

    Scoped by account and restricted to rows with a null hotkey, mirroring
    `submissions_session_idempotency_unique`. The account half is what stops it answering
    another caller's submission; the null-hotkey half is now true of every new row and is kept
    only so the query still matches the partial index it was written for.
    """
    result = await session.execute(
        select(Submission).where(
            Submission.account_id == account_id,
            Submission.hotkey.is_(None),
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
    session: AsyncSession, submission_id: uuid.UUID, signer_coldkey: str
) -> SubmissionView:
    submission = await session.get(Submission, submission_id)
    # Another miner's submission is reported as absent rather than forbidden, so identifiers
    # cannot be probed for existence.
    if submission is None or submission.signer_coldkey != signer_coldkey:
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
        session, request.signer_coldkey, request.idempotency_key
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
        signer_coldkey=request.signer_coldkey,
        public_credit_name=request.public_credit_name,
        public_credit_url=request.public_credit_url,
        public_credit_orcid=request.public_credit_orcid,
        idempotency_key=request.idempotency_key,
        request_digest=digests.to_bytes(request.request_digest),
        task_id=request.task_id,
        task_bundle_sha256=digests.to_bytes(request.task_bundle_sha256),
        problem_id=request.problem_id,
        reward_target_id=request.reward_target_id,
        task_mode=request.task_mode,
        proof_digest=digests.to_bytes(request.proof_sha256),
        payment_reference=request.payment_reference,
        payment_sender=request.payment_sender,
        payment_amount_rao=request.payment_amount_rao,
        payment_block=request.payment_block,
        signer_signature=request.signer_signature,
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
) -> RecordedVerdict:
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

    already_ruled = submission.verification_status != VerificationState.UNVERIFIED
    if not already_ruled:
        verdict = VerificationState.VERIFIED if accepted else VerificationState.REJECTED
        submission.verification_status = verdict
        if not accepted:
            submission.failure_reason = reason_code
        # Sessions run with autoflush=False, so the verdict is flushed before the problem-level
        # checks below: they query this submission too, and must see it as VERIFIED.
        await session.flush()

        if accepted and not submission.manual_review_required:
            # Manual review is disabled for this submission, so eligibility is automatic — but
            # it is still recorded as a policy decision rather than left implicit.
            await approve_automatically(session, submission)

    submission.verification_lease_until = None
    submission.verification_lease_owner = None
    await session.flush()
    return RecordedVerdict(run=run, applied=not already_ruled)


async def reward_target_holder(
    session: AsyncSession, submission: Submission
) -> uuid.UUID | None:
    """The other submission already holding this reward target's single reward, if any.

    Mirrors the predicate of `submissions_reward_target_reward_unique`. The index is the authority;
    this only lets the service give the reason instead of surfacing an IntegrityError.
    """
    result = await session.execute(
        select(Submission.id)
        .where(
            Submission.reward_target_id == submission.reward_target_id,
            Submission.id != submission.id,
            Submission.reward_status != RewardState.INELIGIBLE,
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def problem_verified_modes(
    session: AsyncSession, submission: Submission
) -> set[TaskMode]:
    """Which task modes of this problem have a Lean-valid proof."""
    result = await session.execute(
        select(Submission.task_mode)
        .where(
            Submission.problem_id == submission.problem_id,
            Submission.verification_status == VerificationState.VERIFIED,
        )
        .distinct()
    )
    return set(result.scalars())


async def approve_automatically(
    session: AsyncSession, submission: Submission
) -> ReviewDecision:
    """Record the AUTOMATIC review decision and make the submission reward-eligible.

    Two problem-level facts can stop that, because one conjecture carries one reward across
    both of its tasks:

    * the conjecture has been proved *and* refuted -- one of those proofs must be wrong, or the
      generated negation is not the negation. Nothing automatic should pay either, so the
      submission is left `UNREVIEWED` with an ADVISORY note and enters the human review queue;
    * another submission already holds the reward. That is not a fault in this proof, but it
      cannot be paid, so the reward decision is a recorded rejection rather than an approval.
    """
    verified_modes = await problem_verified_modes(session, submission)
    if len(verified_modes) > 1:
        # Advisory, so it is evidence rather than a decision: a human must look at a conjecture
        # that appears to be both true and false before anyone is paid for either answer.
        advisory = ReviewDecision(
            submission_id=submission.id,
            decision=ReviewOutcome.REJECTED,
            kind=ReviewerKind.ADVISORY,
            reviewer="system",
            policy_version=submission.review_policy_version,
            reason_code=PROBLEM_CONTRADICTED,
            notes=(
                "problem verified in both modes: "
                + ", ".join(sorted(mode.value for mode in verified_modes))
            ),
        )
        session.add(advisory)
        await session.flush()
        return advisory

    holder = await reward_target_holder(session, submission)
    if holder is not None:
        superseded = ReviewDecision(
            submission_id=submission.id,
            decision=ReviewOutcome.REJECTED,
            kind=ReviewerKind.AUTOMATIC,
            reviewer="system",
            policy_version=submission.review_policy_version,
            reason_code=PROBLEM_ALREADY_AWARDED,
            notes=f"reward target {submission.reward_target_id} is already held by {holder}",
        )
        session.add(superseded)
        await session.flush()
        submission.manual_review_status = ManualReviewState.REJECTED
        # reward_status stays INELIGIBLE: the proof is valid, but the reward is spent.
        await session.flush()
        return superseded

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


# The three ways a human decision is refused by durable state rather than by policy. All are
# conflicts rather than bad requests: the body was well formed and would have been accepted a
# moment earlier, which is exactly what a reviewer needs told apart from a rejected reason code.
REVIEW_ALREADY_DECIDED = "REVIEW_ALREADY_DECIDED"
REWARD_ALREADY_IN_FLIGHT = "REWARD_ALREADY_IN_FLIGHT"
REWARD_TARGET_ALREADY_HELD = "REWARD_TARGET_ALREADY_HELD"

# And the three a *correction* is refused by, which are different failures with different
# remedies — see `correct_human_decision`.
REVIEW_NOT_DECIDED = "REVIEW_NOT_DECIDED"
REVIEW_CORRECTION_STALE = "REVIEW_CORRECTION_STALE"
REWARD_ALREADY_PAID = "REWARD_ALREADY_PAID"


async def record_human_decision(
    session: AsyncSession,
    submission_id: uuid.UUID,
    *,
    decision: ReviewOutcome,
    reason_code: str,
    reviewer: str,
    notes: str | None = None,
    notes_public: str | None = None,
) -> ReviewDecision:
    """Record the binding HUMAN review decision and move the reward with it.

    The counterpart of `approve_automatically` for the case manual review exists for, and the one
    write in this module that turns a Lean-valid proof into money. Four properties, each of which
    is the reason a line below is there rather than an obvious simplification:

    * **The submission row is locked before anything is read off it.** Two reviewers with the panel
      open on the same submission is the ordinary case, not the exotic one, and without the lock
      the "still `UNREVIEWED`" check below would be a guess that both callers pass. `FOR UPDATE`
      makes the check a decision: the second transaction waits, then sees the first one's outcome
      and is refused. This is the concurrency answer the read-only router asked for.
    * **A second decision is refused, not superseded.** `review_decisions` is append-only and its
      `supersedes_id` chain exists precisely so a correction can be recorded — but a correction is
      a different act from a decision. It re-prices a payout that may already be in flight, and it
      needs its own reason for overriding a colleague. So this raises on an already-decided
      submission and leaves the chain to whatever records corrections.
    * **`reward_status` must still be `INELIGIBLE`.** Only an approval moves it off that value, so
      an `UNREVIEWED` submission that is `ELIGIBLE` or beyond means money is already moving on a
      submission nobody has decided. That is an anomaly, and a decision written over it would
      either double-pay or silently un-pay. It is refused rather than corrected here.
    * **A contradicted problem is not refused.** `approve_automatically` leaves a problem verified
      in both modes `UNREVIEWED` with an ADVISORY note *so that a human sees it*. Refusing the
      human decision on the same ground would make that submission undecidable by the only party
      able to resolve it.

    `reason_code` is checked against the published policy allowlists at the API boundary, not here:
    the allowlist is the API's published vocabulary (`submission_api.credits`), and the workers that
    also write this table have their own codes — `PROBLEM_ALREADY_AWARDED` is not a code any human
    may pick, and it is written by the function above.
    """
    submission = await session.get(Submission, submission_id, with_for_update=True)
    if submission is None:
        raise RecordNotFound("no such submission")
    # Same answer as `GET /v1/admin/reviews/{id}`, which serves Lean-verified submissions only:
    # there is nothing to decide about work the kernel has not accepted, and review can never make
    # a Lean-invalid proof valid.
    if submission.verification_status != VerificationState.VERIFIED:
        raise RecordNotFound("no such submission")

    if submission.manual_review_status != ManualReviewState.UNREVIEWED:
        raise RecordConflict(
            "this submission has already been decided",
            reason_code=REVIEW_ALREADY_DECIDED,
            manual_review_status=str(submission.manual_review_status),
        )
    if submission.reward_status != RewardState.INELIGIBLE:
        raise RecordConflict(
            "this submission's reward is already in flight; it cannot be decided now",
            reason_code=REWARD_ALREADY_IN_FLIGHT,
            reward_status=str(submission.reward_status),
        )

    if decision == ReviewOutcome.APPROVED:
        # Mirrors `submissions_reward_target_reward_unique`, which is the authority: setting
        # `reward_status` below would raise an IntegrityError naming the index. Checked first so
        # the reviewer is told which submission holds it and which code applies, rather than
        # reading a constraint name out of a 409.
        holder = await reward_target_holder(session, submission)
        if holder is not None:
            raise RecordConflict(
                "another submission already holds this reward target's reward; reject this one "
                "as DUPLICATE_OF_EARLIER_SUBMISSION instead",
                reason_code=REWARD_TARGET_ALREADY_HELD,
                reward_target_id=submission.reward_target_id,
                held_by=str(holder),
            )

    recorded = ReviewDecision(
        submission_id=submission.id,
        decision=decision,
        kind=ReviewerKind.HUMAN,
        reviewer=reviewer,
        # The submission's own policy version, matching `approve_automatically`: a decision is
        # taken under the policy the submission was accepted under, not under whichever version
        # happens to be current when the reviewer gets to it.
        policy_version=submission.review_policy_version,
        reason_code=reason_code,
        notes=notes,
        notes_public=notes_public,
    )
    session.add(recorded)
    await session.flush()

    if decision == ReviewOutcome.APPROVED:
        submission.manual_review_status = ManualReviewState.APPROVED
        # The money. `reward_events` is written by the payout path, which reads this column; the
        # trigger on that table re-checks the amount against the submission's bounty lock or the
        # decision recorded above, so an approval cannot price its own payout from here.
        submission.reward_status = RewardState.ELIGIBLE
    else:
        submission.manual_review_status = ManualReviewState.REJECTED
        # `reward_status` stays INELIGIBLE, which is where the guard above proved it already is.

    await session.flush()
    return recorded


async def latest_binding_decision(
    session: AsyncSession, submission_id: uuid.UUID
) -> ReviewDecision | None:
    """The current binding decision on a submission, or None if nobody has decided it.

    ADVISORY rows are excluded, for the reason `db.public._latest_reviews` gives: a model's
    assessment is evidence, and a later piece of evidence must not be able to hide the decision a
    human or the automatic path took. Highest id wins rather than latest `created_at` — the column
    is `Identity(always=True)`, so ids are the durable order and two decisions committed inside the
    same clock tick still have one.
    """
    return await session.scalar(
        select(ReviewDecision)
        .where(
            ReviewDecision.submission_id == submission_id,
            ReviewDecision.kind != ReviewerKind.ADVISORY,
        )
        .order_by(ReviewDecision.id.desc())
        .limit(1)
    )


async def _reward_event_exists(session: AsyncSession, submission_id: uuid.UUID) -> bool:
    """Whether any payout row has ever been opened against this submission.

    The authority on "money has started moving", and deliberately stronger than
    `reward_status`: an event in PENDING has been committed before the extrinsic is signed, and
    a FAILED one is an attempt that happened. A correction must not be able to un-approve a
    submission on the strength of a status column while a row in `reward_events` says otherwise.
    """
    found = await session.scalar(
        select(RewardEvent.id).where(RewardEvent.submission_id == submission_id).limit(1)
    )
    return found is not None


async def correct_human_decision(
    session: AsyncSession,
    submission_id: uuid.UUID,
    *,
    supersedes_id: int,
    decision: ReviewOutcome,
    reason_code: str,
    reviewer: str,
    notes: str,
    notes_public: str,
) -> ReviewDecision:
    """Correct a binding decision by appending the row that supersedes it.

    The counterpart `record_human_decision` deliberately refuses to be: that function turns an
    `UNREVIEWED` submission into a decided one and rejects a second attempt outright, because
    "decide" and "override a colleague" are different acts with different risks. This one is the
    second act, and every rule below is one of those risks.

    **It names the decision it corrects, and the caller must have seen it.** `supersedes_id` is
    required and is checked against the current binding decision under the row lock. A reviewer
    correcting a decision that somebody else has already corrected is refused with
    `REVIEW_CORRECTION_STALE` and the id that is actually current, rather than silently appending
    a second correction to a decision that is no longer the live one. This is also what makes a
    double-click safe without an idempotency key: the second request carries the id the first one
    just superseded.

    **A submission nobody decided cannot be corrected.** `REVIEW_NOT_DECIDED` rather than a
    quiet fallback to recording a first decision — the two endpoints ask for different things
    (this one requires the reason the earlier call was wrong) and a correction that silently
    became an initial decision would put that reason on a row nobody is overriding.

    **Un-approving is bounded by whether the money moved.** Turning APPROVED into REJECTED sets
    `reward_status` back to INELIGIBLE, which is safe exactly while no payout exists: nothing has
    been signed, and the submission simply leaves the payout notifier's queue. Once a
    `reward_events` row exists — in any state, including PENDING and FAILED — the correction is
    refused with `REWARD_ALREADY_PAID`, because nothing in this database can recall an extrinsic
    and a status column quietly walked backwards under a paid submission is worse than a refusal
    an operator has to act on. `RewardState.REWARDED` and `FAILED` are refused on the same
    ground even in the anomalous case where no event row accompanies them.

    **Re-approving re-checks the reward target.** A rejected submission's reward target may have
    been awarded to somebody else in the meantime, so the same
    `submissions_reward_target_reward_unique` guard `record_human_decision` applies is applied
    here — otherwise correcting a rejection would raise an IntegrityError naming an index instead
    of telling the reviewer which submission holds it.

    **A correction that does not change the outcome is still a correction.** Fixing a reason code
    or a published explanation on a decision that stays APPROVED leaves both status columns
    exactly where they are and appends the row anyway, because the wrong published sentence is
    the mistake being repaired and the history is what proves it was repaired rather than edited.

    `notes` is required rather than optional, unlike on a first decision: the internal audit
    trail is where the reason for overriding a colleague lives, and a correction with no stated
    reason is the one an operator cannot reconstruct later.
    """
    submission = await session.get(Submission, submission_id, with_for_update=True)
    if submission is None:
        raise RecordNotFound("no such submission")
    if submission.verification_status != VerificationState.VERIFIED:
        raise RecordNotFound("no such submission")

    current = await latest_binding_decision(session, submission_id)
    if current is None:
        raise RecordConflict(
            "this submission has no binding decision to correct",
            reason_code=REVIEW_NOT_DECIDED,
            manual_review_status=str(submission.manual_review_status),
        )
    if current.id != supersedes_id:
        raise RecordConflict(
            "this decision is no longer the current one; re-read it and correct that",
            reason_code=REVIEW_CORRECTION_STALE,
            # Named `current_*` rather than `decision`/`reason_code`, because `reason_code` is
            # already the refusal's own code in a problem body and two of them would be read as
            # one. These say what the live decision is, so the panel can re-render and retry
            # against it without a second request.
            review_decision_id=current.id,
            current_decision=str(current.decision),
            current_reason_code=current.reason_code,
        )

    was_approved = current.decision == ReviewOutcome.APPROVED
    now_approved = decision == ReviewOutcome.APPROVED

    if was_approved and not now_approved:
        paid = submission.reward_status in (RewardState.REWARDED, RewardState.FAILED)
        if paid or await _reward_event_exists(session, submission_id):
            raise RecordConflict(
                "a payout already exists for this submission; the approval cannot be "
                "withdrawn here",
                reason_code=REWARD_ALREADY_PAID,
                reward_status=str(submission.reward_status),
            )
    if not was_approved and now_approved:
        holder = await reward_target_holder(session, submission)
        if holder is not None:
            raise RecordConflict(
                "another submission already holds this reward target's reward; this rejection "
                "cannot be corrected to an approval",
                reason_code=REWARD_TARGET_ALREADY_HELD,
                reward_target_id=submission.reward_target_id,
                held_by=str(holder),
            )

    corrected = ReviewDecision(
        submission_id=submission.id,
        decision=decision,
        kind=ReviewerKind.HUMAN,
        reviewer=reviewer,
        # The submission's policy version, not the current one — matching both writes above. A
        # correction repairs a decision taken under a policy; it does not retry it under a newer
        # one, which would be a different act again.
        policy_version=submission.review_policy_version,
        reason_code=reason_code,
        notes=notes,
        notes_public=notes_public,
        supersedes_id=current.id,
    )
    session.add(corrected)
    # Before the status columns move, so `review_decisions_supersedes_unique` gets its say first:
    # if a concurrent correction beat this one past the staleness check, the insert is what
    # refuses it, and nothing has been written to the submission by then.
    #
    # The row lock above should make this unreachable — two corrections on one submission are
    # serialised, and the second one fails the staleness check. It is caught anyway because the
    # index is the authority and the check is the courtesy: a future caller that reaches this
    # function without the lock must get the conflict, not a 500 naming an index.
    try:
        await session.flush()
    except IntegrityError as exc:  # pragma: no cover - the row lock serialises corrections
        if not _violates(exc, SUPERSEDES_CONSTRAINT):
            raise
        raise RecordConflict(
            "this decision is no longer the current one; re-read it and correct that",
            reason_code=REVIEW_CORRECTION_STALE,
            review_decision_id=current.id,
        ) from exc

    if now_approved:
        submission.manual_review_status = ManualReviewState.APPROVED
        submission.reward_status = RewardState.ELIGIBLE
    else:
        submission.manual_review_status = ManualReviewState.REJECTED
        # Back to where a rejection leaves it. Reachable only through the guard above, which has
        # already proved no payout exists, so this cannot un-pay anything.
        submission.reward_status = RewardState.INELIGIBLE

    await session.flush()
    return corrected


async def log_rejection(
    session: AsyncSession,
    *,
    reason_code: str,
    http_status: int | None = None,
    claimed_ss58: str | None = None,
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
            claimed_ss58=claimed_ss58,
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
# from `get_for_miner` above, which scopes to the coldkey that signed one submission and
# predates accounts: a signed-in miner sees everything their account owns, including the
# session-authorised submissions that carry no key to scope by at all.


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


async def counts_for_account(
    session: AsyncSession, account_id: uuid.UUID
) -> AccountCounts:
    """The three per-account totals, in one pass over that account's rows.

    Filtered aggregates rather than three round trips, the same shape `public.queue_depths`
    uses: this is read on every session load, so it is one query against
    `submissions_account_idx` rather than three separate scans of the same rows.

    `submissions_in_review` uses the same predicate as the public in-review feed —
    Lean-verified and not yet decided — so an account's own count and the public queue cannot
    disagree about what "in review" means.

    `rewards_unclaimed` counts `ELIGIBLE`, which is *approved and not yet paid out*. There is no
    claim action in this system — a reward is pushed on chain by the payout worker, never pulled
    — so this is money owed rather than money waiting to be collected, and a client should label
    it accordingly.
    """
    statement = select(
        func.count(),
        func.count().filter(
            (Submission.verification_status == VerificationState.VERIFIED)
            & (Submission.manual_review_status == ManualReviewState.UNREVIEWED)
        ),
        func.count().filter(Submission.reward_status == RewardState.ELIGIBLE),
    ).where(Submission.account_id == account_id)
    row = (await session.execute(statement)).one()
    return AccountCounts(
        submissions_total=row[0],
        submissions_in_review=row[1],
        rewards_unclaimed=row[2],
    )


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
        # A PENDING event is an internal payout instruction, not on-chain activity.  The account
        # reward tracker begins at SUBMITTED (best-chain event) and advances to CONFIRMED only
        # after finality, so it cannot claim a signer is paying merely because a command exists.
        .where(
            Submission.account_id == account_id,
            RewardEvent.chain_observed.is_(True),
            RewardEvent.status.in_((PayoutState.SUBMITTED, PayoutState.CONFIRMED)),
        )
        .order_by(RewardEvent.id.desc())
        .limit(limit)
    )
    if after_id is not None:
        statement = statement.where(RewardEvent.id < after_id)
    return [(row[0], row[1]) for row in (await session.execute(statement)).all()]
