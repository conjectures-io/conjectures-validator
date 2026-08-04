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
from submission_api import sessions as session_layer
from submission_api.auth import Authenticator
from submission_api.conjectures import ConjectureIndex
from submission_api.credits import CreditPackage, SubmissionTerms
from submission_api.errors import Forbidden, Unauthorized
from submission_api.mail import MailSender
from submission_api.sessions import Principal
from submission_api.payments import PaymentVerifier
from submission_api.pins import PinSet
from submission_api.settings import Settings
from submission_api.taskpool import TaskCatalog
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
    # The public view of `catalog`: tasks grouped into slug-addressable conjectures. Derived
    # rather than passed so that building a `Services` at all runs the slug collision check —
    # including in tests, which construct this directly. A pool that cannot be addressed by
    # stable slug fails here, at startup, rather than serving one conjecture at another's URL.
    index: ConjectureIndex = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "index", ConjectureIndex.build(self.catalog))


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
#   WriterDep            — must be signed in AND pass the CSRF check. Every write.
#
# A handler that changes state and names PrincipalDep instead of WriterDep is a CSRF hole, so
# the names are deliberately not interchangeable-looking.


async def get_optional_principal(
    request: Request, services: ServicesDep, session: SessionDep
) -> Principal | None:
    """Resolve the session cookie, if there is one. Never raises for absence.

    Also rolls the 30-day window when one is due, and re-sends the cookies when it does — a
    cookie whose `Max-Age` is never refreshed expires in the browser while the row is still
    live. The refresh writes, so it is committed here: the read handlers this sits under have
    nothing else to commit and would otherwise roll it back.
    """
    principal = await session_layer.resolve(
        session, request.cookies.get(session_layer.SESSION_COOKIE), now=_now()
    )
    if principal is None:
        return None

    settings = services.settings
    lifetime = timedelta(days=settings.session_days)
    moved = await session_layer.refresh(
        session,
        principal,
        now=_now(),
        lifetime=lifetime,
        refresh_after=timedelta(minutes=settings.session_refresh_minutes),
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
    """
    if not session_layer.csrf_matches(
        principal, request.headers.get(session_layer.CSRF_HEADER)
    ):
        raise Forbidden(
            f"{session_layer.CSRF_HEADER} is missing or does not match this session",
            reason_code=session_layer.REASON_CSRF_FAILED,
        )
    return principal


WriterDep = Annotated[Principal, Depends(require_writer)]


def _now() -> datetime:
    return datetime.now(UTC)


def require_role(role: str):
    """A dependency factory for role-gated routes.

    Unused until the Stage 3 review queue, and defined here so that when it arrives the
    role check is one line in a signature rather than a body-level `if`.
    """

    async def check(principal: PrincipalDep) -> Principal:
        if not principal.has_role(role):
            # Absent rather than forbidden would be better still, but these routes are
            # under /v1/admin and their existence is not a secret.
            raise Forbidden(
                "this endpoint requires a role your account does not have",
                reason_code="ROLE_REQUIRED",
            )
        return principal

    return check
