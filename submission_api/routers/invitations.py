"""Invitation links: the public page that describes one, and the operator surface that issues them.

Two audiences in one module because they are two halves of one feature, and keeping them
adjacent is what stops the refusal codes drifting apart.

**The public half is deliberately thin.** `GET /v1/invitations/{code}` says what a link offers
and writes nothing; `POST /v1/invitations/{code}/redeem` grants the credits to the signed-in
caller. The preflight exists as its own route rather than as part of redeeming because a
recipient who is not signed in yet has to be told what they are signing up for *before* they
create an account — without it the page can offer nothing but "sign in and find out".

**Redeeming is `CookieWriterDep`, not `WriterDep`.** It moves money onto an account, so it obeys
the rule the rest of the credit path obeys: not from a CLI token. A bearer token is minted by a
hotkey, and a hotkey sits unencrypted on a mining box; it is not evidence that the account holder
is present for a grant. See `require_cookie_writer`.

**The operator half is gated on its own router.** Everything under `/v1/admin/invitations`
requires ADMIN, declared once on the router so a route added here later is closed by default
rather than by someone remembering the dependency — the argument `routers/reviews.py` makes. The
writes additionally use `require_role_writer`, which refuses a CLI credential outright.

**The code is returned exactly once.** `POST /v1/admin/invitations` is the only response in this
module that carries a live code, which is why it alone is `no-store`-and-never-logged in the same
way the CLI token endpoint is. Nothing can read it back afterwards: the database holds a digest.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from conjectures_subnet.db import credits as credit_store
from conjectures_subnet.db import invitations as invitation_store
from conjectures_subnet.db.errors import RecordConflict, RecordNotFound
from conjectures_subnet.db.models import ADMIN_ROLE
from submission_api import schemas_account as schemas
from submission_api.dependencies import (
    CookieWriterDep,
    ServicesDep,
    SessionDep,
    require_role,
    require_role_writer,
)
from submission_api.errors import BadRequest, Conflict, Gone, NotFound
from submission_api.observability import get_axiom
from submission_api.sessions import Principal

# The code is a URL path segment, so it is bounded here rather than left to whatever a caller
# sends: `secrets.token_urlsafe(32)` is 43 characters, and the ceiling keeps a megabyte of path
# from reaching the digest function.
CODE_MIN = 16
CODE_MAX = 128

MAX_NOTE = 200
MAX_LISTING = 200

public_router = APIRouter(prefix="/v1/invitations", tags=["invitations"])

admin_router = APIRouter(
    prefix="/v1/admin/invitations",
    tags=["admin"],
    # On the router for the reason `routers/reviews.py` states: a route added later is gated by
    # default rather than by remembering to repeat this. The write routes name
    # `require_role_writer` on top, which also refuses a CLI credential.
    dependencies=[Depends(require_role(ADMIN_ROLE))],
)

# Built once at module scope: `require_role_writer` returns a fresh closure per call and FastAPI
# caches a resolved dependency by function identity, so an inline factory would resolve twice.
AdminWriter = Annotated[Principal, Depends(require_role_writer(ADMIN_ROLE))]

CodePath = Annotated[str, Path(min_length=CODE_MIN, max_length=CODE_MAX)]


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateInvitationRequest(Payload):
    credits: int = Field(
        ge=1,
        le=invitation_store.MAX_GRANT_CREDITS,
        description="Verification attempts one redemption grants.",
    )
    # Stripped *before* the length check, so a note of three spaces is refused here with a 400
    # rather than reaching `invitation_note_present` and surfacing as an unhandled integrity
    # error. Whitespace is not a reason an invitation exists.
    note: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_NOTE)
    ] = Field(
        description="Why this link exists. Required: an unlabelled invitation cannot be audited."
    )
    max_redemptions: int = Field(
        default=1, ge=1, le=10_000, description="How many accounts may redeem it."
    )
    expires_at: dt.datetime | None = None
    email_domain: str | None = Field(
        default=None,
        max_length=253,
        description=(
            "Restrict redemption to verified addresses at this domain. Null means any address."
        ),
    )


class InvitationOffer(schemas.Model):
    """What the public page may say. Carries no code and names no redeemer."""

    credits: int
    remaining: int
    expires_at: dt.datetime | None = None


class RedeemResponse(schemas.Model):
    credits_granted: int
    credits_available: int
    balance_rao: int


class InvitationSummary(schemas.Model):
    id: uuid.UUID
    credits: int
    max_redemptions: int
    redeemed_count: int
    email_domain: str | None = None
    expires_at: dt.datetime | None = None
    revoked_at: dt.datetime | None = None
    note: str
    created_at: dt.datetime


class CreatedInvitation(InvitationSummary):
    """The one response that carries a live code.

    `code` and `url` appear here and in no other response, ever. An operator who loses them
    issues a new invitation; there is no recovery, because the database stores only a digest.
    """

    code: str
    url: str


class RedemptionView(schemas.Model):
    account_id: uuid.UUID
    credit_ledger_id: int | None = None
    created_at: dt.datetime


class InvitationDetail(InvitationSummary):
    redemptions: tuple[RedemptionView, ...] = ()


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Vary"] = "Authorization, Cookie"


def _summary(row) -> InvitationSummary:
    return InvitationSummary(
        id=row.id,
        credits=row.credits,
        max_redemptions=row.max_redemptions,
        redeemed_count=row.redeemed_count,
        email_domain=row.email_domain,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        note=row.note,
        created_at=row.created_at,
    )


def _translate(exc: Exception):
    """Store failures to HTTP, preserving the store's reason code.

    The status codes differ but the words do not: whatever refused the redemption says so with
    the same string the capability surface and the docs use, so "why is this link dead" has one
    answer rather than one per layer.
    """
    if isinstance(exc, invitation_store.InvitationUnusable):
        if exc.reason_code == invitation_store.REASON_EMAIL_DOMAIN:
            # 409 rather than 410: the link is alive and someone else can use it. This caller
            # cannot, which is a fact about the account rather than about the invitation.
            return Conflict(
                exc.message, reason_code=exc.reason_code, extra=dict(exc.details)
            )
        return Gone(exc.message, reason_code=exc.reason_code)
    if isinstance(exc, RecordNotFound):
        return NotFound(exc.message, reason_code=exc.reason_code)
    if isinstance(exc, RecordConflict):
        return Conflict(exc.message, reason_code=exc.reason_code)
    return None


# --- Public --------------------------------------------------------------------------------


@public_router.get(
    "/{code}",
    response_model=InvitationOffer,
    summary="What this invitation offers, without redeeming it",
)
async def read_invitation(
    session: SessionDep, response: Response, code: CodePath
) -> InvitationOffer:
    """Anonymous, and writes nothing.

    No session is required because the page has to render for someone who has not signed up yet
    — that is the whole reason this route is separate from redeeming. It is `no-store` all the
    same: the URL contains a credential, and a shared cache holding the response would hold the
    code alongside it.
    """
    _no_store(response)
    try:
        state = await invitation_store.read(session, code)
    except (RecordNotFound, RecordConflict) as exc:
        raise _translate(exc) or exc from exc

    # Refused here rather than inside `read`, so the store can answer "what is this" for the
    # operator surface without the public rules applied.
    now = _now()
    if state.revoked_at is not None:
        raise Gone(
            "this invitation has been withdrawn",
            reason_code=invitation_store.REASON_REVOKED,
        )
    if state.expires_at is not None and state.expires_at <= now:
        raise Gone(
            "this invitation has expired", reason_code=invitation_store.REASON_EXPIRED
        )
    if state.remaining == 0:
        raise Gone(
            "this invitation has been fully redeemed",
            reason_code=invitation_store.REASON_EXHAUSTED,
        )
    return InvitationOffer(
        credits=state.credits, remaining=state.remaining, expires_at=state.expires_at
    )


@public_router.post(
    "/{code}/redeem",
    response_model=RedeemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Grant this invitation's credits to the signed-in account",
)
async def redeem_invitation(
    principal: CookieWriterDep,
    session: SessionDep,
    services: ServicesDep,
    response: Response,
    code: CodePath,
) -> RedeemResponse:
    """One credit grant, once, in one transaction.

    The price used is the one in force now, not when the link was issued: an invitation stores a
    number of attempts, so the recipient receives the attempts they were promised whatever has
    happened to the price since.
    """
    _no_store(response)
    account_id = principal.account.id
    price = services.settings.payment_amount_rao
    try:
        result = await invitation_store.redeem(
            session,
            code=code,
            account_id=account_id,
            credit_price_rao=price,
            now=_now(),
        )
        await session.commit()
    except (RecordNotFound, RecordConflict) as exc:
        await session.rollback()
        raise _translate(exc) or exc from exc

    # After the commit, and naming the invitation rather than the code. Issuing, revoking and
    # redeeming are the three moments an invitation moves money, and role assignment aside they
    # are the only operator actions that would otherwise leave no trace. The code never appears:
    # it is a live credential, and a log is exactly where one should not be.
    get_axiom().info(
        source="api-invitations",
        event_type="invitation_redeemed",
        invitation_id=str(result.invitation_id),
        invitation_redemption_id=str(result.redemption_id),
        account_id=str(account_id),
        credits=result.credits,
        amount_rao=result.amount_rao,
        credit_ledger_id=result.credit_ledger_id,
    )

    balance = await _balance(session, account_id, price)
    return RedeemResponse(
        credits_granted=result.credits,
        credits_available=balance.credits_available,
        balance_rao=balance.balance_rao,
    )


async def _balance(session, account_id: uuid.UUID, price: int):
    return await credit_store.credit_balance(
        session, account_id, credit_price_rao=price, now=_now()
    )


# --- Operator ------------------------------------------------------------------------------


@admin_router.post(
    "",
    response_model=CreatedInvitation,
    status_code=status.HTTP_201_CREATED,
    summary="Issue an invitation link",
)
async def create_invitation(
    principal: AdminWriter,
    session: SessionDep,
    services: ServicesDep,
    response: Response,
    payload: CreateInvitationRequest,
) -> CreatedInvitation:
    """The only response that contains a code. It cannot be re-read afterwards."""
    _no_store(response)
    now = _now()
    if payload.expires_at is not None and payload.expires_at <= now:
        raise BadRequest("expires_at must be in the future")

    try:
        row, code = await invitation_store.create(
            session,
            credits=payload.credits,
            note=payload.note,
            created_by=principal.account.id,
            max_redemptions=payload.max_redemptions,
            expires_at=payload.expires_at,
            email_domain=payload.email_domain,
        )
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        raise BadRequest(str(exc)) from exc

    get_axiom().info(
        source="api-admin",
        event_type="invitation_issued",
        invitation_id=str(row.id),
        actor_account_id=str(principal.account.id),
        credits=row.credits,
        max_redemptions=row.max_redemptions,
        email_domain=row.email_domain,
    )

    base = services.settings.website_base_url.rstrip("/")
    return CreatedInvitation(
        **_summary(row).model_dump(), code=code, url=f"{base}/invite/{code}"
    )


@admin_router.get(
    "", response_model=tuple[InvitationSummary, ...], summary="List issued invitations"
)
async def list_invitations(
    _: Annotated[Principal, Depends(require_role(ADMIN_ROLE))],
    session: SessionDep,
    response: Response,
    state: Annotated[str | None, Query(pattern="^(active|expired|revoked|exhausted)$")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LISTING)] = 50,
) -> tuple[InvitationSummary, ...]:
    """Counts and state, never codes. There is nothing here that can redeem anything."""
    _no_store(response)
    rows = await invitation_store.listing(
        session, now=_now(), state=state, limit=limit
    )
    return tuple(_summary(row) for row in rows)


@admin_router.get(
    "/{invitation_id}",
    response_model=InvitationDetail,
    summary="One invitation, with who redeemed it",
)
async def read_invitation_detail(
    _: Annotated[Principal, Depends(require_role(ADMIN_ROLE))],
    session: SessionDep,
    response: Response,
    invitation_id: uuid.UUID,
) -> InvitationDetail:
    _no_store(response)
    try:
        row = await invitation_store.get(session, invitation_id)
    except RecordNotFound as exc:
        raise NotFound(exc.message, reason_code=exc.reason_code) from exc
    used = await invitation_store.redemptions_for(session, invitation_id)
    return InvitationDetail(
        **_summary(row).model_dump(),
        redemptions=tuple(
            RedemptionView(
                account_id=item.account_id,
                credit_ledger_id=item.credit_ledger_id,
                created_at=item.created_at,
            )
            for item in used
        ),
    )


@admin_router.delete(
    "/{invitation_id}",
    response_model=InvitationSummary,
    summary="Withdraw an invitation",
)
async def revoke_invitation(
    principal: AdminWriter,
    session: SessionDep,
    response: Response,
    invitation_id: uuid.UUID,
) -> InvitationSummary:
    """Soft, and idempotent.

    Never a DELETE of the row: ledger entries reach it through their redemptions, so removing it
    would orphan the explanation for credits already granted. Re-revoking keeps the original
    timestamp, because when the link stopped working is the fact worth preserving.
    """
    _no_store(response)
    try:
        row = await invitation_store.revoke(session, invitation_id, now=_now())
        await session.commit()
    except RecordNotFound as exc:
        await session.rollback()
        raise NotFound(exc.message, reason_code=exc.reason_code) from exc

    get_axiom().info(
        source="api-admin",
        event_type="invitation_revoked",
        invitation_id=str(row.id),
        actor_account_id=str(principal.account.id),
        redeemed_count=row.redeemed_count,
    )
    return _summary(row)
