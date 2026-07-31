"""Crossing the trust boundary: how one proof gets verified.

`docs/SUBNET.md` requires that the database never be mounted into the hostile-proof verifier,
and this worker holds the database credentials. So in production it does not import the verifier
at all — it runs the published container and reads a report off its stdout. The interface is the
CLI in `verifier/cli.py`, which prints `VerificationReport.to_dict()` as sorted JSON and exits
with a coded status; `verifier/service_adapter.py` is on the container's side of that line, not
this one.

`docker run` rather than `docker compose run`: the compose profile selects the proof through the
`FC_SUBMISSION_FILE` environment variable, which is process-global, so two workers would
overwrite each other's input. Each job passes its own bind mounts instead, and mounts one task
directory rather than the whole pool — the verifier is entitled to the task it was asked about
and nothing else.

The exit code is deliberately not the verdict. `accepted` and `reason_code` come from the report,
which is the artifact we store and can re-check; the exit code is a lossy projection of it. A
payload that is not a full report at all — the CLI's error shape, or nothing — is a runner
failure, never a rejection.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from verifier.hashing import canonical_json_bytes, is_sha256

# Inside the image: WORKDIR /opt/fc-verifier, USER verifier (10001), ENTRYPOINT python3 -m
# verifier. Mount points are ours to choose; these two are the whole input surface.
CONTAINER_TASK_DIR = "/inputs/task"
CONTAINER_PROOF_PATH = "/inputs/submissions/Main.lean"
CONTAINER_UID_GID = "10001:10001"
CONTAINER_WORK_TMPFS = "/opt/fc-verifier/.work"

# A report we can act on has all of these. Their absence means we are looking at the CLI's
# error shape — `{"accepted": false, "reason_code": ..., "error": ...}` — which is emitted for
# failures outside verify() and describes the runner, not the proof.
REPORT_KEYS = ("accepted", "reason_code", "stage", "checks", "sandbox_mode")

# Docker needs to find its own socket and credentials; nothing else is inherited.
PASSTHROUGH_ENV = (
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "DOCKER_CONFIG",
    "DOCKER_CERT_PATH",
    "DOCKER_TLS_VERIFY",
)


class RunnerFailure(RuntimeError):
    """The verifier did not return a usable report. Never a statement about the proof."""


@dataclass(frozen=True)
class VerifierRun:
    """One completed verifier invocation, and the identity of what ran it."""

    report: Mapping[str, Any]
    # The exact bytes stored in verification_runs.report, so its digest stays recomputable.
    report_bytes: bytes
    container_digest: str  # sha256:<hex>
    verifier_version: str

    @property
    def accepted(self) -> bool:
        return bool(self.report["accepted"])

    @property
    def reason_code(self) -> str:
        return str(self.report["reason_code"])

    @property
    def stage(self) -> str:
        return str(self.report["stage"])

    @property
    def sandbox_mode(self) -> str:
        return str(self.report["sandbox_mode"])

    @property
    def checks(self) -> dict[str, bool]:
        raw = self.report.get("checks") or {}
        return {str(key): bool(value) for key, value in raw.items()}


class VerifierRunner(Protocol):
    async def run(
        self,
        *,
        task_dir: Path,
        proof: bytes,
        expected_task_sha256: str,
        timeout_seconds: int,
    ) -> VerifierRun:
        """Verify one proof, or raise RunnerFailure."""
        ...


def _report_from(payload: bytes) -> dict[str, Any]:
    try:
        report = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunnerFailure(f"verifier produced no JSON report: {exc}") from exc
    if not isinstance(report, dict):
        raise RunnerFailure("verifier report is not a JSON object")
    missing = [key for key in REPORT_KEYS if key not in report]
    if missing:
        raise RunnerFailure(
            "verifier returned an error rather than a report; missing "
            + ", ".join(missing)
            + f": {report.get('error') or report}"
        )
    return report


def _docker_env() -> dict[str, str]:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C", "LC_ALL": "C"}
    home = os.environ.get("HOME")
    if home:
        env["HOME"] = home
    for key in PASSTHROUGH_ENV:
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


def resolve_container_digest(docker_binary: str, image: str) -> str:
    """The immutable identity of the image that will run, asked of the image itself.

    `verification_runs.container_digest` is a NOT NULL sha256 because a report is only
    reproducible if you know what produced it. A tag is not that — `:local` points wherever the
    last build left it — so the config digest is read here and the worker refuses to start if
    the answer is not a digest.
    """
    try:
        result = subprocess.run(
            (docker_binary, "image", "inspect", "--format", "{{.Id}}", image),
            capture_output=True,
            check=False,
            env=_docker_env(),
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RunnerFailure(f"cannot inspect verifier image {image!r}: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise RunnerFailure(f"verifier image {image!r} is not available: {detail}")
    digest = result.stdout.decode("utf-8", "replace").strip()
    if not is_sha256(digest):
        raise RunnerFailure(
            f"verifier image {image!r} reported {digest!r}, which is not a sha256 digest"
        )
    return digest


@dataclass(frozen=True)
class ContainerVerifierRunner:
    """Run one proof in one fresh container, then throw the container away.

    The flags mirror the `verifier` service in `docker-compose.yml`, because that profile is
    what `SECURITY.md` describes and reviewed: no network, read-only root, unprivileged uid, no
    capabilities, no new privileges, bounded pids/memory/cpu, and tmpfs for the two paths that
    must be writable.
    """

    image: str
    container_digest: str
    verifier_version: str
    docker_binary: str = "docker"
    memory: str = "72g"
    cpus: str = "4"
    pids_limit: int = 512

    def argv(self, *, task_dir: Path, proof_path: Path, expected_task_sha256: str, name: str) -> tuple[str, ...]:
        return (
            self.docker_binary,
            "run",
            "--rm",
            # Named so a timeout can kill the container itself. Killing `docker run` only
            # detaches the client and would leave the Lean build burning a core.
            "--name",
            name,
            "--network",
            "none",
            "--read-only",
            "--user",
            CONTAINER_UID_GID,
            "--security-opt",
            "no-new-privileges:true",
            "--cap-drop",
            "ALL",
            "--pids-limit",
            str(self.pids_limit),
            "--memory",
            self.memory,
            "--cpus",
            self.cpus,
            "--ulimit",
            "nofile=1024:1024",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,noexec,size=2g",
            "--tmpfs",
            (f"{CONTAINER_WORK_TMPFS}:rw,nosuid,nodev,noexec,size=2g,"
            f"uid=10001,gid=10001,mode=0700"),
            "--volume",
            f"{task_dir}:{CONTAINER_TASK_DIR}:ro",
            "--volume",
            f"{proof_path}:{CONTAINER_PROOF_PATH}:ro",
            self.image,
            "verify",
            "--task",
            CONTAINER_TASK_DIR,
            "--submission",
            CONTAINER_PROOF_PATH,
            "--expected-task-sha256",
            expected_task_sha256,
        )

    async def run(
        self,
        *,
        task_dir: Path,
        proof: bytes,
        expected_task_sha256: str,
        timeout_seconds: int,
    ) -> VerifierRun:
        if not is_sha256(expected_task_sha256):
            raise RunnerFailure("expected task digest is not a sha256 commitment")
        with tempfile.TemporaryDirectory(prefix="conjectures-verify-") as temporary:
            # The container runs as uid 10001 and must be able to traverse to the mount source,
            # so the directory is world-readable. It holds only bytes the miner already sent us.
            directory = Path(temporary)
            os.chmod(directory, 0o755)
            proof_path = directory / "Main.lean"
            proof_path.write_bytes(proof)
            os.chmod(proof_path, 0o644)

            name = f"conjectures-verify-{directory.name}"
            argv = self.argv(
                task_dir=task_dir,
                proof_path=proof_path,
                expected_task_sha256=expected_task_sha256,
                name=name,
            )
            stdout, stderr = await self._communicate(argv, name, timeout_seconds)

        report = _report_from(stdout)
        if not stdout.strip():  # pragma: no cover - _report_from already raised
            raise RunnerFailure(stderr.decode("utf-8", "replace")[-2000:])
        return VerifierRun(
            report=report,
            report_bytes=canonical_json_bytes(report),
            container_digest=self.container_digest,
            verifier_version=self.verifier_version,
        )

    async def _communicate(
        self, argv: tuple[str, ...], name: str, timeout_seconds: int
    ) -> tuple[bytes, bytes]:
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_docker_env(),
            )
        except OSError as exc:
            raise RunnerFailure(f"cannot start {self.docker_binary!r}: {exc}") from exc
        try:
            return await asyncio.wait_for(process.communicate(), timeout_seconds)
        except TimeoutError as exc:
            # The verifier enforces the task's own deadline internally, so reaching this one
            # means the container itself is stuck. Kill the container, not just our client.
            await self._kill(name)
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
            raise RunnerFailure(
                f"verifier container exceeded its {timeout_seconds}s outer bound"
            ) from exc

    async def _kill(self, name: str) -> None:
        with contextlib.suppress(OSError):
            killer = await asyncio.create_subprocess_exec(
                self.docker_binary,
                "kill",
                name,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=_docker_env(),
            )
            await killer.wait()


@dataclass(frozen=True)
class InProcessVerifierRunner:
    """Run the verifier in this process. Development and tests only.

    Uses the unchanged `ProductionVerifierAdapter` contract and passes none of the CLI's
    development override flags, so a local run exercises the same acceptance path. It puts
    hostile Lean in the same process as the database credentials, which is why
    `WorkerSettings` refuses it in production.
    """

    project_root: Path
    verifier_version: str = "in-process"
    container_digest: str = "sha256:" + "00" * 32

    async def run(
        self,
        *,
        task_dir: Path,
        proof: bytes,
        expected_task_sha256: str,
        timeout_seconds: int,
    ) -> VerifierRun:
        del timeout_seconds  # verify() enforces the manifest's own deadline
        from verifier.service_adapter import ProductionVerifierAdapter

        adapter = ProductionVerifierAdapter(project_root=self.project_root)
        try:
            report = await asyncio.to_thread(
                adapter.verify_bytes,
                task_dir=task_dir,
                submission=proof,
                expected_task_sha256=expected_task_sha256,
            )
        except (OSError, ValueError, TypeError) as exc:
            raise RunnerFailure(f"in-process verifier failed: {exc}") from exc
        payload = report.to_dict()
        return VerifierRun(
            report=payload,
            report_bytes=canonical_json_bytes(payload),
            container_digest=self.container_digest,
            verifier_version=self.verifier_version,
        )


def build_runner(settings: Any) -> VerifierRunner:
    """The runner this deployment configured, with the image identified before any work starts."""
    from verification_worker.settings import CONTAINER_RUNNER, IN_PROCESS_RUNNER

    if settings.runner == CONTAINER_RUNNER:
        digest = settings.container_digest or resolve_container_digest(
            settings.docker_binary, settings.verifier_image
        )
        if not is_sha256(digest):
            raise RunnerFailure(
                "VERIFIER_CONTAINER_DIGEST must be a sha256:<hex> digest"
            )
        return ContainerVerifierRunner(
            image=settings.verifier_image,
            container_digest=digest,
            verifier_version=settings.verifier_version,
            docker_binary=settings.docker_binary,
            memory=settings.memory,
            cpus=settings.cpus,
            pids_limit=settings.pids_limit,
        )
    if settings.runner == IN_PROCESS_RUNNER:
        if settings.production:  # pragma: no cover - WorkerSettings already refuses this
            raise RuntimeError("the in-process runner is not permitted in production")
        return InProcessVerifierRunner(project_root=settings.verifier_project_root)
    raise RuntimeError(f"unknown verification runner: {settings.runner}")


__all__ = [
    "ContainerVerifierRunner",
    "InProcessVerifierRunner",
    "RunnerFailure",
    "VerifierRun",
    "VerifierRunner",
    "build_runner",
    "resolve_container_digest",
]
