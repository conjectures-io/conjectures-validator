"""Handoff from the API into the isolated Lean verifier.

There is no queue table. A submission is queued by virtue of
`verification_status = 'UNVERIFIED'`, which the partial index
`submissions_verification_queue_idx` covers; a worker claims work with
`SELECT ... FOR UPDATE SKIP LOCKED` over that index. So the default dispatcher does nothing at
intake — the row is the queue — and that is the correct production behaviour, because
docs/SUBNET.md and SECURITY.md require that the network-facing API, the database and payment
credentials never share a security context with hostile proof compilation.

The in-process dispatcher exists for local development and tests, and `Settings` refuses it in
production.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from conjectures_subnet.db import digests
from conjectures_subnet.db import submissions as store
from conjectures_subnet.db.models import Submission
from submission_api.settings import IN_PROCESS_DISPATCH, QUEUE_DISPATCH, Settings
from verifier.hashing import canonical_json_bytes
from verifier.service_adapter import ProductionVerifierAdapter


class VerificationDispatcher(Protocol):
    async def dispatch(
        self, session: AsyncSession, submission: Submission, task_dir: Path
    ) -> None:
        """Arrange for the submission's proof to be verified."""


@dataclass(frozen=True)
class QueueDispatcher:
    """Leave the submission UNVERIFIED for a worker to claim. The production path."""

    async def dispatch(
        self, session: AsyncSession, submission: Submission, task_dir: Path
    ) -> None:
        return None


@dataclass(frozen=True)
class InProcessDispatcher:
    """Run the verifier inline and record the run. Development and tests only.

    Uses the unchanged `ProductionVerifierAdapter` contract and never passes the CLI
    development override flags, so a local run exercises the same acceptance path.
    """

    project_root: Path
    verifier_version: str = "in-process"
    container_digest: str = "sha256:" + "00" * 32

    async def dispatch(
        self, session: AsyncSession, submission: Submission, task_dir: Path
    ) -> None:

        proof = await store.proof_bytes(session, submission.proof_digest)
        if proof is None:  # pragma: no cover - the proof was just written
            return
        adapter = ProductionVerifierAdapter(project_root=self.project_root)
        started = datetime.now(UTC)
        try:
            report = await run_in_threadpool(
                lambda: adapter.verify_bytes(
                    task_dir=task_dir,
                    submission=proof,
                    expected_task_sha256=_prefixed(submission.task_bundle_sha256),
                )
            )
        except Exception:  # noqa: BLE001 - a retryable infrastructure failure, not a verdict
            # Leave the submission UNVERIFIED so it stays on the queue.
            return
        await store.record_verification_result(
            session,
            submission,
            accepted=report.accepted,
            reason_code=report.reason_code.value,
            stage=report.stage,
            verifier_version=self.verifier_version,
            container_digest=self.container_digest,
            sandbox_mode=report.sandbox_mode,
            checks=dict(report.checks),
            report=canonical_json_bytes(report.to_dict()),
            started_at=started,
            finished_at=datetime.now(UTC),
        )
        return


def _prefixed(raw: bytes | memoryview) -> str:
    return digests.to_prefixed(raw)


def build_dispatcher(settings: Settings) -> VerificationDispatcher:
    if settings.dispatcher == QUEUE_DISPATCH:
        return QueueDispatcher()
    if settings.dispatcher == IN_PROCESS_DISPATCH:
        if settings.production:  # pragma: no cover - Settings already refuses this
            raise RuntimeError(
                "the in-process dispatcher is not permitted in production"
            )
        return InProcessDispatcher(project_root=settings.verifier_project_root)
    raise RuntimeError(f"unknown dispatcher: {settings.dispatcher}")
