"""The loop: claim an unverified submission, verify it, record the verdict.

Three transactions per submission, none of them open while Lean runs — claim, then extend the
lease once the task's declared timeout is known, then record. See
`conjectures_subnet/db/verification.py` for why holding one transaction across the whole job is
not available to us.

Nothing here decides anything about a proof. The verifier decides, `outcomes.classify` decides
whether the answer is about the proof at all, and this module's only judgement is the refusal to
record an accept that was not produced under the real sandbox.

Every pass runs inside a `work_context`, so the Axiom events below and every log record the pass
produces underneath them share one `request_id`. That is what makes a single submission's journey
— claimed, verified, recorded, or handed back — one query rather than a timestamp correlation.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conjectures_subnet.axiom import get_axiom, work_context
from conjectures_subnet.db import submissions as store
from conjectures_subnet.db import verification as queue
from conjectures_subnet.db.engine import async_session_scope
from verification_worker.outcomes import Outcome, classify
from verification_worker.runner import (
    RunnerFailure,
    VerifierRunner,
    assert_production_report,
)
from verification_worker.settings import WorkerSettings
from verification_worker.tasks import TaskNotAllowed, TaskResolver
from verifier.errors import ReasonCode

logger = logging.getLogger("verification_worker")

# The only sandbox mode that means the isolation `SECURITY.md` describes was actually in force.
PRODUCTION_SANDBOX_MODE = "landrun+seccomp"

# Not a ReasonCode: the verifier never ran, so there is nothing of its vocabulary to report.
LEASE_LOST = "LEASE_LOST"


@dataclass(frozen=True)
class Processed:
    """What one pass of the loop did, for logs, tests and metrics."""

    submission_id: uuid.UUID
    outcome: Outcome
    reason_code: str
    attempts: int
    applied: bool = False  # whether this run decided the submission


class VerificationWorker:
    def __init__(
        self,
        *,
        settings: WorkerSettings,
        sessions: async_sessionmaker[AsyncSession],
        runner: VerifierRunner,
        tasks: TaskResolver,
    ) -> None:
        self.settings = settings
        self.sessions = sessions
        self.runner = runner
        self.tasks = tasks

    async def process_one(self) -> Processed | None:
        """Verify at most one submission. None means the queue is empty."""
        with work_context():
            return await self._process_one()

    async def _process_one(self) -> Processed | None:
        async with async_session_scope(self.sessions) as session:
            claim = await queue.claim_next(
                session,
                owner=self.settings.owner,
                lease_seconds=self.settings.claim_lease_seconds,
                max_attempts=self.settings.max_attempts,
            )
        if claim is None:
            return None
        logger.info(
            "claimed submission=%s task=%s attempt=%d",
            claim.submission_id,
            claim.task_id,
            claim.attempts,
        )
        get_axiom().info(
            source="verification-worker",
            event_type="submission_claimed",
            submission_id=str(claim.submission_id),
            task_id=claim.task_id,
            attempt=claim.attempts,
            owner=self.settings.owner,
        )

        # The digest comes from the row, not the request: a task whose published bundle has
        # since changed fails closed here rather than being verified against different bytes.
        try:
            task = self.tasks.resolve(
                task_id=claim.task_id, task_bundle_sha256=claim.task_bundle_sha256
            )
        except TaskNotAllowed as exc:
            return await self._operator(
                claim, ReasonCode.TASK_COMMITMENT_MISMATCH, str(exc)
            )

        task_timeout = task.timeout_seconds
        async with async_session_scope(self.sessions) as session:
            held = await queue.extend(
                session,
                claim.submission_id,
                owner=self.settings.owner,
                lease_seconds=self.settings.lease_seconds(task_timeout),
            )
        if not held:
            # Someone else owns this row now. Do not start work whose verdict we could not
            # write, and do not touch the row — the lease we would clear is theirs.
            logger.warning("lost lease before starting submission=%s", claim.submission_id)
            return self._lease_lost(claim, stage="before_start")

        async with async_session_scope(self.sessions) as session:
            proof = await store.proof_bytes(session, claim.proof_digest_bytes)
        if proof is None:  # pragma: no cover - the FK on proof_digest guarantees the row
            return await self._operator(
                claim, ReasonCode.SUBMISSION_NOT_FOUND, "stored proof bytes are missing"
            )

        started_at = datetime.now(UTC)
        try:
            run = await self.runner.run(
                task_dir=task.task_dir,
                proof=proof,
                expected_task_sha256=claim.task_bundle_sha256,
                timeout_seconds=self.settings.container_timeout(task_timeout),
            )
        except RunnerFailure as exc:
            return await self._operator(claim, ReasonCode.INTERNAL_ERROR, str(exc))
        finished_at = datetime.now(UTC)

        if self.settings.production:
            try:
                assert_production_report(
                    run,
                    expected_task_id=claim.task_id,
                    expected_task_sha256=claim.task_bundle_sha256,
                    expected_submission_sha256=claim.proof_digest,
                    expected_nanoda_enabled=task.enable_nanoda,
                )
            except RunnerFailure as exc:
                return await self._operator(claim, ReasonCode.INTERNAL_ERROR, str(exc))

        outcome = self._outcome_of(run.reason_code)
        if outcome is Outcome.OPERATOR:
            return await self._operator(claim, run.reason_code, f"stage={run.stage}")

        # An accept produced without the real Landlock and seccomp isolation says nothing sound
        # about the proof, whatever the report claims — so it is refused unless this deployment
        # has explicitly asked for an insecure sandbox, which WorkerSettings permits only for the
        # in-process runner outside production. The verdict is still recorded with the sandbox mode
        # that actually ran, so nothing downstream can mistake it for a production one.
        if run.accepted and run.sandbox_mode != PRODUCTION_SANDBOX_MODE:
            if not self.settings.allow_insecure_sandbox:
                return await self._operator(
                    claim,
                    ReasonCode.INSECURE_SANDBOX,
                    f"accepted under sandbox_mode={run.sandbox_mode!r}",
                )
            logger.warning(
                "recording an accept produced without production isolation submission=%s "
                "sandbox_mode=%s: VERIFICATION_ALLOW_INSECURE_SANDBOX is set, so this verdict is "
                "evidence that the Lean checks out and nothing more",
                claim.submission_id,
                run.sandbox_mode,
            )
            # `warning` and not `info`, even though it is a configured allowance: an accept that
            # nothing isolated is a verdict whose provenance an operator needs to be able to find
            # later, and finding it should not depend on having grepped the right container's logs.
            get_axiom().warn(
                source="verification-worker",
                event_type="insecure_sandbox_accept",
                submission_id=str(claim.submission_id),
                task_id=claim.task_id,
                sandbox_mode=run.sandbox_mode,
                attempt=claim.attempts,
            )

        async with async_session_scope(self.sessions) as session:
            submission = await queue.lock_owned_for_recording(
                session, claim.submission_id, owner=self.settings.owner
            )
            if submission is None:
                logger.warning(
                    "lost lease before recording submission=%s", claim.submission_id
                )
                # The worse of the two lease losses: the proof has already been compiled, so this
                # is work paid for and thrown away. Reported at the same severity, distinguished
                # by `stage`.
                return self._lease_lost(claim, stage="before_recording")
            verdict = await store.record_verification_result(
                session,
                submission,
                accepted=run.accepted,
                reason_code=run.reason_code,
                stage=run.stage,
                verifier_version=run.verifier_version,
                container_digest=run.container_digest,
                sandbox_mode=run.sandbox_mode,
                checks=run.checks,
                report=run.report_bytes,
                started_at=started_at,
                finished_at=finished_at,
            )
        logger.info(
            "recorded submission=%s accepted=%s reason=%s applied=%s",
            claim.submission_id,
            run.accepted,
            run.reason_code,
            verdict.applied,
        )
        # The event the subnet is actually judged by. `applied` matters as much as `accepted`: a
        # verdict this run computed but did not decide the submission with is a different fact.
        get_axiom().info(
            source="verification-worker",
            event_type="verdict_recorded",
            submission_id=str(claim.submission_id),
            task_id=claim.task_id,
            accepted=run.accepted,
            reason_code=run.reason_code,
            stage=run.stage,
            applied=verdict.applied,
            attempt=claim.attempts,
            sandbox_mode=run.sandbox_mode,
            verifier_version=run.verifier_version,
            container_digest=run.container_digest,
            duration_ms=round((finished_at - started_at).total_seconds() * 1000, 3),
        )
        return Processed(
            submission_id=claim.submission_id,
            outcome=Outcome.VERDICT,
            reason_code=run.reason_code,
            attempts=claim.attempts,
            applied=verdict.applied,
        )

    async def drain(self, *, limit: int | None = None) -> list[Processed]:
        """Work until the queue is empty. For `--once` runs and tests."""
        done: list[Processed] = []
        while limit is None or len(done) < limit:
            processed = await self.process_one()
            if processed is None:
                return done
            done.append(processed)
        return done

    async def run_forever(self, *, stop: asyncio.Event | None = None) -> None:
        """Poll until asked to stop. Polling, because the queue is a row, not a channel."""
        halt = stop or asyncio.Event()
        while not halt.is_set():
            try:
                processed = await self.process_one()
            except asyncio.CancelledError:
                raise
            except Exception:
                # A submission must never be able to end the worker. The lease it holds expires
                # on its own, so the row returns to the queue without our help.
                logger.exception("verification pass failed; continuing")
                # Emitted explicitly rather than left to the logging bridge, because this is the
                # one place a worker swallows a crash and keeps going: an operator has to be able
                # to alert on it, and `event_type == "unexpected_error"` is a sharper predicate
                # than "a log record whose severity happens to be error".
                get_axiom().exception(
                    source="verification-worker",
                    event_type="unexpected_error",
                    owner=self.settings.owner,
                )
                processed = None
            if processed is not None:
                continue
            with_timeout = asyncio.wait_for(
                halt.wait(), self.settings.idle_sleep_seconds
            )
            try:
                await with_timeout
            except TimeoutError:
                pass

    def _outcome_of(self, reason_code: str) -> Outcome:
        try:
            return classify(ReasonCode(reason_code))
        except ValueError:
            # An unknown or unclassified reason code is treated as our failure, never as a
            # rejection: guessing wrong here would charge a miner for a code we do not
            # understand. outcomes.py explains why the safe side is OPERATOR.
            logger.error("unclassified verifier reason_code=%r", reason_code)
            get_axiom().error(
                source="verification-worker",
                event_type="unclassified_reason_code",
                reason_code=reason_code,
            )
            return Outcome.OPERATOR

    def _lease_lost(
        self, claim: queue.ClaimedSubmission, *, stage: str
    ) -> Processed:
        """Report a lease that expired under us and hand the pass back undecided.

        The row is deliberately not touched — the lease now belongs to whoever took it — so this
        event is the only record on our side that the work happened.
        """
        get_axiom().warn(
            source="verification-worker",
            event_type="lease_lost",
            submission_id=str(claim.submission_id),
            task_id=claim.task_id,
            attempt=claim.attempts,
            owner=self.settings.owner,
            stage=stage,
        )
        return Processed(
            submission_id=claim.submission_id,
            outcome=Outcome.OPERATOR,
            reason_code=LEASE_LOST,
            attempts=claim.attempts,
        )

    async def _operator(
        self,
        claim: queue.ClaimedSubmission,
        reason: ReasonCode | str,
        detail: str,
    ) -> Processed:
        """Give the row back. The failure was ours, so no verdict is written."""
        reason_code = str(reason)
        logger.error(
            "verification failed for our own reasons submission=%s reason=%s attempt=%d: %s",
            claim.submission_id,
            reason_code,
            claim.attempts,
            detail,
        )
        get_axiom().error(
            source="verification-worker",
            event_type="verification_operator_failure",
            submission_id=str(claim.submission_id),
            task_id=claim.task_id,
            reason_code=reason_code,
            attempt=claim.attempts,
            detail=detail,
            owner=self.settings.owner,
        )
        async with async_session_scope(self.sessions) as session:
            await queue.release(
                session, claim.submission_id, owner=self.settings.owner
            )
        if claim.attempts >= self.settings.max_attempts:
            logger.error(
                "submission=%s has reached %d attempts and will not be claimed again; "
                "an operator must decide whether it is owed a refund",
                claim.submission_id,
                claim.attempts,
            )
            # `critical`, and the only place in the codebase that uses it. A submission stuck here
            # is a miner who paid, got no verdict, and will not get one without a person looking:
            # it is the one condition that should page rather than accumulate on a dashboard.
            get_axiom().critical(
                source="verification-worker",
                event_type="attempts_exhausted",
                submission_id=str(claim.submission_id),
                task_id=claim.task_id,
                reason_code=reason_code,
                attempts=claim.attempts,
                max_attempts=self.settings.max_attempts,
            )
        return Processed(
            submission_id=claim.submission_id,
            outcome=Outcome.OPERATOR,
            reason_code=reason_code,
            attempts=claim.attempts,
        )


__all__ = ["PRODUCTION_SANDBOX_MODE", "Processed", "VerificationWorker"]
