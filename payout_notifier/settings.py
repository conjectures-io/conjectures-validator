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
    # Empty when no webhook is configured OR when the configured one is malformed; both
    # disable delivery and leave obligation seeding running on its own.  See `from_env`.
    webhook_url: str
    # Why delivery is off, when it is off because of a bad value rather than an absent one.
    # Carried rather than raised so the caller can keep saying it, loudly, on every pass.
    webhook_error: str | None
    poll_seconds: float
    retry_seconds: float
    lease_seconds: float
    worker_id: str
    taostats_api_key: str
    bounty_netuid: int
    taostats_timeout_seconds: float

    @property
    def notifications_enabled(self) -> bool:
        """Whether a Discord webhook is configured.  Obligation seeding runs either way."""
        return bool(self.webhook_url)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> NotifierSettings:
        env = os.environ if environ is None else environ
        # Discord is a convenience, not part of the payout mechanism.  The obligation that
        # actually matters is the `reward_events` row, and the chain watcher reconciles that row
        # whoever signed the call and however they were told to.  Requiring a webhook made an
        # optional notification channel a hard dependency of creating obligations at all: with
        # none configured the worker exited 2 on startup, so no obligation was ever written and
        # nothing was ever paid.
        #
        # Unset means "do not notify" and the worker still seeds obligations.
        #
        # A malformed value used to be a hard error, on the reasoning that a typo in a channel
        # somebody believes is live should fail loudly rather than deliver nothing in silence.
        # The loudness was right; killing the process to achieve it was not, and it reproduced
        # the exact bug the paragraph above describes: `PAYOUT_DISCORD_WEBHOOK_URL` set to a
        # four-character placeholder exited 2 on every start, so no obligation was seeded for
        # five days and three approved rewards were never payable.  A notification channel must
        # not be able to stop obligations being created, however it is broken.
        #
        # So a malformed value now disables delivery exactly as an absent one does, and the
        # reason travels with the settings so the worker can report it on every pass.  Loud, and
        # survivable.
        webhook_url = env.get("PAYOUT_DISCORD_WEBHOOK_URL", "").strip()
        webhook_error: str | None = None
        if webhook_url:
            try:
                validate_discord_webhook(webhook_url)
            except ValueError as exc:
                webhook_error = str(exc)
                webhook_url = ""
        taostats_api_key = env.get("TAOSTATS_API_KEY", "").strip()
        if not taostats_api_key:
            raise SettingsError(
                "TAOSTATS_API_KEY is required to price formalization-defect awards"
            )
        return cls(
            database_url=env.get("DATABASE_URL", "").strip() or database_url(),
            webhook_url=webhook_url,
            webhook_error=webhook_error,
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
