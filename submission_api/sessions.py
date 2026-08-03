"""The browser session: an opaque cookie, and the CSRF token bound to it.

Deliberately not a JWT. A JWT here would have to be either short-lived — which means a
refresh mechanism, which means a second credential — or long-lived and unrevocable,
which means a logout that does not log anything out. An opaque token backed by a row is
revocable in one UPDATE, and the row is where "30-day rolling" actually lives.

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
from conjectures_subnet.db.models import Account, AccountSession

SESSION_COOKIE = "conjectures_session"
CSRF_COOKIE = "conjectures_csrf"
CSRF_HEADER = "X-Conjectures-CSRF"

# 256 bits from the OS CSPRNG, URL-safe. `token_urlsafe(32)` is 43 characters, so the
# cookie stays small, and guessing is not a strategy at any request rate.
TOKEN_BYTES = 32

REASON_NOT_AUTHENTICATED = "NOT_AUTHENTICATED"
REASON_CSRF_FAILED = "CSRF_CHECK_FAILED"


@dataclass(frozen=True)
class IssuedSession:
    """A newly created session: the row, plus the two secrets only the caller holds."""

    row: AccountSession
    token: str
    csrf_token: str


@dataclass(frozen=True)
class Principal:
    """Who is making this request."""

    account: Account
    session: AccountSession

    def has_role(self, role: str) -> bool:
        return role in (self.account.roles or ())


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


async def issue(
    session,
    account: Account,
    *,
    now: dt.datetime,
    lifetime: dt.timedelta,
    user_agent: str | None = None,
    source_ip: str | None = None,
) -> IssuedSession:
    """Create a session and return the secrets to set as cookies.

    The tokens are generated here and hashed on the way in, so this is the only moment
    they exist in the process. Nothing later can recover them, which is what makes the
    stored form safe.
    """
    token = new_token()
    csrf_token = new_token()
    row = await account_store.create_session(
        session,
        account,
        token_digest=account_store.digest(token),
        csrf_digest=account_store.digest(csrf_token),
        expires_at=now + lifetime,
        user_agent=user_agent,
        source_ip=source_ip,
    )
    return IssuedSession(row=row, token=token, csrf_token=csrf_token)


async def resolve(
    session, token: str | None, *, now: dt.datetime
) -> Principal | None:
    """Authenticate a request from its session cookie, or return None.

    A read. The rolling extension is `refresh` below, called only when one is due:
    extending on every request would mean a row lock and a WAL record for every page
    load, and the website polls.
    """
    if not token:
        return None
    found = await account_store.authenticate(
        session, account_store.digest(token), now=now
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
) -> bool:
    """Extend the window if it has gone unused for longer than `refresh_after`.

    Returns whether it moved, which is what tells the caller whether to re-send the
    cookie with a new `Max-Age`. A cookie whose `Max-Age` is never refreshed would
    expire in the browser while the row was still live.
    """
    return await account_store.touch_session(
        session,
        principal.session.id,
        now=now,
        expires_at=now + lifetime,
        refresh_after=refresh_after,
    )


def csrf_matches(principal: Principal, header_value: str | None) -> bool:
    """Whether the request carried the CSRF token for *this* session.

    Compared against the digest on the session row, so a value the client can set is
    not itself the proof. `compare_digest` because the comparison is against a stored
    secret's digest and there is no reason to leak timing.
    """
    if not header_value:
        return False
    return hmac.compare_digest(
        account_store.digest(header_value), bytes(principal.session.csrf_sha256)
    )


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
    "CSRF_COOKIE",
    "CSRF_HEADER",
    "REASON_CSRF_FAILED",
    "REASON_NOT_AUTHENTICATED",
    "SESSION_COOKIE",
    "IssuedSession",
    "Principal",
    "cleared_cookies",
    "csrf_cookie",
    "csrf_matches",
    "issue",
    "new_token",
    "refresh",
    "resolve",
    "session_cookie",
]
