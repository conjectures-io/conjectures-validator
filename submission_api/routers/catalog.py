"""The public conjecture catalog.

Unauthenticated, and read by a browser. The task pool and its digests are already published, so
there is nothing here a caller could not derive from the repository — but that is a reason these
endpoints are safe to expose, not a reason to be careless with what they return. Four rules shape
the module:

* **The slug is stable, and it is not the task id.** A URL here gets bookmarked, cited and
  indexed, so it has to outlive what produced it. `task_id` is seeded with the pinned source
  revision and changes on every weekly rotation, so the slug is derived from `reward_target_id`
  instead — see `submission_api/slugs.py`. The task ids stay in the response, because a solver
  building a bundle commits to one; they are fields of a conjecture rather than its name. A URL
  built from a task id is redirected rather than 404ed, so links minted before this existed and
  ids copied out of bundles both still land.
* **One entry per conjecture, not per task.** Every theorem is issued as one task per mode, so a
  task list would show each conjecture twice and would make "attempts" mean attempts in one
  direction. Grouping happens once at startup in `submission_api/conjectures.py`.
* **Every list is bounded.** `limit` is capped, the free-text filter is length-capped and is a
  substring test rather than a pattern, and the facets are computed over a fixed in-memory list.
  Each repeatable filter is bounded twice — on how many times it may repeat and on the length of
  one value — because those are two different limits and only one of them can live on the `Query`.
  See `MAX_FILTER_VALUES`. None of these endpoints can be made to do unbounded work.
* **The statement is served from the audited bytes.** Each task's `challenge_lean` is the exact
  `Challenge.lean` hashed into its published `task_bundle_sha256`, held in memory since startup,
  so what a reader verifies is what the verifier compiles.

Only the attempt counters touch the database. Everything else is a projection of the conjecture
index built at startup from `TaskCatalog.load`.
"""

from __future__ import annotations

import hmac
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Path, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import Field, StringConstraints

from conjectures_subnet.bounty import BountyPoolSnapshot, LiveBounty
from conjectures_subnet.db import public as public_store
from submission_api import conjectures, credits as credit_config
from submission_api import schemas_account as account_schemas, schemas_public as public
from submission_api.conjectures import Conjecture, ConjectureIndex
from submission_api.dependencies import ServicesDep, SessionDep
from submission_api.errors import NotFound
from submission_api.pins import PinSet
from submission_api.retired import RetiredConjecture
from submission_api.settings import (
    DEFAULT_ACTIVITY_ITEMS,
    DEFAULT_PAGE_SIZE,
    MAX_ACTIVITY_ITEMS,
    MAX_PAGE_SIZE,
    Settings,
)
from submission_api.taskpool import TaskEntry
from submission_api.taostats import amount_usd
from verifier.bundle import BUNDLE_FORMAT
from verifier.hashing import sha256_text

router = APIRouter(prefix="/v1/catalog", tags=["catalog"])

# The path pattern for a conjecture slug. Also matches a `task_id`, deliberately: that is what
# makes the legacy redirect reachable instead of rejected by validation before a handler runs.
SLUG_PATH = Path(pattern=r"^[a-z0-9][a-z0-9-]{0,254}$")

# One credit buys one verification attempt. The credit ledger itself is Stage 2; until it
# exists, a credit is exactly the configured submission price and this is the constant that
# says so in one place.
CREDITS_PER_ATTEMPT = 1

# A pseudonym long enough that collisions across a pool of solvers are not a practical concern,
# short enough not to look like a key. It is a truncated MAC, so truncation is what limits what
# an offline guessing attack recovers even if the salt leaked.
PSEUDONYM_LENGTH = 12

# One Markdown inline link, found anywhere in a reference rather than assumed to span all of it.
#
# Three details, each forced by what the pinned catalog actually contains:
#
#   * **The URL may contain balanced parentheses.** `Diameter_(group_theory)` and DOIs like
#     `10.1002/(SICI)1097-0118(199804)27:4…` are both real, so a plain `[^)\s]+` would truncate
#     them — and half a URL is worse than none, because it looks usable and 404s.
#   * **`https?://` is required, not just "something in parentheses".** Entries like
#     `[Er56](Erdős, P., Problems and results…)` and `[Kanold](No references found)` put prose
#     where a link goes. Matching those would publish a sentence as a `url`.
#   * **A Markdown title after the URL is dropped.** `[…](https://…/open-problems.pdf#section.1
#     Problem 1)` is the title form written without quotes. The link is the part before the
#     whitespace; the rest is a label for the link, not part of the address.
REFERENCE_LINK = re.compile(
    r"\[(?P<text>[^\]]*)\]\(\s*"
    r"(?P<url>https?://(?:[^\s()]|\([^\s()]*\))+)"
    r"(?:\s+[^)]*)?\)"
)

# The repeatable filters are bounded on two axes, and the distinction is the whole point of these
# two names. `Query(max_length=...)` on a `list` parameter bounds *how many times* the filter may
# repeat; a bound on each value has to sit on the item type inside the list, which is what the
# aliases below carry. Writing one and meaning the other is how `ams_subject` came to 500 on every
# request that used it: `Query(ge=0, le=99)` on `list[int]` asked Pydantic to compare a *list*
# against 0, which raises `TypeError: Unable to apply constraint 'ge' to supplied value [11]` —
# after validation, so the error surfaced as an unhandled 500 rather than a 422.
MAX_FILTER_VALUES = 64
MAX_FILTER_VALUE_LENGTH = 64

# One value of a repeatable string filter. The length bound is on the string, not on the list.
FilterValue = Annotated[str, StringConstraints(max_length=MAX_FILTER_VALUE_LENGTH)]

# One AMS subject: the top-level MSC classification, two digits. The pinned pool uses 3 through
# 94, so the range is the classification scheme's, not the data's — a subject the next rotation
# introduces must not be rejected for being new.
AmsSubject = Annotated[int, Field(ge=0, le=99)]


def _cache(response: Response, settings: Settings, *, seconds: int | None = None) -> None:
    """Mark a response publicly cacheable.

    `public` is correct and deliberate: none of these endpoints varies by caller and none carries
    a credential, so a shared cache in front of the API may serve one copy to everyone. That is
    also the cheapest rate-limit relief available. Anything that ever becomes
    caller-dependent must lose this header in the same change.
    """
    max_age = settings.public_cache_seconds if seconds is None else seconds
    if max_age <= 0:
        response.headers["Cache-Control"] = "no-store"
        return
    response.headers["Cache-Control"] = f"public, max-age={max_age}"


def _reference(raw: str) -> public.Reference:
    """Split a catalog reference into a display label and a link.

    A reference *may contain* a Markdown link; it is not a string that *is* one. The pinned
    catalog records free-form citations, and the link sits wherever the bibliography put it:

        [Er64](https://users.renyi.hu/~p_erdos/1964-10.pdf) P. Erdős, Some problem (1964)
        J. Černý, [*Poznámka…*](https://dml.cz/…), Matematicko-fyzikálny 14 (1964)
        Arora, Sanjeev, and Boaz Barak. Computational complexity. Cambridge, 2009.

    So the link is *searched for*, not assumed to span the whole string. Anchoring on the string's
    own ends is what the earlier version did, and it failed both ways on real data: a citation
    whose link is in the middle was published as raw Markdown with no URL, and one that merely
    *ended* in `)` — every `… (1970)` — was split at its first `](`, which ran the URL on through
    the author list. Roughly 8% of the pool hit one case or the other.

    `url` is the first link's target; `label` is the whole citation with every link flattened to
    its own text, so no words are dropped and no Markdown reaches a client that asked for none.
    First rather than only, because a handful of entries cite a paper and its arXiv mirror; one
    `url` field can hold one of them, and the leading link is the one the bibliography led with.

    A reference with no link — 40% of the pool, and every entry whose parenthesised part is prose
    rather than an address — keeps its text verbatim and reports `url=None`. Inventing a link for
    those would be worse than admitting there is none.
    """
    match = REFERENCE_LINK.search(raw)
    if match is None:
        return public.Reference(label=raw)
    label = REFERENCE_LINK.sub(lambda found: found.group("text"), raw).strip()
    # `or raw` guards the degenerate `[](https://…)`, where flattening leaves nothing to show.
    return public.Reference(label=label or raw, url=match.group("url"))


def _bounty(quote: LiveBounty, *, alpha_usd: Decimal | None) -> public.BountyInfo:
    return public.BountyInfo(
        amount_rao=quote.amount_rao,
        amount_usd=amount_usd(quote.amount_rao, alpha_usd=alpha_usd),
        policy_version=quote.policy_version,
        available=quote.available,
        reason=quote.reason,
        as_of=quote.as_of,
        locked=False,
    )


def _bounty_pool(
    snapshot: BountyPoolSnapshot, *, alpha_usd: Decimal | None
) -> public.BountyPoolInfo:
    return public.BountyPoolInfo(
        policy_version=snapshot.policy_version,
        balance_rao=snapshot.balance_rao,
        balance_usd=amount_usd(snapshot.balance_rao, alpha_usd=alpha_usd),
        wallet_coldkey=snapshot.wallet_coldkey,
        wallet_hotkey=snapshot.wallet_hotkey,
        netuid=snapshot.netuid,
        asset=snapshot.asset,
        open_targets=snapshot.open_targets,
        total_age_weight=snapshot.total_age_weight,
        constant_numerator=snapshot.constant_numerator,
        constant_denominator=snapshot.constant_denominator,
        as_of=snapshot.as_of,
        locked_at_submission=True,
    )


def _pins(pins: PinSet) -> tuple[public.PinInfo, ...]:
    return tuple(
        public.PinInfo(
            component=pin.component,
            repository=pin.repository,
            commit=pin.commit,
            toolchain=pin.toolchain,
            version=pin.version,
            enabled=pin.enabled,
        )
        for pin in pins.pins
    )


def _task(entry: TaskEntry, *, attempts: int) -> public.ConjectureTask:
    return public.ConjectureTask(
        task_id=entry.task_id,
        task_mode=entry.manifest.task_mode,
        task_bundle_sha256=entry.task_bundle_sha256,
        attempts=attempts,
    )


def _task_detail(
    entry: TaskEntry, *, settings: Settings, attempts: int
) -> public.ConjectureTaskDetail:
    return public.ConjectureTaskDetail(
        task_id=entry.task_id,
        task_mode=entry.manifest.task_mode,
        task_bundle_sha256=entry.task_bundle_sha256,
        attempts=attempts,
        challenge_lean=entry.challenge_lean,
        machine_contract=_machine_contract(entry, settings),
    )


def _summary(
    item: Conjecture,
    *,
    quote: LiveBounty,
    attempts: int,
    by_task: Mapping[str, int],
    alpha_usd: Decimal | None,
) -> public.ConjectureSummary:
    name = conjectures.display_name(item)
    return public.ConjectureSummary(
        slug=item.slug,
        display_title=name.display_title,
        title_parts=public.TitleParts.of(name),
        title=conjectures.title(item),
        statement=item.source.type_pretty,
        summary=item.source.docstring,
        category=item.source.category,
        classification=item.classification,
        task_modes=item.task_modes,
        tier=item.tier,
        ams_subjects=item.source.ams_subjects,
        is_open=conjectures.is_open(item),
        problem_id=item.problem_id,
        reward_target_id=item.reward_target_id,
        tasks=tuple(
            _task(entry, attempts=by_task.get(entry.task_id, 0)) for entry in item.tasks
        ),
        bounty=_bounty(quote, alpha_usd=alpha_usd),
        attempts=attempts,
    )


def _machine_contract(entry: TaskEntry, settings: Settings) -> public.MachineContract:
    manifest = entry.manifest
    return public.MachineContract(
        task_id=entry.task_id,
        reward_target_id=entry.reward_target_id,
        task_bundle_sha256=entry.task_bundle_sha256,
        target_type_sha256s=entry.target_type_sha256s,
        bundle_format=BUNDLE_FORMAT,
        task_mode=manifest.task_mode,
        classification=manifest.classification.value,
        challenge_module=manifest.challenge_module,
        solution_module=manifest.solution_module,
        target_theorem=manifest.target_theorem,
        theorem_names=manifest.theorem_names,
        definition_names=manifest.definition_names,
        permitted_axioms=manifest.permitted_axioms,
        forbidden_dependencies=manifest.forbidden_dependencies,
        timeout_seconds=manifest.timeout_seconds,
        max_submission_bytes=manifest.max_submission_bytes,
        max_bundle_bytes=settings.max_bundle_bytes,
        adapter_version=manifest.adapter_version,
        answer_policy=dict(manifest.answer_policy),
    )


def _redirect(index: ConjectureIndex, slug: str, settings: Settings, *, suffix: str = "") -> RedirectResponse:
    """A 301 to the stable slug for a task-id-shaped URL, or 404.

    Reached only when `slug` is not a known conjecture. Two kinds of caller land here: a link
    minted before stable slugs existed, and a solver who pasted a task id out of a bundle or a
    report. Both mean one conjecture unambiguously, so a redirect is more useful than a 404.

    301, not 302: the mapping really is permanent. `resolve_legacy` matches on the theorem
    fragment inside a task id, which does not depend on the pinned revision, so the target does
    not change at the next rotation either — and it returns None rather than guessing when a
    fragment is ambiguous, so this cannot make up a destination.
    """
    target = index.resolve_legacy(slug)
    if target is None:
        raise NotFound("no such conjecture")
    response = RedirectResponse(
        url=f"{router.prefix}/conjectures/{target}{suffix}", status_code=301
    )
    _cache(response, settings)
    return response


def _withdrawn_bounty(settings: Settings) -> public.BountyInfo:
    """The bounty block for a conjecture that has left the pool.

    The pricer is not consulted at all. A retired target is not among the reward targets it was
    built with, so asking it would mean asking a question with no defined answer and then
    discarding it — and quoting an amount, even a stale one, next to a target nothing can be
    submitted against is the one number on this page that could mislead a solver into work.

    `as_of` is the moment of the response rather than a cached snapshot, because the statement
    being made — this target is closed — is true now and was true when it was retired.
    """
    return public.BountyInfo(
        amount_rao=None,
        amount_usd=None,
        policy_version=settings.bounty_policy_version,
        available=False,
        reason="WITHDRAWN",
        as_of=datetime.now(UTC),
        locked=False,
    )


def _retired_detail(
    item: RetiredConjecture,
    *,
    services,
    settings: Settings,
    attempts: int,
    by_task: Mapping[str, int],
) -> public.ConjectureDetail:
    """A retired conjecture rendered as a detail page.

    Every field a live conjecture publishes about the *problem* is here, recovered from the
    bundles at the commit that deleted them: statement, docstring, category, AMS subjects,
    references, and the exact `Challenge.lean`. What is deliberately absent is everything about
    *submitting*: `machine_contract` is null on each task and `bounty.reason` is `WITHDRAWN`.

    Attempt counts are read from the database exactly as they are for a live target. They are the
    reason the page exists — the work recorded against this conjecture does not stop being real
    when the target closes.
    """
    name = conjectures.display_name(item)
    return public.ConjectureDetail(
        slug=item.slug,
        # Named by the same two functions a live conjecture is, so the two cannot drift: a reader
        # must not see a conjecture renamed by the event that closed it.
        display_title=name.display_title,
        title_parts=public.TitleParts.of(name),
        title=conjectures.title(item),
        statement=item.source.type_pretty,
        summary=item.source.docstring,
        category=item.source.category,
        classification=item.classification,
        task_modes=item.task_modes,
        tier=item.tier,
        ams_subjects=item.source.ams_subjects,
        # Read from the source declaration, exactly as `conjectures.is_open` does for a live
        # target: whether the conjecture is open *upstream* is a fact about mathematics, and it
        # does not change because this pool stopped issuing tasks for it. Whether it can be
        # submitted against is what `retirement` and `bounty.reason` report.
        is_open=item.source.category == conjectures.OPEN_CATEGORY,
        problem_id=item.problem_id,
        reward_target_id=item.reward_target_id,
        source_theorem=item.source.theorem,
        source_module=item.source.module,
        source_path=item.source.source_path,
        supported_modes=item.source.supported_modes,
        references=tuple(_reference(value) for value in item.source.references),
        tasks=tuple(
            public.ConjectureTaskDetail(
                task_id=task.task_id,
                task_mode=task.task_mode,
                task_bundle_sha256=task.task_bundle_sha256,
                attempts=by_task.get(task.task_id, 0),
                challenge_lean=task.challenge_lean,
                machine_contract=None,
            )
            for task in item.tasks
        ),
        bounty=_withdrawn_bounty(settings),
        retirement=public.RetirementInfo(
            retired_on=item.retired_on,
            reason_code=item.reason_code,
            reason=item.reason,
            decision_url=item.decision_url,
            recovered_from_commit=item.recovered_from_commit,
        ),
        submission_price_rao=settings.payment_amount_rao,
        attempts=attempts,
        repository_commit=services.index.repository_commit,
        pins=_pins(services.pins),
    )


# --- Endpoints -----------------------------------------------------------------------------


@router.get(
    "/conjectures",
    response_model=public.ConjectureListResponse,
    summary="List conjectures with filters and facet counts",
)
async def list_conjectures(
    response: Response,
    services: ServicesDep,
    session: SessionDep,
    # Each of these is repeatable, so every bound is spelled twice on purpose: `max_length` on the
    # `Query` caps how many times the filter may appear, and the item alias caps one value. See
    # `MAX_FILTER_VALUES` for why putting a value's bound on the `Query` is not a shorthand for
    # this but a different, broken thing.
    category: Annotated[list[FilterValue] | None, Query(max_length=MAX_FILTER_VALUES)] = None,
    classification: Annotated[
        list[FilterValue] | None, Query(max_length=MAX_FILTER_VALUES)
    ] = None,
    task_mode: Annotated[list[FilterValue] | None, Query(max_length=MAX_FILTER_VALUES)] = None,
    tier: Annotated[list[FilterValue] | None, Query(max_length=MAX_FILTER_VALUES)] = None,
    ams_subject: Annotated[list[AmsSubject] | None, Query(max_length=MAX_FILTER_VALUES)] = None,
    is_open: bool | None = None,
    q: Annotated[str | None, Query(max_length=conjectures.MAX_QUERY_LENGTH)] = None,
    sort: Annotated[str, Query(pattern="^[a-z]{1,16}$")] = conjectures.SORT_SLUG,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> public.ConjectureListResponse:
    settings = services.settings
    filters = conjectures.ConjectureFilters(
        category=tuple(category or ()),
        classification=tuple(classification or ()),
        task_mode=tuple(task_mode or ()),
        tier=tuple(tier or ()),
        ams_subject=tuple(ams_subject or ()),
        is_open=is_open,
        query=q or "",
    )
    # An unknown sort key falls back to slug rather than erroring: the ordering of a public list
    # is not worth a 4xx, and the pattern above already bounds what reaches this.
    page = conjectures.query(
        services.index, filters, sort=sort, limit=limit, offset=offset
    )
    # Two grouped scans, both index-only and both for the whole pool: the conjecture total that
    # the summary reports, and the per-task split that lets a reader see which direction has been
    # attempted. Cheaper than a per-row count and it keeps the two numbers consistent.
    attempts = await public_store.attempts_by_conjecture(session)
    by_task = await public_store.attempts_by_task(session)
    snapshot = await services.pricing.quote_many(
        session,
        reward_target_ids=tuple(item.reward_target_id for item in page.items),
    )
    # The first pricing read registers any newly pinned stable targets. Subsequent reads are
    # no-ops, but this commit is what makes their original age survive a restart.
    await session.commit()
    alpha_usd = await services.bounty_usd.alpha_usd() if page.items else None

    _cache(response, settings)
    return public.ConjectureListResponse(
        total=page.total,
        items=tuple(
            _summary(
                item,
                quote=snapshot.quotes[item.reward_target_id],
                attempts=attempts.get(item.reward_target_id, 0),
                by_task=by_task,
                alpha_usd=alpha_usd,
            )
            for item in page.items
        ),
        facets=tuple(
            public.Facet(
                field=field,
                values=tuple(
                    public.FacetValue(value=item.value, count=item.count)
                    for item in values
                ),
            )
            for field, values in page.facets
        ),
        repository_commit=services.catalog.repository_commit,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/conjectures/{slug}",
    response_model=public.ConjectureDetail,
    summary="One conjecture in full, with a Lean challenge per attack direction",
)
async def read_conjecture(
    slug: Annotated[str, SLUG_PATH],
    response: Response,
    services: ServicesDep,
    session: SessionDep,
) -> public.ConjectureDetail | Response:
    settings = services.settings
    item = services.index.get(slug)
    if item is None:
        # A miss on the live index is not necessarily a dead URL. A retired target keeps its page
        # so the results and attribution already earned against it stay citable; only then does a
        # miss fall through to the legacy-slug redirect and finally to 404.
        withdrawn = services.index.get_retired(slug)
        if withdrawn is not None:
            attempts = await public_store.attempts_for_conjecture(
                session, withdrawn.reward_target_id
            )
            by_task = await public_store.attempts_by_task(session)
            await session.commit()
            _cache(response, settings)
            return _retired_detail(
                withdrawn,
                services=services,
                settings=settings,
                attempts=attempts,
                by_task=by_task,
            )
        return _redirect(services.index, slug, settings)

    attempts = await public_store.attempts_for_conjecture(session, item.reward_target_id)
    by_task = await public_store.attempts_by_task(session)
    quote = await services.pricing.quote(
        session, reward_target_id=item.reward_target_id
    )
    await session.commit()
    alpha_usd = await services.bounty_usd.alpha_usd()
    _cache(response, settings)
    name = conjectures.display_name(item)
    return public.ConjectureDetail(
        slug=item.slug,
        display_title=name.display_title,
        title_parts=public.TitleParts.of(name),
        title=conjectures.title(item),
        statement=item.source.type_pretty,
        summary=item.source.docstring,
        category=item.source.category,
        classification=item.classification,
        task_modes=item.task_modes,
        tier=item.tier,
        ams_subjects=item.source.ams_subjects,
        is_open=conjectures.is_open(item),
        problem_id=item.problem_id,
        reward_target_id=item.reward_target_id,
        source_theorem=item.source.theorem,
        source_module=item.source.module,
        source_path=item.source.source_path,
        supported_modes=item.source.supported_modes,
        references=tuple(_reference(value) for value in item.source.references),
        tasks=tuple(
            _task_detail(
                entry, settings=settings, attempts=by_task.get(entry.task_id, 0)
            )
            for entry in item.tasks
        ),
        bounty=_bounty(quote, alpha_usd=alpha_usd),
        submission_price_rao=settings.payment_amount_rao,
        attempts=attempts,
        repository_commit=services.index.repository_commit,
        pins=_pins(services.pins),
    )


@router.get(
    "/index",
    response_model=public.ConjectureIndexResponse,
    summary="Every problem in the pool, with its variants, as one flat index",
)
async def read_index(
    request: Request, response: Response, services: ServicesDep
) -> public.ConjectureIndexResponse | Response:
    """A problem-level table of contents over the pool.

    The one endpoint here that reads nothing but the startup index — no database at all, not even
    the attempt counters. That is what makes it safe to leave unpaginated: the work is a grouping
    of a few hundred in-memory objects into a response of identifiers, with no statement, no Lean
    and no bounty quote in it. A caller that wants any of those follows a slug to
    `/v1/catalog/conjectures/{slug}`.

    It carries a strong `ETag` and honours `If-None-Match`, and this is the endpoint that argument
    fits best. The body is a pure function of the pinned pool: it holds no counter, no bounty quote
    and no price, so it is byte-identical on every request until a pin rotation replaces the pool —
    and a table of contents is the thing a site re-fetches most. Without a validator a client
    re-downloads the whole index every time `max-age` lapses, to be handed the same bytes.

    Retired targets are present and flagged, at both levels — the one place this surface differs
    from `/v1/catalog/conjectures`, which lists only what may be submitted against. A table of
    contents that omitted them would disagree with the detail pages it links to, since a retired
    conjecture stays readable so the results earned against it remain citable. `retired` is what
    keeps their presence from reading as an offer, and it describes one conjecture rather than a
    family: a problem headed by a retired root can still hold submittable variants.
    """
    settings = services.settings
    grouped = conjectures.families(services.index)
    index = public.ConjectureIndexResponse(
        total=len(grouped),
        items=tuple(_index_entry(family) for family in grouped),
        repository_commit=services.index.repository_commit,
    )
    not_modified = _conditional(request, response, settings, index)
    return not_modified if not_modified is not None else index


def _index_entry(family: conjectures.ProblemFamily) -> public.ConjectureIndexEntry:
    return public.ConjectureIndexEntry(
        slug=family.slug,
        # Read off the family rather than derived here: `families` computed it from the same
        # declaration it picked the representative from, and re-deriving it at serialisation time is
        # how an index entry comes to disagree with the detail page it links to.
        display_title=family.name.display_title,
        title_parts=public.TitleParts.of(family.name),
        source_theorem=family.source_theorem,
        erdos_problem_number=family.erdos_problem_number,
        qualifier=family.qualifier,
        retired=family.retired,
        variants=tuple(
            public.ConjectureVariantRef(
                slug=variant.slug, task_mode=mode, retired=variant.retired
            )
            # `task_modes` is already ordered by `PRODUCTION_TASK_MODES` on both a live conjecture
            # and a retired one, so `formalized` precedes `counterexample` regardless of how the
            # pool was walked or whether the target has left it.
            for variant in family.variants
            for mode in variant.task_modes
        ),
    )


@router.get(
    "/meta",
    response_model=public.PoolMeta,
    summary="Pool metadata: counts, credit price, treasury, bounty model, pins",
)
async def read_meta(
    request: Request,
    response: Response,
    services: ServicesDep,
    session: SessionDep,
) -> public.PoolMeta | Response:
    """Pool metadata and its current dynamic-pricing snapshot, with a strong `ETag`."""
    settings = services.settings
    items = services.index.all()
    snapshot = await services.pricing.quote_many(
        session,
        reward_target_ids=tuple(item.reward_target_id for item in items),
    )
    await session.commit()
    alpha_usd = await services.bounty_usd.alpha_usd()
    meta = _meta(services, settings, items, snapshot, alpha_usd=alpha_usd)
    not_modified = _conditional(request, response, settings, meta)
    return not_modified if not_modified is not None else meta


def _conditional(
    request: Request, response: Response, settings: Settings, payload: public.Model
) -> Response | None:
    """Tag `response` with a strong `ETag`, and answer `304` when the caller already has that body.

    Returns the 304 to send instead of the payload, or None to send the payload.

    The tag is hashed from the *serialised payload* rather than assembled from the inputs that
    built it, which is what makes it impossible for the validator to drift from the body: anything
    that changes what is published changes the tag, including a field added to the model later.
    Truncated to 32 hex characters — 128 bits, far past where an accidental collision between two
    revisions of one endpoint's body is a practical concern.

    Correctness does not depend on where the payload came from; usefulness does. A body that is
    recomputed from the database on every request and changes constantly would be tagged honestly
    and never hit, so the caller would pay the hash and get nothing back. Worth attaching only
    where the body is stable between requests — which is why the result feeds do not have one.
    """
    etag = '"' + sha256_text(payload.model_dump_json())[len("sha256:") :][:32] + '"'
    _cache(response, settings)
    response.headers["ETag"] = etag
    if not _matches(request.headers.get("if-none-match"), etag):
        return None
    # A 304 must repeat the validator and the caching headers, and must carry no body.
    return Response(
        status_code=304,
        headers={"ETag": etag, "Cache-Control": response.headers["Cache-Control"]},
    )


def _matches(header: str | None, etag: str) -> bool:
    """Whether `If-None-Match` names this entity.

    A list, per RFC 9110, and `*` matches anything. Weak-comparison prefixes are stripped
    because the tag above is strong and a weakened form of it still identifies the same bytes.
    """
    if not header:
        return False
    candidates = [item.strip() for item in header.split(",")]
    return "*" in candidates or any(
        item.removeprefix("W/") == etag for item in candidates
    )


def _meta(
    services,
    settings: Settings,
    items,
    snapshot: BountyPoolSnapshot,
    *,
    alpha_usd: Decimal | None,
) -> public.PoolMeta:
    return public.PoolMeta(
        repository_commit=services.index.repository_commit,
        bundle_format=BUNDLE_FORMAT,
        # Conjectures, not tasks. Halved against what this reported before grouping, because a
        # theorem issued in two modes was being counted twice.
        conjectures=len(items),
        open_conjectures=sum(1 for item in items if conjectures.is_open(item)),
        tiers=_counts(items, conjectures.FACET_TIER),
        task_modes=_counts(items, conjectures.FACET_TASK_MODE),
        categories=_counts(items, conjectures.FACET_CATEGORY),
        credit_price_rao=settings.payment_amount_rao,
        credits_per_attempt=CREDITS_PER_ATTEMPT,
        treasury_address=settings.payment_recipient,
        max_bundle_bytes=settings.max_bundle_bytes,
        bounty=_bounty_pool(snapshot, alpha_usd=alpha_usd),
        pins=_pins(services.pins),
        pins_sha256=services.pins.lock_sha256,
    )


@router.get(
    "/conjectures/{slug}/activity",
    response_model=public.ConjectureActivity,
    summary="Anonymised activity on one conjecture",
)
async def read_activity(
    slug: Annotated[str, SLUG_PATH],
    response: Response,
    services: ServicesDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=MAX_ACTIVITY_ITEMS)] = DEFAULT_ACTIVITY_ITEMS,
) -> public.ConjectureActivity | Response:
    settings = services.settings
    item = services.index.get(slug)
    if item is None:
        # Activity is the whole point of keeping a retired page: it is where the attempts,
        # solvers and verified results earned against the target are shown.
        item = services.index.get_retired(slug)
    if item is None:
        return _redirect(services.index, slug, settings, suffix="/activity")

    target = item.reward_target_id
    activity = await public_store.activity(
        session,
        target,
        limit=limit,
        pseudonymise=lambda hotkey: _pseudonym(settings, target, hotkey),
    )
    _cache(response, settings)
    return public.ConjectureActivity(
        slug=item.slug,
        attempts=activity.attempts,
        solvers=activity.solvers,
        verified=activity.verified,
        certified=activity.certified,
        items=tuple(
            public.PublicActivityItem(
                event=item.event,
                occurred_at=_to_hour(item.occurred_at),
                solver=item.solver,
            )
            for item in activity.items
        ),
    )


@router.get(
    "/credit-pricing",
    response_model=account_schemas.CreditPricing,
    summary="What a credit costs and how to buy one",
)
async def read_credit_pricing(
    response: Response, services: ServicesDep
) -> account_schemas.CreditPricing:
    """Unauthenticated on purpose: a visitor decides whether to sign up by reading this.

    `price_usd` is null unless an operator pinned one. Converting TAO to USD needs a live
    external rate this validator does not have, and a made-up number on a purchase page is worse
    than no number — so the field is null, and when it is set it carries the date it was set, so
    a reader can judge how stale it is.
    """
    settings = services.settings
    _cache(response, settings)
    return account_schemas.CreditPricing(
        price_rao=settings.payment_amount_rao,
        price_usd=settings.credit_price_usd or None,
        price_usd_asof=(
            date.fromisoformat(settings.credit_price_usd_asof)
            if settings.credit_price_usd and settings.credit_price_usd_asof
            else None
        ),
        packages=tuple(
            account_schemas.CreditPackage(
                credits=item.credits,
                bonus_credits=item.bonus_credits,
                total_credits=item.total_credits,
                price_rao=item.price_rao,
            )
            for item in services.packages
            if item.credits == 1 and item.bonus_credits == 0
        ),
        # Filtered by what this deployment can actually take money through: a method the page
        # renders and the API refuses is worse than one it never offers.
        methods=credit_config.payment_methods(
            tmc_pay_enabled=settings.tmc_pay_enabled
        ),
        recipient=settings.payment_recipient,
    )


@router.get(
    "/submission-terms",
    response_model=account_schemas.SubmissionTerms,
    summary="The submission terms and manual-review reason codes",
)
async def read_submission_terms(
    response: Response, services: ServicesDep
) -> account_schemas.SubmissionTerms:
    """The terms a miner accepts by submitting, and the complete lists of reasons a review may
    approve or refuse a reward.

    The lists are shared with the Stage 3 review page deliberately: a reviewer must choose a
    published code, and one source in one place is what guarantees that.
    """
    terms = services.terms
    _cache(response, services.settings)
    return account_schemas.SubmissionTerms(
        version=terms.version,
        body_md=terms.body_md,
        effective_from=terms.effective_from,
        approval_reasons=tuple(
            account_schemas.ApprovalReason(code=code, description=description)
            for code, description in terms.approval_reasons
        ),
        disqualification_reasons=tuple(
            account_schemas.DisqualificationReason(code=code, description=description)
            for code, description in terms.disqualification_reasons
        ),
    )


def _pseudonym(settings: Settings, reward_target_id: str, hotkey: str) -> str:
    """A per-conjecture pseudonym for a hotkey.

    The conjecture's identity is inside the MAC, not concatenated after it, so the same solver
    gets a different pseudonym on every conjecture and the pseudonyms cannot be joined across the
    catalog to rebuild one solver's history. Length-prefixed so `(conjecture, key)` pairs cannot
    be chosen to collide by shifting the boundary between them.

    Keyed on the reward target rather than on a task id, matching the activity query. A task-keyed
    MAC would give one solver two pseudonyms on a page that now shows both attack directions,
    making one person look like two, and would rename every solver at each pin rotation.

    The salt is `PUBLIC_ACTIVITY_SALT`, which production must set to something that is not the
    published development constant — a hotkey is a 48-character address from a known alphabet, so
    an unsalted or publicly-salted digest is reversible by enumeration for anyone with a list of
    subnet hotkeys.
    """
    message = (
        f"{len(reward_target_id)}:{reward_target_id}:{len(hotkey)}:{hotkey}"
    ).encode("utf-8")
    digest = hmac.new(settings.activity_salt.encode("utf-8"), message, "sha256").hexdigest()
    return digest[:PSEUDONYM_LENGTH]


def _to_hour(value: datetime) -> datetime:
    """Truncate to the hour.

    An attempt is funded by a transfer that is visible on chain, with its sender, at a known
    block time. A per-second timestamp here would let anyone join the two and undo the pseudonym;
    an hour bucket makes that join ambiguous whenever more than one transfer landed in the hour,
    and costs a reader nothing — the stream is for "is anyone working on this", not forensics.
    """
    moment = value if value.tzinfo else value.replace(tzinfo=UTC)
    return moment.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def _counts(items, field: str) -> tuple[public.FacetValue, ...]:
    return tuple(
        public.FacetValue(value=value.value, count=value.count)
        for value in conjectures.tally(items, field)
    )


__all__ = ["CREDITS_PER_ATTEMPT", "PSEUDONYM_LENGTH", "SLUG_PATH", "router"]
