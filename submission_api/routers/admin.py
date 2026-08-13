"""The operator surface: who holds which role, and cutting a compromised account off.

Small on purpose. This is not an admin panel — it is the two things that cannot be done any
other way once the system is running, and every one of them is an action taken *against another
account*, which is what makes the rules below stricter than anywhere else in the API.

**Roles are the whole of the IAM model.** `accounts.roles` is a `TEXT[]` constrained to
`{MINER, REVIEWER, ADMIN}`, and that is deliberately not a permission system. Three values, no
attributes on the relation, and every read wants all of them at once, so a join table and a
policy engine would both be machinery with nothing to hold. What a role means is decided at the
point of use — `require_role(...)` in a route signature — rather than in a table of grants that
has to be kept in step with the code that consults it.

Four rules, each of which is a decision rather than an accident:

* **ADMIN cannot be exercised from a CLI session.** `require_role_writer` refuses it. A bearer
  token is minted by a hotkey, which Bittensor stores unencrypted on disk; an admin credential
  must not be reachable by reading one file off a mining box. Privileged work happens in a
  browser, behind a coldkey signature or a mailbox, with an HttpOnly cookie and CSRF.
* **There is no bootstrap endpoint.** The first ADMIN is granted with SQL, by someone with
  database access — see `scripts/grant_admin.sql`. An endpoint that could mint the first admin
  is an endpoint that can mint the second one, and its access control would have to be some
  other secret that then needs its own rotation story.
* **An admin cannot remove their own ADMIN role.** Not paternalism: with no other admin, the
  role becomes unrecoverable without database access, and the failure is silent until the next
  time somebody needs it.
* **Every grant is an Axiom event naming both accounts.** A role change is the one write here
  that leaves no other trace — `accounts.roles` is overwritten in place, so without the event
  there is no answer to "who made this account a reviewer, and when".

Reads of other accounts are deliberately absent. There is no `GET /v1/admin/accounts` listing
every email address on the platform, because nothing here needs one and it would be the single
most valuable object in the system to anyone who got an admin session.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from conjectures_subnet.axiom import get_axiom
from conjectures_subnet.db import accounts as account_store
from conjectures_subnet.db.errors import RecordNotFound
from conjectures_subnet.db.models import (
    ACCOUNT_ROLES,
    ADMIN_ROLE,
    AccountSessionKind,
)
from submission_api import schemas_account as schemas
from submission_api.dependencies import (
    SessionDep,
    require_role,
    require_role_writer,
)
from submission_api.errors import BadRequest, Conflict, NotFound
from submission_api.routers._account import account_response, session_view
from submission_api.sessions import Principal

router = APIRouter(prefix="/v1/admin", tags=["admin"])

UUID_LENGTH = 36

REASON_LAST_ADMIN = "CANNOT_REMOVE_OWN_ADMIN"

# Built once at module scope rather than per route. `require_role` returns a fresh closure each
# call, and FastAPI caches a resolved dependency per request by function identity — so a factory
# invoked inline in two signatures would resolve twice for one request.
AdminReader = Annotated[Principal, Depends(require_role(ADMIN_ROLE))]
AdminWriter = Annotated[Principal, Depends(require_role_writer(ADMIN_ROLE))]


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RolesRequest(Payload):
    """The complete role set, not a delta.

    PUT-the-set rather than grant/revoke, because the set is what the column stores and what
    every read wants. A delta API over a three-element array would invent a lost-update problem
    that replacing the whole value does not have.
    """

    roles: tuple[str, ...] = Field(
        max_length=len(ACCOUNT_ROLES),
        description="Any of MINER, REVIEWER, ADMIN. MINER is always retained.",
    )


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Vary"] = "Authorization, Cookie"


def _as_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise BadRequest("account id must be a UUID") from exc


async def _load(session, account_id: str):
    try:
        return await account_store.get_account(session, _as_uuid(account_id))
    except RecordNotFound as exc:
        raise NotFound("no such account", reason_code="ACCOUNT_NOT_FOUND") from exc


@router.get(
    "/accounts/{account_id}",
    response_model=schemas.Account,
    summary="One account, in full",
)
async def read_account(
    account_id: Annotated[str, Path(min_length=UUID_LENGTH, max_length=UUID_LENGTH)],
    response: Response,
    principal: AdminReader,
    session: SessionDep,
) -> schemas.Account:
    """Addressed by id only.

    No lookup by email, and no listing. Both would turn an admin session into a way to
    enumerate the platform's users, and neither is needed to do the job this router exists for:
    an operator acting on a specific account already has its id, from a support request or from
    an event.
    """
    _no_store(response)
    account = await _load(session, account_id)
    return await account_response(session, account)


@router.put(
    "/accounts/{account_id}/roles",
    response_model=schemas.Account,
    summary="Replace an account's roles",
)
async def put_roles(
    account_id: Annotated[str, Path(min_length=UUID_LENGTH, max_length=UUID_LENGTH)],
    payload: RolesRequest,
    response: Response,
    principal: AdminWriter,
    session: SessionDep,
) -> schemas.Account:
    """Set the role set. MINER is always retained; unknown roles are a 409.

    Removing your own ADMIN is refused. If you are the only admin it is unrecoverable without
    database access, and the check cannot tell "only admin" from "one of several" without a
    count that races anything happening concurrently — so it refuses the self-demotion outright
    and leaves the two-admin case to the other admin, which is the situation that should exist
    before anyone gives up the role anyway.
    """
    _no_store(response)
    account = await _load(session, account_id)
    requested = {role.strip().upper() for role in payload.roles if role.strip()}

    if account.id == principal.account.id and ADMIN_ROLE not in requested:
        raise Conflict(
            "an admin cannot remove their own ADMIN role; have another admin do it",
            reason_code=REASON_LAST_ADMIN,
        )

    before = tuple(account.roles or ())
    # Raises RecordConflict for an unknown role, which the database error handler maps to 409.
    await account_store.set_roles(session, account, sorted(requested))
    after = tuple(account.roles or ())
    await session.commit()

    # After the commit, and naming both accounts. `accounts.roles` is overwritten in place, so
    # this event is the only record that the change happened at all — there is no history table
    # behind it and the row shows only the current state. Ids, never the email address: the
    # privacy rule the auth router sets out applies with more force here, because the subject of
    # this event is someone other than the caller.
    get_axiom().info(
        source="api-admin",
        event_type="roles_changed",
        account_id=str(account.id),
        actor_account_id=str(principal.account.id),
        roles_before=list(before),
        roles_after=list(after),
    )
    return await account_response(session, account)


@router.get(
    "/accounts/{account_id}/sessions",
    response_model=tuple[schemas.SessionView, ...],
    summary="An account's live sessions",
)
async def list_account_sessions(
    account_id: Annotated[str, Path(min_length=UUID_LENGTH, max_length=UUID_LENGTH)],
    response: Response,
    principal: AdminReader,
    session: SessionDep,
) -> tuple[schemas.SessionView, ...]:
    """What is live for an account, for answering "I think I have been compromised".

    `current` is always false here: the caller's own session belongs to the admin, not to the
    account being inspected, so no row in this list can be it.
    """
    _no_store(response)
    account = await _load(session, account_id)
    rows = await account_store.live_sessions_for(session, account.id, now=_now())
    return tuple(session_view(row, current_id=None) for row in rows)


@router.delete(
    "/accounts/{account_id}/sessions",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an account's sessions",
)
async def revoke_account_sessions(
    account_id: Annotated[str, Path(min_length=UUID_LENGTH, max_length=UUID_LENGTH)],
    response: Response,
    principal: AdminWriter,
    session: SessionDep,
    kind: Annotated[
        str | None,
        Query(
            pattern="^(COOKIE|BEARER)$",
            description="Limit to one kind. Omit to revoke both.",
        ),
    ] = None,
) -> None:
    """Cut every live credential for an account, or every one of a kind.

    Unlike the self-service version under `/v1/me`, nothing is spared: the caller's own session
    is not among them, and an operator invoking this has already decided the account's
    credentials are not to be trusted. 204 whether or not anything was live, because "revoke
    everything" is a statement about the end state, and a caller who has to retry should get the
    same answer the second time.
    """
    _no_store(response)
    account = await _load(session, account_id)
    revoked = await account_store.revoke_all_sessions(
        session,
        account.id,
        kind=AccountSessionKind(kind) if kind else None,
    )
    await session.commit()
    get_axiom().warn(
        source="api-admin",
        event_type="session_revoked",
        account_id=str(account.id),
        actor_account_id=str(principal.account.id),
        reason="admin_action",
        kind=kind or "ALL",
        revoked=revoked,
    )


__all__ = ["router"]
