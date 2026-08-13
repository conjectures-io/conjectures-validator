"""The reviewer's queue: submissions awaiting a reward decision, and what the models said.

Read-only, and role-gated at the router rather than per route. Every path here answers with model
prose about a named miner's submission, so there is no mixed surface to reason about: if the caller
does not hold `REVIEWER`, none of it is served.

**Why these live in this API rather than beside the panel.** The review panel is a page in the
public site's Next.js app, which reaches exactly one API base and forwards the caller's session
cookie on server-side fetches. Serving the queue from a second service would mean a second base URL,
a second credential and a second database login for data the session already authorises. The role
seam this uses was written for this queue — see `dependencies.require_role`.

**Composed, not re-implemented.** The submission half of every response comes from
`conjectures_subnet.db.public`, the same queries `/v1/results/in-review` reads, and the conjecture
is named by `results.named_of`, the same function the public feed uses. Only the advisory half is
new. A reviewer and a visitor therefore cannot be shown different answers to "which submissions are
awaiting review" or "what is this conjecture called".

**Nothing here writes.** Recording a decision advances `reward_status` and is the one action on a
submission that spends money; it belongs with the service that already owns that transaction, not
with a queue view. So this module has no POST, and adding one would need the concurrency argument
in `conjectures-review`'s `docs/api.md` answered again rather than assumed.

The queue lists `UNREVIEWED` submissions. The detail route serves any Lean-verified one, decided or
not, so a reviewer can reread the advisory record behind a decision already taken — the same
asymmetry `public_result` has, and for the same reason: a wider list would change what the queue
means, while reading one row by id changes nothing.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Response

from conjectures_subnet.db import autoreview as autoreview_store
from conjectures_subnet.db import digests
from conjectures_subnet.db import public as public_store
from conjectures_subnet.db.models import REVIEWER_ROLE
from submission_api import schemas_admin as admin
from submission_api.conjectures import ConjectureIndex
from submission_api.dependencies import ServicesDep, SessionDep, require_role
from submission_api.errors import NotFound
from submission_api.pagination import decode_cursor, encode_cursor
from submission_api.routers.results import named_of, slug_of
from submission_api.schemas_public import CursorPage
from submission_api.settings import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Settings

router = APIRouter(
    prefix="/v1/admin",
    tags=["admin"],
    # On the router, so a route added later is gated by default rather than by remembering to
    # repeat the dependency. There is nothing on this prefix an anonymous caller may read.
    dependencies=[Depends(require_role(REVIEWER_ROLE))],
)


def _no_store(response: Response) -> None:
    """Never cached, anywhere.

    The public feeds set `public, max-age=...` because their bodies are the same for every caller.
    These bodies are not: they are authorised by a session cookie and carry unpublished review
    material, so a shared cache in front of this API must not keep one reviewer's page to answer the
    next request. `no-store` rather than `private` because the deployment puts nginx in front of the
    site, and `private` still permits the browser disk cache to keep it.
    """
    response.headers["Cache-Control"] = "no-store"


def _search(raw: dict[str, Any] | None) -> admin.AdminSearch | None:
    if raw is None:
        return None
    return admin.AdminSearch(
        id=raw.get("id"), engine=raw.get("engine"), max_results=raw.get("max_results")
    )


def _verdict(raw: dict[str, Any] | None) -> admin.AdminVerdict | None:
    """The archived verdict, field by named field.

    Named rather than splatted: `**raw` would forward whatever `conjectures-autoreview` writes into
    the column next, and `extra="forbid"` would then turn a new field upstream into a 500 here. This
    way a new field is invisible until someone adds a line, which is the failure mode worth having.
    """
    if raw is None:
        return None
    return admin.AdminVerdict(
        reason_code=raw.get("reason_code"),
        confidence=raw.get("confidence"),
        summary=raw.get("summary"),
        findings=tuple(
            admin.AdminFinding(
                claim=item.get("claim"),
                quote=item.get("quote"),
                where=item.get("where"),
                severity=item.get("severity"),
            )
            for item in raw.get("findings") or ()
        ),
        input_attempted_to_instruct=raw.get("input_attempted_to_instruct"),
        informal_reading=raw.get("informal_reading"),
        formal_reading=raw.get("formal_reading"),
        settled_portion=raw.get("settled_portion"),
        definitions_not_shown=tuple(raw.get("definitions_not_shown") or ()),
        target_reading=raw.get("target_reading"),
        searched_for=tuple(raw.get("searched_for") or ()),
        prior_sources=tuple(
            admin.AdminPriorSource(
                url=item.get("url"),
                title=item.get("title"),
                published=item.get("published"),
                date_evidence=item.get("date_evidence"),
                establishes=item.get("establishes"),
                resolves_this_target=item.get("resolves_this_target"),
                correspondence=item.get("correspondence"),
            )
            for item in raw.get("prior_sources") or ()
        ),
        catalogue_signal=raw.get("catalogue_signal"),
    )


def _attempt(row: autoreview_store.AttemptRow) -> admin.AdminStageAttempt:
    return admin.AdminStageAttempt(
        key=row.key,
        submission_id=row.submission_id,
        attempt=row.attempt,
        stage=row.stage,
        stage_version=row.stage_version,
        # The enum's value, matching how every other status on this API serialises.
        status=str(row.status),
        detail=row.detail,
        outcome=None if row.outcome is None else str(row.outcome),
        model_requested=row.model_requested,
        model_served=row.model_served,
        provider=row.provider,
        search=_search(dict(row.search) if row.search is not None else None),
        usage=admin.AdminUsage(
            prompt_tokens=row.prompt_tokens, completion_tokens=row.completion_tokens
        ),
        # `str`, not `float`: six decimal places of a NUMERIC column survive a string and do not
        # survive a float, and this is what an operator reconciles a provider invoice against.
        cost_usd=None if row.cost_usd is None else str(row.cost_usd),
        citations=tuple(
            admin.AdminCitation(
                url=item.get("url"),
                title=item.get("title"),
                retrieved_at=item.get("retrieved_at"),
            )
            for item in row.citations
        ),
        verdict=_verdict(dict(row.verdict) if row.verdict is not None else None),
        review_policy_version=row.review_policy_version,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def _review(
    row: public_store.ResultRow,
    index: ConjectureIndex,
    attempts: tuple[autoreview_store.AttemptRow, ...],
) -> admin.AdminReview:
    named = named_of(index, row)
    return admin.AdminReview(
        submission_id=row.id,
        slug=slug_of(row),
        display_title=named.display_title,
        task_id=row.task_id,
        hotkey=row.hotkey,
        statement=named.statement,
        task_bundle_sha256=digests.to_prefixed(row.task_bundle_sha256),
        verified_at=row.verified_at,
        review_policy_version=row.review_policy_version,
        report_available=row.report_available,
        manual_review_status=str(row.manual_review_status),
        attempts=tuple(_attempt(attempt) for attempt in attempts),
    )


@router.get(
    "/reviews",
    response_model=CursorPage[admin.AdminReview],
    summary="Submissions awaiting a reward decision, with every advisory assessment recorded",
)
async def list_reviews(
    response: Response,
    services: ServicesDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=256)] = None,
) -> CursorPage[admin.AdminReview]:
    """Lean-verified submissions with no binding decision yet, newest first.

    The assessments are embedded rather than fetched per row. The panel renders a verdict cell per
    stage on the queue itself and expands the full attempt inline, so a list of submissions alone
    would be followed immediately by one request per row — and the whole page costs one extra query
    here, keyed by the ids already read.

    Ordered newest first, like every other feed in this API, and paged by the same signed keyset
    cursor. Oldest-first would be the fairer queue order, and the panel sorts by its own attention
    score anyway; matching the rest of the API is worth more than a default the client overrides.

    A submission the advisory service has not reached carries `attempts: []`. It belongs on the
    queue regardless: review is required for it, and a reviewer may decide it unaided.
    """
    settings: Settings = services.settings
    after = None
    if cursor:
        position = decode_cursor(settings.cursor_secret, cursor)
        after = (position.created_at, position.id)

    # `limit + 1`, so `next_cursor` is null exactly when the feed is exhausted rather than
    # addressing an empty page — the same trick `results._feed` uses.
    rows = await public_store.in_review_page(session, limit=limit + 1, after=after)
    page = rows[:limit]
    attempts = await autoreview_store.attempts_for(session, [row.id for row in page])

    next_cursor = None
    if len(rows) > limit and page:
        last = page[-1]
        next_cursor = encode_cursor(
            settings.cursor_secret, created_at=last.created_at, id=last.id
        )

    _no_store(response)
    return CursorPage[admin.AdminReview](
        items=tuple(
            _review(row, services.index, attempts.get(row.id, ())) for row in page
        ),
        next_cursor=next_cursor,
    )


@router.get(
    "/reviews/{submission_id}",
    response_model=admin.AdminReview,
    summary="One submission's full advisory record, whether or not it is still awaiting review",
)
async def read_review(
    response: Response,
    services: ServicesDep,
    session: SessionDep,
    submission_id: Annotated[uuid.UUID, Path()],
) -> admin.AdminReview:
    """One submission and every assessment recorded against it.

    Serves any Lean-verified submission, not only the ones still on the queue: the advisory record
    behind a decision already taken is exactly what somebody asks for when a decision is questioned,
    and withholding it after the fact would make the queue the only place it could ever be read.

    A submission Lean has not verified answers `404` rather than `403`, matching
    `GET /v1/results/{id}`. There is no advisory record to serve for unverified work — the service
    does not assess it — so the distinction would only tell a caller that an id exists.
    """
    row = await public_store.public_result(session, submission_id)
    if row is None:
        raise NotFound("no such submission")
    attempts = await autoreview_store.attempts_for(session, [row.id])

    _no_store(response)
    return _review(row, services.index, attempts.get(row.id, ()))
