"""Public results: what has been certified, and what is waiting on review.

The strictest disclosure surface in the API. Three rules, each enforced structurally rather than
by remembering to omit a field:

* **Solver credit, but no money trail.** `conjectures_subnet.db.public.ResultRow` carries the
  submitting `hotkey`, and every result here is credited to it. It still has no coldkey, payment
  reference or extrinsic, so this module cannot publish those — it was never handed them.
* **Proof bytes only after approval.** `Main.lean` is served by `/{id}/solution`, and only once
  review has approved the submission. An in-review result carries no artifact: the proof has
  passed the Lean kernel but not the reward decision, and handing the artifact out before that
  decision would let anyone take a pending result elsewhere. The gate is
  `conjectures_subnet.db.public.accepted_solution`, which filters in the query, so a handler
  cannot serve an unapproved proof by forgetting to check. Verifier *output* is still withheld at
  every state — see the allowlist below.
* **The report is an allowlist, not a redaction.** `_public_report` names the fields it copies.
  `stdout_tail` and `stderr_tail` are excluded because Lean's output quotes the submitted proof
  back verbatim — but the point of the allowlist is that a field added to `VerificationReport`
  later is withheld by default rather than published because nobody remembered to add it to a
  denylist.

`GET /v1/results/{id}` answers `404` for a submission that is not on a public feed, rather than
`403`. A submission id is a UUID a miner holds; distinguishing "not published" from "does not
exist" would turn this endpoint into a probe for the state of submissions that have not been
published yet.

The three feeds are one predicate apart and share `_feed`: `/certified` is paid out, `/in-review`
is awaiting the reward decision, and `/submissions` is their union for a dashboard that wants both
in one request. None of them can widen past what `conjectures_subnet.db.public` will publish —
each is a named query in that module, and an unfiltered read of `submissions` is not one of them.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Response

from conjectures_subnet.db import digests
from conjectures_subnet.db import public as public_store
from submission_api import conjectures, slugs
from submission_api import schemas_public as public
from submission_api.conjectures import ConjectureIndex
from submission_api.dependencies import ServicesDep, SessionDep
from submission_api.errors import NotFound
from submission_api.pagination import decode_cursor, encode_cursor
from submission_api.settings import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Settings
from submission_api.taostats import amount_usd
from verifier.bundle import PROOF_NAME

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
#   problem_id                — the per-revision identity of the conjecture. It moves on every
#                               pin rotation, so publishing it here would invite a client to key
#                               on it; the response's own `slug` is the stable identity, and the
#                               conjecture detail endpoint publishes `problem_id` alongside it for
#                               anyone who needs the revision-specific name.
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


def _slug(row: public_store.ResultRow) -> str:
    """The stable slug for the conjecture a result is against.

    Derived from the row's own `reward_target_id`, not looked up in the catalog. A result outlives
    the pin set it was produced under, so after a rotation its `task_id` names a task the current
    pool no longer carries — but the reward target is the same string the current conjecture's
    slug is derived from, so a years-old result still links to the live page.

    Falls back to the raw identity when it is not a `fc-target:` one. `V004` backfilled
    `reward_target_id` from a fixed table of known problem ids and left `ELSE problem_id` for
    anything it did not recognise, so rows predating that migration can carry a problem id here.
    A public feed must not 500 over one historical row, and the fallback is honest: it does not
    resolve to a conjecture, so the label degrades and the link does not pretend to work.
    """
    try:
        return slugs.slug_for(row.reward_target_id)
    except slugs.SlugError:
        return row.reward_target_id


def _title_and_statement(index: ConjectureIndex, row: public_store.ResultRow) -> tuple[str, str]:
    """The conjecture a result is against, as the catalog states it.

    Looked up by slug rather than by `task_id`, so a result produced under an earlier pin still
    gets its current statement and title instead of degrading. A conjecture that has since left
    the pool entirely is not in the index at all; that is a normal state, not an error — the
    durable record keeps the old digests and reports — so the labels degrade to the slug rather
    than failing the read.
    """
    item = index.get(_slug(row))
    if item is None:
        return _slug(row), ""
    return conjectures.title(item), item.source.type_pretty


def _certified(
    row: public_store.ResultRow,
    index: ConjectureIndex,
    alpha_usd: Decimal | None,
) -> public.PublicResult:
    title, statement = _title_and_statement(index, row)
    return public.PublicResult(
        id=row.id,
        hotkey=row.hotkey,
        slug=_slug(row),
        task_id=row.task_id,
        title=title,
        statement=statement,
        task_bundle_sha256=digests.to_prefixed(row.task_bundle_sha256),
        verified_at=_utc(row.verified_at),
        certified_at=_utc(row.certified_at),
        bounty_amount_rao=row.bounty_amount_rao,
        bounty_amount_usd=amount_usd(row.bounty_amount_rao, alpha_usd=alpha_usd),
        bounty_policy_version=row.bounty_policy_version,
        verifier_version=row.verifier_version,
        sandbox_mode=row.sandbox_mode,
        report_available=row.report_available,
        review=(
            None
            if row.review is None
            else public.PublicReviewDecision(
                decision=str(row.review.decision),
                reason_code=row.review.reason_code,
                notes_public=row.review.notes_public,
                policy_version=row.review.policy_version,
                decided_at=_utc(row.review.decided_at),
            )
        ),
        solution_available=row.solution_available,
    )


def _in_review(
    row: public_store.ResultRow,
    index: ConjectureIndex,
    _alpha_usd: Decimal | None,
) -> public.InReviewResult:
    title, statement = _title_and_statement(index, row)
    return public.InReviewResult(
        id=row.id,
        hotkey=row.hotkey,
        slug=_slug(row),
        task_id=row.task_id,
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
    price_bounties: bool,
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
    alpha_usd = (
        await services.bounty_usd.alpha_usd() if page and price_bounties else None
    )
    next_cursor = None
    if len(rows) > limit and page:
        last = page[-1]
        next_cursor = encode_cursor(
            settings.cursor_secret, created_at=last.created_at, id=last.id
        )

    _cache(response, settings)
    return {
        "items": tuple(shape(row, services.index, alpha_usd) for row in page),
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
        price_bounties=True,
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
        price_bounties=False,
    )
    return public.CursorPage[public.InReviewResult](**page)


# Declared before `/{result_id}`: FastAPI matches in declaration order, and a literal path that
# follows a `{uuid}` parameter on the same prefix is never reached.
@router.get(
    "/submissions",
    response_model=public.CursorPage[public.PublicResult],
    summary="Every published result in one feed, for the public dashboard",
)
async def list_all(
    response: Response,
    services: ServicesDep,
    session: SessionDep,
    # 50 rather than `DEFAULT_PAGE_SIZE`, unlike the two feeds above: a dashboard renders one
    # long list, and this is the default both authors of the endpoint chose.
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    cursor: Annotated[str | None, Query(max_length=256)] = None,
) -> public.CursorPage[public.PublicResult]:
    """The certified and in-review feeds interleaved, so a dashboard makes one request.

    Shaped as `PublicResult` in both cases, for the reason `read_result` gives: an in-review row
    simply has no `certified_at`, and a client that has to branch on which of two response models
    it got is worse than one that reads a nullable field.

    Declared above `/{result_id}` because Starlette matches routes in declaration order and
    `submissions` is a valid path segment: registered after, every request to this path is parsed
    as a UUID instead and answered `400`.
    """
    page = await _feed(
        fetch=public_store.all_results_page,
        shape=_certified,
        services=services,
        session=session,
        response=response,
        limit=limit,
        cursor=cursor,
        price_bounties=True,
    )
    return public.CursorPage[public.PublicResult](**page)


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
    alpha_usd = await services.bounty_usd.alpha_usd()
    _cache(response, services.settings)
    # Shaped as a certified result in both cases. An in-review row simply has no `certified_at`,
    # which is the honest representation — the alternative is two response models on one path,
    # and a client that has to branch on which one it got.
    return _certified(row, services.index, alpha_usd)


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
        slug=_slug(row),
        # The digest of the *full* report, not of the subset below, so it still matches the
        # immutable bytes recorded on the run and the miner's own copy of the same report.
        report_sha256=digests.to_prefixed(digest),
        report=_public_report(raw),
    )


@router.get(
    "/{result_id}/solution",
    response_model=public.PublicSolution,
    summary="The proof that closed the conjecture, for an approved result",
)
async def read_solution(
    result_id: Annotated[uuid.UUID, Path()],
    response: Response,
    services: ServicesDep,
    session: SessionDep,
) -> public.PublicSolution:
    """The verified `Main.lean`, published once review has approved the submission.

    Two lookups rather than one, and the order matters. `public_result` establishes that the id
    names something on a public feed at all; `accepted_solution` applies the stricter approval
    gate. Both answer `404`, and that is deliberate — a listed-but-unapproved result and an
    unpublished one are reported identically, so the response cannot be used to tell whether a
    pending submission exists.
    """
    row = await public_store.public_result(session, result_id)
    if row is None:
        raise NotFound("no such result")
    found = await public_store.accepted_solution(session, result_id)
    if found is None:
        # Listed, but review has not approved it. Same 404 as an id that is not published at
        # all: the proof is the one disclosure here that cannot be taken back, so "not yet" and
        # "never" look the same from outside.
        raise NotFound("no solution is published for this result")
    content, digest, byte_length = found

    _cache(response, services.settings)
    return public.PublicSolution(
        id=row.id,
        hotkey=row.hotkey,
        slug=_slug(row),
        # The name the bytes carry inside the verified bundle, from the module that enforces it,
        # so the published filename cannot drift from the one intake accepted.
        filename=PROOF_NAME,
        # Decoded rather than served as bytes: intake already rejected anything that is not
        # UTF-8, so this cannot fail on bytes that reached the durable record.
        source=content.decode("utf-8"),
        proof_sha256=digests.to_prefixed(digest),
        byte_length=byte_length,
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
