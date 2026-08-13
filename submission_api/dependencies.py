"""Request-scoped wiring.

Everything expensive — settings, engine, task catalog, authenticator, payment verifier — is
built once during lifespan and stored on the application state. Routers reach it through these
dependencies rather than through module-level globals, spelled as the `*Dep` aliases below so a
handler signature names what it needs without repeating the wiring.

The database belongs to `conjectures_subnet.db`, the validator's shared durable store; this
module only borrows a session from it per request.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from conjectures_subnet.bounty import BountyPricer
from conjectures_subnet.db import accounts as account_store
from conjectures_subnet.db.models import MINER_ROLE, AccountSessionKind
from submission_api import sessions as session_layer
from submission_api.auth import Authenticator
from submission_api.conjectures import ConjectureIndex
from submission_api.credits import CreditPackage, SubmissionTerms
from submission_api.errors import Forbidden, Unauthorized
from submission_api.mail import MailSender
from submission_api.sessions import Principal
from submission_api.payments import PaymentVerifier
from submission_api.pins import PinSet
from submission_api.retired import RetiredIndex
from submission_api.settings import Settings
from submission_api.taskpool import TaskCatalog
from submission_api.taostats import AlphaUsdPriceReader, UnavailableAlphaUsdPriceReader
from submission_api.verification import VerificationDispatcher


@dataclass(frozen=True)
class Services:
    settings: Settings
    engine: AsyncEngine
    sessions: async_sessionmaker
    catalog: TaskCatalog
    authenticator: Authenticator
    payments: PaymentVerifier
    dispatcher: VerificationDispatcher
    pricing: BountyPricer
    # The active pin set, read once at startup like the catalog. A running API serves one pin
    # set; a rotation is a restart, which is what the weekly drain-and-rotate policy already
    # requires.
    pins: PinSet
    # Stage 2. All immutable and read once, like everything else here.
    mail: MailSender
    packages: tuple[CreditPackage, ...]
    terms: SubmissionTerms
    bounty_usd: AlphaUsdPriceReader = field(default_factory=UnavailableAlphaUsdPriceReader)
    # Targets that have left the pool, kept readable so results already earned against them stay
    # citable. Separate from `catalog` on purpose: `catalog` is what a submission resolves
    # against, and nothing in this field can ever reach that path. Empty by default, so a test
    # that cares about admission does not have to know this exists.
    retired: RetiredIndex = field(default_factory=RetiredIndex.empty)
    # The public view of `catalog`: tasks grouped into slug-addressable conjectures. Derived
    # rather than passed so that building a `Services` at all runs the slug collision check —
    # including in tests, which construct this directly. A pool that cannot be addressed by
    # stable slug fails here, at startup, rather than serving one conjecture at another's URL.
    index: ConjectureIndex = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "index", ConjectureIndex.build(self.catalog, retired=self.retired)
        )


def get_services(request: Request) -> Services:
    return request.app.state.services


ServicesDep = Annotated[Services, Depends(get_services)]


def get_settings(services: ServicesDep) -> Settings:
    return services.settings


SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_session(services: ServicesDep) -> AsyncIterator[AsyncSession]:
    """One session per request, always closed.

    The handler commits explicitly, so a request that fails mid-write rolls back rather than
    leaving a partial submission behind.
    """
    async with services.sessions() as session:
        try:
            yield session
        finally:
            await session.close()


SessionDep = Annotated[AsyncSession, Depends(get_session)]


# --- The signed-in principal --------------------------------------------------------------
# Three dependencies, in increasing strictness. Which one a handler names *is* its access
# control, so the choice is visible in the signature rather than buried in the body:
#
#   OptionalPrincipalDep — may be signed in. `GET /v1/auth/session` and nothing else.
#   PrincipalDep         — must be signed in. Every read under /v1/me.
#   WriterDep            — must be signed in AND proved the request was not cross-site.
#   CookieWriterDep      — all of that, from a browser session specifically. The writes a CLI
#                          token must not be able to make; see `require_cookie_writer`.
#
# A handler that changes state and names PrincipalDep instead of WriterDep is a CSRF hole, so
# the names are deliberately not interchangeable-looking.
#
# Two credentials can satisfy them: the browser's cookie and the CLI's bearer token. Which one
# arrived is on the resolved principal, and only the two writer dependencies care.


async def get_optional_principal(
    request: Request, services: ServicesDep, session: SessionDep
) -> Principal | None:
    """Resolve whichever credential the request carries, if any. Never raises for absence.

    **An `Authorization: Bearer` header, when present, is the credential — there is no
    fallback to the cookie if it turns out to be expired or revoked.** A client that offers
    a bearer token is asserting which identity it wants to act as, and silently substituting
    a different one would mean a CLI with a dead token acting as whoever last signed in to
    the browser on that machine. It also keeps "which credential authenticated this request"
    a function of the request alone, which is what makes the CSRF rule below auditable.

    Also rolls the window when one is due, and re-sends the cookies when it does — a cookie
    whose `Max-Age` is never refreshed expires in the browser while the row is still live. The
    refresh writes, so it is committed here: the read handlers this sits under have nothing
    else to commit and would otherwise roll it back.
    """
    settings = services.settings
    offered = session_layer.bearer_token(
        request.headers.get(session_layer.AUTHORIZATION_HEADER)
    )
    if offered is not None:
        principal = await session_layer.resolve(
            session, offered, kind=AccountSessionKind.BEARER, now=_now()
        )
        # A bearer window rolls like a cookie one, but not without limit: `max_lifetime` is
        # the absolute ceiling from `issued_at`. A cookie lives in a browser the person can
        # inspect and clear; a bearer token lives in a file, and a file that stays valid for
        # as long as anything keeps touching it never stops being a credential.
        lifetime = timedelta(days=settings.cli_session_days)
        max_lifetime = timedelta(days=settings.cli_session_max_days)
    else:
        principal = await session_layer.resolve(
            session,
            request.cookies.get(session_layer.SESSION_COOKIE),
            kind=AccountSessionKind.COOKIE,
            now=_now(),
        )
        lifetime = timedelta(days=settings.session_days)
        max_lifetime = None

    if principal is None:
        return None

    # A bearer session exists because a hotkey proved control of itself, and it is scoped to
    # that hotkey. If the link is gone — unlinked, or moved to another account — the basis for
    # the token is gone, and it must stop working on the next request rather than at the next
    # expiry. `revoke_sessions_for_hotkey` does this eagerly when the API is what removes the
    # link; this check is what makes the guarantee hold when something else did, including a
    # direct database change. One indexed lookup on a UNIQUE column, on bearer requests only.
    if principal.is_bearer:
        scope = principal.hotkey_scope
        if scope is None or not await account_store.hotkey_still_linked(
            session, hotkey=scope, account_id=principal.account.id
        ):
            return None

    moved = await session_layer.refresh(
        session,
        principal,
        now=_now(),
        lifetime=lifetime,
        refresh_after=timedelta(minutes=settings.session_refresh_minutes),
        max_lifetime=max_lifetime,
    )
    if moved:
        # Committed here: the read handlers this sits under have nothing else to commit and
        # would otherwise roll the extension back. The browser's own cookie expiry is kept in
        # step by SessionCookieRefreshMiddleware, which needs no state from here.
        await session.commit()
    return principal


OptionalPrincipalDep = Annotated[Principal | None, Depends(get_optional_principal)]


async def require_principal(
    principal: OptionalPrincipalDep,
) -> Principal:
    """A signed-in account, or 401.

    401 rather than 403: the caller may retry with a credential, which is exactly what the
    status code means, and the website uses it to decide whether to show the sign-in page.
    """
    if principal is None:
        raise Unauthorized(
            "sign in to use this endpoint",
            reason_code=session_layer.REASON_NOT_AUTHENTICATED,
        )
    return principal


PrincipalDep = Annotated[Principal, Depends(require_principal)]


async def require_writer(
    request: Request, principal: PrincipalDep
) -> Principal:
    """A signed-in account that also proved this request was not cross-site.

    The third of the three CSRF checks — the other two, `Origin` and `Sec-Fetch-Site`, are in
    `CsrfMiddleware`. This one is here rather than in middleware because only the resolved
    route knows which session is authenticated, and the token is compared against the digest
    stored on *that* session row.

    403, not 401: the caller is authenticated. What is missing is proof that they meant it.

    **A bearer session skips the check, because there is nothing for it to prove.** CSRF is
    the risk that a browser attaches an *ambient* credential to a request some other page
    caused. A bearer token is not ambient: it is sent only by code that deliberately set the
    header, and code on this origin that can set it can already make the request directly. So
    there is no confused deputy — and there is also no cookie for the CLI to read a CSRF value
    out of, which would make every CLI write a 403 for no security gain. The exemption is read
    off the authenticated session row (`requires_csrf`), never off the shape of the request, so
    it cannot be claimed by a caller who merely presents a header.
    """
    if not principal.requires_csrf:
        return principal
    if not session_layer.csrf_matches(
        principal, request.headers.get(session_layer.CSRF_HEADER)
    ):
        raise Forbidden(
            f"{session_layer.CSRF_HEADER} is missing or does not match this session",
            reason_code=session_layer.REASON_CSRF_FAILED,
        )
    return principal


WriterDep = Annotated[Principal, Depends(require_writer)]


REASON_BROWSER_SESSION_REQUIRED = "BROWSER_SESSION_REQUIRED"


async def require_cookie_writer(principal: WriterDep) -> Principal:
    """A write that a CLI token may not make. The browser's cookie, or nothing.

    Not every write is equally consequential, and the bearer credential is the weaker of the
    two. A Bittensor hotkey is stored **unencrypted** on disk by default — that is the point of
    the coldkey/hotkey split — and the token it mints is another file next to it. So anything
    that can change *who the account is* or *where its money goes* is deliberately out of a CLI
    token's reach:

      * linking a hotkey — otherwise a token scoped to one key extends its own scope at will;
      * setting the payout destination — the end of the chain that turns a hotkey read off a
        mining box into permanent theft of that account's rewards;
      * editing the profile, and claiming a deposit against an address.

    Without this gate those three compose into full account takeover from a stolen file: link
    an attacker-controlled hotkey, point the payout at it, collect. With it, the CLI keeps
    exactly what it needs — reading its own work, and the intent flow that spends credits it
    already has — and the destructive half requires a coldkey or a mailbox, in a browser, with
    an HttpOnly cookie and a CSRF token.

    403 rather than 401: the caller is authenticated and their account may well be permitted to
    do this. The credential is what is insufficient, and the reason code says so, so a CLI can
    print "do this at conjectures.io" instead of "log in again".
    """
    if principal.is_bearer:
        raise Forbidden(
            "this change cannot be made from a CLI session; sign in at the website",
            reason_code=REASON_BROWSER_SESSION_REQUIRED,
        )
    return principal


CookieWriterDep = Annotated[Principal, Depends(require_cookie_writer)]

REASON_HOTKEY_OUT_OF_SCOPE = "HOTKEY_OUT_OF_SCOPE"


def assert_hotkey_in_scope(principal: Principal, hotkey: str) -> None:
    """Refuse a bearer session acting for a hotkey other than the one that minted it.

    An account may own several hotkeys, and `owns_hotkey` is what checks that — at the level of
    the *account*. That is the right check for a browser session, which represents the person
    who owns all of them. It is the wrong check for a bearer token, which represents one key:
    a token minted on rig A would otherwise be able to spend the account's credits and have the
    resulting submission — and its reward — attributed to rig B.

    Called by handlers rather than expressed as a dependency because the hotkey arrives in the
    body, and a dependency cannot see it without parsing the body a second time. Every such
    handler must call this; a cookie principal passes through untouched.
    """
    if not principal.is_bearer:
        return
    if principal.hotkey_scope != hotkey:
        raise Forbidden(
            "this CLI session is scoped to a different hotkey; run the login again from the "
            "machine holding that key, or use the website",
            reason_code=REASON_HOTKEY_OUT_OF_SCOPE,
        )


def _now() -> datetime:
    return datetime.now(UTC)


# --- Roles ---------------------------------------------------------------------------------

REASON_ROLE_REQUIRED = "ROLE_REQUIRED"
REASON_ROLE_NEEDS_BROWSER = "ROLE_REQUIRES_BROWSER_SESSION"

# Which roles a CLI bearer token may exercise. MINER, and only MINER.
#
# This is a deliberate ceiling, not an oversight. A bearer session is minted by a *hotkey*
# signature, and a hotkey is an operational key: it lives on mining machines, it is used by
# automation, and the whole point of the coldkey/hotkey split in Bittensor is that a hotkey is
# the less protected of the two. The token it mints then sits in a file. None of that is a
# suitable basis for granting REVIEWER or ADMIN, which decide whether a proof earns money.
#
# So privileged work requires the coldkey-or-mailbox sign-in in a browser: a credential that is
# HttpOnly, CSRF-guarded, revoked on every re-authentication, and visible to the person holding
# it. An account may hold ADMIN and still use the CLI as a miner; what it may not do is act as
# an admin over a hotkey-minted token.
BEARER_ROLES = frozenset({MINER_ROLE})


def require_role(role: str):
    """A dependency factory for role-gated reads.

    Two refusals, not one, because they mean different things and a caller can act on the
    difference: the account does not have the role, or it does but this credential may not
    exercise it. Collapsing them into one message would send an admin round in circles
    wondering why their admin account is being refused.
    """

    async def check(principal: PrincipalDep) -> Principal:
        if not principal.has_role(role):
            # Absent rather than forbidden would be better still, but these routes are
            # under /v1/admin and their existence is not a secret.
            raise Forbidden(
                "this endpoint requires a role your account does not have",
                reason_code=REASON_ROLE_REQUIRED,
            )
        if principal.is_bearer and role not in BEARER_ROLES:
            raise Forbidden(
                f"the {role} role cannot be exercised from a CLI session; "
                "sign in at the website for this",
                reason_code=REASON_ROLE_NEEDS_BROWSER,
            )
        return principal

    return check


def require_role_writer(role: str):
    """The same gate for writes: the CSRF check *and* the role.

    A separate factory rather than a flag, so that a handler which changes state names a
    dependency whose name says `writer`. `require_writer` runs first by being the annotated
    parameter, so a privileged write that fails CSRF never reaches the role lookup.
    """

    async def check(principal: WriterDep) -> Principal:
        if not principal.has_role(role):
            raise Forbidden(
                "this endpoint requires a role your account does not have",
                reason_code=REASON_ROLE_REQUIRED,
            )
        if principal.is_bearer and role not in BEARER_ROLES:
            raise Forbidden(
                f"the {role} role cannot be exercised from a CLI session; "
                "sign in at the website for this",
                reason_code=REASON_ROLE_NEEDS_BROWSER,
            )
        return principal

    return check
