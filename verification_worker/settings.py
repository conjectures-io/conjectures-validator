"""Typed, fail-closed configuration for the verification worker.

Read once at startup, so a misconfigured worker refuses to boot rather than parking submissions
one at a time. Mirrors `submission_api/settings.py` deliberately — same `APP_MODE`, same
`SettingsError`, same production refusals — because the two processes are configured by the same
operator from the same `.env`.

Two refusals matter in production:

* the in-process runner, which would compile hostile Lean inside the process holding the
  database credentials, the exact thing `docs/SUBNET.md` and `SECURITY.md` forbid; and
* an unstated `VERIFIER_VERSION`, because `verification_runs.verifier_version` is the record of
  what decided a submission and a default would make every report claim the same thing.

The worker configures no database of its own; `conjectures_subnet.db.database_url()` resolves
`DATABASE_URL` or the `POSTGRES_*` variables `.env.example` already defines.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEVELOPMENT_MODE = "DEV"
PRODUCTION_MODE = "PROD"
APP_MODES = (DEVELOPMENT_MODE, PRODUCTION_MODE)

CONTAINER_RUNNER = "container"
IN_PROCESS_RUNNER = "in-process"
RUNNERS = (CONTAINER_RUNNER, IN_PROCESS_RUNNER)

DEFAULT_IMAGE = "formal-conjectures-verifier:local"

# Long enough to resolve the task and start a container, short enough that a worker killed
# between claiming and starting does not park the row. The lease is re-stamped from the task's
# own declared timeout before the verifier runs.
DEFAULT_CLAIM_LEASE_SECONDS = 120
# Container startup, image pull, and the Lean toolchain coming up, on top of the deadline the
# verifier enforces for itself.
DEFAULT_CONTAINER_GRACE_SECONDS = 300
# Slack between the outer container bound and the lease, so a container being killed at its
# bound still has time to have its verdict recorded under a live lease.
DEFAULT_LEASE_MARGIN_SECONDS = 300
# Three claims of one submission is enough to establish that something is systematically wrong
# with it. A fourth would not learn anything, and each one costs a full task timeout.
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_IDLE_SLEEP_SECONDS = 5.0

# Matching docker-compose.yml's verifier profile. Comparator asks for 64GiB and the image build
# defaults to sixteen Lean threads, so these are the working figures rather than round numbers.
DEFAULT_MEMORY = "72g"
DEFAULT_CPUS = "4"
DEFAULT_PIDS_LIMIT = 512


class SettingsError(RuntimeError):
    """The process is misconfigured and must not start."""


def _choice(
    environ: Mapping[str, str], key: str, options: tuple[str, ...], default: str
) -> str:
    value = environ.get(key, default).strip()
    if value not in options:
        raise SettingsError(f"{key} must be one of {', '.join(options)}, got {value!r}")
    return value


def _positive_int(environ: Mapping[str, str], key: str, default: int) -> int:
    raw = environ.get(key, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise SettingsError(f"{key} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise SettingsError(f"{key} must be positive, got {value}")
    return value


def _positive_float(environ: Mapping[str, str], key: str, default: float) -> float:
    raw = environ.get(key, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise SettingsError(f"{key} must be a number, got {raw!r}") from exc
    if value <= 0:
        raise SettingsError(f"{key} must be positive, got {value}")
    return value


def _directory(environ: Mapping[str, str], key: str, default: Path) -> Path:
    raw = environ.get(key, "").strip()
    return Path(os.path.abspath(default if not raw else Path(raw)))


def default_owner() -> str:
    """Identifies the process holding a lease, so an operator can go and look at it."""
    return f"{socket.gethostname()}/{os.getpid()}"


@dataclass(frozen=True)
class WorkerSettings:
    app_mode: str
    database_url: str
    runner: str
    owner: str
    verifier_image: str
    verifier_version: str
    container_digest: str  # empty means "ask the image at startup"
    docker_binary: str
    task_allowlist_path: Path
    task_pool_root: Path
    verifier_project_root: Path
    claim_lease_seconds: int
    container_grace_seconds: int
    lease_margin_seconds: int
    max_attempts: int
    idle_sleep_seconds: float
    memory: str
    cpus: str
    pids_limit: int

    @property
    def production(self) -> bool:
        return self.app_mode == PRODUCTION_MODE

    def container_timeout(self, task_timeout_seconds: int) -> int:
        """The outer bound on one container, over the deadline the verifier enforces itself."""
        return task_timeout_seconds + self.container_grace_seconds

    def lease_seconds(self, task_timeout_seconds: int) -> int:
        """How long to hold the row: the container's whole bound, plus room to record."""
        return self.container_timeout(task_timeout_seconds) + self.lease_margin_seconds

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> WorkerSettings:
        env = os.environ if environ is None else environ
        app_mode = _choice(env, "APP_MODE", APP_MODES, DEVELOPMENT_MODE)
        production = app_mode == PRODUCTION_MODE

        runner = _choice(
            env,
            "VERIFICATION_RUNNER",
            RUNNERS,
            CONTAINER_RUNNER if production else IN_PROCESS_RUNNER,
        )
        if production and runner != CONTAINER_RUNNER:
            raise SettingsError(
                "production requires VERIFICATION_RUNNER=container; the worker holds database "
                "credentials and must not compile a miner's proof in its own trust domain"
            )

        verifier_version = env.get("VERIFIER_VERSION", "").strip()
        if production and not verifier_version:
            raise SettingsError(
                "VERIFIER_VERSION is required in production; it is the record of what decided "
                "a submission and must not fall back to a default"
            )

        container_digest = env.get("VERIFIER_CONTAINER_DIGEST", "").strip()

        return cls(
            app_mode=app_mode,
            database_url=env.get("DATABASE_URL", "").strip(),
            runner=runner,
            owner=env.get("VERIFICATION_WORKER_ID", "").strip() or default_owner(),
            verifier_image=env.get("VERIFIER_IMAGE", "").strip() or DEFAULT_IMAGE,
            verifier_version=verifier_version or "development",
            container_digest=container_digest,
            docker_binary=env.get("DOCKER_BINARY", "").strip() or "docker",
            task_allowlist_path=_directory(
                env,
                "TASK_ALLOWLIST_PATH",
                PROJECT_ROOT / "task_pool" / "allowlist.json",
            ),
            task_pool_root=_directory(
                env, "TASK_POOL_ROOT", PROJECT_ROOT / "tasks" / "pool"
            ),
            verifier_project_root=_directory(
                env, "VERIFIER_PROJECT_ROOT", PROJECT_ROOT
            ),
            claim_lease_seconds=_positive_int(
                env, "VERIFICATION_CLAIM_LEASE_SECONDS", DEFAULT_CLAIM_LEASE_SECONDS
            ),
            container_grace_seconds=_positive_int(
                env, "VERIFIER_CONTAINER_GRACE_SECONDS", DEFAULT_CONTAINER_GRACE_SECONDS
            ),
            lease_margin_seconds=_positive_int(
                env, "VERIFICATION_LEASE_MARGIN_SECONDS", DEFAULT_LEASE_MARGIN_SECONDS
            ),
            max_attempts=_positive_int(
                env, "VERIFICATION_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS
            ),
            idle_sleep_seconds=_positive_float(
                env, "VERIFICATION_IDLE_SLEEP_SECONDS", DEFAULT_IDLE_SLEEP_SECONDS
            ),
            memory=env.get("VERIFIER_MEMORY", "").strip() or DEFAULT_MEMORY,
            cpus=env.get("VERIFIER_CPUS", "").strip() or DEFAULT_CPUS,
            pids_limit=_positive_int(env, "VERIFIER_PIDS_LIMIT", DEFAULT_PIDS_LIMIT),
        )


__all__ = [
    "CONTAINER_RUNNER",
    "IN_PROCESS_RUNNER",
    "RUNNERS",
    "SettingsError",
    "WorkerSettings",
    "default_owner",
]
