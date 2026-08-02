"""Typed, fail-closed configuration read once from the environment.

Every value is validated at startup rather than at first use, so a misconfigured deployment
refuses to boot instead of failing on the first miner request.

Three refusals are deliberate guardrails rather than conveniences: production will not start
with the development authenticator, the development payment verifier, or the in-process
verification dispatcher. Each would otherwise silently weaken the boundary that
`docs/SUBNET.md` and `SECURITY.md` describe — unauthenticated miners, unpaid submissions, or
hostile Lean compiled inside the API's own trust domain.

The API configures no database of its own; `conjectures_subnet.db.database_url()` resolves
`DATABASE_URL` or the `POSTGRES_*` variables that `.env.example` already defines.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from verifier.bundle import MAX_BUNDLE_BYTES, SS58_ADDRESS


PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEVELOPMENT_MODE = "DEV"
PRODUCTION_MODE = "PROD"
APP_MODES = (DEVELOPMENT_MODE, PRODUCTION_MODE)

HOTKEY_SIGNATURE_AUTH = "hotkey-signature"
DEVELOPMENT_AUTH = "development-static-key"
AUTHENTICATORS = (HOTKEY_SIGNATURE_AUTH, DEVELOPMENT_AUTH)

CHAIN_PAYMENTS = "chain"
DEVELOPMENT_PAYMENTS = "development"
PAYMENT_VERIFIERS = (CHAIN_PAYMENTS, DEVELOPMENT_PAYMENTS)

QUEUE_DISPATCH = "queue"
IN_PROCESS_DISPATCH = "in-process"
DISPATCHERS = (QUEUE_DISPATCH, IN_PROCESS_DISPATCH)

# TAO carries nine decimal places, so its integer base unit is the rao. Payment accounting is
# integer-only; see docs/SUBNET.md on never using floating point for amounts.
RAO_PER_TAO = 1_000_000_000
DEFAULT_SUBMISSION_PRICE_RAO = RAO_PER_TAO // 2

POLICY_VERSION = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}$")
NETWORK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+.:/_-]{0,254}$")


class SettingsError(RuntimeError):
    """The process is misconfigured and must not start."""


def _require(environ: Mapping[str, str], key: str) -> str:
    value = environ.get(key, "").strip()
    if not value:
        raise SettingsError(f"{key} is required")
    return value


def _choice(environ: Mapping[str, str], key: str, options: tuple[str, ...], default: str) -> str:
    value = environ.get(key, default).strip()
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


def _flag(environ: Mapping[str, str], key: str, default: bool) -> bool:
    raw = environ.get(key, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise SettingsError(f"{key} must be a boolean, got {raw!r}")


def _directory(environ: Mapping[str, str], key: str, default: Path) -> Path:
    raw = environ.get(key, "").strip()
    return Path(os.path.abspath(default if not raw else Path(raw)))


def _address(environ: Mapping[str, str], key: str, value: str) -> str:
    if SS58_ADDRESS.fullmatch(value) is None:
        raise SettingsError(f"{key} is not a valid SS58 address")
    return value


def _csv(environ: Mapping[str, str], key: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in environ.get(key, "").split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    app_mode: str
    # Empty means "whatever conjectures_subnet.db resolves". The API does not own the
    # database; it reuses the validator's shared store.
    database_url: str
    task_allowlist_path: Path
    task_pool_root: Path
    verifier_project_root: Path
    payment_recipient: str
    payment_amount_rao: int
    subtensor_network: str
    authenticator: str
    payment_verifier: str
    dispatcher: str
    development_hotkeys: tuple[str, ...]
    development_coldkey: str
    development_payment_references: tuple[str, ...]
    nonce_window_seconds: int
    max_bundle_bytes: int
    manual_review_enabled: bool
    review_policy_version: str

    @property
    def production(self) -> bool:
        return self.app_mode == PRODUCTION_MODE

    @property
    def expose_docs(self) -> bool:
        return not self.production

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if environ is None else environ
        app_mode = _choice(env, "APP_MODE", APP_MODES, DEVELOPMENT_MODE)
        production = app_mode == PRODUCTION_MODE

        authenticator = _choice(
            env,
            "SUBMISSION_AUTHENTICATOR",
            AUTHENTICATORS,
            HOTKEY_SIGNATURE_AUTH if production else DEVELOPMENT_AUTH,
        )
        payment_verifier = _choice(
            env,
            "SUBMISSION_PAYMENT_VERIFIER",
            PAYMENT_VERIFIERS,
            CHAIN_PAYMENTS if production else DEVELOPMENT_PAYMENTS,
        )
        dispatcher = _choice(env, "SUBMISSION_DISPATCHER", DISPATCHERS, QUEUE_DISPATCH)
        if production and authenticator != HOTKEY_SIGNATURE_AUTH:
            raise SettingsError("production requires SUBMISSION_AUTHENTICATOR=hotkey-signature")
        if production and payment_verifier != CHAIN_PAYMENTS:
            raise SettingsError("production requires SUBMISSION_PAYMENT_VERIFIER=chain")
        if production and dispatcher != QUEUE_DISPATCH:
            raise SettingsError(
                "production requires SUBMISSION_DISPATCHER=queue; the API process must not "
                "share a trust domain with the proof verifier"
            )

        recipient = _address(
            env, "PAYMENT_RECIPIENT_SS58", _require(env, "PAYMENT_RECIPIENT_SS58")
        )

        review_policy_version = env.get("REVIEW_POLICY_VERSION", "v1").strip()
        if POLICY_VERSION.fullmatch(review_policy_version) is None:
            raise SettingsError("REVIEW_POLICY_VERSION must match [a-z0-9][a-z0-9.-]{0,63}")

        development_hotkeys = _csv(env, "DEVELOPMENT_HOTKEYS")
        invalid = tuple(
            item for item in development_hotkeys if SS58_ADDRESS.fullmatch(item) is None
        )
        if invalid:
            raise SettingsError(
                "DEVELOPMENT_HOTKEYS contains invalid addresses: " + ", ".join(invalid)
            )
        if authenticator == DEVELOPMENT_AUTH and not development_hotkeys:
            raise SettingsError(
                "DEVELOPMENT_HOTKEYS must list at least one address when using the "
                "development authenticator"
            )

        development_coldkey = env.get("DEVELOPMENT_COLDKEY", "").strip() or recipient
        if SS58_ADDRESS.fullmatch(development_coldkey) is None:
            raise SettingsError("DEVELOPMENT_COLDKEY is not a valid SS58 address")

        subtensor_network = env.get("SUBTENSOR_NETWORK", "finney").strip() or "finney"
        if NETWORK.fullmatch(subtensor_network) is None:
            raise SettingsError("SUBTENSOR_NETWORK contains invalid characters")

        return cls(
            app_mode=app_mode,
            database_url=env.get("DATABASE_URL", "").strip(),
            task_allowlist_path=_directory(
                env,
                "TASK_ALLOWLIST_PATH",
                PROJECT_ROOT / "task_pool" / "allowlist.json",
            ),
            task_pool_root=_directory(
                env, "TASK_POOL_ROOT", PROJECT_ROOT / "tasks" / "pool"
            ),
            verifier_project_root=_directory(env, "VERIFIER_PROJECT_ROOT", PROJECT_ROOT),
            payment_recipient=recipient,
            payment_amount_rao=_positive_int(
                env, "PAYMENT_AMOUNT_RAO", DEFAULT_SUBMISSION_PRICE_RAO
            ),
            subtensor_network=subtensor_network,
            authenticator=authenticator,
            payment_verifier=payment_verifier,
            dispatcher=dispatcher,
            development_hotkeys=development_hotkeys,
            development_coldkey=development_coldkey,
            development_payment_references=_csv(env, "DEVELOPMENT_PAYMENT_REFERENCES"),
            nonce_window_seconds=_positive_int(env, "NONCE_WINDOW_SECONDS", 120, maximum=3600),
            max_bundle_bytes=_positive_int(
                env, "MAX_BUNDLE_BYTES", MAX_BUNDLE_BYTES, maximum=MAX_BUNDLE_BYTES
            ),
            manual_review_enabled=_flag(env, "MANUAL_REWARD_REVIEW_ENABLED", True),
            review_policy_version=review_policy_version,
        )
