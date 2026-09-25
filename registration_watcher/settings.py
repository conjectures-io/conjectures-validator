"""Configuration for the registration watcher.

The subnet is a code constant, not a variable. Which subnet's registrations buy competition
submissions has to be the subnet the emissions worker pays, and a watcher pointed at another
one would hand out submission slots for registrations nobody paid this subnet for. So it is
fixed here, and `tests/test_registration_watcher.py` pins it to `emissions_worker.NETUID`.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from conjectures_subnet.db.engine import competition_database_url

NETUID: Final = 66
DEFAULT_NETWORK: Final = "finney"
# One block. Registrations are rare and the diff of an unchanged subnet writes nothing, so
# polling faster buys nothing and slower only delays the moment a new miner may submit.
DEFAULT_POLL_SECONDS: Final = 12.0
DEFAULT_HEARTBEAT: Final = Path("/tmp/registration-watcher.heartbeat")


class SettingsError(RuntimeError):
    """The watcher cannot run safely with its current environment."""


def _positive_float(
    environ: Mapping[str, str], key: str, default: float, *, maximum: float
) -> float:
    raw = environ.get(key, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise SettingsError(f"{key} must be a number, got {raw!r}") from exc
    if value <= 0 or value > maximum:
        raise SettingsError(f"{key} must be greater than zero and at most {maximum:g}")
    return value


@dataclass(frozen=True)
class RegistrationWatcherSettings:
    database_url: str
    network: str
    archive_network: str
    netuid: int
    poll_seconds: float
    heartbeat: Path
    watcher_id: str

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> RegistrationWatcherSettings:
        env = os.environ if environ is None else environ
        production = env.get("APP_MODE", "DEV").strip().upper() == "PROD"

        # The competition database, never the proofs one, and never inferred in production:
        # the fallback assembles a URL from POSTGRES_*, and the table this writes decides who
        # may submit. Same rule as competition_worker.
        explicit = env.get("COMPETITION_DATABASE_URL", "").strip()
        if production and not explicit:
            raise SettingsError(
                "COMPETITION_DATABASE_URL is required in production: this process writes the "
                "registrations that decide who may submit, and which database that is is not "
                "something to leave to a default"
            )

        network = env.get("BITTENSOR_NETWORK", "").strip() or DEFAULT_NETWORK
        return cls(
            database_url=explicit or competition_database_url(),
            network=network,
            # Registration heights are usually older than a lite node's pruned-state window, so
            # their timestamps may need an archive endpoint. Following the head does not.
            archive_network=env.get("BITTENSOR_ARCHIVE_NETWORK", "").strip() or network,
            netuid=NETUID,
            poll_seconds=_positive_float(
                env, "REGISTRATION_WATCH_POLL_SECONDS", DEFAULT_POLL_SECONDS, maximum=3_600
            ),
            heartbeat=Path(
                env.get("REGISTRATION_WATCH_HEARTBEAT", "").strip() or DEFAULT_HEARTBEAT
            ),
            watcher_id=(
                env.get("REGISTRATION_WATCHER_ID", "").strip()
                or f"{socket.gethostname()}/{os.getpid()}"
            ),
        )


__all__ = [
    "DEFAULT_HEARTBEAT",
    "DEFAULT_NETWORK",
    "DEFAULT_POLL_SECONDS",
    "NETUID",
    "RegistrationWatcherSettings",
    "SettingsError",
]
