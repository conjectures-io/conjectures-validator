"""Typed, fail-closed configuration for the deposit watcher.

Read once at startup, so a misconfigured watcher refuses to boot rather than crediting the wrong
money once per block. Mirrors `verification_worker/settings.py` deliberately — same `APP_MODE`,
same `SettingsError`, same production refusals — because the same operator configures both from
the same `.env`.

Four values decide which money this validator considers its own, and every one of them is
required with no default at all:

* `DEPOSIT_WATCH_RECIPIENT_SS58` — the address transfers must arrive at;
* `DEPOSIT_WATCH_NETUID` and `DEPOSIT_WATCH_UID` — where that address is registered, checked
  against the chain at startup so a typo cannot make the watcher follow a stranger's balance;
* `DEPOSIT_WATCH_FROM` — the genesis timestamp, before which transfers are not this validator's
  business.

A default for any of them would be a default about money. `CREDIT_PRICE_RAO` does have one,
0.5 TAO, because `submission_api/settings.py` already ships that same figure as
`DEFAULT_SUBMISSION_PRICE_RAO` and the two must agree — an API quoting one price while the watcher
credits at another is the one inconsistency that would show up as a wrong balance.

The watcher configures no database of its own; `conjectures_subnet.db.database_url()` resolves
`DATABASE_URL` or the `POSTGRES_*` variables `.env.example` already defines.
"""

from __future__ import annotations

import datetime as dt
import os
import socket
from collections.abc import Mapping
from dataclasses import dataclass

from submission_api.settings import RAO_PER_TAO
from verifier.bundle import SS58_ADDRESS

DEVELOPMENT_MODE = "DEV"
PRODUCTION_MODE = "PROD"
APP_MODES = (DEVELOPMENT_MODE, PRODUCTION_MODE)

# One credit is one submission. 0.5 TAO, the same figure submission_api ships as
# DEFAULT_SUBMISSION_PRICE_RAO — see the module docstring on why they must agree.
DEFAULT_CREDIT_PRICE_RAO = RAO_PER_TAO // 2

# `finney` is mainnet. Named rather than defaulted to `local`, because a watcher pointed at a
# development chain silently credits nothing on a production deployment.
DEFAULT_NETWORK = "finney"

# How many blocks one pass reads before writing the cursor. Bounded so a watcher starting from a
# months-old genesis timestamp makes visible, resumable progress instead of one enormous
# transaction that a restart discards entirely.
DEFAULT_BATCH_BLOCKS = 200
MAX_BATCH_BLOCKS = 5_000

# Roughly one Subtensor block. Polling, because there is no push subscription for "events at an
# address" and the finalized head advances at this rate anyway.
DEFAULT_POLL_SECONDS = 12.0

# How long a deposit row the watcher creates for an undeclared arrival stays open. It is created
# already credited, so this only fills a NOT NULL column — but a value in the past would make an
# already-settled row look expired to anything reading the deposits table.
DEFAULT_ADOPTED_DEPOSIT_HOURS = 24


class SettingsError(RuntimeError):
    """The process is misconfigured and must not start."""


def _require(environ: Mapping[str, str], key: str) -> str:
    value = environ.get(key, "").strip()
    if not value:
        raise SettingsError(f"{key} is required")
    return value


def _choice(
    environ: Mapping[str, str], key: str, options: tuple[str, ...], default: str
) -> str:
    value = environ.get(key, "").strip() or default
    if value not in options:
        raise SettingsError(f"{key} must be one of {', '.join(options)}, got {value!r}")
    return value


def _positive_int(
    environ: Mapping[str, str], key: str, default: int, maximum: int | None = None
) -> int:
    raw = environ.get(key, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise SettingsError(f"{key} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise SettingsError(f"{key} must be positive, got {value}")
    if maximum is not None and value > maximum:
        raise SettingsError(f"{key} must not exceed {maximum}, got {value}")
    return value


def _nonnegative_int(environ: Mapping[str, str], key: str) -> int:
    raw = _require(environ, key)
    try:
        value = int(raw)
    except ValueError as exc:
        raise SettingsError(f"{key} must be an integer, got {raw!r}") from exc
    if value < 0:
        raise SettingsError(f"{key} must not be negative, got {value}")
    return value


def _positive_float(
    environ: Mapping[str, str], key: str, default: float, maximum: float
) -> float:
    raw = environ.get(key, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise SettingsError(f"{key} must be a number, got {raw!r}") from exc
    if value <= 0:
        raise SettingsError(f"{key} must be positive, got {value}")
    if value > maximum:
        raise SettingsError(f"{key} must not exceed {maximum}, got {value}")
    return value


def _flag(environ: Mapping[str, str], key: str, default: bool = False) -> bool:
    raw = environ.get(key, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise SettingsError(f"{key} must be a boolean, got {raw!r}")


def _address(environ: Mapping[str, str], key: str) -> str:
    value = _require(environ, key)
    if SS58_ADDRESS.fullmatch(value) is None:
        raise SettingsError(f"{key} is not a valid SS58 address")
    return value


def _timestamp(environ: Mapping[str, str], key: str) -> dt.datetime:
    """The genesis timestamp, as an ISO-8601 instant.

    A timezone is mandatory. `2026-08-01T00:00:00` is not an instant — it is that wall clock in
    whichever zone the reader assumes, and the two readers here are this process and a Subtensor
    block header. Requiring the offset is what stops a deployment from starting to credit hours
    early or late depending on where its container thinks it is.
    """
    raw = _require(environ, key)
    try:
        value = dt.datetime.fromisoformat(raw)
    except ValueError as exc:
        raise SettingsError(
            f"{key} must be an ISO-8601 timestamp, e.g. 2026-08-01T00:00:00Z, got {raw!r}"
        ) from exc
    if value.tzinfo is None:
        raise SettingsError(
            f"{key} must carry a timezone offset, e.g. 2026-08-01T00:00:00Z; a bare local "
            "time is not an instant and would shift the start of crediting"
        )
    return value.astimezone(dt.UTC)


def default_watcher_id() -> str:
    """Identifies the process in the log, so an operator can go and look at it."""
    return f"{socket.gethostname()}/{os.getpid()}"


@dataclass(frozen=True)
class WatcherSettings:
    app_mode: str
    database_url: str
    network: str
    # Bisecting to the genesis block reads state far outside a lite node's pruned window, so that
    # one search may need an archive endpoint. Following the head does not.
    archive_network: str
    recipient: str
    netuid: int
    uid: int
    watch_from: dt.datetime
    credit_price_rao: int
    batch_blocks: int
    poll_seconds: float
    adopted_deposit_hours: int
    verify_registration: bool
    watcher_id: str

    @property
    def production(self) -> bool:
        return self.app_mode == PRODUCTION_MODE

    @property
    def adopted_deposit_window(self) -> dt.timedelta:
        return dt.timedelta(hours=self.adopted_deposit_hours)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> WatcherSettings:
        env = os.environ if environ is None else environ
        app_mode = _choice(env, "APP_MODE", APP_MODES, DEVELOPMENT_MODE)
        production = app_mode == PRODUCTION_MODE

        network = env.get("BITTENSOR_NETWORK", "").strip() or DEFAULT_NETWORK

        # The startup check that the configured address really is the hotkey registered at the
        # configured (netuid, uid). Skippable only outside production, and only because a local
        # Subtensor has no subnet 66 to answer about — on a real chain a mistyped address is a
        # watcher following somebody else's balance and crediting nothing, forever, quietly.
        verify_registration = _flag(env, "DEPOSIT_WATCH_VERIFY_REGISTRATION", True)
        if production and not verify_registration:
            raise SettingsError(
                "DEPOSIT_WATCH_VERIFY_REGISTRATION must not be false in production; it is the "
                "only check that the address being watched is this validator's own"
            )

        return cls(
            app_mode=app_mode,
            database_url=env.get("DATABASE_URL", "").strip(),
            network=network,
            archive_network=env.get("BITTENSOR_ARCHIVE_NETWORK", "").strip() or network,
            recipient=_address(env, "DEPOSIT_WATCH_RECIPIENT_SS58"),
            netuid=_nonnegative_int(env, "DEPOSIT_WATCH_NETUID"),
            uid=_nonnegative_int(env, "DEPOSIT_WATCH_UID"),
            watch_from=_timestamp(env, "DEPOSIT_WATCH_FROM"),
            credit_price_rao=_positive_int(
                env, "CREDIT_PRICE_RAO", DEFAULT_CREDIT_PRICE_RAO
            ),
            batch_blocks=_positive_int(
                env,
                "DEPOSIT_WATCH_BATCH_BLOCKS",
                DEFAULT_BATCH_BLOCKS,
                maximum=MAX_BATCH_BLOCKS,
            ),
            poll_seconds=_positive_float(
                env, "DEPOSIT_WATCH_POLL_SECONDS", DEFAULT_POLL_SECONDS, maximum=3600.0
            ),
            adopted_deposit_hours=_positive_int(
                env,
                "DEPOSIT_WATCH_ADOPTED_DEPOSIT_HOURS",
                DEFAULT_ADOPTED_DEPOSIT_HOURS,
                maximum=8_760,
            ),
            verify_registration=verify_registration,
            watcher_id=env.get("DEPOSIT_WATCHER_ID", "").strip() or default_watcher_id(),
        )


__all__ = [
    "APP_MODES",
    "DEFAULT_BATCH_BLOCKS",
    "DEFAULT_CREDIT_PRICE_RAO",
    "DEFAULT_NETWORK",
    "DEFAULT_POLL_SECONDS",
    "MAX_BATCH_BLOCKS",
    "SettingsError",
    "WatcherSettings",
    "default_watcher_id",
]
