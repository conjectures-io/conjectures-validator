"""Sessions: the browser's opaque cookie with its bound CSRF token, and the CLI's bearer token.

Deliberately not a JWT. A JWT here would have to be either short-lived — which means a
refresh mechanism, which means a second credential — or long-lived and unrevocable,
which means a logout that does not log anything out. An opaque token backed by a row is
revocable in one UPDATE, and the row is where "30-day rolling" actually lives.

**Two kinds, and the difference is CSRF.** A cookie is an *ambient* credential: the browser
attaches it to any request to this origin, including one a hostile page caused, which is the
whole of what CSRF is. A bearer token is not ambient — nothing attaches it unless code chose
to, and code on the page that could choose to is already same-origin and could simply make
the request. So a bearer session is exempt from the CSRF token check, and that exemption is a
consequence of the threat model rather than a convenience: there is no cookie for the CLI to
read a CSRF value out of, and demanding one would make every write from the CLI a 403.

The two are non-interchangeable at the store, not merely by convention: ``accounts.authenticate``
takes the kind it expects and puts it in the predicate, so a cookie token replayed in an
``Authorization`` header resolves to nothing and vice versa. See its docstring for why forbidding
that is cheaper than reasoning about it.

What the browser holds:

* ``conjectures_session`` — HttpOnly, Secure, SameSite=Lax, the session token. HttpOnly
  so script on the page cannot read it, which is what keeps an XSS from exfiltrating a
  durable credential rather than merely acting within the page.
* ``conjectures_csrf`` — readable by script *on purpose*, because the frontend has to
  copy it into a request header. Not a secret in the same sense: it is only useful to
  code that can already read the page's cookies, which is precisely what a cross-site
  attacker cannot do.

Only digests are stored. ``accounts.digest`` hashes both tokens before they touch the
database, so a dump, a replica, or an over-broad SELECT yields nothing replayable.

The CSRF token is stored on the session row rather than being a pure double-submit
cookie. A bare double-submit compares two values the client supplied to each other,
which fails to a subdomain cookie-injection attack; comparing the header against the
digest recorded server-side does not.
"""

from __future__ import annotations

import datetime as dt
import hmac
import secrets
from dataclasses import dataclass

from conjectures_subnet.db import accounts as account_store
from conjectures_subnet.db.models import Account, AccountSession, AccountSessionKind

SESSION_COOKIE = "conjectures_session"
CSRF_COOKIE = "conjectures_csrf"
CSRF_HEADER = "X-Conjectures-CSRF"

AUTHORIZATION_HEADER = "Authorization"
BEARER_SCHEME = "bearer"
BEARER_TOKEN_TYPE = "bearer"
# Prefixed so a leaked token is findable. See `new_bearer_token`.
BEARER_TOKEN_PREFIX = "conj_cli_"
# `token_urlsafe(32)` is 43 characters. A generous ceiling on what will be hashed, so a
# multi-megabyte Authorization header is discarded before it reaches SHA-256 rather than
# after — the digest of an absurd value is a wasted allocation, not a match.
MAX_BEARER_LENGTH = 256

# 256 bits from the OS CSPRNG, URL-safe. `token_urlsafe(32)` is 43 characters, so the
# cookie stays small, and guessing is not a strategy at any request rate.
TOKEN_BYTES = 32

REASON_NOT_AUTHENTICATED = "NOT_AUTHENTICATED"
REASON_CSRF_FAILED = "CSRF_CHECK_FAILED"


@dataclass(frozen=True)
class IssuedSession:
    """A newly created cookie session: the row, plus the two secrets only the caller holds."""

    row: AccountSession
    token: str
    csrf_token: str


@dataclass(frozen=True)
class IssuedBearer:
    """A newly created CLI session: the row, plus the one secret only the caller holds.

    No CSRF token, and no field to hold one. A bearer session that carried a CSRF value
    would imply a check nothing performs — see the module docstring.
    """

    row: AccountSession
    token: str


@dataclass(frozen=True)
class Principal:
    """Who is making this request, and which credential said so."""

    account: Account
    session: AccountSession

    def has_role(self, role: str) -> bool:
        return role in (self.account.roles or ())

    @property
    def is_bearer(self) -> bool:
        return self.session.kind is AccountSessionKind.BEARER

    @property
    def requires_csrf(self) -> bool:
        """Whether a write by this principal has to prove it was not cross-site.

        True exactly for cookie sessions. Derived from the session row rather than from
        how the request looked, so a caller cannot opt out of the check by presenting a
        cookie credential in a different place — `authenticate` already refuses that, and
        this is the second half of the same guarantee.
        """
        return self.session.kind is AccountSessionKind.COOKIE

    @property
    def hotkey_scope(self) -> str | None:
        """The hotkey a bearer session's authority is bounded to. None for a cookie."""
        return self.session.hotkey_scope


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def new_bearer_token() -> str:
    """A CLI token with a fixed, recognisable prefix.

    The prefix buys nothing cryptographically — the entropy is the same 256 bits — and it is
    there for what happens to tokens that leak. A bearer token ends up in shell history, CI
    logs, a screenshot, a committed dotfile. A bare `token_urlsafe` string is indistinguishable
    from any other base64url blob, so no secret scanner can find it and no log scrubber can
    redact it. A stable prefix makes it greppable by push-protection tooling and by us.

    `digest()` hashes the whole string, so the prefix is inside the credential rather than
    metadata attached to it, and an attacker who strips it has a different token.
    """
    return f"{BEARER_TOKEN_PREFIX}{secrets.token_urlsafe(TOKEN_BYTES)}"


def accept_bearer(token: str | None) -> str | None:
    """The credential this API is willing to hash, out of whatever arrived, or None.

    Split out of `bearer_token` so the same rule applies however the value was extracted —
    the `fastapi.security` declaration in `security.py` parses the header itself, and an
    acceptance rule that only ran on one of the two paths would be no rule at all.

    An absurdly long value is None rather than an error: the digest of a multi-megabyte
    header is a wasted allocation, not a match.
    """
    if not token:
        return None
    token = token.strip()
    if not token or len(token) > MAX_BEARER_LENGTH:
        return None
    return token


def bearer_token(header_value: str | None) -> str | None:
    """The token out of an `Authorization: Bearer <t>` header, or None.

    The scheme is matched case-insensitively because RFC 7235 says it is case-insensitive
    and clients differ. Anything else — a Basic header, a bare token with no scheme, an
    absurdly long value — is None rather than an error: this runs on every request,
    including unauthenticated ones, and "no bearer credential here" is the ordinary answer.
    """
    if not header_value:
        return None
    scheme, _, token = header_value.partition(" ")
    if scheme.lower() != BEARER_SCHEME:
        return None
    return accept_bearer(token)


async def issue(
    session,
    account: Account,
    *,
    now: dt.datetime,
    lifetime: dt.timedelta,
    user_agent: str | None = None,
    source_ip: str | None = None,
) -> IssuedSession:
    """Create a browser session and return the secrets to set as cookies.

    The tokens are generated here and hashed on the way in, so this is the only moment
    they exist in the process. Nothing later can recover them, which is what makes the
    stored form safe.
    """
    token = new_token()
    csrf_token = new_token()
    row = await account_store.create_session(
        session,
        account,
        kind=AccountSessionKind.COOKIE,
        token_digest=account_store.digest(token),
        csrf_digest=account_store.digest(csrf_token),
        expires_at=now + lifetime,
        user_agent=user_agent,
        source_ip=source_ip,
    )
    return IssuedSession(row=row, token=token, csrf_token=csrf_token)


async def issue_bearer(
    session,
    account: Account,
    *,
    hotkey: str,
    now: dt.datetime,
    lifetime: dt.timedelta,
    user_agent: str | None = None,
    source_ip: str | None = None,
) -> IssuedBearer:
    """Create a CLI session scoped to one linked hotkey.

    Same entropy and same digest-on-the-way-in as the cookie path, so the credential is
    no weaker; what differs is that the caller will put it in a header rather than a
    cookie, that it is bounded to the key that proved it, and that it carries a prefix
    making it recognisable if it leaks.
    """
    token = new_bearer_token()
    row = await account_store.create_session(
        session,
        account,
        kind=AccountSessionKind.BEARER,
        token_digest=account_store.digest(token),
        hotkey_scope=hotkey,
        expires_at=now + lifetime,
        user_agent=user_agent,
        source_ip=source_ip,
    )
    return IssuedBearer(row=row, token=token)


async def resolve(
    session,
    token: str | None,
    *,
    kind: AccountSessionKind,
    now: dt.datetime,
) -> Principal | None:
    """Authenticate a request from one credential, or return None.

    A read. The rolling extension is `refresh` below, called only when one is due:
    extending on every request would mean a row lock and a WAL record for every page
    load, and the website polls.

    `kind` is passed down into the store's predicate, so this cannot resolve a credential
    of the other kind — the caller says which place it read the token from, and only a
    session issued for that place matches.
    """
    if not token:
        return None
    found = await account_store.authenticate(
        session, account_store.digest(token), kind=kind, now=now
    )
    if found is None:
        return None
    return Principal(account=found.account, session=found.session_row)


async def refresh(
    session,
    principal: Principal,
    *,
    now: dt.datetime,
    lifetime: dt.timedelta,
    refresh_after: dt.timedelta,
    max_lifetime: dt.timedelta | None = None,
) -> bool:
    """Extend the window if it has gone unused for longer than `refresh_after`.

    Returns whether it moved, which is what tells the caller whether to re-send the
    cookie with a new `Max-Age`. A cookie whose `Max-Age` is never refreshed would
    expire in the browser while the row was still live.

    `max_lifetime` caps how far a rolling window may roll from `issued_at`. Used for
    bearer sessions, where "rolling" would otherwise mean a token in a file that never
    expires as long as anything keeps using it.
    """
    return await account_store.touch_session(
        session,
        principal.session.id,
        now=now,
        expires_at=now + lifetime,
        refresh_after=refresh_after,
        max_lifetime=max_lifetime,
    )


def csrf_matches(principal: Principal, header_value: str | None) -> bool:
    """Whether the request carried the CSRF token for *this* session.

    Compared against the digest on the session row, so a value the client can set is
    not itself the proof. `compare_digest` because the comparison is against a stored
    secret's digest and there is no reason to leak timing.

    A session with no stored digest is a bearer session, and the answer is False: this
    function reports whether a CSRF token matched, and for a bearer session none can. The
    decision about whether that matters belongs to the caller, which asks
    `principal.requires_csrf` first — answering True here to mean "no check needed" would
    make a missing check indistinguishable from a passed one.
    """
    stored = principal.session.csrf_sha256
    if not header_value or stored is None:
        return False
    return hmac.compare_digest(account_store.digest(header_value), bytes(stored))


# --- Cookie serialisation ----------------------------------------------------------
# Written by hand rather than through Starlette's `set_cookie` so that the flags are
# visible in one place and reviewable as a set. `Secure` is conditional on production
# only because a development server on plain-HTTP localhost would otherwise be handed
# cookies the browser refuses to send back.


def _cookie(
    name: str,
    value: str,
    *,
    max_age: int,
    http_only: bool,
    secure: bool,
) -> str:
    parts = [
        f"{name}={value}",
        "Path=/",
        f"Max-Age={max_age}",
        # Lax, not Strict: a magic link arrives from a mail client as a cross-site
        # top-level navigation, and Strict would drop the cookie on exactly the
        # request that has to carry it. Lax sends it on top-level GETs and withholds
        # it from cross-site subrequests, which is the CSRF-relevant half.
        "SameSite=Lax",
    ]
    if http_only:
        parts.append("HttpOnly")
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def session_cookie(token: str, *, max_age: int, secure: bool) -> str:
    """The session credential. HttpOnly, so page script cannot read it."""
    return _cookie(SESSION_COOKIE, token, max_age=max_age, http_only=True, secure=secure)


def csrf_cookie(token: str, *, max_age: int, secure: bool) -> str:
    """The CSRF token. Readable by script on purpose — the frontend copies it into a
    header, and only same-origin code can read it."""
    return _cookie(CSRF_COOKIE, token, max_age=max_age, http_only=False, secure=secure)


def cleared_cookies(*, secure: bool) -> tuple[str, str]:
    """Both cookies, expired.

    `Max-Age=0` with an empty value, and the same `Path`, `SameSite` and `Secure` as
    when they were set — a browser only replaces a cookie whose attributes match, so a
    clear that omits them leaves the original in place.
    """
    return (
        _cookie(SESSION_COOKIE, "", max_age=0, http_only=True, secure=secure),
        _cookie(CSRF_COOKIE, "", max_age=0, http_only=False, secure=secure),
    )


__all__ = [
    "AUTHORIZATION_HEADER",
    "BEARER_SCHEME",
    "BEARER_TOKEN_PREFIX",
    "BEARER_TOKEN_TYPE",
    "CSRF_COOKIE",
    "CSRF_HEADER",
    "MAX_BEARER_LENGTH",
    "REASON_CSRF_FAILED",
    "REASON_NOT_AUTHENTICATED",
    "SESSION_COOKIE",
    "IssuedBearer",
    "IssuedSession",
    "Principal",
    "accept_bearer",
    "bearer_token",
    "cleared_cookies",
    "csrf_cookie",
    "csrf_matches",
    "issue",
    "issue_bearer",
    "new_bearer_token",
    "new_token",
    "refresh",
    "resolve",
    "session_cookie",
]
