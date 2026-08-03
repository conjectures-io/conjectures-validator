"""Public results: what has been certified, and what is waiting on review.

The strictest disclosure surface in the API. Three rules, each enforced structurally rather than
by remembering to omit a field:

* **No author credit, ever.** `conjectures_subnet.db.public.ResultRow` has no hotkey, coldkey,
  payment reference or extrinsic on it, so this module cannot publish one — it was never handed
  one. Results are attributed to conjectures.io.
* **No proof bytes.** `Main.lean` is not served here at any state. An in-review result carries no
  artifact at all: the proof has passed the Lean kernel but not the reward decision, and handing
  the artifact out before that decision would let anyone take a pending result elsewhere.
* **The report is an allowlist, not a redaction.** `_public_report` names the fields it copies.
  `stdout_tail` and `stderr_tail` are excluded because Lean's output quotes the submitted proof
  back verbatim — but the point of the allowlist is that a field added to `VerificationReport`
  later is withheld by default rather than published because nobody remembered to add it to a
  denylist.

`GET /v1/results/{id}` answers `404` for a submission that is not on a public feed, rather than
`403`. A submission id is a UUID a miner holds; distinguishing "not published" from "does not
exist" would turn this endpoint into a probe for the state of submissions that have not been
published yet.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Callable

from fastapi import APIRouter, Path, Query, Response

from conjectures_subnet.db import digests
from conjectures_subnet.db import public as public_store
from submission_api import conjectures, schemas_public as public
from submission_api.dependencies import ServicesDep, SessionDep
from submission_api.errors import NotFound
from submission_api.pagination import decode_cursor, encode_cursor
from submission_api.settings import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Settings
from submission_api.taskpool import TaskCatalog, TaskEntry, TaskNotAllowed

router = APIRouter(prefix="/v1/results", tags=["results"])

# The fields of `verifier.models.VerificationReport` that may be published, and nothing else.
#
# Excluded, with reasons:
#   stdout_tail, stderr_tail  — Lean's output quotes the submitted proof back.
#   submission_sha256         — `submissions.proof_digest` is globally UNIQUE, so publishing the
#                               digest would let anyone test a candidate proof for prior
#                               submission and get a definitive answer.
#   workspace_retained        — an operator debugging flag; says nothing about the result.
#   comparator_exit_code      — internal process detail, and a channel for verifier internals.
#   problem_id                — an upstream identifier that duplicates task_id without adding
#                               anything a reader can act on.
PUBLIC_REPORT_FIELDS = (
    "schema_version",
    "task_id",
    "repository_commit",
    "source_theorem",
    "task_mode",
    "task_bundle_sha256",
    "accepted",
    "stage",
    "reason_code",
    "checks",
    "theorem_names",
    "permitted_axioms",
    "duration_ms",
    "sandbox_mode",
)


def _cache(response: Response, settings: Settings) -> None:
    """Results are cacheable but shorter-lived than the catalog.

    The catalog changes on a pin rotation; a feed changes whenever a payout confirms. Half the
    catalog window keeps a certified result from sitting behind a stale cache for a full minute
    without giving up shared caching entirely.
    """
    seconds = settings.public_cache_seconds // 2
    response.headers["Cache-Control"] = (
        f"public, max-age={seconds}" if seconds > 0 else "no-store"
    )


def _title_and_statement(catalog: TaskCatalog, task_id: str) -> tuple[str, str]:
    """The conjecture a result is against, as the catalog states it.

    A result outlives the pin set it was produced under: after a rotation, `task_id` may name a
    task the current pool no longer carries. That is a normal state, not an error — the durable
    record keeps the old digests and reports — so the labels degrade to the task id rather than
    failing the read.
    """
    try:
        entry: TaskEntry = catalog.get(task_id)
    except TaskNotAllowed:
        return task_id, ""
    return conjectures.title(entry), entry.source.type_pretty


def _certified(row: public_store.ResultRow, catalog: TaskCatalog) -> public.PublicResult:
    title, statement = _title_and_statement(catalog, row.task_id)
    return public.PublicResult(
        id=row.id,
        slug=row.task_id,
        title=title,
        statement=statement,
        task_bundle_sha256=digests.to_prefixed(row.task_bundle_sha256),
        verified_at=_utc(row.verified_at),
        certified_at=_utc(row.certified_at),
        bounty_amount_rao=row.bounty_amount_rao,
        bounty_policy_version=row.bounty_policy_version,
        verifier_version=row.verifier_version,
        sandbox_mode=row.sandbox_mode,
        report_available=row.report_available,
    )


def _in_review(row: public_store.ResultRow, catalog: TaskCatalog) -> public.InReviewResult:
    title, statement = _title_and_statement(catalog, row.task_id)
    return public.InReviewResult(
        id=row.id,
        slug=row.task_id,
        title=title,
        statement=statement,
        task_bundle_sha256=digests.to_prefixed(row.task_bundle_sha256),
        verified_at=_utc(row.verified_at),
        review_policy_version=row.review_policy_version,
        report_available=row.report_available,
    )


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def _feed(
    *,
    fetch: Callable,
    shape: Callable,
    services,
    session,
    response: Response,
    limit: int,
    cursor: str | None,
):
    """One page of a keyset feed, and the cursor for the next.

    `limit + 1` rows are read and the extra one is discarded. That is what makes `next_cursor`
    null exactly when the feed is exhausted, instead of handing back a cursor that turns out to
    address an empty page — a client should not have to make a wasted request to discover the end.
    """
    settings: Settings = services.settings
    after = None
    if cursor:
        position = decode_cursor(settings.cursor_secret, cursor)
        after = (position.created_at, position.id)

    rows = await fetch(session, limit=limit + 1, after=after)
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit and page:
        last = page[-1]
        next_cursor = encode_cursor(
            settings.cursor_secret, created_at=last.created_at, id=last.id
        )

    _cache(response, settings)
    return {
        "items": tuple(shape(row, services.catalog) for row in page),
        "next_cursor": next_cursor,
    }


@router.get(
    "/certified",
    response_model=public.CursorPage[public.PublicResult],
    summary="Certified results: Lean-verified, approved, and paid out",
)
async def list_certified(
    response: Response,
    services: ServicesDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=256)] = None,
) -> public.CursorPage[public.PublicResult]:
    page = await _feed(
        fetch=public_store.certified_page,
        shape=_certified,
        services=services,
        session=session,
        response=response,
        limit=limit,
        cursor=cursor,
    )
    return public.CursorPage[public.PublicResult](**page)


@router.get(
    "/in-review",
    response_model=public.CursorPage[public.InReviewResult],
    summary="Lean-verified results awaiting manual review",
)
async def list_in_review(
    response: Response,
    services: ServicesDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=256)] = None,
) -> public.CursorPage[public.InReviewResult]:
    page = await _feed(
        fetch=public_store.in_review_page,
        shape=_in_review,
        services=services,
        session=session,
        response=response,
        limit=limit,
        cursor=cursor,
    )
    return public.CursorPage[public.InReviewResult](**page)


@router.get(
    "/{result_id}",
    response_model=public.PublicResult,
    summary="One published result",
)
async def read_result(
    result_id: Annotated[uuid.UUID, Path()],
    response: Response,
    services: ServicesDep,
    session: SessionDep,
) -> public.PublicResult:
    row = await public_store.public_result(session, result_id)
    if row is None:
        raise NotFound("no such result")
    _cache(response, services.settings)
    # Shaped as a certified result in both cases. An in-review row simply has no `certified_at`,
    # which is the honest representation — the alternative is two response models on one path,
    # and a client that has to branch on which one it got.
    return _certified(row, services.catalog)


@router.get(
    "/{result_id}/report",
    response_model=public.PublicVerificationReport,
    summary="The published subset of the verifier report",
)
async def read_report(
    result_id: Annotated[uuid.UUID, Path()],
    response: Response,
    services: ServicesDep,
    session: SessionDep,
) -> public.PublicVerificationReport:
    row = await public_store.public_result(session, result_id)
    if row is None:
        raise NotFound("no such result")
    found = await public_store.public_report(session, result_id)
    if found is None:
        # The result is published but its run recorded no report — a run that died before
        # writing one. Absent rather than an error: there is nothing to serve and nothing wrong.
        raise NotFound("no verification report is published for this result")
    raw, digest = found

    _cache(response, services.settings)
    return public.PublicVerificationReport(
        id=row.id,
        slug=row.task_id,
        # The digest of the *full* report, not of the subset below, so it still matches the
        # immutable bytes recorded on the run and the miner's own copy of the same report.
        report_sha256=digests.to_prefixed(digest),
        report=_public_report(raw),
    )


def _public_report(raw: bytes) -> dict[str, Any]:
    """Reduce the stored report to the published fields.

    Copies only the names in `PUBLIC_REPORT_FIELDS`. A malformed or non-object report yields an
    empty projection rather than an exception: the bytes are immutable and were written by an
    older verifier, so a schema this code does not recognise is a reason to publish nothing from
    it, not a reason to fail the request.
    """
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(document, dict):
        return {}
    return {
        field: document[field] for field in PUBLIC_REPORT_FIELDS if field in document
    }


__all__ = ["PUBLIC_REPORT_FIELDS", "router"]
