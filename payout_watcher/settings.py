"""Configuration for the outbound payout-chain watcher.

The identities are imported from the payout command renderer rather than duplicated in environment
variables.  A command generated for one treasury and a watcher following another would be worse
than a startup failure: every real payout would remain pending while unrelated transfers could be
matched.  Changing the payout stake position is therefore a reviewed code change in one place.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Mapping
from dataclasses import dataclass

from conjectures_subnet.db.engine import database_url
from payout_notifier.discord import (
    DEFAULT_NETUID,
    DEFAULT_NETWORK,
    DEFAULT_ORIGIN_HOTKEY,
    DEFAULT_PROXY_FOR,
    LEGACY_ORIGIN_HOTKEY,
)

DEFAULT_BATCH_BLOCKS = 200
MAX_BATCH_BLOCKS = 5_000
DEFAULT_POLL_SECONDS = 12.0


class SettingsError(RuntimeError):
    """The watcher cannot run safely with its current environment."""


def _positive_int(
    environ: Mapping[str, str], key: str, default: int, *, maximum: int
) -> int:
    raw = environ.get(key, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise SettingsError(f"{key} must be an integer, got {raw!r}") from exc
    if value <= 0 or value > maximum:
        raise SettingsError(f"{key} must be greater than zero and at most {maximum}")
    return value


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
class PayoutWatcherSettings:
    database_url: str
    network: str
    archive_network: str
    origin_coldkey: str
    origin_hotkey: str
    netuid: int
    batch_blocks: int
    poll_seconds: float
    watcher_id: str
    # Optional: empty disables the payout-time price (see `watcher.PRICE_FRESHNESS`). Nothing about
    # recording or matching a payout depends on it.
    taostats_api_key: str = ""
    taostats_timeout_seconds: float = 10.0

    @property
    def accepted_origin_hotkeys(self) -> tuple[str, ...]:
        """Follow Arbos and the treasury's historical delegation without losing the cursor.

        The exception is specific to this treasury, network and subnet. Other watcher
        configurations still accept only their configured stake position.
        """
        if (
            self.network == DEFAULT_NETWORK
            and self.origin_coldkey == DEFAULT_PROXY_FOR
            and self.origin_hotkey == DEFAULT_ORIGIN_HOTKEY
            and self.netuid == DEFAULT_NETUID
        ):
            return (DEFAULT_ORIGIN_HOTKEY, LEGACY_ORIGIN_HOTKEY)
        return (self.origin_hotkey,)

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> PayoutWatcherSettings:
        env = os.environ if environ is None else environ
        # DEFAULT_NETWORK is also passed to every generated btcli command.  The archive override
        # changes only where old state is read; it does not change chain identity.
        network = DEFAULT_NETWORK
        return cls(
            database_url=env.get("DATABASE_URL", "").strip() or database_url(),
            network=network,
            archive_network=(
                env.get("BITTENSOR_ARCHIVE_NETWORK", "").strip() or network
            ),
            origin_coldkey=DEFAULT_PROXY_FOR,
            origin_hotkey=DEFAULT_ORIGIN_HOTKEY,
            netuid=DEFAULT_NETUID,
            batch_blocks=_positive_int(
                env,
                "PAYOUT_WATCH_BATCH_BLOCKS",
                DEFAULT_BATCH_BLOCKS,
                maximum=MAX_BATCH_BLOCKS,
            ),
            poll_seconds=_positive_float(
                env,
                "PAYOUT_WATCH_POLL_SECONDS",
                DEFAULT_POLL_SECONDS,
                maximum=3_600,
            ),
            watcher_id=(
                env.get("PAYOUT_WATCHER_ID", "").strip()
                or f"{socket.gethostname()}/{os.getpid()}"
            ),
            taostats_api_key=env.get("TAOSTATS_API_KEY", "").strip(),
            taostats_timeout_seconds=_positive_float(
                env, "PAYOUT_TAOSTATS_TIMEOUT_SECONDS", 10.0, maximum=60
            ),
        )


__all__ = [
    "DEFAULT_BATCH_BLOCKS",
    "DEFAULT_POLL_SECONDS",
    "MAX_BATCH_BLOCKS",
    "PayoutWatcherSettings",
    "SettingsError",
]
