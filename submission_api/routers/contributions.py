"""Partial work contributed to the conjecture pool, mirrored from public GitHub.

Unauthenticated and world-readable, like the catalog and the results feeds, and for a stronger
reason than either: every byte published here is already public in
`conjectures-io/conjectures-contribution`, which anyone can clone. What this surface adds is not
access but *shape* — the corpus joined to this validator's own conjecture identities, queryable by
the same slugs the rest of the public API uses, and served from memory rather than from 210 files a
browser would otherwise have to fetch one at a time.

Four rules shape the module:

* **Answered from a snapshot, never from a network.** A request here never waits on github.com. The
  refresh runs on its own task (`submission_api/github.py`) and these handlers read whatever it last
  produced, so a GitHub outage makes the listing stale rather than making the API slow.
* **Unfetched is not empty.** When the mirror has never loaded, every endpoint answers `503` with
  `CONTRIBUTIONS_UNAVAILABLE` instead of serving empty lists. "There are no contributions" is a
  claim about the corpus; "we have not read the corpus" is a claim about this process, and
  publishing the second as the first is the failure mode worth spending a status code on.
* **The join is `reward_target_id`, and the target slug is not it.** A target is addressable here by
  four names — the corpus's own slug (`erdos-535`), this API's conjecture slug
  (`erdos535-erdos-535`), the durable `fc-target:` reward id, and the per-revision problem id — and
  all four resolve through the index built in `submission_api/contributions.py`. None of them is
  derived from another by string manipulation.
* **Every list is bounded.** `limit` is capped, each repeatable filter is bounded on both how many
  times it may repeat and how long one value may be, and the free-text filters are substring tests
  rather than patterns. The whole corpus is a few hundred rows in memory, so no filter combination
  here can be made to do unbounded work.

Every response carries a strong `ETag`. Unlike the result feeds, these bodies genuinely are stable
between requests — they change only when a refresh lands — so revalidation is nearly free and a
website polling the listing costs one `304` per read.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Path, Query, Request, Response
from pydantic import StringConstraints

from submission_api import conditional, github
from submission_api import schemas_contributions as schemas
from submission_api.conjectures import ConjectureIndex
from submission_api.contributions import (
    Contribution,
    ContributionAuthor,
    ContributionSnapshot,
    ContributionTarget,
    PendingContribution,
)
from submission_api.dependencies import ServicesDep
from submission_api.errors import NotFound, ServiceUnavailable
from submission_api.settings import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Settings

router = APIRouter(prefix="/v1/contributions", tags=["contributions"])

REASON_UNAVAILABLE = "CONTRIBUTIONS_UNAVAILABLE"

# The same two-axis bound the catalog filters use: how many times a filter may repeat, and how long
# one of its values may be. They are different limits and only one of them can live on the `Query`
# — see the note in `routers/catalog.py` for the 500 that taught us that.
MAX_FILTER_VALUES = 64
MAX_FILTER_VALUE_LENGTH = 255
MAX_SEARCH_LENGTH = 100

# How far into a listing an offset may point. The corpus is a few hundred rows, so this is not a
# cost bound — it is there so a caller paging a shrinking list gets a `422` naming the limit rather
# than an empty page they might read as "no results".
MAX_OFFSET = 100_000

FilterValue = Annotated[str, StringConstraints(max_length=MAX_FILTER_VALUE_LENGTH)]
SearchValue = Annotated[str, StringConstraints(max_length=MAX_SEARCH_LENGTH)]

# A target may be addressed by any of its four names, so this admits more than a conjecture slug:
# `fc-target:Erdos535.erdos_535` and `fc-379fc029-erdos535-…-problem` are both valid path values.
# Kept as one permissive pattern rather than three alternatives because the resolution order lives
# in `ContributionSnapshot.target`, and splitting it across a route pattern would put half of an
# identity rule in a regex.
TARGET_PATH = Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$")

# A full contribution id or an unambiguous prefix of one. Constrained to hex so it can never shadow
# the fixed segments below it, whatever order the routes end up declared in.
CONTRIBUTION_PATH = Path(pattern=r"^[0-9a-f]{8,64}$")

SORT_FIELDS = ("added", "target", "title")
TARGET_SORT_FIELDS = ("target", "contributions", "last_added", "first_added")
AUTHOR_SORT_FIELDS = ("contributions", "author", "last_seen", "first_seen")
ORDERS = ("asc", "desc")


# --- Endpoints -------------------------------------------------------------------------------


@router.get(
    "/meta",
    response_model=schemas.ContributionsMeta,
    summary="What the mirrored contribution corpus is, and how fresh it is",
)
async def read_meta(
    request: Request, response: Response, services: ServicesDep
) -> schemas.ContributionsMeta | Response:
    """The snapshot's provenance and freshness. Read this before trusting a listing."""
    snapshot = _snapshot(services)
    return _answer(request, response, services.settings, _meta(snapshot, services.settings))


@router.get(
    "",
    response_model=schemas.ContributionPage,
    summary="List contribution metadata, newest first",
)
async def list_contributions(
    request: Request,
    response: Response,
    services: ServicesDep,
    target: Annotated[list[FilterValue] | None, Query(max_length=MAX_FILTER_VALUES)] = None,
    conjecture: Annotated[
        list[FilterValue] | None, Query(max_length=MAX_FILTER_VALUES)
    ] = None,
    author: Annotated[list[FilterValue] | None, Query(max_length=MAX_FILTER_VALUES)] = None,
    coldkey: Annotated[list[FilterValue] | None, Query(max_length=MAX_FILTER_VALUES)] = None,
    hotkey: Annotated[list[FilterValue] | None, Query(max_length=MAX_FILTER_VALUES)] = None,
    kind: Annotated[list[FilterValue] | None, Query(max_length=MAX_FILTER_VALUES)] = None,
    mode: Annotated[list[FilterValue] | None, Query(max_length=MAX_FILTER_VALUES)] = None,
    declares: SearchValue | None = None,
    q: SearchValue | None = None,
    since: date | None = None,
    until: date | None = None,
    rewarded: bool | None = None,
    sort: Annotated[str, Query(pattern=r"^(added|target|title)$")] = "added",
    order: Annotated[str, Query(pattern=r"^(asc|desc)$")] = "desc",
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0, le=MAX_OFFSET)] = 0,
) -> schemas.ContributionPage | Response:
    """Every accepted contribution, filtered and paged.

    `target` matches the contribution repository's target slug; `conjecture` matches this API's
    conjecture slug. Both are exact and repeatable, and repeating one ORs its values while
    different filters AND together — the same composition `/v1/catalog/conjectures` uses.

    `author`, `coldkey` and `hotkey` match on a prefix, so the truncations that appear in listings
    and in `contrib ls` output are usable here without being expanded first. `declares` is a
    case-insensitive substring test over declaration names, and `q` the same over titles; neither
    is a glob, deliberately, because a pattern language on a public endpoint is a cost a caller
    gets to choose.
    """
    snapshot = _snapshot(services)
    rows = _filter(
        snapshot.contributions,
        targets=target,
        conjectures=conjecture,
        authors=author,
        coldkeys=coldkey,
        hotkeys=hotkey,
        kinds=kind,
        modes=mode,
        declares=declares,
        query=q,
        since=since,
        until=until,
        rewarded=rewarded,
    )
    rows = _sorted(rows, sort=sort, order=order)
    page = schemas.ContributionPage(
        items=tuple(
            _item(row, services.index, snapshot)
            for row in rows[offset : offset + limit]
        ),
        total=len(rows),
        limit=limit,
        offset=offset,
    )
    return _answer(request, response, services.settings, page)


@router.get(
    "/targets",
    response_model=schemas.ContributionTargetPage,
    summary="One row per target, with what has accumulated on it",
)
async def list_targets(
    request: Request,
    response: Response,
    services: ServicesDep,
    empty: bool | None = None,
    # Shadows the builtin, deliberately: the query parameter has to be named after the field
    # it filters, and the corpus calls it `open`. Nothing in this handler calls `open()`.
    open: bool | None = None,
    in_pool: bool | None = None,
    author: Annotated[list[FilterValue] | None, Query(max_length=MAX_FILTER_VALUES)] = None,
    declares: SearchValue | None = None,
    q: SearchValue | None = None,
    sort: Annotated[
        str, Query(pattern=r"^(target|contributions|last_added|first_added)$")
    ] = "target",
    order: Annotated[str, Query(pattern=r"^(asc|desc)$")] = "asc",
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0, le=MAX_OFFSET)] = 0,
) -> schemas.ContributionTargetPage | Response:
    """The target grain.

    `empty=false` is the "what is being worked on" query and `empty=true` the "what needs someone"
    one. Both are answerable because the corpus writes a page for every target it tracks, not only
    for the ones with work on them — see `contributions.parse_empty_target`.

    `open` is the corpus's flag for whether a target accepts contributions; `in_pool` is this
    validator's separate answer to whether the conjecture is still offered for solving. They come
    from different places and can legitimately disagree: the contribution repository pins its own
    revision of the pool, so a rotation here moves `in_pool` days before the corpus notices.
    """
    snapshot = _snapshot(services)
    rows = tuple(
        row
        for row in snapshot.targets
        if _target_matches(
            row,
            empty=empty,
            open_=open,
            in_pool=in_pool,
            index=services.index,
            authors=author,
            declares=declares,
            query=q,
        )
    )
    rows = _sorted_targets(rows, sort=sort, order=order)
    page = schemas.ContributionTargetPage(
        items=tuple(
            _target_summary(row, services.index, snapshot)
            for row in rows[offset : offset + limit]
        ),
        total=len(rows),
        limit=limit,
        offset=offset,
    )
    return _answer(request, response, services.settings, page)


@router.get(
    "/targets/{target}",
    response_model=schemas.ContributionTargetDetail,
    summary="Everything contributed to one target",
)
async def read_target(
    target: Annotated[str, TARGET_PATH],
    request: Request,
    response: Response,
    services: ServicesDep,
) -> schemas.ContributionTargetDetail | Response:
    """One target by any of its four names: target slug, conjecture slug, reward target, problem id.

    `404` means the corpus holds no directory for it — which for a conjecture that exists in the
    pool means nobody has contributed to it yet, not that the conjecture is unknown. The catalog is
    where a conjecture's existence is answered; this endpoint only answers what has been
    contributed.
    """
    snapshot = _snapshot(services)
    row = snapshot.target(target)
    if row is None:
        raise NotFound("no contributions are recorded for this target")
    detail = schemas.ContributionTargetDetail(
        target=_target_summary(row, services.index, snapshot),
        contributions=tuple(
            _item(item, services.index, snapshot) for item in row.contributions
        ),
    )
    return _answer(request, response, services.settings, detail)


@router.get(
    "/authors",
    response_model=schemas.ContributionAuthorPage,
    summary="One row per author key, across every target it is active on",
)
async def list_authors(
    request: Request,
    response: Response,
    services: ServicesDep,
    author: Annotated[list[FilterValue] | None, Query(max_length=MAX_FILTER_VALUES)] = None,
    coldkey: Annotated[list[FilterValue] | None, Query(max_length=MAX_FILTER_VALUES)] = None,
    hotkey: Annotated[list[FilterValue] | None, Query(max_length=MAX_FILTER_VALUES)] = None,
    shared_coldkey: bool | None = None,
    sort: Annotated[
        str, Query(pattern=r"^(contributions|author|last_seen|first_seen)$")
    ] = "contributions",
    order: Annotated[str, Query(pattern=r"^(asc|desc)$")] = "desc",
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0, le=MAX_OFFSET)] = 0,
) -> schemas.ContributionAuthorPage | Response:
    """The author cross-cut. `author`, `coldkey` and `hotkey` all match on a prefix."""
    snapshot = _snapshot(services)
    rows = tuple(
        row
        for row in snapshot.authors
        if _prefixed((row.author,), author)
        and _prefixed(row.coldkeys, coldkey)
        and _prefixed(row.hotkeys, hotkey)
        and (shared_coldkey is None or row.shared_coldkey == shared_coldkey)
    )
    rows = _sorted_authors(rows, sort=sort, order=order)
    page = schemas.ContributionAuthorPage(
        items=tuple(_author(row) for row in rows[offset : offset + limit]),
        total=len(rows),
        limit=limit,
        offset=offset,
    )
    return _answer(request, response, services.settings, page)


@router.get(
    "/pending",
    response_model=schemas.PendingContributionPage,
    summary="Open contribution pull requests: offered, not yet accepted",
)
async def list_pending(
    request: Request,
    response: Response,
    services: ServicesDep,
    target: Annotated[list[FilterValue] | None, Query(max_length=MAX_FILTER_VALUES)] = None,
    conjecture: Annotated[
        list[FilterValue] | None, Query(max_length=MAX_FILTER_VALUES)
    ] = None,
    draft: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0, le=MAX_OFFSET)] = 0,
) -> schemas.PendingContributionPage | Response:
    """What is in flight right now — the one question the corpus on disk cannot answer.

    These are open pull requests. Nothing here has been reviewed or accepted, `target` is a
    CI-applied label rather than a signed field, and a pull request may be closed without ever
    becoming a contribution. Read it as "somebody is working here", not as work that exists.
    """
    snapshot = _snapshot(services)
    rows = tuple(
        row
        for row in snapshot.pending
        if _exact((row.target,) if row.target else (), target)
        and _exact((row.conjecture_slug,) if row.conjecture_slug else (), conjecture)
        and (draft is None or row.draft == draft)
    )
    page = schemas.PendingContributionPage(
        items=tuple(_pending(row) for row in rows[offset : offset + limit]),
        total=len(rows),
        limit=limit,
        offset=offset,
    )
    return _answer(request, response, services.settings, page)


@router.get(
    "/{contribution_id}",
    response_model=schemas.ContributionItem,
    summary="One contribution by id",
)
async def read_contribution(
    contribution_id: Annotated[str, CONTRIBUTION_PATH],
    request: Request,
    response: Response,
    services: ServicesDep,
) -> schemas.ContributionItem | Response:
    """One contribution by its full 64-character id, or by an unambiguous prefix of at least 8.

    The prefix form exists because the corpus's own tooling prints ids truncated to 12. An
    ambiguous prefix is a `404` rather than a guess: serving one row under another's identifier
    would be worse than not answering.
    """
    snapshot = _snapshot(services)
    row = snapshot.contribution(contribution_id)
    if row is None:
        raise NotFound("no contribution has this id")
    return _answer(
        request, response, services.settings, _item(row, services.index, snapshot)
    )


# --- Rendering -------------------------------------------------------------------------------


def _snapshot(services) -> ContributionSnapshot:  # type: ignore[no-untyped-def]
    """The served snapshot, or a refusal when the mirror has never loaded one.

    `503` rather than an empty `200`, and it carries `retry_after_seconds`: the caller's correct
    response is to come back, and the refresh interval is how long that is.
    """
    snapshot = services.contributions.snapshot()
    if not snapshot.available:
        raise ServiceUnavailable(
            "the contribution corpus has not been read yet",
            reason_code=REASON_UNAVAILABLE,
            extra={"retry_after_seconds": services.settings.contributions_refresh_seconds},
        )
    return snapshot


def _meta(
    snapshot: ContributionSnapshot, settings: Settings
) -> schemas.ContributionsMeta:
    age = snapshot.age_seconds()
    return schemas.ContributionsMeta(
        repository=snapshot.repository,
        repository_url=github.repository_url(snapshot.repository),
        branch=snapshot.branch,
        head_commit=snapshot.head_commit,
        fetched_at=snapshot.fetched_at,
        age_seconds=age,
        stale=age > settings.contributions_stale_seconds,
        refresh_seconds=settings.contributions_refresh_seconds,
        targets=len(snapshot.targets),
        contributions=len(snapshot.contributions),
        authors=len(snapshot.authors),
        pending=len(snapshot.pending),
        unreadable_targets=len(snapshot.unreadable),
    )


def _item(
    row: Contribution, index: ConjectureIndex, snapshot: ContributionSnapshot
) -> schemas.ContributionItem:
    return schemas.ContributionItem(
        contribution_id=row.contribution_id,
        short_id=row.short_id,
        target=row.target,
        reward_target_id=row.reward_target_id,
        problem_id=row.problem_id,
        conjecture_slug=row.conjecture_slug,
        in_pool=_in_pool(row.conjecture_slug, index),
        title=row.title,
        author=row.author,
        coldkey=row.coldkey,
        hotkey=row.hotkey,
        kind=row.kind,
        mode=row.mode,
        added=row.added,
        declarations=row.declarations,
        parents=row.parents,
        artifacts=row.artifacts,
        tasks_commit=row.tasks_commit,
        rewarded=row.rewarded,
        html_url=_tree_url(snapshot, row.path),
    )


def _target_summary(
    row: ContributionTarget, index: ConjectureIndex, snapshot: ContributionSnapshot
) -> schemas.ContributionTargetSummary:
    return schemas.ContributionTargetSummary(
        target=row.target,
        reward_target_id=row.reward_target_id,
        problem_id=row.problem_id,
        conjecture_slug=row.conjecture_slug,
        in_pool=_in_pool(row.conjecture_slug, index),
        open=row.open,
        contributions=row.contribution_count,
        authors=row.authors,
        coldkeys=row.coldkeys,
        declarations=row.declarations,
        kinds=row.kinds,
        modes=row.modes,
        first_added=row.first_added,
        last_added=row.last_added,
        html_url=_tree_url(snapshot, f"contributions/{row.target}"),
    )


def _author(row: ContributionAuthor) -> schemas.ContributionAuthorSummary:
    return schemas.ContributionAuthorSummary(
        author=row.author,
        contributions=row.contributions,
        targets=row.targets,
        conjecture_slugs=row.conjecture_slugs,
        declarations=row.declarations,
        coldkeys=row.coldkeys,
        hotkeys=row.hotkeys,
        first_seen=row.first_seen,
        last_seen=row.last_seen,
        shared_coldkey=row.shared_coldkey,
    )


def _pending(row: PendingContribution) -> schemas.PendingContributionItem:
    return schemas.PendingContributionItem(
        number=row.number,
        title=row.title,
        target=row.target,
        conjecture_slug=row.conjecture_slug,
        hotkey=row.hotkey,
        author_login=row.author_login,
        branch=row.branch,
        draft=row.draft,
        created_at=row.created_at,
        updated_at=row.updated_at,
        html_url=row.html_url,
    )


def _in_pool(slug: str | None, index: ConjectureIndex) -> bool:
    """Whether this validator currently offers the conjecture a contribution is about.

    Read from the live index rather than from the mirrored snapshot on purpose: the pool rotates
    weekly and the contribution repository pins its own revision of it, so the corpus's view of
    what is in the pool can lag this process's by days. The question "is this offered right now" has
    exactly one authority in this service, and it is not a mirror of somebody else's checkout.
    """
    return slug is not None and index.get(slug) is not None


def _tree_url(snapshot: ContributionSnapshot, path: str) -> str:
    """A link into the mirrored repository at the branch the snapshot was read from.

    Built from the snapshot rather than from the module default, so a deployment pointed at a fork
    or a non-default branch links into what it is actually serving instead of into somewhere the
    row does not exist.
    """
    return f"{github.repository_url(snapshot.repository)}/tree/{snapshot.branch}/{path}"


def _answer(
    request: Request, response: Response, settings: Settings, payload
):  # type: ignore[no-untyped-def]
    """Tag the response and answer `304` when the caller already holds this body."""
    etag = conditional.etag_for(payload)
    max_age = settings.public_cache_seconds
    cache_control = (
        f"public, max-age={max_age}" if max_age > 0 else "no-store"
    )
    response.headers["Cache-Control"] = cache_control
    response.headers["ETag"] = etag
    if conditional.matches(request.headers.get("if-none-match"), etag):
        return conditional.not_modified(etag, cache_control)
    return payload


# --- Filtering -------------------------------------------------------------------------------


def _filter(
    rows: Iterable[Contribution],
    *,
    targets: list[str] | None,
    conjectures: list[str] | None,
    authors: list[str] | None,
    coldkeys: list[str] | None,
    hotkeys: list[str] | None,
    kinds: list[str] | None,
    modes: list[str] | None,
    declares: str | None,
    query: str | None,
    since: date | None,
    until: date | None,
    rewarded: bool | None,
) -> tuple[Contribution, ...]:
    """Every filter AND-ed; repeating one ORs its values.

    An absent value never matches a filter. A contribution that opted out of a reward is not
    returned by `coldkey=5C…` — not because it fails the prefix test, but because it has no coldkey
    to test, and treating a missing value as a non-match is the only reading that does not invent
    one.
    """
    return tuple(
        row
        for row in rows
        if _exact((row.target,), targets)
        and _exact((row.conjecture_slug,) if row.conjecture_slug else (), conjectures)
        and _prefixed((row.author,), authors)
        and _prefixed((row.coldkey,) if row.coldkey else (), coldkeys)
        and _prefixed((row.hotkey,) if row.hotkey else (), hotkeys)
        and _exact((row.kind,), kinds)
        and _exact((row.mode,), modes)
        and _contains(row.declarations, declares)
        and _contains((row.title,), query)
        and (since is None or row.added >= since)
        and (until is None or row.added <= until)
        and (rewarded is None or row.rewarded == rewarded)
    )


def _target_matches(
    row: ContributionTarget,
    *,
    empty: bool | None,
    open_: bool | None,
    in_pool: bool | None,
    index: ConjectureIndex,
    authors: list[str] | None,
    declares: str | None,
    query: str | None,
) -> bool:
    if empty is not None and (row.contribution_count == 0) != empty:
        return False
    if open_ is not None and row.open != open_:
        return False
    if in_pool is not None and _in_pool(row.conjecture_slug, index) != in_pool:
        return False
    return (
        _prefixed(row.authors, authors)
        and _contains(row.declarations, declares)
        and _contains((row.target,), query)
    )


def _exact(values: tuple[str, ...], wanted: list[str] | None) -> bool:
    return not wanted or any(value in wanted for value in values)


def _prefixed(values: tuple[str, ...], wanted: list[str] | None) -> bool:
    return not wanted or any(
        value.startswith(prefix) for value in values for prefix in wanted
    )


def _contains(values: tuple[str, ...], needle: str | None) -> bool:
    """A case-insensitive substring test. Not a glob, and deliberately not one.

    `docs/querying.md` in the contribution repository offers globs because it runs locally against
    a checkout the caller already has. This endpoint is anonymous and remote, so the pattern
    language a caller gets to choose is the cost this service gets to pay.
    """
    if not needle:
        return True
    lowered = needle.lower()
    return any(lowered in value.lower() for value in values)


# --- Ordering --------------------------------------------------------------------------------
# Every sort breaks ties on the row's own identity, so two refreshes of one commit produce the same
# page and the `ETag` on it is stable.


def _sorted(
    rows: tuple[Contribution, ...], *, sort: str, order: str
) -> tuple[Contribution, ...]:
    keys = {
        "added": lambda row: (row.added.isoformat(), row.contribution_id),
        "target": lambda row: (row.target, row.contribution_id),
        "title": lambda row: (row.title.lower(), row.contribution_id),
    }
    return tuple(sorted(rows, key=keys[sort], reverse=order == "desc"))


def _sorted_targets(
    rows: tuple[ContributionTarget, ...], *, sort: str, order: str
) -> tuple[ContributionTarget, ...]:
    keys = {
        "target": lambda row: ("", row.target),
        "contributions": lambda row: (f"{row.contribution_count:09d}", row.target),
        "last_added": lambda row: (
            row.last_added.isoformat() if row.last_added else "",
            row.target,
        ),
        "first_added": lambda row: (
            row.first_added.isoformat() if row.first_added else "",
            row.target,
        ),
    }
    return tuple(sorted(rows, key=keys[sort], reverse=order == "desc"))


def _sorted_authors(
    rows: tuple[ContributionAuthor, ...], *, sort: str, order: str
) -> tuple[ContributionAuthor, ...]:
    keys = {
        "contributions": lambda row: (f"{row.contributions:09d}", row.author),
        "author": lambda row: ("", row.author),
        "last_seen": lambda row: (row.last_seen.isoformat(), row.author),
        "first_seen": lambda row: (row.first_seen.isoformat(), row.author),
    }
    return tuple(sorted(rows, key=keys[sort], reverse=order == "desc"))


__all__ = [
    "AUTHOR_SORT_FIELDS",
    "MAX_FILTER_VALUES",
    "MAX_FILTER_VALUE_LENGTH",
    "MAX_OFFSET",
    "ORDERS",
    "REASON_UNAVAILABLE",
    "SORT_FIELDS",
    "TARGET_SORT_FIELDS",
    "router",
]
