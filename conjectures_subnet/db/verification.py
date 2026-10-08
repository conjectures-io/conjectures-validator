"""The verification seam: claiming work off the queue and giving it back."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from conjectures_subnet.db import digests
from conjectures_subnet.db.models import Submission, VerificationState

# One statement, so the claim is atomic without the caller holding a transaction open. The
# inner SELECT locks exactly one candidate row and skips any a concurrent worker already holds;
# the outer UPDATE stamps the lease on it. now() is the transaction timestamp, which is what
# makes "expired" mean the same thing to every statement in this transaction.
#
# The candidate must be one of the exact (task_id, task_bundle_sha256, problem_id) triples the
# calling worker serves. `problem_id` commits to the source snapshot that accepted the
# submission, so a worker can only ever lease paid work whose original verification environment
# it is. Everything else is invisible to the claim and therefore never has an attempt counted
# against it here: the increment below happens in the same statement as the filter.
CLAIM = text(
    """
    UPDATE submissions AS s
       SET verification_lease_until = now() + make_interval(secs => :lease_seconds),
           verification_lease_owner = :owner,
           verification_attempts    = s.verification_attempts + 1
     WHERE s.id = (
            SELECT candidate.id
              FROM submissions AS candidate
              JOIN unnest(CAST(:served_task_ids AS text[]),
                          CAST(:served_digests AS bytea[]),
                          CAST(:served_problem_ids AS text[]))
                   AS served(task_id, task_bundle_sha256, problem_id)
                ON served.task_id = candidate.task_id
               AND served.task_bundle_sha256 = candidate.task_bundle_sha256
               AND served.problem_id = candidate.problem_id
             WHERE candidate.verification_status = 'UNVERIFIED'
               AND (candidate.verification_lease_until IS NULL
                    OR candidate.verification_lease_until < now())
               AND candidate.verification_attempts < :max_attempts
               AND candidate.id <> ALL(CAST(:excluded_ids AS uuid[]))
             ORDER BY candidate.created_at
               FOR UPDATE OF candidate SKIP LOCKED
             LIMIT 1)
    RETURNING s.id,
              s.task_id,
              s.task_bundle_sha256,
              s.problem_id,
              s.proof_digest,
              s.verification_attempts,
              s.verification_lease_until
    """
)

# Unverified paid work this worker does not serve: it waits for its own environment. Counted
# for the operator, never claimed.
UNSERVED = text(
    """
    SELECT count(*) AS pending
      FROM submissions AS candidate
     WHERE candidate.verification_status = 'UNVERIFIED'
       AND NOT EXISTS (
            SELECT 1
              FROM unnest(CAST(:served_task_ids AS text[]),
                          CAST(:served_digests AS bytea[]),
                          CAST(:served_problem_ids AS text[]))
                   AS served(task_id, task_bundle_sha256, problem_id)
             WHERE served.task_id = candidate.task_id
               AND served.task_bundle_sha256 = candidate.task_bundle_sha256
               AND served.problem_id = candidate.problem_id)
    """
)

# Re-stamp an existing lease. Called once, after the task is resolved and its declared timeout
# is known — not on a timer. A claim cannot know how long the work will take, because the task
# is only identified by the row it just claimed.
EXTEND = text(
    """
    UPDATE submissions
       SET verification_lease_until = now() + make_interval(secs => :lease_seconds)
     WHERE id = :submission_id
       AND verification_status = 'UNVERIFIED'
       AND verification_lease_owner = :owner
    RETURNING id
    """
)

# Give back a claim this worker should never have made: drop the lease and refund the attempt
# the claim counted. Only for a submission that turned out not to be ours to verify.
RELEASE_UNCONSUMED = text(
    """
    UPDATE submissions
       SET verification_lease_until = NULL,
           verification_lease_owner = NULL,
           verification_attempts    = verification_attempts - 1
     WHERE id = :submission_id
       AND verification_status = 'UNVERIFIED'
       AND verification_lease_owner = :owner
       AND verification_attempts > 0
    RETURNING id
    """
)

# Hand the row back without spending the wait: used when the failure was ours, not the proof's.
RELEASE = text(
    """
    UPDATE submissions
       SET verification_lease_until = NULL,
           verification_lease_owner = NULL
     WHERE id = :submission_id
       AND verification_status = 'UNVERIFIED'
       AND verification_lease_owner = :owner
    RETURNING id
    """
)


@dataclass(frozen=True)
class ClaimedSubmission:
    """One leased submission, as plain data rather than an ORM row.

    The claim transaction has already committed by the time a caller sees this, so an attached
    instance would only invite reads against a closed unit of work. Digests are the prefixed
    `sha256:<hex>` form the verifier CLI expects, converted here so the worker never handles
    raw bytes.
    """

    submission_id: uuid.UUID
    task_id: str
    task_bundle_sha256: str
    proof_digest: str
    attempts: int
    lease_until: dt.datetime
    # Commits to the source snapshot that accepted the submission; routes it to its environment.
    problem_id: str = ""

    @property
    def proof_digest_bytes(self) -> bytes:
        """The raw form `proofs.digest` is keyed by."""
        return digests.to_bytes(self.proof_digest)


@dataclass(frozen=True)
class ServedSubmissionKey:
    """One exact (task, bundle, intake snapshot) triple a worker may claim."""

    task_id: str
    task_bundle_sha256: str  # sha256:<hex>
    problem_id: str


def _served_parameters(served: Sequence[ServedSubmissionKey]) -> dict[str, list]:
    return {
        "served_task_ids": [key.task_id for key in served],
        "served_digests": [digests.to_bytes(key.task_bundle_sha256) for key in served],
        "served_problem_ids": [key.problem_id for key in served],
    }


async def claim_next(
    session: AsyncSession,
    *,
    owner: str,
    lease_seconds: int,
    max_attempts: int,
    served: Sequence[ServedSubmissionKey],
    excluded: Sequence[uuid.UUID] = (),
) -> ClaimedSubmission | None:
    """Lease the oldest unclaimed unverified submission this worker serves, or None.

    `served` is required and is the whole routing decision: only a submission whose exact
    task, bundle digest and intake problem identity appear in it can be leased, and only a
    leased submission has an attempt counted. An empty set claims nothing.

    `lease_seconds` should cover the task's own `timeout_seconds` plus enough margin for
    container startup and the recording transaction; the verifier enforces the real deadline,
    so a lease that is too short only causes duplicated work, never a wrong verdict.

    `max_attempts` bounds how often one submission may be claimed. A row at the cap stops being
    returned and waits for an operator instead of cycling.
    """
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    if max_attempts <= 0:
        raise ValueError("max_attempts must be positive")
    if isinstance(served, (str, bytes)) or not all(
        isinstance(key, ServedSubmissionKey) for key in served
    ):
        raise TypeError("served must be a sequence of ServedSubmissionKey")
    if not served:
        return None
    row = (
        await session.execute(
            CLAIM,
            {
                "owner": owner,
                "lease_seconds": lease_seconds,
                "max_attempts": max_attempts,
                "excluded_ids": list(excluded),
                **_served_parameters(served),
            },
        )
    ).one_or_none()
    if row is None:
        return None
    return ClaimedSubmission(
        submission_id=row.id,
        task_id=row.task_id,
        task_bundle_sha256=digests.to_prefixed(row.task_bundle_sha256),
        proof_digest=digests.to_prefixed(row.proof_digest),
        attempts=row.verification_attempts,
        lease_until=row.verification_lease_until,
        problem_id=row.problem_id,
    )


async def unserved_pending(
    session: AsyncSession, *, served: Sequence[ServedSubmissionKey]
) -> int:
    """How many unverified submissions belong to some other verification environment."""
    row = (await session.execute(UNSERVED, _served_parameters(served))).one()
    return int(row.pending)


async def release_unconsumed(
    session: AsyncSession, submission_id: uuid.UUID, *, owner: str
) -> bool:
    """Undo our own claim, attempt included: the submission was never ours to verify."""
    released = await session.execute(
        RELEASE_UNCONSUMED, {"submission_id": submission_id, "owner": owner}
    )
    return released.one_or_none() is not None


async def extend(
    session: AsyncSession,
    submission_id: uuid.UUID,
    *,
    owner: str,
    lease_seconds: int,
) -> bool:
    """Re-stamp our lease to cover the work now that its declared timeout is known.

    False means the lease is no longer ours — it expired and another worker took the row, or a
    verdict has already been recorded. The caller should abandon the job rather than start a
    verifier whose result it is no longer entitled to write.
    """
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    # The id is returned rather than counted: `id` is the primary key, so a returned row means
    # the guarded UPDATE matched, and it asks nothing of the driver's rowcount.
    updated = await session.execute(
        EXTEND,
        {
            "submission_id": submission_id,
            "owner": owner,
            "lease_seconds": lease_seconds,
        },
    )
    return updated.one_or_none() is not None


async def release(
    session: AsyncSession, submission_id: uuid.UUID, *, owner: str
) -> bool:
    """Drop our lease so the submission is claimable again now.

    For a failure that is the validator's — no sandbox, a dead container, an unparseable
    report. The attempt is not refunded, so a systematic failure still converges on the
    `max_attempts` cap instead of spinning.

    Scoped to `owner` and to `UNVERIFIED`, so a worker whose lease already expired cannot strip
    the lease a second worker is now holding.
    """
    released = await session.execute(
        RELEASE, {"submission_id": submission_id, "owner": owner}
    )
    return released.one_or_none() is not None


async def lock_owned_for_recording(
    session: AsyncSession, submission_id: uuid.UUID, *, owner: str
) -> Submission | None:
    """Lock the row only if this live lease still authorizes a final verdict.

    The lock and the caller's verdict write share one transaction. A worker that finishes after
    expiry, or after another worker reclaimed the row, therefore cannot clear the new lease or
    apply a stale report.
    """
    result = await session.execute(
        select(Submission)
        .where(
            Submission.id == submission_id,
            Submission.verification_status == VerificationState.UNVERIFIED,
            Submission.verification_lease_owner == owner,
            Submission.verification_lease_until.is_not(None),
            Submission.verification_lease_until >= func.now(),
        )
        .with_for_update()
    )
    return result.scalar_one_or_none()


__all__ = [
    "ClaimedSubmission",
    "ServedSubmissionKey",
    "claim_next",
    "extend",
    "lock_owned_for_recording",
    "release",
    "release_unconsumed",
    "unserved_pending",
]
