from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from typing import Mapping

from conjectures_subnet.db.engine import database_url
from payout_notifier.discord import validate_discord_webhook


class SettingsError(ValueError):
    """The payout notifier cannot start safely with its current environment."""


def _positive_float(
    env: Mapping[str, str], name: str, default: float, maximum: float
) -> float:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise SettingsError(f"{name} must be a number") from exc
    if not 0 < value <= maximum:
        raise SettingsError(f"{name} must be greater than zero and at most {maximum:g}")
    return value


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise SettingsError(f"{name} must be an integer") from exc
    if value <= 0:
        raise SettingsError(f"{name} must be positive")
    return value


@dataclass(frozen=True)
class NotifierSettings:
    database_url: str
    webhook_url: str
    poll_seconds: float
    retry_seconds: float
    lease_seconds: float
    worker_id: str
    taostats_api_key: str
    bounty_netuid: int
    taostats_timeout_seconds: float

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> NotifierSettings:
        env = os.environ if environ is None else environ
        webhook_url = env.get("PAYOUT_DISCORD_WEBHOOK_URL", "").strip()
        if not webhook_url:
            raise SettingsError("PAYOUT_DISCORD_WEBHOOK_URL is required")
        try:
            validate_discord_webhook(webhook_url)
        except ValueError as exc:
            raise SettingsError(str(exc)) from exc
        taostats_api_key = env.get("TAOSTATS_API_KEY", "").strip()
        if not taostats_api_key:
            raise SettingsError(
                "TAOSTATS_API_KEY is required to price formalization-defect awards"
            )
        return cls(
            database_url=env.get("DATABASE_URL", "").strip() or database_url(),
            webhook_url=webhook_url,
            poll_seconds=_positive_float(
                env, "PAYOUT_NOTIFIER_POLL_SECONDS", 5.0, 3600.0
            ),
            retry_seconds=_positive_float(
                env, "PAYOUT_NOTIFIER_RETRY_SECONDS", 30.0, 86400.0
            ),
            lease_seconds=_positive_float(
                env, "PAYOUT_NOTIFIER_LEASE_SECONDS", 60.0, 3600.0
            ),
            worker_id=(
                env.get("PAYOUT_NOTIFIER_ID", "").strip()
                or f"{socket.gethostname()}/{os.getpid()}"
            ),
            taostats_api_key=taostats_api_key,
            bounty_netuid=_positive_int(env, "BOUNTY_NETUID", 66),
            taostats_timeout_seconds=_positive_float(
                env, "PAYOUT_TAOSTATS_TIMEOUT_SECONDS", 10.0, 60.0
            ),
        )


__all__ = ["NotifierSettings", "SettingsError"]
