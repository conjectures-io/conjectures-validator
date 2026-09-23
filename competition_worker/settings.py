"""Typed, fail-closed configuration for the gate worker.

Read once at startup, so a misconfigured host refuses to boot instead of failing on the
first claimed submission -- which on this side means failing forty-five minutes in, having
already taken a submission out of the queue.

Production refuses two things. An unsandboxed gate, because `VERIFY_SANDBOX=off` runs
miner-authored Rust with the worker's own privileges and exists for miners testing locally.
And an implicit database URL, for the same reason `verification_worker` does: the fallback
assembles one from `POSTGRES_*`, and which database to record verdicts in is not a guess
worth shipping.
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Final

PRODUCTION_SANDBOX = "bwrap"

DEFAULT_REGISTRY: Final = Path("deploy/competitions/registry.json")
# Longer than the gate's own timeout, or a slow but live verification gets stolen from
# under a worker that is still running it.
DEFAULT_STALE_CLAIM_SECONDS: Final = 7200.0
# How many times one submission may be requeued by a validator-side error before it is
# marked `error` and left alone. Small: if three separate claims all broke the gate, the
# next thousand will too, and the queue behind it deserves to move.
DEFAULT_MAX_ATTEMPTS: Final = 3


class SettingsError(RuntimeError):
    """Refuse to start rather than run misconfigured."""


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    registry_path: Path
    worker_id: str
    competitions: tuple[str, ...]
    stale_claim_seconds: float
    max_attempts: int
    idle_seconds: float
    sweep_every: int
    production: bool

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> Settings:
        env = dict(os.environ if environ is None else environ)
        production = env.get("APP_MODE", "DEV").strip().upper() == "PROD"

        database_url = env.get("COMPETITION_DATABASE_URL", "").strip()
        if production and not database_url:
            raise SettingsError(
                "COMPETITION_DATABASE_URL is required in production: the fallback assembles "
                "one from POSTGRES_*, and which database to record verdicts in is not "
                "something to leave to a default"
            )

        sandbox = env.get("VERIFY_SANDBOX", PRODUCTION_SANDBOX).strip() or PRODUCTION_SANDBOX
        if production and sandbox != PRODUCTION_SANDBOX:
            raise SettingsError(
                f"VERIFY_SANDBOX must be {PRODUCTION_SANDBOX!r} in production, got "
                f"{sandbox!r}: the gate compiles and runs miner-authored Rust, and 'off' "
                "exists for miners testing their own submissions locally"
            )

        listed = [
            slug.strip()
            for slug in env.get("COMPETITION_WORKER_COMPETITIONS", "").split(",")
            if slug.strip()
        ]
        return cls(
            database_url=database_url,
            registry_path=Path(
                env.get("COMPETITION_REGISTRY_PATH", "").strip() or DEFAULT_REGISTRY
            ),
            # Identifies the claim holder in `submissions.worker_id`, so a stale claim can be
            # traced to the machine that abandoned it.
            worker_id=(
                env.get("COMPETITION_WORKER_ID", "").strip()
                or f"{socket.gethostname()}:{os.getpid()}"
            ),
            # Empty means every gate in the registry. A host that holds one competition's
            # toolchain names it, so it cannot claim work it has no gate for.
            competitions=tuple(listed),
            stale_claim_seconds=float(
                env.get("COMPETITION_STALE_CLAIM_SECONDS", DEFAULT_STALE_CLAIM_SECONDS)
            ),
            max_attempts=int(
                env.get("COMPETITION_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS)
            ),
            idle_seconds=float(env.get("COMPETITION_IDLE_SECONDS", 2.0)),
            sweep_every=int(env.get("COMPETITION_SWEEP_EVERY", 30)),
            production=production,
        )
