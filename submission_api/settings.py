"""Typed, fail-closed configuration read once from the environment.

Every value is validated at startup rather than at first use, so a misconfigured deployment
refuses to boot instead of failing on the first miner request.

Three refusals are deliberate guardrails rather than conveniences: production will not start
with the development authenticator, the development payment verifier, or the in-process
verification dispatcher. Each would otherwise silently weaken the boundary that
`docs/SUBNET.md` and `SECURITY.md` describe — unauthenticated miners, unpaid submissions, or
hostile Lean compiled inside the API's own trust domain.

The public read surface adds four more, for the same reason. Production refuses a wildcard CORS
origin, refuses to disable rate limiting, and requires a real `PUBLIC_CURSOR_SECRET` and
`PUBLIC_ACTIVITY_SALT` rather than inheriting the development constants — a shipped development
salt would make the pseudonyms in `/v1/catalog/conjectures/{slug}/activity` reversible by
anyone who read this file.

The API configures no database of its own; `conjectures_subnet.db.database_url()` resolves
`DATABASE_URL` or the `POSTGRES_*` variables that `.env.example` already defines.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from verifier.bundle import MAX_BUNDLE_BYTES, SS58_ADDRESS

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TASKS_ROOT = PROJECT_ROOT.parent / "conjectures-tasks"

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

# The bounty a verified submission is owed, in alpha base units. There is no defensible
# default for money owed, so production must state it; development gets one so a local run
# needs no extra variable.
DEVELOPMENT_BOUNTY_RAO = RAO_PER_TAO
DEFAULT_BOUNTY_POLICY_VERSION = "flat-v1"

POLICY_VERSION = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}$")

# --- Public read surface -------------------------------------------------------------------
# The catalog, results, and status endpoints are read by a browser on a public website, so they
# carry configuration the miner-facing surface never needed: which origins may read them, how
# hard one client may hit them, and the two secrets that keep pseudonyms and cursors from being
# forged.

# An origin is a scheme, host and optional port — no path, no trailing slash, no wildcard. The
# browser sends exactly this string in `Origin`, and CORSMiddleware compares it exactly.
CORS_ORIGIN = re.compile(r"^https?://[A-Za-z0-9.-]{1,253}(:[0-9]{1,5})?$")
CORS_WILDCARD = "*"
# Stage 2 added writes on the account surface, so the browser needs the verbs for them.
#
# `POST /v1/submissions` shares the POST verb, so the verb list can no longer be what protects
# it. The protection moves to CORS_REQUEST_HEADERS below: that endpoint requires
# `X-Conjectures-Hotkey`, `-Timestamp`, `-Signature`, `-Task-Id`, `-Task-Sha256`,
# `-Proof-Sha256` and `-Payment-Ref`, and none of them is on the allowlist. A browser cannot
# send a header the preflight did not permit, so a page on an allowed origin still cannot form
# a valid submission — and the endpoint authenticates a hotkey signature rather than a cookie,
# so there is no ambient credential for it to ride on either.
CORS_METHODS = ("GET", "HEAD", "OPTIONS", "POST", "PATCH", "PUT", "DELETE")
# Deliberately narrow, and deliberately without any `X-Conjectures-*` header except the CSRF
# token. Adding a signature header here would undo the paragraph above.
CORS_REQUEST_HEADERS = (
    "Accept",
    "Accept-Language",
    "Content-Type",
    "If-None-Match",
    "X-Conjectures-CSRF",
)
# Exposed so a browser can actually read them off a cross-origin response. Without this the
# rate-limit budget and ETag are invisible to the page that is subject to them.
CORS_EXPOSED_HEADERS = (
    "ETag",
    "RateLimit-Limit",
    "RateLimit-Remaining",
    "RateLimit-Reset",
    "Retry-After",
)
CORS_MAX_AGE_SECONDS = 600

DEFAULT_RATE_LIMIT_REQUESTS = 120
DEFAULT_RATE_LIMIT_WINDOW_SECONDS = 60
# The limiter is in-process, so its table is a memory budget an attacker controls the keys of.
# Bounded here rather than trusted to stay small.
DEFAULT_RATE_LIMIT_MAX_CLIENTS = 50_000

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100
DEFAULT_ACTIVITY_ITEMS = 50
MAX_ACTIVITY_ITEMS = 200

DEFAULT_PUBLIC_CACHE_SECONDS = 60

# HTTP/3 is advertised, not implemented here: an ASGI app never sees the transport. The value
# names the authority a client should retry over QUIC, which only the deployment knows.
DEFAULT_ALT_SVC = 'h3=":443"; ma=86400'
DEFAULT_HSTS_MAX_AGE = 31_536_000  # one year, the minimum preload lists accept

# Long enough that guessing is not a strategy. Production must set both; these are what a local
# run uses, and they are named so that finding one in a production environment is unambiguous.
MIN_SECRET_LENGTH = 32
DEVELOPMENT_CURSOR_SECRET = "development-cursor-secret-never-use-in-production"
DEVELOPMENT_ACTIVITY_SALT = "development-activity-salt-never-use-in-production"

# The weekly drain-and-rotate window from README.md's "Pins, cache, and reproducibility".
# Configured rather than derived: only the operator knows when they take the system down.
DEFAULT_PIN_ROTATION_WEEKDAY = 1  # Monday=0 … Sunday=6, matching date.weekday()
DEFAULT_PIN_ROTATION_START = "02:00"
DEFAULT_PIN_ROTATION_MINUTES = 240
CLOCK_TIME = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")

MAX_BANNER_LENGTH = 500

# --- Accounts and sessions (Stage 2) --------------------------------------------------------

SMTP_MAIL = "smtp"
CONSOLE_MAIL = "console"
MAIL_SENDERS = (SMTP_MAIL, CONSOLE_MAIL)

# 30-day rolling, as the account contract states. Rolling means extended on use, which is
# why the refresh interval exists: without it every authenticated request would write.
DEFAULT_SESSION_DAYS = 30
DEFAULT_SESSION_REFRESH_MINUTES = 60

# Magic links and signing nonces are short-lived because they are single-use credentials in
# transit. Fifteen minutes is long enough to find the email and short enough that a link
# left in a browser history or a referrer header is already dead.
DEFAULT_EMAIL_LINK_MINUTES = 15
DEFAULT_CHALLENGE_MINUTES = 5

# Per-identity limits, on top of the per-IP limiter. Mailing a link is an action taken
# against someone else's mailbox, so what has to be bounded is requests per address rather
# than requests per requester.
DEFAULT_EMAIL_LINKS_PER_HOUR = 5
DEFAULT_CHALLENGES_PER_HOUR = 30

# How long a held credit stays held. Long enough to upload a bundle and sign a digest,
# short enough that an abandoned intent does not strand a credit for the day.
DEFAULT_INTENT_MINUTES = 30
# How long an account has to make the transfer a deposit expects.
DEFAULT_DEPOSIT_HOURS = 24

DEFAULT_CREDIT_PACKAGES = "1,10:1,50:8"
DEFAULT_TERMS_VERSION = "v1"
DEFAULT_TERMS_DATE = "2026-08-01"

# The domain that goes into a signed login message, binding the signature to this
# deployment so one produced for another instance is not valid here.
DEFAULT_LOGIN_DOMAIN = "conjectures.io"
LOGIN_DOMAIN = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


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
    """One of a fixed set, or the default.

    An empty value means "not set", exactly as it does for `_positive_int` and `_flag`. That
    matters because `docker compose` substitutes an unset variable as the empty string, so
    `SUBMISSION_AUTHENTICATOR: ${SUBMISSION_AUTHENTICATOR:-}` reaches this as `""` — and
    treating that as an invalid choice would make a deployment that meant "use the default"
    refuse to boot.
    """
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


def _bounded_int(
    environ: Mapping[str, str],
    key: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    """Like `_positive_int`, but for values where zero is a meaningful setting.

    `PUBLIC_CACHE_SECONDS=0` means "do not cache" and `TRUSTED_PROXY_HOPS=0` means "trust no
    forwarding header", so neither can go through the positive-only reader.
    """
    raw = environ.get(key, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise SettingsError(f"{key} must be an integer, got {raw!r}") from exc
    if not minimum <= value <= maximum:
        raise SettingsError(f"{key} must be between {minimum} and {maximum}, got {value}")
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
    return tuple(
        item.strip() for item in environ.get(key, "").split(",") if item.strip()
    )


def _clock_minutes(environ: Mapping[str, str], key: str, default: str) -> int:
    """An `HH:MM` wall-clock time as minutes past midnight UTC."""
    raw = environ.get(key, "").strip() or default
    matched = CLOCK_TIME.fullmatch(raw)
    if matched is None:
        raise SettingsError(f"{key} must be a UTC time as HH:MM, got {raw!r}")
    return int(matched.group(1)) * 60 + int(matched.group(2))


def _secret(
    environ: Mapping[str, str], key: str, *, production: bool, development_default: str
) -> str:
    """A shared secret, required in production and never inherited from this file there.

    The development constants are published in this module, so accepting one under
    `APP_MODE=PROD` would mean the activity pseudonyms and page cursors of a live deployment
    were derived from a value in a public repository.
    """
    value = environ.get(key, "").strip()
    if not production:
        return value or development_default
    if not value:
        raise SettingsError(f"{key} is required in production")
    if value == development_default:
        raise SettingsError(
            f"{key} is the published development constant and must not be used in production"
        )
    if len(value) < MIN_SECRET_LENGTH:
        raise SettingsError(
            f"{key} must be at least {MIN_SECRET_LENGTH} characters, got {len(value)}"
        )
    return value


def _cors_origins(
    environ: Mapping[str, str], key: str, *, production: bool
) -> tuple[str, ...]:
    """An exact allowlist of browser origins.

    No wildcard in production, and no pattern matching in any mode: a subdomain wildcard turns
    one XSS on any subdomain into read access to this API, and there is no origin here that is
    not known at deploy time. An empty list is a valid, fail-closed answer — it means no
    browser may read the API, which is correct until a site exists.
    """
    origins = _csv(environ, key)
    if CORS_WILDCARD in origins:
        if production:
            raise SettingsError(
                f"{key} must not contain '{CORS_WILDCARD}' in production; list the exact "
                "origins the website is served from"
            )
        return (CORS_WILDCARD,)
    invalid = tuple(item for item in origins if CORS_ORIGIN.fullmatch(item) is None)
    if invalid:
        raise SettingsError(
            f"{key} entries must be scheme://host[:port] with no trailing slash: "
            + ", ".join(invalid)
        )
    insecure = tuple(
        item
        for item in origins
        if item.startswith("http://")
        and not item.startswith(("http://localhost", "http://127.0.0.1"))
    )
    if production and insecure:
        raise SettingsError(
            f"{key} must use https in production: " + ", ".join(insecure)
        )
    # Deduplicated and ordered, so the same configuration always produces the same allowlist.
    return tuple(sorted(set(origins)))


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
    bounty_amount_rao: int
    bounty_policy_version: str

    # --- Public read surface ---------------------------------------------------------------
    pins_path: Path
    cors_allowed_origins: tuple[str, ...]
    rate_limit_enabled: bool
    rate_limit_requests: int
    rate_limit_window_seconds: int
    rate_limit_max_clients: int
    # How many rightmost `X-Forwarded-For` entries this deployment put there itself. Zero means
    # the header is not trusted at all and the peer address is used.
    trusted_proxy_hops: int
    cursor_secret: str
    activity_salt: str
    public_cache_seconds: int
    alt_svc: str
    hsts_max_age: int
    submissions_paused: bool
    status_banner: str
    pin_rotation_weekday: int
    pin_rotation_start_minute: int
    pin_rotation_minutes: int

    # --- Accounts and sessions (Stage 2) ---------------------------------------------------
    mail_sender: str
    # Where the magic link points. The website's origin, not this API's: the link is
    # clicked by a person in a browser and lands on a page, which then calls the API.
    website_base_url: str
    login_domain: str
    session_days: int
    session_refresh_minutes: int
    email_link_minutes: int
    challenge_minutes: int
    email_links_per_hour: int
    challenges_per_hour: int
    intent_minutes: int
    deposit_hours: int
    credit_packages: str
    credit_price_usd: str
    credit_price_usd_asof: str
    submission_terms_path: Path
    submission_terms_version: str
    submission_terms_effective_from: str

    @property
    def production(self) -> bool:
        return self.app_mode == PRODUCTION_MODE

    @property
    def expose_docs(self) -> bool:
        return not self.production

    @property
    def cors_enabled(self) -> bool:
        return bool(self.cors_allowed_origins)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
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
            raise SettingsError(
                "production requires SUBMISSION_AUTHENTICATOR=hotkey-signature"
            )
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
            raise SettingsError(
                "REVIEW_POLICY_VERSION must match [a-z0-9][a-z0-9.-]{0,63}"
            )

        # Every accepted submission is quoted a bounty it is then owed, so a production
        # deployment states the amount rather than inheriting one.
        if production and not env.get("BOUNTY_AMOUNT_RAO", "").strip():
            raise SettingsError(
                "BOUNTY_AMOUNT_RAO is required in production; it is what a verified "
                "submission is owed and must not fall back to a default"
            )
        bounty_amount_rao = _positive_int(
            env, "BOUNTY_AMOUNT_RAO", DEVELOPMENT_BOUNTY_RAO
        )
        bounty_policy_version = env.get(
            "BOUNTY_POLICY_VERSION", DEFAULT_BOUNTY_POLICY_VERSION
        ).strip()
        if POLICY_VERSION.fullmatch(bounty_policy_version) is None:
            raise SettingsError(
                "BOUNTY_POLICY_VERSION must match [a-z0-9][a-z0-9.-]{0,63}"
            )

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

        # Turning the limiter off in production would leave an unauthenticated, database-backed
        # read surface with no ceiling on it at all.
        rate_limit_enabled = _flag(env, "RATE_LIMIT_ENABLED", True)
        if production and not rate_limit_enabled:
            raise SettingsError(
                "RATE_LIMIT_ENABLED must not be false in production; the public read "
                "endpoints are unauthenticated"
            )

        banner = env.get("STATUS_BANNER", "").strip()
        if len(banner) > MAX_BANNER_LENGTH:
            raise SettingsError(
                f"STATUS_BANNER must not exceed {MAX_BANNER_LENGTH} characters"
            )

        mail_sender = _choice(
            env, "MAIL_SENDER", MAIL_SENDERS, SMTP_MAIL if production else CONSOLE_MAIL
        )
        if production and mail_sender != SMTP_MAIL:
            raise SettingsError(
                "production requires MAIL_SENDER=smtp; the console sender writes sign-in "
                "links, which are credentials, to the process log"
            )

        login_domain = env.get("LOGIN_DOMAIN", DEFAULT_LOGIN_DOMAIN).strip()
        if LOGIN_DOMAIN.fullmatch(login_domain) is None:
            raise SettingsError("LOGIN_DOMAIN must be a bare hostname")

        # The magic link is clicked by a person in a browser, so it points at the website,
        # not at this API. Production must say where that is: a link to a guessed origin is
        # a sign-in credential sent somewhere nobody chose.
        website_base_url = env.get("WEBSITE_BASE_URL", "").strip()
        if production and not website_base_url:
            raise SettingsError(
                "WEBSITE_BASE_URL is required in production; it is where the emailed "
                "sign-in link points"
            )
        if website_base_url and not website_base_url.startswith(("http://", "https://")):
            raise SettingsError("WEBSITE_BASE_URL must be an absolute http(s) URL")
        if production and website_base_url.startswith("http://"):
            raise SettingsError("WEBSITE_BASE_URL must use https in production")
        if not website_base_url:
            website_base_url = "http://localhost:3000"

        terms_date = env.get(
            "SUBMISSION_TERMS_EFFECTIVE_FROM", DEFAULT_TERMS_DATE
        ).strip()
        if ISO_DATE.fullmatch(terms_date) is None:
            raise SettingsError(
                "SUBMISSION_TERMS_EFFECTIVE_FROM must be an ISO date, YYYY-MM-DD"
            )
        terms_version = env.get(
            "SUBMISSION_TERMS_VERSION", DEFAULT_TERMS_VERSION
        ).strip()
        if POLICY_VERSION.fullmatch(terms_version) is None:
            raise SettingsError(
                "SUBMISSION_TERMS_VERSION must match [a-z0-9][a-z0-9.-]{0,63}"
            )

        # A pinned USD rate, or nothing. Converting TAO to USD needs a live external rate
        # this validator does not have, so the field is null rather than invented — and it
        # is a string all the way through, because a price must not become a float.
        credit_price_usd = env.get("CREDIT_PRICE_USD", "").strip()
        if credit_price_usd and not re.fullmatch(r"\d{1,9}(\.\d{1,4})?", credit_price_usd):
            raise SettingsError(
                "CREDIT_PRICE_USD must be a plain decimal amount, e.g. 4.50"
            )
        credit_price_usd_asof = env.get("CREDIT_PRICE_USD_ASOF", "").strip()
        if credit_price_usd and ISO_DATE.fullmatch(credit_price_usd_asof) is None:
            raise SettingsError(
                "CREDIT_PRICE_USD_ASOF must be an ISO date when CREDIT_PRICE_USD is set; "
                "a quoted price with no date cannot be judged for staleness"
            )

        tasks_root = _directory(env, "CONJECTURES_TASKS_ROOT", DEFAULT_TASKS_ROOT)
        return cls(
            app_mode=app_mode,
            database_url=env.get("DATABASE_URL", "").strip(),
            # Renamed with the pool itself: neither gold/allowlist.json nor a gold pool exists
            # any more, so the old names could only ever have resolved to nothing.
            task_allowlist_path=_directory(
                env,
                "TASK_ALLOWLIST_PATH",
                tasks_root / "allowlist.json",
            ),
            task_pool_root=_directory(
                env, "TASK_POOL_ROOT", tasks_root / "pool"
            ),
            verifier_project_root=_directory(
                env, "VERIFIER_PROJECT_ROOT", PROJECT_ROOT
            ),
            payment_recipient=recipient,
            payment_amount_rao=_positive_int(
                env, "PAYMENT_AMOUNT_RAO", DEFAULT_SUBMISSION_PRICE_RAO
            ),
            authenticator=authenticator,
            payment_verifier=payment_verifier,
            dispatcher=dispatcher,
            development_hotkeys=development_hotkeys,
            development_coldkey=development_coldkey,
            development_payment_references=_csv(env, "DEVELOPMENT_PAYMENT_REFERENCES"),
            nonce_window_seconds=_positive_int(
                env, "NONCE_WINDOW_SECONDS", 120, maximum=3600
            ),
            max_bundle_bytes=_positive_int(
                env, "MAX_BUNDLE_BYTES", MAX_BUNDLE_BYTES, maximum=MAX_BUNDLE_BYTES
            ),
            manual_review_enabled=_flag(env, "MANUAL_REWARD_REVIEW_ENABLED", True),
            review_policy_version=review_policy_version,
            bounty_amount_rao=bounty_amount_rao,
            bounty_policy_version=bounty_policy_version,
            pins_path=_directory(env, "PINS_LOCK_PATH", PROJECT_ROOT / "pins.lock.json"),
            cors_allowed_origins=_cors_origins(
                env, "CORS_ALLOWED_ORIGINS", production=production
            ),
            rate_limit_enabled=rate_limit_enabled,
            rate_limit_requests=_positive_int(
                env, "RATE_LIMIT_REQUESTS", DEFAULT_RATE_LIMIT_REQUESTS, maximum=1_000_000
            ),
            rate_limit_window_seconds=_positive_int(
                env,
                "RATE_LIMIT_WINDOW_SECONDS",
                DEFAULT_RATE_LIMIT_WINDOW_SECONDS,
                maximum=3600,
            ),
            rate_limit_max_clients=_positive_int(
                env,
                "RATE_LIMIT_MAX_CLIENTS",
                DEFAULT_RATE_LIMIT_MAX_CLIENTS,
                maximum=5_000_000,
            ),
            trusted_proxy_hops=_bounded_int(
                env, "TRUSTED_PROXY_HOPS", 0, minimum=0, maximum=8
            ),
            cursor_secret=_secret(
                env,
                "PUBLIC_CURSOR_SECRET",
                production=production,
                development_default=DEVELOPMENT_CURSOR_SECRET,
            ),
            activity_salt=_secret(
                env,
                "PUBLIC_ACTIVITY_SALT",
                production=production,
                development_default=DEVELOPMENT_ACTIVITY_SALT,
            ),
            public_cache_seconds=_bounded_int(
                env,
                "PUBLIC_CACHE_SECONDS",
                DEFAULT_PUBLIC_CACHE_SECONDS,
                minimum=0,
                maximum=86_400,
            ),
            alt_svc=env.get("ALT_SVC", DEFAULT_ALT_SVC if production else "").strip(),
            hsts_max_age=_bounded_int(
                env, "HSTS_MAX_AGE", DEFAULT_HSTS_MAX_AGE, minimum=0, maximum=63_072_000
            ),
            submissions_paused=_flag(env, "SUBMISSIONS_PAUSED", False),
            status_banner=banner,
            pin_rotation_weekday=_bounded_int(
                env,
                "PIN_ROTATION_WEEKDAY",
                DEFAULT_PIN_ROTATION_WEEKDAY,
                minimum=0,
                maximum=6,
            ),
            pin_rotation_start_minute=_clock_minutes(
                env, "PIN_ROTATION_START_UTC", DEFAULT_PIN_ROTATION_START
            ),
            pin_rotation_minutes=_positive_int(
                env,
                "PIN_ROTATION_DURATION_MINUTES",
                DEFAULT_PIN_ROTATION_MINUTES,
                maximum=10_080,
            ),
            mail_sender=mail_sender,
            website_base_url=website_base_url,
            login_domain=login_domain,
            session_days=_positive_int(
                env, "SESSION_DAYS", DEFAULT_SESSION_DAYS, maximum=365
            ),
            session_refresh_minutes=_positive_int(
                env,
                "SESSION_REFRESH_MINUTES",
                DEFAULT_SESSION_REFRESH_MINUTES,
                maximum=10_080,
            ),
            email_link_minutes=_positive_int(
                env, "EMAIL_LINK_MINUTES", DEFAULT_EMAIL_LINK_MINUTES, maximum=1_440
            ),
            challenge_minutes=_positive_int(
                env, "LOGIN_CHALLENGE_MINUTES", DEFAULT_CHALLENGE_MINUTES, maximum=60
            ),
            email_links_per_hour=_positive_int(
                env, "EMAIL_LINKS_PER_HOUR", DEFAULT_EMAIL_LINKS_PER_HOUR, maximum=1_000
            ),
            challenges_per_hour=_positive_int(
                env, "LOGIN_CHALLENGES_PER_HOUR", DEFAULT_CHALLENGES_PER_HOUR, maximum=10_000
            ),
            intent_minutes=_positive_int(
                env, "INTENT_MINUTES", DEFAULT_INTENT_MINUTES, maximum=1_440
            ),
            deposit_hours=_positive_int(
                env, "DEPOSIT_HOURS", DEFAULT_DEPOSIT_HOURS, maximum=720
            ),
            credit_packages=env.get(
                "CREDIT_PACKAGES", DEFAULT_CREDIT_PACKAGES
            ).strip()
            or DEFAULT_CREDIT_PACKAGES,
            credit_price_usd=credit_price_usd,
            credit_price_usd_asof=credit_price_usd_asof,
            submission_terms_path=_directory(
                env, "SUBMISSION_TERMS_PATH", PROJECT_ROOT / "docs" / "SUBMISSION_TERMS.md"
            ),
            submission_terms_version=terms_version,
            submission_terms_effective_from=terms_date,
        )
