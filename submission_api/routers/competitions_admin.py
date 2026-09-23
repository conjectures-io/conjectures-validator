"""The operator surface for competitions: what is stuck, and putting it back.

This exists because `competition_worker` promises it. When the gate fails to produce a
verdict `max_attempts` times the worker gives up, marks the submission `error` and writes
into the report the miner will read:

    ERROR: the gate failed to produce a verdict on N attempts; an operator has been asked
    to look.

Until this module there was nowhere to look. The queue was visible only through `psql`, and
putting a submission back required an `UPDATE` typed by hand against a table whose state
column three other processes also write. A promise in a miner-visible message with no way to
keep it is worse than not making it.

Three endpoints, and deliberately no more:

* **the queue** -- errors, plus claims older than a gate run, newest first;
* **one submission in full** -- the same row the public endpoint serves, plus the queue
  bookkeeping that endpoint omits: which worker holds it, how many times it has been
  claimed, whether its files are still there;
* **requeue** -- put one back, as if never claimed.

What is *not* here matters as much. There is no way to accept, reject or rescore a
submission, and no way to edit one. A verdict is the gate's, arrived at by proving a Lean
theorem and measuring a corpus, and an operator who could set `state = 'accepted'` by hand
could spend a registration and move the leaderboard without any of that having happened.
The one intervention offered is the one that cannot forge anything: run it again.

Addressed at `/v1/competitions/{slug}/admin/...`, not under `/v1/admin`. That prefix is the
proofs platform's operator surface -- accounts, reviews, invitations -- over the other
database; this is the competition's, and the whole competition lives under one prefix. A
separate module from `routers/competitions.py` all the same, so nothing public and nothing
role-gated shares an import list.

ADMIN rather than REVIEWER: a reviewer's judgement is about mathematics, and there is none
to exercise here. Requeuing is an operational act on the validator's own queue, which is the
line `routers/admin.py` already draws. And as on every role-gated route, `require_role_writer`
refuses a bearer token, so this cannot be driven from a token read off a mining rig.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response

from conjectures_subnet.axiom import get_axiom
from conjectures_subnet.competition import iso, queries
from conjectures_subnet.competition import models as competition_models
from conjectures_subnet.db.models import ADMIN_ROLE
from submission_api import schemas_competitions as schemas
from submission_api.dependencies import (
    CompetitionSessionDep,
    ServicesDep,
    require_role,
    require_role_writer,
)
from submission_api.errors import NotFound
from submission_api.competition_pagination import (
    CursorQuery,
    LimitQuery,
    feed_after,
    feed_cursor,
    split_page,
)
from submission_api.routers.competitions import resolve_competition
from submission_api.sessions import Principal
from submission_api.settings import DEFAULT_PAGE_SIZE

# Under the competition's own prefix, not `/v1/admin`. `/v1/admin` is the proofs platform's
# operator surface -- accounts, reviews, invitations -- and nothing there reads this
# database. Putting the competition's queue beside them would make `/v1/admin` two products'
# operator surfaces sharing a prefix and nothing else, while splitting the competition across
# two prefixes. The protection does not live in the prefix anyway: every route here takes
# `require_role` or `require_role_writer`, and nothing in the proxy, the CORS layer or the
# write guard keys on `/v1/admin` -- CORS is scoped to all of `/v1`, and the write guard
# applies to every state-changing request wherever it is addressed.
router = APIRouter(prefix="/v1/competitions", tags=["competitions", "admin"])

SlugPath = Path(description="The competition's slug", max_length=64)

# Built once at module scope rather than inline in two signatures, for the reason
# `routers/admin.py` gives: `require_role` returns a fresh closure per call and FastAPI caches
# a resolved dependency by function identity, so an inline factory resolves twice per request.
AdminReader = Annotated[Principal, Depends(require_role(ADMIN_ROLE))]
AdminWriter = Annotated[Principal, Depends(require_role_writer(ADMIN_ROLE))]


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Vary"] = "Authorization, Cookie"


def _operator_view(
    competition, row: competition_models.Submission, has_sources: bool
) -> schemas.OperatorSubmission:
    return schemas.OperatorSubmission(
        competition=competition.slug,
        id=row.id,
        hotkey=row.hotkey,
        digest=row.digest,
        submitted_at=iso(row.submitted_at),
        state=row.state,
        exit_code=row.exit_code,
        attempts=row.attempts,
        worker_id=row.worker_id,
        claimed_at=iso(row.claimed_at) if row.claimed_at else None,
        finished_at=iso(row.finished_at) if row.finished_at else None,
        account_id=str(row.account_id) if row.account_id else None,
        # Computed by the query, not read off the row: the two source columns are deferred
        # on the model precisely so a listing does not carry up to a megabyte per row it
        # never looks at. Postgres answers "are they there" without sending them.
        has_sources=has_sources,
        report=row.report,
    )


@router.get(
    "/{slug}/admin/queue",
    response_model=schemas.OperatorSubmissionPage,
    summary="Submissions an operator has been asked to look at",
)
async def queue(
    response: Response,
    principal: AdminReader,
    services: ServicesDep,
    session: CompetitionSessionDep,
    slug: str = SlugPath,
    limit: LimitQuery = DEFAULT_PAGE_SIZE,
    cursor: CursorQuery = None,
) -> schemas.OperatorSubmissionPage:
    """What the gate could not finish: abandoned submissions and dead claims.

    Two populations, one question. A row in `error` is one the worker gave up on after
    `max_attempts` -- the case whose report says an operator has been asked to look. A row
    still `verifying` whose claim is older than `COMPETITION_STALE_CLAIM_SECONDS` is a worker
    that died mid-gate; the worker's own sweep normally reclaims those, so anything that
    lingers here means no worker is running at all, which is the more urgent reading.

    Deliberately not "every submission": that feed is public at
    `/v1/competitions/{slug}/submissions`, and an operator surface which duplicates a public
    one is a second thing to maintain and a larger credential to steal. This is the subset
    that needs a decision.
    """
    _no_store(response)
    competition = resolve_competition(services, slug)
    settings = services.settings
    secret = settings.cursor_secret
    claimed_before = datetime.now(UTC) - timedelta(
        seconds=settings.competition_stale_claim_seconds
    )
    rows = await queries.stuck_submissions(
        session,
        claimed_before=claimed_before,
        after=feed_after(secret, cursor),
        limit=limit + 1,
    )
    page, more = split_page(rows, limit)
    return schemas.OperatorSubmissionPage(
        items=tuple(_operator_view(competition, row, flag) for row, flag in page),
        next_cursor=feed_cursor(secret, page[-1][0]) if more and page else None,
    )


@router.get(
    "/{slug}/admin/submissions/{submission_id}",
    response_model=schemas.OperatorSubmission,
    summary="One submission, with its queue bookkeeping",
)
async def read_submission(
    response: Response,
    principal: AdminReader,
    services: ServicesDep,
    session: CompetitionSessionDep,
    slug: str = SlugPath,
    submission_id: int = Path(ge=1),
) -> schemas.OperatorSubmission:
    """The same submission the public endpoint serves, plus how the queue has treated it.

    Any state, not only a stuck one: the question an operator asks after a requeue is
    whether it worked, and having to switch endpoints to find out is how you end up reading
    the queue listing as if it were the whole truth.
    """
    _no_store(response)
    competition = resolve_competition(services, slug)
    found = await queries.submission_for_operator(session, submission_id)
    if found is None:
        raise NotFound("no such submission")
    row, has_sources = found
    return _operator_view(competition, row, has_sources)


@router.post(
    "/{slug}/admin/submissions/{submission_id}/requeue",
    response_model=schemas.Requeued,
    summary="Put a stuck submission back in the queue",
)
async def requeue(
    response: Response,
    principal: AdminWriter,
    services: ServicesDep,
    session: CompetitionSessionDep,
    slug: str = SlugPath,
    submission_id: int = Path(ge=1),
    reason: Annotated[str | None, Query(max_length=200)] = None,
) -> schemas.Requeued:
    """Return one submission to `queued`, clearing its claim and its attempt count.

    Only from `error` or `verifying`, and the database predicate is the guard rather than a
    check in this handler -- so a worker finishing a claim between the read and the write
    loses the race cleanly and updates nothing. `accepted` and `rejected` are refused
    outright: an accept has spent a registration and a reject is a verdict the miner has
    already been shown, and re-running either would double-spend a slot or quietly replace
    an answer someone has acted on.

    `attempts` resets to zero, because the cap exists to force exactly this event and
    leaving the count would put the submission back over it on its next claim.

    `requeued: false` rather than a 409 when the state did not permit it. The caller's
    question is "is this queued now", the answer is no and the response says which state it
    is in instead -- and an operator retrying a requeue that already happened should get the
    same answer the second time.
    """
    _no_store(response)
    competition = resolve_competition(services, slug)
    row = await queries.get_submission(session, submission_id)
    if row is None:
        raise NotFound("no such submission")

    before = row.state
    done = await queries.requeue(session, submission_id)
    await session.commit()
    after = await queries.get_submission(session, submission_id)

    # After the commit. `submissions.state` is overwritten in place and carries no history,
    # so without this event there is no answer to "who put this back, and why" -- the same
    # reasoning `routers/admin.py` gives for logging a role change. Ids and hotkeys, never
    # the submitted files.
    get_axiom().warn(
        source="api-competitions",
        event_type="submission_requeued",
        competition=competition.slug,
        submission_id=submission_id,
        hotkey=row.hotkey,
        actor_account_id=str(principal.account.id),
        state_before=before,
        state_after=after.state if after else before,
        attempts_before=row.attempts,
        requeued=done,
        reason=reason or "",
    )
    return schemas.Requeued(
        competition=competition.slug,
        id=submission_id,
        requeued=done,
        state=after.state if after else before,
    )


__all__ = ["router"]
