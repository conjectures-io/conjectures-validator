"""The operator surface for competitions: what is stuck, and putting it back.

Three endpoints, and deliberately no more:

* **the queue** -- submissions in `error`, and claims older than a gate run;
* **one submission in full** -- the public view plus the queue bookkeeping it omits;
* **requeue** -- put one back, as if never claimed.

There is no way to accept, reject, rescore or edit a submission. A verdict is the gate's,
reached by proving a Lean theorem and measuring a corpus; an operator who could set it by hand
could spend a registration and move the board without any of that happening. The one
intervention offered is the one that cannot forge anything: run it again.

Each is an optional adapter capability, so a competition without an operator queue answers
404 `NOT_SUPPORTED` here and needs no code to say so.

Addressed at `/v1/competitions/{slug}/admin/...`, not `/v1/admin`, which is the proofs
platform's operator surface over the other database. ADMIN rather than REVIEWER: requeuing is an
operational act, not a mathematical judgement. `require_role_writer` refuses a bearer token, as
on every role-gated write.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from conjectures_subnet.axiom import get_axiom
from conjectures_subnet.db.models import ADMIN_ROLE
from submission_api import schemas_competitions as schemas
from submission_api.competitions import Competition, Unsupported
from submission_api.competitions import base
from submission_api.competitions.pagination import (
    CursorQuery,
    LimitQuery,
    decode,
    encode,
    split_page,
)
from submission_api.dependencies import ServicesDep, require_role, require_role_writer
from submission_api.errors import BadRequest, NotFound
from submission_api.pagination import REASON_INVALID_CURSOR
from submission_api.routers.competitions import (
    CompetitionDep,
    CompetitionSessionDep,
    SubmissionIdPath,
    _iso,
    _submission_id,
    _view,
)
from submission_api.sessions import Principal
from submission_api.settings import DEFAULT_PAGE_SIZE

router = APIRouter(prefix="/v1/competitions", tags=["competitions", "admin"])

# Built once at module scope: `require_role` returns a fresh closure per call and FastAPI caches
# a resolved dependency by function identity, so an inline factory would resolve twice.
AdminReader = Annotated[Principal, Depends(require_role(ADMIN_ROLE))]
AdminWriter = Annotated[Principal, Depends(require_role_writer(ADMIN_ROLE))]


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Vary"] = "Authorization, Cookie"


def _unsupported(exc: Unsupported) -> NotFound:
    return NotFound(str(exc), reason_code="NOT_SUPPORTED")


def _operator(competition: Competition, row: base.OperatorView) -> schemas.OperatorSubmission:
    return schemas.OperatorSubmission(
        competition=competition.slug,
        submission=_view(competition, row.submission),
        worker_id=row.worker_id,
        claimed_at=_iso(row.claimed_at) if row.claimed_at else None,
        exit_code=row.exit_code,
        has_files=row.has_files,
        report=row.report,
    )


@router.get(
    "/{slug}/admin/queue",
    response_model=schemas.OperatorSubmissionPage,
    summary="Submissions an operator should look at",
)
async def queue(
    response: Response,
    principal: AdminReader,
    services: ServicesDep,
    competition: CompetitionDep,
    session: CompetitionSessionDep,
    limit: LimitQuery = DEFAULT_PAGE_SIZE,
    cursor: CursorQuery = None,
) -> schemas.OperatorSubmissionPage:
    """What the gate could not finish: errors, and claims older than a gate run.

    A stale claim normally goes back to the queue by the competition worker's own sweep, so one
    that lingers here means no worker is running -- the more urgent reading. Not every
    submission: that feed is public, and duplicating it behind a role would only make a larger
    credential worth stealing.
    """
    _no_store(response)
    secret = services.settings.cursor_secret
    claimed_before = datetime.now(UTC) - timedelta(
        seconds=services.settings.competition_stale_claim_seconds
    )
    after = decode(secret, "ops", competition.slug, cursor)
    try:
        rows = list(
            await competition.adapter.stuck(
                session, claimed_before=claimed_before, after=after, limit=limit + 1
            )
        )
    except Unsupported as exc:
        raise _unsupported(exc) from exc
    except (ValueError, TypeError) as exc:
        raise BadRequest(
            "cursor is not one this API issued", reason_code=REASON_INVALID_CURSOR
        ) from exc
    page, more = split_page(rows, limit)
    return schemas.OperatorSubmissionPage(
        items=tuple(_operator(competition, row) for row in page),
        next_cursor=encode(secret, "ops", competition.slug, page[-1].submission.position)
        if more and page
        else None,
    )


@router.get(
    "/{slug}/admin/submissions/{submission_id}",
    response_model=schemas.OperatorSubmission,
    summary="One submission, with its queue bookkeeping",
)
async def read_submission(
    response: Response,
    principal: AdminReader,
    competition: CompetitionDep,
    session: CompetitionSessionDep,
    submission_id: str = SubmissionIdPath,
) -> schemas.OperatorSubmission:
    """Any state, not only a stuck one: after a requeue the question is whether it worked."""
    _no_store(response)
    try:
        found = await competition.adapter.operator_view(
            session, _submission_id(competition, submission_id)
        )
    except Unsupported as exc:
        raise _unsupported(exc) from exc
    if found is None:
        raise NotFound("no such submission")
    return _operator(competition, found)


@router.post(
    "/{slug}/admin/submissions/{submission_id}/requeue",
    response_model=schemas.Requeued,
    summary="Put a stuck submission back in the queue",
)
async def requeue(
    response: Response,
    principal: AdminWriter,
    competition: CompetitionDep,
    session: CompetitionSessionDep,
    submission_id: str = SubmissionIdPath,
    reason: Annotated[str | None, Query(max_length=200)] = None,
) -> schemas.Requeued:
    """Return one submission to `queued`, from `error` or `verifying` only.

    `requeued: false` rather than a 409 when its state does not permit it: the caller's question
    is "is it queued now", and a retried requeue should get the same answer twice.
    """
    _no_store(response)
    parsed = _submission_id(competition, submission_id)
    before = await competition.adapter.submission(session, parsed)
    if before is None:
        raise NotFound("no such submission")
    try:
        done = await competition.adapter.requeue(session, parsed)
    except Unsupported as exc:
        raise _unsupported(exc) from exc
    after = await competition.adapter.submission(session, parsed)
    state = (after or before).state.value
    # The competition's state column is overwritten in place, so without this there is no
    # answer to "who put this back, and why". Ids and hotkeys, never files.
    get_axiom().warn(
        source="api-competitions",
        event_type="competition_submission_requeued",
        competition=competition.slug,
        submission_id=parsed,
        hotkey=before.hotkey or "",
        actor_account_id=str(principal.account.id),
        state_before=before.state.value,
        state_after=state,
        requeued=done,
        reason=reason or "",
    )
    return schemas.Requeued(competition=competition.slug, id=parsed, requeued=done, state=state)


__all__ = ["router"]
