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
from dataclasses import dataclass, field
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

# Development has no chain wallet to read, so it uses a deterministic four-Alpha pool. With the
# default 1/4 policy constant, an average-age task is displayed at one Alpha. Production never
# uses this value: its balance is read from the configured Subnet 66 stake position.
DEVELOPMENT_BOUNTY_BALANCE_RAO = 4 * RAO_PER_TAO
DEFAULT_BOUNTY_POLICY_VERSION = "dynamic-age-v1"
DEFAULT_BOUNTY_CONSTANT_NUMERATOR = 1
DEFAULT_BOUNTY_CONSTANT_DENOMINATOR = 4
DEFAULT_BOUNTY_AGE_PERIOD_SECONDS = 86_400
DEFAULT_BOUNTY_BALANCE_CACHE_SECONDS = 60
DEFAULT_BITTENSOR_NETWORK = "finney"
DEFAULT_BOUNTY_NETUID = 66
DEFAULT_TAOSTATS_PRICE_CACHE_SECONDS = 60

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
#
# **`Authorization` must never be added to this list**, and that is now load-bearing in a second
# way. A CLI bearer session is exempt from the CSRF token check — correctly, because a bearer
# token is not an ambient credential and no browser attaches one on its own. This allowlist is
# what keeps that true: it is the only thing preventing a page on an allowlisted origin from
# sending an `Authorization` header cross-origin at all. Adding it here would make the CSRF
# exemption browser-reachable, which is the one way the exemption becomes a hole.
#
# The CLI is not a browser, sends no `Origin`, and is not subject to CORS, so it loses nothing.
# `tests/test_api_accounts.py` asserts the absence.
CORS_REQUEST_HEADERS = (
    "Accept",
    "Accept-Language",
    "Content-Type",
    "If-None-Match",
    "X-Conjectures-CSRF",
)
# Exposed so a browser can actually read them off a cross-origin response. Without this the
# rate-limit budget and ETag are invisible to the page that is subject to them.
#
# `X-Request-Id` is here so a failing page can quote the id of the request that failed. It is the
# key the same request's Axiom event and every log record underneath it are tagged with, which
# turns "the site is broken" into one lookup. Reading it grants nothing: the value is minted by
# this process per request, identifies no account, and is not accepted as input.
CORS_EXPOSED_HEADERS = (
    "ETag",
    "RateLimit-Limit",
    "RateLimit-Remaining",
    "RateLimit-Reset",
    "Retry-After",
    "X-Request-Id",
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
SMTP_STARTTLS = "starttls"
SMTP_IMPLICIT_TLS = "implicit-tls"
SMTP_PLAINTEXT = "none"
SMTP_SECURITY_MODES = (SMTP_STARTTLS, SMTP_IMPLICIT_TLS, SMTP_PLAINTEXT)
DEFAULT_SMTP_PORT = 587
DEFAULT_SMTP_TIMEOUT_SECONDS = 10.0
SMTP_FROM_ADDRESS = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$"
)

# 30-day rolling, as the account contract states. Rolling means extended on use, which is
# why the refresh interval exists: without it every authenticated request would write.
DEFAULT_SESSION_DAYS = 30
DEFAULT_SESSION_REFRESH_MINUTES = 60

# The CLI's bearer token. Shorter than the browser's cookie, and unlike it, capped absolutely.
#
# A cookie lives in a browser its owner can inspect and clear, is HttpOnly so no script reads
# it, and is replaced on every sign-in. A bearer token lives in a file on a miner's machine at
# mode 0600, and is copied nowhere the person can see. So: a shorter rolling window, and a
# ceiling past which rolling stops, because "rolling" with no ceiling means a credential that
# never expires as long as any cron job keeps touching it.
DEFAULT_CLI_SESSION_DAYS = 14
DEFAULT_CLI_SESSION_MAX_DAYS = 90

# How many live CLI tokens one account may hold at once. A miner legitimately has several —
# a laptop, a couple of rigs, CI — and each `conjectures auth login` mints another. The ceiling
# is what keeps a compromised hotkey from minting an unbounded pile of durable credentials that
# each have to be revoked individually; reaching it evicts the oldest rather than refusing the
# newest, so a stale token on a decommissioned box cannot lock a miner out of their own tooling.
DEFAULT_CLI_SESSIONS_PER_ACCOUNT = 10

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

# How many signatures may be offered against one challenge before it is spent.
#
# The signature flows verify before consuming, so a wrong signature does not burn the nonce and
# an attacker cannot grief a known address by sending garbage. The cost of that is an open
# challenge accepting unlimited verification attempts on an unauthenticated path. Five is well
# past any plausible client bug and nowhere near useful for guessing a 64-byte signature.
DEFAULT_CHALLENGE_ATTEMPTS = 5

# Public OAuth client identifier, not a secret. Empty keeps Google sign-in disabled without
# weakening either of the existing login methods.
GOOGLE_CLIENT_ID_SHAPE = re.compile(
    r"^[0-9]+-[A-Za-z0-9_-]{10,200}\.apps\.googleusercontent\.com$"
)

# How long a held credit stays held. Long enough to upload a bundle and sign a digest,
# short enough that an abandoned intent does not strand a credit for the day.
DEFAULT_INTENT_MINUTES = 30
# How long an account has to make the transfer a deposit expects.
DEFAULT_DEPOSIT_HOURS = 24

# Which chain the payment verifier reads. `finney` is mainnet. Named rather than defaulted to
# `local`, because a verifier pointed at a development chain would refuse every real payment.
# Shared with the deposit watcher under the same variable names, so one setting configures both and
# they cannot end up reading different chains.
DEFAULT_BITTENSOR_NETWORK = "finney"

DEFAULT_CREDIT_PACKAGES = "1"
# Bumped to v3 by the v2 manual-review rule that expands `NOT_NOVEL`. The terms version and
# manual-review version are separate counters because terms v2 was already published.
# `docs/SUBMISSION_TERMS.md` is served as `body_md` under this version, so the two move together:
# leaving it at v2 would serve rewritten terms under a version string a miner already accepted.
DEFAULT_TERMS_VERSION = "v3"
DEFAULT_TERMS_DATE = "2026-08-07"

# The domain that goes into a signed login message, binding the signature to this
# deployment so one produced for another instance is not valid here.
DEFAULT_LOGIN_DOMAIN = "conjectures.io"
LOGIN_DOMAIN = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# --- TMC PAY -------------------------------------------------------------------------------
# The processor-settled way to buy credits. Off unless `TMC_PAY_API_BASE_URL`, `TMC_PAY_API_KEY`
# and `TMC_PAY_WEBHOOK_SECRET` are all set: two of them are credentials and the third is a host
# the published documentation quotes as `api.example.com`, so there is nothing here to guess and a
# partially-configured deployment must not offer the payment method at all.
#
# `submission_api/tmc_pay.py` explains the trust difference this path introduces. These are the
# knobs, and every default is chosen so that a deployment which sets only the three required
# values behaves correctly.

# ISO 4217, and the minor-unit count that goes with it. Both configurable because TMC PAY quotes
# in fiat and a merchant may be onboarded in something other than dollars; `TMC_PAY_FIAT_DECIMALS`
# exists because zero-decimal currencies (JPY, KRW) would otherwise be quoted to the cent.
DEFAULT_TMC_PAY_FIAT_CURRENCY = "USD"
DEFAULT_TMC_PAY_FIAT_DECIMALS = 2
FIAT_CURRENCY = re.compile(r"^[A-Z]{3}$")

# How much above the credit price the fiat request is sized, in basis points.
#
# TMC PAY locks its own exchange rate when it creates the invoice, a moment after this process
# estimated one from TaoStats. 25 bps absorbs ordinary movement in that gap so the common case
# needs one round trip rather than two; `routers/tmc_pay.py` still verifies the invoice it got
# back, so this is an optimisation and never the guarantee. The overshoot is not lost — it lands
# in the buyer's own balance as `remainder_rao`.
DEFAULT_TMC_PAY_QUOTE_MARGIN_BPS = 25
MAX_TMC_PAY_QUOTE_MARGIN_BPS = 2_000

# How much more than the credit price an invoice may lock before it is thrown away and requoted —
# the acceptable slippage, in basis points.
#
# This is the *ceiling* of the quote band, and it is a policy rather than a derivation: the floor
# protects the validator from selling a credit below `CREDIT_PRICE_RAO`, and this protects the buyer
# from being overcharged when the estimated rate came in high. 1% by default, which is loose enough
# that ordinary rate movement between the estimate and the lock does not cost a second invoice, and
# tight enough that a stale or wrong-currency rate is caught rather than charged.
#
# Must be at least `TMC_PAY_QUOTE_MARGIN_BPS`: the margin is added to every ask on purpose, so a
# tolerance below it would put every invoice outside the deployment's own band and make each
# purchase fail after exhausting its attempts. `Settings` refuses that combination at startup.
DEFAULT_TMC_PAY_MAX_SLIPPAGE_BPS = 100
MAX_TMC_PAY_MAX_SLIPPAGE_BPS = 5_000

# How many invoices may be created for one purchase. The first uses the estimated rate; a second
# uses the rate the first invoice actually locked, which is exact. More than two would mean the
# rate is moving faster than the round trip, and the honest answer then is to refuse the sale.
DEFAULT_TMC_PAY_QUOTE_ATTEMPTS = 2
MAX_TMC_PAY_QUOTE_ATTEMPTS = 4

# The invoice TTL. TMC PAY allows 5 to 1440 minutes and defaults to 30; 30 is enough to open a
# wallet and send TAO, and short enough that an abandoned invoice stops occupying the account's
# open-order allowance within the hour.
DEFAULT_TMC_PAY_TTL_MINUTES = 30
MIN_TMC_PAY_TTL_MINUTES = 5
MAX_TMC_PAY_TTL_MINUTES = 1_440

# How many invoices one account may have outstanding. The endpoint's side effect is an invoice at
# a payment processor, so the ceiling is what stops one account filling somebody else's dashboard.
DEFAULT_TMC_PAY_MAX_OPEN_ORDERS = 3

# The largest single purchase. Not a policy about wealth: an invoice is quoted in fiat from an
# estimated rate, and a mistyped credit count should fail here rather than become a five-figure
# invoice somebody has to explain.
DEFAULT_TMC_PAY_MAX_CREDITS = 1_000

DEFAULT_TMC_PAY_TIMEOUT_SECONDS = 10.0

# How often the owner reading their own order may cause a poll of TMC PAY. The payment page polls
# every few seconds by design, and each poll is an outbound request against a shared API, so the
# read endpoint refreshes at most this often and serves stored state in between.
DEFAULT_TMC_PAY_POLL_SECONDS = 5

# How long a rate observed on one of our own invoices is reused to price the next one.
#
# Every invoice reports the rate TMC PAY locked, and that beats any third-party feed as a seed: it
# is the same rate source that will price the next invoice, and it is already denominated in the
# merchant's currency. TaoStats is the cold-start fallback, not the primary. Five minutes trades a
# little staleness for far fewer outbound calls and far fewer requotes — and the quote band makes
# even a bad seed a wasted round trip rather than a wrong price.
DEFAULT_TMC_PAY_RATE_TTL_SECONDS = 300

# The one currency the external feeds can price. Both publish TAO in dollars, so a merchant
# onboarded in anything else is seeded from its own past invoices or not at all.
EXTERNAL_RATE_CURRENCY = "USD"

# TaoMarketCap's public market-data host: the preferred external price source, because
# TaoMarketCap is TMC PAY and its own candles are closer to the rate that platform will lock than
# any third party's. Public — no API key — so it is on by default and a deployment needs no rate
# configuration at all. Set to `none` to switch it off and fall back to TaoStats.
DEFAULT_TAOMARKETCAP_API_BASE_URL = "https://api.taomarketcap.com"


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


def _positive_float(
    environ: Mapping[str, str], key: str, default: float, *, maximum: float
) -> float:
    """A duration in seconds. Never used for money — see the note in `credits.py`."""
    raw = environ.get(key, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise SettingsError(f"{key} must be a number, got {raw!r}") from exc
    if not value > 0:
        raise SettingsError(f"{key} must be positive, got {value}")
    if value > maximum:
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
    # Read by the chain payment verifier only. The archive endpoint is a fallback for a reference
    # naming a block outside a lite node's pruned-state window; empty means "the same network".
    bittensor_network: str
    bittensor_archive_network: str
    bounty_wallet_coldkey: str
    bounty_wallet_hotkey: str
    bounty_netuid: int
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
    bounty_pool_balance_rao: int
    bounty_policy_version: str
    bounty_constant_numerator: int
    bounty_constant_denominator: int
    bounty_age_period_seconds: int
    bounty_balance_cache_seconds: int
    # Optional because USD is display metadata: without a key (or during an upstream outage),
    # bounty `amount_usd` is null while the Alpha-denominated quote remains available.
    taostats_api_key: str = field(repr=False)
    taostats_price_cache_seconds: int
    # TaoMarketCap's public candle feed, the preferred TAO/USD source for pricing a TMC PAY
    # invoice. Empty disables it, leaving TaoStats as the only external source.
    taomarketcap_base_url: str
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
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str = field(repr=False)
    smtp_from_address: str
    smtp_security: str
    smtp_timeout_seconds: float
    # Where the magic link points. The website's origin, not this API's: the link is
    # clicked by a person in a browser and lands on a page, which then calls the API.
    website_base_url: str
    login_domain: str
    google_client_id: str
    session_days: int
    session_refresh_minutes: int
    cli_session_days: int
    cli_session_max_days: int
    cli_sessions_per_account: int
    email_link_minutes: int
    challenge_minutes: int
    email_links_per_hour: int
    challenges_per_hour: int
    challenge_attempts: int
    intent_minutes: int
    deposit_hours: int
    credit_packages: str
    credit_price_usd: str
    credit_price_usd_asof: str
    submission_terms_path: Path
    submission_terms_version: str
    submission_terms_effective_from: str

    # --- TMC PAY ---------------------------------------------------------------------------
    # Empty base URL means the payment method is not offered. Both secrets are `repr=False`: the
    # settings object is logged at startup by way of the Axiom `service_started` event, and a
    # merchant API key can create invoices payable to this validator's merchant account.
    tmc_pay_base_url: str
    tmc_pay_api_key: str = field(repr=False)
    tmc_pay_webhook_secret: str = field(repr=False)
    # TMC PAY's hosted payment page, if the buyer should be sent to one. Separate from the API
    # base URL because they are different hosts, and optional because a purchase page that
    # renders the deposit address and amount itself needs no redirect — every field it would
    # need is already on the order.
    tmc_pay_hosted_base_url: str
    # Optional. When set, a webhook whose payload names a different merchant is refused rather
    # than matched on invoice id alone — the one check that a delivery aimed at somebody else's
    # integration cannot move money here.
    tmc_pay_merchant_id: str
    tmc_pay_fiat_currency: str
    tmc_pay_fiat_decimals: int
    tmc_pay_quote_margin_bps: int
    # The acceptable overcharge before an invoice is thrown away and requoted. Never below
    # `tmc_pay_quote_margin_bps` — see the constant, and the startup check in `from_env`.
    tmc_pay_max_slippage_bps: int
    tmc_pay_quote_attempts: int
    tmc_pay_ttl_minutes: int
    tmc_pay_max_open_orders: int
    tmc_pay_max_credits: int
    tmc_pay_timeout_seconds: float
    tmc_pay_poll_seconds: int
    tmc_pay_rate_ttl_seconds: int
    # Whether a payment confirmed after its invoice expired issues credits automatically. Off by
    # default: TMC PAY documents `late_payment` as a manual reconciliation case, and this process
    # cannot tell from the outside whether such a payment settles to the treasury or is returned
    # to the sender. An operator who has established that it settles can turn it on.
    tmc_pay_credit_late_payments: bool

    @property
    def tmc_pay_enabled(self) -> bool:
        """Whether credits can be bought through TMC PAY on this deployment.

        All three of the base URL, the API key and the webhook secret, because each is
        load-bearing: without the key no invoice can be created, and without the secret a webhook
        cannot be authenticated — and an integration that creates invoices it can never confirm
        would take money and issue nothing.
        """
        return bool(
            self.tmc_pay_base_url
            and self.tmc_pay_api_key
            and self.tmc_pay_webhook_secret
        )

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
        bounty_wallet_coldkey = _address(
            env,
            "BOUNTY_WALLET_COLDKEY_SS58",
            env.get("BOUNTY_WALLET_COLDKEY_SS58", "").strip() or recipient,
        )
        bounty_hotkey_raw = env.get("BOUNTY_WALLET_HOTKEY_SS58", "").strip()
        if production and not bounty_hotkey_raw:
            raise SettingsError("BOUNTY_WALLET_HOTKEY_SS58 is required in production")
        bounty_wallet_hotkey = _address(
            env,
            "BOUNTY_WALLET_HOTKEY_SS58",
            bounty_hotkey_raw or recipient,
        )

        review_policy_version = env.get("REVIEW_POLICY_VERSION", "v2").strip()
        if POLICY_VERSION.fullmatch(review_policy_version) is None:
            raise SettingsError(
                "REVIEW_POLICY_VERSION must match [a-z0-9][a-z0-9.-]{0,63}"
            )

        # Production prices from the live bounty stake. A configured balance there would quietly
        # turn a moving chain value into a stale promise, so the deterministic override is
        # development-only.
        if production and env.get("BOUNTY_POOL_BALANCE_RAO", "").strip():
            raise SettingsError(
                "BOUNTY_POOL_BALANCE_RAO is development-only; production reads the live "
                "Subnet Alpha stake"
            )
        bounty_pool_balance_rao = _positive_int(
            env, "BOUNTY_POOL_BALANCE_RAO", DEVELOPMENT_BOUNTY_BALANCE_RAO
        )
        bounty_policy_version = env.get(
            "BOUNTY_POLICY_VERSION", DEFAULT_BOUNTY_POLICY_VERSION
        ).strip()
        if POLICY_VERSION.fullmatch(bounty_policy_version) is None:
            raise SettingsError(
                "BOUNTY_POLICY_VERSION must match [a-z0-9][a-z0-9.-]{0,63}"
            )
        bittensor_network = env.get(
            "BITTENSOR_NETWORK", DEFAULT_BITTENSOR_NETWORK
        ).strip()
        if not bittensor_network or len(bittensor_network) > 255 or "\x00" in bittensor_network:
            raise SettingsError("BITTENSOR_NETWORK must contain 1 to 255 non-NUL characters")
        taostats_api_key = env.get("TAOSTATS_API_KEY", "").strip()
        if len(taostats_api_key) > 512 or any(
            ord(char) < 32 for char in taostats_api_key
        ):
            raise SettingsError(
                "TAOSTATS_API_KEY must not exceed 512 characters or contain control characters"
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

        smtp_host = env.get("SMTP_HOST", "").strip()
        smtp_port = _positive_int(env, "SMTP_PORT", DEFAULT_SMTP_PORT, maximum=65_535)
        smtp_username = env.get("SMTP_USERNAME", "").strip()
        smtp_password = env.get("SMTP_PASSWORD", "")
        smtp_from_address = env.get("SMTP_FROM_ADDRESS", "").strip()
        smtp_security = _choice(
            env, "SMTP_SECURITY", SMTP_SECURITY_MODES, SMTP_STARTTLS
        )
        smtp_timeout_seconds = _positive_float(
            env,
            "SMTP_TIMEOUT_SECONDS",
            DEFAULT_SMTP_TIMEOUT_SECONDS,
            maximum=120.0,
        )
        if mail_sender == SMTP_MAIL:
            if not smtp_host:
                raise SettingsError("SMTP_HOST is required when MAIL_SENDER=smtp")
            if (
                len(smtp_host) > 253
                or any(char.isspace() for char in smtp_host)
                or any(ord(char) < 32 for char in smtp_host)
            ):
                raise SettingsError("SMTP_HOST must be a hostname without whitespace")
            if not smtp_from_address:
                raise SettingsError(
                    "SMTP_FROM_ADDRESS is required when MAIL_SENDER=smtp"
                )
            if SMTP_FROM_ADDRESS.fullmatch(smtp_from_address) is None:
                raise SettingsError("SMTP_FROM_ADDRESS must be one email address")
            if bool(smtp_username) != bool(smtp_password):
                raise SettingsError(
                    "SMTP_USERNAME and SMTP_PASSWORD must either both be set or both be empty"
                )
            if "\x00" in smtp_password or "\r" in smtp_password or "\n" in smtp_password:
                raise SettingsError("SMTP_PASSWORD must not contain NUL or line breaks")
            if production and smtp_security == SMTP_PLAINTEXT:
                raise SettingsError("production SMTP must use TLS")

        login_domain = env.get("LOGIN_DOMAIN", DEFAULT_LOGIN_DOMAIN).strip()
        if LOGIN_DOMAIN.fullmatch(login_domain) is None:
            raise SettingsError("LOGIN_DOMAIN must be a bare hostname")

        # The CLI token's rolling window and the ceiling it may not roll past. Checked against
        # each other here rather than trusted: a maximum below the rolling window silently
        # means "every token expires at the maximum", which is a lifetime nobody configured
        # and which would look like tokens dying early for no reason.
        cli_session_days = _positive_int(
            env, "CLI_SESSION_DAYS", DEFAULT_CLI_SESSION_DAYS, maximum=365
        )
        cli_session_max_days = _positive_int(
            env, "CLI_SESSION_MAX_DAYS", DEFAULT_CLI_SESSION_MAX_DAYS, maximum=365
        )
        if cli_session_max_days < cli_session_days:
            raise SettingsError(
                "CLI_SESSION_MAX_DAYS must not be less than CLI_SESSION_DAYS; the maximum is "
                "the ceiling a rolling window may not pass, not a second window"
            )

        google_client_id = env.get("GOOGLE_CLIENT_ID", "").strip()
        if google_client_id and GOOGLE_CLIENT_ID_SHAPE.fullmatch(google_client_id) is None:
            raise SettingsError(
                "GOOGLE_CLIENT_ID must be a Google OAuth web client ID ending in "
                ".apps.googleusercontent.com"
            )

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

        # --- TMC PAY ------------------------------------------------------------------------
        # Validated together, because the three required values are only useful as a set: a
        # deployment with a key and no secret would create invoices it could never confirm.
        tmc_pay_base_url = env.get("TMC_PAY_API_BASE_URL", "").strip().rstrip("/")
        tmc_pay_api_key = env.get("TMC_PAY_API_KEY", "").strip()
        tmc_pay_webhook_secret = env.get("TMC_PAY_WEBHOOK_SECRET", "").strip()
        tmc_pay_configured = tuple(
            name
            for name, value in (
                ("TMC_PAY_API_BASE_URL", tmc_pay_base_url),
                ("TMC_PAY_API_KEY", tmc_pay_api_key),
                ("TMC_PAY_WEBHOOK_SECRET", tmc_pay_webhook_secret),
            )
            if value
        )
        if tmc_pay_configured and len(tmc_pay_configured) != 3:
            # Half-configured is the dangerous state, not the harmless one: it is the shape in
            # which a purchase page appears and a confirmation never does. Refuse to boot.
            missing = ", ".join(
                name
                for name in (
                    "TMC_PAY_API_BASE_URL",
                    "TMC_PAY_API_KEY",
                    "TMC_PAY_WEBHOOK_SECRET",
                )
                if name not in tmc_pay_configured
            )
            raise SettingsError(
                f"TMC PAY is partially configured; {missing} must be set too, or unset all "
                "three to leave the payment method off"
            )
        if tmc_pay_base_url:
            if not tmc_pay_base_url.startswith(("http://", "https://")):
                raise SettingsError(
                    "TMC_PAY_API_BASE_URL must be an absolute http(s) URL, e.g. "
                    "https://api.pay.example.com"
                )
            if production and not tmc_pay_base_url.startswith("https://"):
                raise SettingsError("TMC_PAY_API_BASE_URL must use https in production")
            if len(tmc_pay_api_key) > 512 or any(
                ord(char) < 32 for char in tmc_pay_api_key
            ):
                raise SettingsError(
                    "TMC_PAY_API_KEY must not exceed 512 characters or contain control "
                    "characters"
                )
            if len(tmc_pay_webhook_secret) < 16:
                # The secret is the only thing standing between an unauthenticated endpoint and
                # the credit ledger. A short one is a typo or a placeholder, not a secret.
                raise SettingsError(
                    "TMC_PAY_WEBHOOK_SECRET must be at least 16 characters; it is what "
                    "authenticates every credit a webhook issues"
                )
        # `none` switches the public candle feed off; empty means "use the default host". Validated
        # here so a typo is a boot failure rather than a purchase that silently falls back.
        taomarketcap_base_url = (
            env.get("TAOMARKETCAP_API_BASE_URL", "").strip()
            or DEFAULT_TAOMARKETCAP_API_BASE_URL
        )
        if taomarketcap_base_url.lower() == "none":
            taomarketcap_base_url = ""
        elif not taomarketcap_base_url.startswith(("http://", "https://")):
            raise SettingsError(
                "TAOMARKETCAP_API_BASE_URL must be an absolute http(s) URL, or `none` to "
                "disable the candle feed"
            )
        elif production and not taomarketcap_base_url.startswith("https://"):
            raise SettingsError("TAOMARKETCAP_API_BASE_URL must use https in production")

        tmc_pay_fiat_currency = (
            env.get("TMC_PAY_FIAT_CURRENCY", "").strip().upper()
            or DEFAULT_TMC_PAY_FIAT_CURRENCY
        )
        if FIAT_CURRENCY.fullmatch(tmc_pay_fiat_currency) is None:
            raise SettingsError(
                "TMC_PAY_FIAT_CURRENCY must be a three-letter ISO 4217 code, e.g. USD"
            )
        # Read together, because the pair is only meaningful as a pair: the margin is what every
        # ask adds on purpose, and the slippage is what the answer may come back as. A tolerance
        # below the margin describes a band no invoice this deployment asks for could land in, so
        # every purchase would burn its attempts and fail. Refused here rather than clamped —
        # silently widening an operator's stated tolerance is not this code's decision to make.
        tmc_pay_quote_margin_bps = _bounded_int(
            env,
            "TMC_PAY_QUOTE_MARGIN_BPS",
            DEFAULT_TMC_PAY_QUOTE_MARGIN_BPS,
            minimum=0,
            maximum=MAX_TMC_PAY_QUOTE_MARGIN_BPS,
        )
        tmc_pay_max_slippage_bps = _bounded_int(
            env,
            "TMC_PAY_MAX_SLIPPAGE_BPS",
            DEFAULT_TMC_PAY_MAX_SLIPPAGE_BPS,
            minimum=0,
            maximum=MAX_TMC_PAY_MAX_SLIPPAGE_BPS,
        )
        if tmc_pay_max_slippage_bps < tmc_pay_quote_margin_bps:
            raise SettingsError(
                f"TMC_PAY_MAX_SLIPPAGE_BPS ({tmc_pay_max_slippage_bps}) must be at least "
                f"TMC_PAY_QUOTE_MARGIN_BPS ({tmc_pay_quote_margin_bps}); the margin is added to "
                "every invoice this deployment asks for, so a tighter tolerance would reject all "
                "of them"
            )

        tmc_pay_merchant_id = env.get("TMC_PAY_MERCHANT_ID", "").strip()
        if len(tmc_pay_merchant_id) > 64:
            raise SettingsError("TMC_PAY_MERCHANT_ID must not exceed 64 characters")
        tmc_pay_hosted_base_url = (
            env.get("TMC_PAY_HOSTED_BASE_URL", "").strip().rstrip("/")
        )
        if tmc_pay_hosted_base_url:
            if not tmc_pay_hosted_base_url.startswith(("http://", "https://")):
                raise SettingsError(
                    "TMC_PAY_HOSTED_BASE_URL must be an absolute http(s) URL, e.g. "
                    "https://pay.example.com"
                )
            if production and not tmc_pay_hosted_base_url.startswith("https://"):
                # It is a link handed to a person who is about to send money. Over http, the
                # address on the page is whatever the network says it is.
                raise SettingsError(
                    "TMC_PAY_HOSTED_BASE_URL must use https in production"
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
            bittensor_network=bittensor_network,
            bittensor_archive_network=env.get(
                "BITTENSOR_ARCHIVE_NETWORK", ""
            ).strip(),
            bounty_wallet_coldkey=bounty_wallet_coldkey,
            bounty_wallet_hotkey=bounty_wallet_hotkey,
            bounty_netuid=_positive_int(
                env, "BOUNTY_NETUID", DEFAULT_BOUNTY_NETUID, maximum=65_535
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
            bounty_pool_balance_rao=bounty_pool_balance_rao,
            bounty_policy_version=bounty_policy_version,
            bounty_constant_numerator=_positive_int(
                env,
                "BOUNTY_CONSTANT_NUMERATOR",
                DEFAULT_BOUNTY_CONSTANT_NUMERATOR,
                maximum=1_000_000,
            ),
            bounty_constant_denominator=_positive_int(
                env,
                "BOUNTY_CONSTANT_DENOMINATOR",
                DEFAULT_BOUNTY_CONSTANT_DENOMINATOR,
                maximum=1_000_000,
            ),
            bounty_age_period_seconds=_positive_int(
                env,
                "BOUNTY_AGE_PERIOD_SECONDS",
                DEFAULT_BOUNTY_AGE_PERIOD_SECONDS,
                maximum=31_536_000,
            ),
            bounty_balance_cache_seconds=_positive_int(
                env,
                "BOUNTY_BALANCE_CACHE_SECONDS",
                DEFAULT_BOUNTY_BALANCE_CACHE_SECONDS,
                maximum=3600,
            ),
            taostats_api_key=taostats_api_key,
            taostats_price_cache_seconds=_positive_int(
                env,
                "TAOSTATS_PRICE_CACHE_SECONDS",
                DEFAULT_TAOSTATS_PRICE_CACHE_SECONDS,
                maximum=3600,
            ),
            taomarketcap_base_url=taomarketcap_base_url,
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
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_username=smtp_username,
            smtp_password=smtp_password,
            smtp_from_address=smtp_from_address,
            smtp_security=smtp_security,
            smtp_timeout_seconds=smtp_timeout_seconds,
            website_base_url=website_base_url,
            login_domain=login_domain,
            google_client_id=google_client_id,
            session_days=_positive_int(
                env, "SESSION_DAYS", DEFAULT_SESSION_DAYS, maximum=365
            ),
            session_refresh_minutes=_positive_int(
                env,
                "SESSION_REFRESH_MINUTES",
                DEFAULT_SESSION_REFRESH_MINUTES,
                maximum=10_080,
            ),
            cli_session_days=cli_session_days,
            cli_session_max_days=cli_session_max_days,
            cli_sessions_per_account=_positive_int(
                env,
                "CLI_SESSIONS_PER_ACCOUNT",
                DEFAULT_CLI_SESSIONS_PER_ACCOUNT,
                maximum=1_000,
            ),
            email_link_minutes=_positive_int(
                env, "EMAIL_LINK_MINUTES", DEFAULT_EMAIL_LINK_MINUTES, maximum=1_440
            ),
            challenge_minutes=_positive_int(
                env, "LOGIN_CHALLENGE_MINUTES", DEFAULT_CHALLENGE_MINUTES, maximum=60
            ),
            challenge_attempts=_positive_int(
                env, "LOGIN_CHALLENGE_ATTEMPTS", DEFAULT_CHALLENGE_ATTEMPTS, maximum=100
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
            tmc_pay_base_url=tmc_pay_base_url,
            tmc_pay_api_key=tmc_pay_api_key,
            tmc_pay_webhook_secret=tmc_pay_webhook_secret,
            tmc_pay_hosted_base_url=tmc_pay_hosted_base_url,
            tmc_pay_merchant_id=tmc_pay_merchant_id,
            tmc_pay_fiat_currency=tmc_pay_fiat_currency,
            tmc_pay_fiat_decimals=_bounded_int(
                env,
                "TMC_PAY_FIAT_DECIMALS",
                DEFAULT_TMC_PAY_FIAT_DECIMALS,
                minimum=0,
                maximum=6,
            ),
            tmc_pay_quote_margin_bps=tmc_pay_quote_margin_bps,
            tmc_pay_max_slippage_bps=tmc_pay_max_slippage_bps,
            tmc_pay_quote_attempts=_positive_int(
                env,
                "TMC_PAY_QUOTE_ATTEMPTS",
                DEFAULT_TMC_PAY_QUOTE_ATTEMPTS,
                maximum=MAX_TMC_PAY_QUOTE_ATTEMPTS,
            ),
            tmc_pay_ttl_minutes=_bounded_int(
                env,
                "TMC_PAY_TTL_MINUTES",
                DEFAULT_TMC_PAY_TTL_MINUTES,
                minimum=MIN_TMC_PAY_TTL_MINUTES,
                maximum=MAX_TMC_PAY_TTL_MINUTES,
            ),
            tmc_pay_max_open_orders=_positive_int(
                env,
                "TMC_PAY_MAX_OPEN_ORDERS",
                DEFAULT_TMC_PAY_MAX_OPEN_ORDERS,
                maximum=100,
            ),
            tmc_pay_max_credits=_positive_int(
                env,
                "TMC_PAY_MAX_CREDITS",
                DEFAULT_TMC_PAY_MAX_CREDITS,
                maximum=1_000_000,
            ),
            tmc_pay_timeout_seconds=_positive_float(
                env,
                "TMC_PAY_TIMEOUT_SECONDS",
                DEFAULT_TMC_PAY_TIMEOUT_SECONDS,
                maximum=60.0,
            ),
            tmc_pay_poll_seconds=_bounded_int(
                env,
                "TMC_PAY_POLL_SECONDS",
                DEFAULT_TMC_PAY_POLL_SECONDS,
                minimum=0,
                maximum=3_600,
            ),
            tmc_pay_rate_ttl_seconds=_bounded_int(
                env,
                "TMC_PAY_RATE_TTL_SECONDS",
                DEFAULT_TMC_PAY_RATE_TTL_SECONDS,
                # Zero disables reuse: every quote is seeded from the external feed. Kept
                # available because it is the honest way to say "always ask a third party".
                minimum=0,
                maximum=86_400,
            ),
            tmc_pay_credit_late_payments=_flag(
                env, "TMC_PAY_CREDIT_LATE_PAYMENTS", False
            ),
        )
