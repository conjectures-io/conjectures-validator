"""Durable one-shot verification worker.

The API only inserts an UNVERIFIED row. This process leases that row, commits
the lease, runs the proof in a fresh networkless read-only container, and then
atomically inserts the immutable report and terminal verdict. Infrastructure
failures release the lease with backoff and never become mathematical verdicts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conjectures_subnet.db import (
    async_session_factory,
    create_async_db_engine,
    database_url,
    digests,
)
from conjectures_subnet.db import submissions as store
from conjectures_subnet.db.models import Submission
from submission_api.taskpool import TaskCatalog
from verifier.errors import CONFIGURATION_REASONS, ReasonCode
from verifier.hashing import canonical_json_bytes, sha256_bytes
from verifier.task_registry import TaskNotAllowed


IMAGE_BY_DIGEST = re.compile(r"^.+@sha256:(?P<digest>[0-9a-f]{64})$")
MAX_REPORT_BYTES = 1_000_000
LOG = logging.getLogger(__name__)


class VerificationInfrastructureError(RuntimeError):
    """The attempt should be retried; it is not a proof rejection."""


@dataclass(frozen=True)
class IsolatedResult:
    accepted: bool
    reason_code: str
    stage: str
    sandbox_mode: str
    checks: dict[str, bool]
    report: bytes
    started_at: datetime
    finished_at: datetime


class DockerVerifierRunner:
    """Run exactly one proof in an immutable production verifier image."""

    def __init__(
        self,
        *,
        image: str,
        docker_binary: str = "docker",
        timeout_seconds: int = 900,
        memory: str = "72g",
        cpus: str = "4",
    ):
        match = IMAGE_BY_DIGEST.fullmatch(image)
        if match is None:
            raise ValueError("verifier image must be pinned as name@sha256:<64 lowercase hex>")
        self.image = image
        self.image_digest = "sha256:" + match.group("digest")
        self.docker_binary = docker_binary
        self.timeout_seconds = timeout_seconds
        self.memory = memory
        self.cpus = cpus

    async def verify(
        self,
        *,
        submission: Submission,
        proof: bytes,
        task_dir: Path,
    ) -> IsolatedResult:
        started = datetime.now(timezone.utc)
        container_name = f"conjectures-{submission.id}-{uuid.uuid4().hex[:12]}"
        with tempfile.TemporaryDirectory(prefix="conjectures-worker-") as temporary:
            proof_path = Path(temporary) / "Main.lean"
            descriptor = os.open(
                proof_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
                0o444,
            )
            try:
                remaining = memoryview(proof)
                while remaining:
                    written = os.write(descriptor, remaining)
                    if written <= 0:
                        raise OSError("short write while materializing proof")
                    remaining = remaining[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

            command = (
                self.docker_binary,
                "run",
                "--rm",
                f"--name={container_name}",
                "--network=none",
                "--read-only",
                "--user=10001:10001",
                "--security-opt=no-new-privileges:true",
                "--cap-drop=ALL",
                "--pids-limit=512",
                f"--memory={self.memory}",
                f"--cpus={self.cpus}",
                "--ulimit=nofile=1024:1024",
                "--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=2g",
                "--tmpfs=/opt/fc-verifier/.work:rw,nosuid,nodev,noexec,size=2g,uid=10001,gid=10001,mode=0700",
                "--mount",
                f"type=bind,src={task_dir.resolve()},dst=/inputs/task,readonly",
                "--mount",
                f"type=bind,src={proof_path.resolve()},dst=/inputs/Main.lean,readonly",
                self.image,
                "verify",
                "--task",
                "/inputs/task",
                "--submission",
                "/inputs/Main.lean",
                "--expected-task-sha256",
                digests.to_prefixed(submission.task_bundle_sha256),
            )
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except OSError as exc:
                raise VerificationInfrastructureError(
                    "cannot start the verifier container runtime"
                ) from exc
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=self.timeout_seconds
                )
            except TimeoutError as exc:
                process.kill()
                await process.wait()
                await self._remove_container(container_name)
                raise VerificationInfrastructureError("verifier container timed out") from exc

        finished = datetime.now(timezone.utc)
        if len(stdout) > MAX_REPORT_BYTES:
            raise VerificationInfrastructureError("verifier report exceeded its byte limit")
        try:
            payload: Any = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            tail = stderr[-1000:].decode("utf-8", errors="replace")
            raise VerificationInfrastructureError(
                f"verifier did not emit a JSON report: {tail}"
            ) from exc
        if not isinstance(payload, dict):
            raise VerificationInfrastructureError("verifier report is not a JSON object")

        reason_raw = payload.get("reason_code")
        try:
            reason = ReasonCode(reason_raw)
        except (TypeError, ValueError) as exc:
            raise VerificationInfrastructureError("verifier report has an unknown reason") from exc
        if reason in CONFIGURATION_REASONS or process.returncode == 2:
            raise VerificationInfrastructureError(
                f"verifier infrastructure/configuration failure: {reason.value}"
            )
        accepted = payload.get("accepted")
        checks = payload.get("checks")
        if not isinstance(accepted, bool) or not isinstance(checks, dict) or not all(
            isinstance(key, str) and isinstance(value, bool) for key, value in checks.items()
        ):
            raise VerificationInfrastructureError("verifier report has malformed verdict fields")
        expected = {
            "task_id": submission.task_id,
            "problem_id": submission.problem_id,
            "task_mode": submission.task_mode,
            "task_bundle_sha256": digests.to_prefixed(submission.task_bundle_sha256),
            "submission_sha256": sha256_bytes(proof),
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            raise VerificationInfrastructureError("verifier report does not match leased submission")
        sandbox_mode = payload.get("sandbox_mode")
        if sandbox_mode != "landrun+seccomp":
            raise VerificationInfrastructureError("verifier did not use the production sandbox")
        if accepted and not checks.get("production_sandbox", False):
            raise VerificationInfrastructureError("accepted report did not pass the sandbox gate")
        if process.returncode not in (0, 1):
            raise VerificationInfrastructureError(
                f"verifier exited unexpectedly with {process.returncode}"
            )
        if accepted != (process.returncode == 0) or accepted != (reason is ReasonCode.VERIFIED):
            raise VerificationInfrastructureError("verifier exit status and report verdict disagree")
        return IsolatedResult(
            accepted=accepted,
            reason_code=reason.value,
            stage=str(payload.get("stage", "UNKNOWN")),
            sandbox_mode=sandbox_mode,
            checks=dict(checks),
            report=canonical_json_bytes(payload),
            started_at=started,
            finished_at=finished,
        )

    async def _remove_container(self, container_name: str) -> None:
        """Best-effort cleanup when killing the Docker client after a timeout."""
        try:
            cleanup = await asyncio.create_subprocess_exec(
                self.docker_binary,
                "rm",
                "--force",
                container_name,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(cleanup.wait(), timeout=30)
        except (OSError, TimeoutError):
            LOG.exception("could not clean up timed-out verifier container %s", container_name)


class VerificationWorker:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        catalog: TaskCatalog,
        runner: DockerVerifierRunner,
        worker_id: str,
        lease_seconds: int = 1200,
        retry_seconds: int = 30,
    ):
        if not worker_id or len(worker_id) > 255 or "\x00" in worker_id:
            raise ValueError("worker id must contain 1 to 255 non-NUL characters")
        if lease_seconds <= 0 or retry_seconds <= 0:
            raise ValueError("lease and retry durations must be positive")
        if lease_seconds < runner.timeout_seconds + 60:
            raise ValueError(
                "verification lease must outlive the container timeout by at least 60 seconds"
            )
        self.sessions = sessions
        self.catalog = catalog
        self.runner = runner
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.retry_seconds = retry_seconds

    async def run_once(self) -> bool:
        async with self.sessions() as session:
            lease = await store.claim_verification(
                session, worker_id=self.worker_id, lease_seconds=self.lease_seconds
            )
            await session.commit()
        if lease is None:
            return False

        submission = lease.submission
        attempt_started = datetime.now(timezone.utc)
        try:
            try:
                entry = self.catalog.resolve(
                    submission.task_id, digests.to_prefixed(submission.task_bundle_sha256)
                )
            except TaskNotAllowed as exc:
                raise VerificationInfrastructureError(
                    "leased task is no longer in the audited catalog"
                ) from exc
            if entry.problem_id != submission.problem_id or entry.mode != submission.task_mode:
                raise VerificationInfrastructureError("task catalog metadata changed after intake")
            result = await self.runner.verify(
                submission=submission,
                proof=lease.proof_content,
                task_dir=entry.task_dir,
            )
            async with self.sessions() as session:
                locked = (
                    await session.execute(
                        select(Submission)
                        .where(Submission.id == submission.id)
                        .with_for_update()
                    )
                ).scalar_one()
                await store.record_verification_result(
                    session,
                    locked,
                    accepted=result.accepted,
                    reason_code=result.reason_code,
                    stage=result.stage,
                    verifier_version="container-v1",
                    container_digest=self.runner.image_digest,
                    sandbox_mode=result.sandbox_mode,
                    checks=result.checks,
                    report=result.report,
                    started_at=result.started_at,
                    finished_at=result.finished_at,
                    actor=self.worker_id,
                    lease_owner=self.worker_id,
                )
                await session.commit()
        except VerificationInfrastructureError:
            LOG.exception("verification infrastructure failure for %s", submission.id)
            async with self.sessions() as session:
                await store.record_verification_infrastructure_failure(
                    session,
                    submission.id,
                    worker_id=self.worker_id,
                    verifier_version="container-v1",
                    container_digest=self.runner.image_digest,
                    retry_after_seconds=min(
                        self.retry_seconds * (2 ** max(0, submission.verification_attempts - 1)),
                        3600,
                    ),
                    started_at=attempt_started,
                    finished_at=datetime.now(timezone.utc),
                )
                await session.commit()
        return True


async def _serve(args: argparse.Namespace) -> None:
    engine = create_async_db_engine(args.database_url)
    sessions = async_session_factory(engine)
    catalog = TaskCatalog.load(
        allowlist_path=args.allowlist,
        pool_root=args.task_pool,
    )
    runner = DockerVerifierRunner(
        image=args.image,
        docker_binary=args.docker_binary,
        timeout_seconds=args.timeout_seconds,
    )
    worker = VerificationWorker(
        sessions=sessions,
        catalog=catalog,
        runner=runner,
        worker_id=args.worker_id,
        lease_seconds=args.lease_seconds,
    )
    try:
        while True:
            worked = await worker.run_once()
            if not worked:
                await asyncio.sleep(args.poll_seconds)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="run the isolated proof verification worker")
    parser.add_argument("--database-url", default=database_url())
    parser.add_argument("--allowlist", type=Path, default=Path("task_pool/allowlist.json"))
    parser.add_argument("--task-pool", type=Path, default=Path("tasks/pool"))
    parser.add_argument("--image", required=True, help="immutable image name@sha256:digest")
    parser.add_argument("--docker-binary", default="docker")
    parser.add_argument("--worker-id", default=f"verifier-{uuid.uuid4()}")
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--lease-seconds", type=int, default=1200)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_serve(parser.parse_args()))
