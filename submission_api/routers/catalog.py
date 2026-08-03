"""The public conjecture catalog.

Unauthenticated, and read by a browser. The task pool and its digests are already published, so
there is nothing here a caller could not derive from the repository — but that is a reason these
endpoints are safe to expose, not a reason to be careless with what they return. Three rules
shape the module:

* **The slug is the task id.** A conjecture is identified by the same string a miner commits to
  in a bundle, so a reader of the website and a solver writing a submission are talking about
  the same object. Minting a second identifier would create two names for one commitment and a
  mapping that could drift.
* **Every list is bounded.** `limit` is capped, the free-text filter is length-capped and is a
  substring test rather than a pattern, and the facets are computed over a fixed in-memory list.
  None of these endpoints can be made to do unbounded work.
* **The statement is served from the audited bytes.** `challenge_lean` is the exact
  `Challenge.lean` hashed into the published `task_bundle_sha256`, held in memory since startup,
  so what a reader verifies is what the verifier compiles.

Only the attempt counters touch the database. Everything else is a projection of the catalog
`TaskCatalog.load` built at startup.
"""

from __future__ import annotations

import hmac
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Path, Query, Request, Response

from conjectures_subnet.db import public as public_store
from submission_api import conjectures, credits as credit_config
from submission_api import schemas_account as account_schemas, schemas_public as public
from submission_api.dependencies import ServicesDep, SessionDep
from submission_api.errors import NotFound
from submission_api.pins import PinSet
from submission_api.settings import (
    DEFAULT_ACTIVITY_ITEMS,
    DEFAULT_PAGE_SIZE,
    MAX_ACTIVITY_ITEMS,
    MAX_PAGE_SIZE,
    Settings,
)
from submission_api.taskpool import TaskEntry, TaskNotAllowed
from verifier.bundle import BUNDLE_FORMAT
from verifier.hashing import sha256_text

router = APIRouter(prefix="/v1/catalog", tags=["catalog"])

# One credit buys one verification attempt. The credit ledger itself is Stage 2; until it
# exists, a credit is exactly the configured submission price and this is the constant that
# says so in one place.
CREDITS_PER_ATTEMPT = 1

# A pseudonym long enough that collisions across a pool of solvers are not a practical concern,
# short enough not to look like a key. It is a truncated MAC, so truncation is what limits what
# an offline guessing attack recovers even if the salt leaked.
PSEUDONYM_LENGTH = 12

REFERENCE_MARKDOWN = "]("


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
    """Split the catalog's Markdown link into a label and a URL.

    The upstream catalog records references as `[label](url)`. Parsed here rather than shipped
    raw so a website does not have to render Markdown to show a link, and so a reference that is
    *not* a link still arrives as a usable label.
    """
    if raw.startswith("[") and REFERENCE_MARKDOWN in raw and raw.endswith(")"):
        label, _, remainder = raw[1:-1].partition(REFERENCE_MARKDOWN)
        return public.Reference(label=label, url=remainder or None)
    return public.Reference(label=raw)


def _bounty(settings: Settings) -> public.BountyInfo:
    return public.BountyInfo(
        amount_rao=settings.bounty_amount_rao,
        policy_version=settings.bounty_policy_version,
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


def _summary(
    entry: TaskEntry, *, settings: Settings, attempts: int
) -> public.ConjectureSummary:
    return public.ConjectureSummary(
        slug=entry.task_id,
        title=conjectures.title(entry),
        statement=entry.source.type_pretty,
        summary=entry.source.docstring,
        category=entry.source.category,
        classification=entry.manifest.classification.value,
        task_mode=entry.manifest.task_mode,
        tier=entry.tier,
        ams_subjects=entry.source.ams_subjects,
        is_open=conjectures.is_open(entry),
        task_bundle_sha256=entry.task_bundle_sha256,
        bounty=_bounty(settings),
        attempts=attempts,
    )


def _machine_contract(entry: TaskEntry, settings: Settings) -> public.MachineContract:
    manifest = entry.manifest
    return public.MachineContract(
        task_id=entry.task_id,
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
    category: Annotated[list[str] | None, Query(max_length=64)] = None,
    classification: Annotated[list[str] | None, Query(max_length=64)] = None,
    task_mode: Annotated[list[str] | None, Query(max_length=64)] = None,
    tier: Annotated[list[str] | None, Query(max_length=64)] = None,
    ams_subject: Annotated[list[int] | None, Query(ge=0, le=99)] = None,
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
        services.catalog, filters, sort=sort, limit=limit, offset=offset
    )
    attempts = await public_store.attempts_by_task(session)

    _cache(response, settings)
    return public.ConjectureListResponse(
        total=page.total,
        items=tuple(
            _summary(
                entry, settings=settings, attempts=attempts.get(entry.task_id, 0)
            )
            for entry in page.entries
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
    summary="One conjecture in full, with its Lean challenge and machine contract",
)
async def read_conjecture(
    slug: Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9-]{0,254}$")],
    response: Response,
    services: ServicesDep,
    session: SessionDep,
) -> public.ConjectureDetail:
    settings = services.settings
    try:
        entry = services.catalog.get(slug)
    except TaskNotAllowed as exc:
        raise NotFound("no such conjecture") from exc

    attempts = await public_store.attempts_for_task(session, entry.task_id)
    _cache(response, settings)
    return public.ConjectureDetail(
        slug=entry.task_id,
        title=conjectures.title(entry),
        statement=entry.source.type_pretty,
        summary=entry.source.docstring,
        category=entry.source.category,
        classification=entry.manifest.classification.value,
        task_mode=entry.manifest.task_mode,
        tier=entry.tier,
        ams_subjects=entry.source.ams_subjects,
        is_open=conjectures.is_open(entry),
        source_theorem=entry.source.theorem,
        source_module=entry.source.module,
        source_path=entry.source.source_path,
        supported_modes=entry.source.supported_modes,
        references=tuple(_reference(item) for item in entry.source.references),
        challenge_lean=entry.challenge_lean,
        machine_contract=_machine_contract(entry, settings),
        bounty=_bounty(settings),
        submission_price_rao=settings.payment_amount_rao,
        attempts=attempts,
        repository_commit=services.catalog.repository_commit,
        pins=_pins(services.pins),
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
) -> public.PoolMeta | Response:
    """Pool metadata, with a strong `ETag`.

    This is the one public endpoint whose payload is derived entirely from startup state — the
    catalog, the pin set and the settings, none of which move while the process runs — so it can
    carry a real validator and answer `304` to a repeat read. The website hits it on every page
    load, and a conditional request there costs a hash rather than a response body.

    The list and detail endpoints deliberately carry no `ETag`: their payloads include live
    attempt counters, so any honest validator would have to be recomputed from the database on
    every request and would change constantly.
    """
    settings = services.settings
    entries = services.catalog.summaries()
    meta = _meta(services, settings, entries)

    # Hashed from the serialised payload rather than assembled from the inputs, so the validator
    # cannot drift from the body: any change to what is published changes the tag.
    etag = '"' + sha256_text(meta.model_dump_json())[len("sha256:") :][:32] + '"'
    _cache(response, settings)
    response.headers["ETag"] = etag
    if _matches(request.headers.get("if-none-match"), etag):
        # 304 must repeat the validator and the caching headers, and must carry no body.
        return Response(
            status_code=304,
            headers={
                "ETag": etag,
                "Cache-Control": response.headers["Cache-Control"],
            },
        )
    return meta


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


def _meta(services, settings: Settings, entries) -> public.PoolMeta:
    return public.PoolMeta(
        repository_commit=services.catalog.repository_commit,
        bundle_format=BUNDLE_FORMAT,
        conjectures=len(entries),
        open_conjectures=sum(1 for entry in entries if conjectures.is_open(entry)),
        tiers=_counts(entries, conjectures.FACET_TIER),
        task_modes=_counts(entries, conjectures.FACET_TASK_MODE),
        categories=_counts(entries, conjectures.FACET_CATEGORY),
        credit_price_rao=settings.payment_amount_rao,
        credits_per_attempt=CREDITS_PER_ATTEMPT,
        treasury_address=settings.payment_recipient,
        max_bundle_bytes=settings.max_bundle_bytes,
        bounty=_bounty(settings),
        pins=_pins(services.pins),
        pins_sha256=services.pins.lock_sha256,
    )


@router.get(
    "/conjectures/{slug}/activity",
    response_model=public.ConjectureActivity,
    summary="Anonymised activity on one conjecture",
)
async def read_activity(
    slug: Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9-]{0,254}$")],
    response: Response,
    services: ServicesDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=MAX_ACTIVITY_ITEMS)] = DEFAULT_ACTIVITY_ITEMS,
) -> public.ConjectureActivity:
    settings = services.settings
    try:
        entry = services.catalog.get(slug)
    except TaskNotAllowed as exc:
        raise NotFound("no such conjecture") from exc

    activity = await public_store.activity(
        session,
        entry.task_id,
        limit=limit,
        pseudonymise=lambda hotkey: _pseudonym(settings, entry.task_id, hotkey),
    )
    _cache(response, settings)
    return public.ConjectureActivity(
        slug=entry.task_id,
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
        ),
        methods=credit_config.PAYMENT_METHODS,
        recipient=settings.payment_recipient,
    )


@router.get(
    "/submission-terms",
    response_model=account_schemas.SubmissionTerms,
    summary="The submission terms and the disqualification reasons",
)
async def read_submission_terms(
    response: Response, services: ServicesDep
) -> account_schemas.SubmissionTerms:
    """The terms a miner accepts by submitting, and the complete list of reasons a review may
    refuse a reward.

    The list is shared with the Stage 3 review page deliberately: a reviewer must not be able to
    reject for a reason the miner was never shown, and one list in one place is what guarantees
    that.
    """
    terms = services.terms
    _cache(response, services.settings)
    return account_schemas.SubmissionTerms(
        version=terms.version,
        body_md=terms.body_md,
        effective_from=terms.effective_from,
        disqualification_reasons=tuple(
            account_schemas.DisqualificationReason(code=code, description=description)
            for code, description in terms.disqualification_reasons
        ),
    )


def _pseudonym(settings: Settings, task_id: str, hotkey: str) -> str:
    """A per-conjecture pseudonym for a hotkey.

    The task id is inside the MAC, not concatenated after it, so the same solver gets a different
    pseudonym on every conjecture and the pseudonyms cannot be joined across the catalog to
    rebuild one solver's history. Length-prefixed so `(task, key)` pairs cannot be chosen to
    collide by shifting the boundary between them.

    The salt is `PUBLIC_ACTIVITY_SALT`, which production must set to something that is not the
    published development constant — a hotkey is a 48-character address from a known alphabet, so
    an unsalted or publicly-salted digest is reversible by enumeration for anyone with a list of
    subnet hotkeys.
    """
    message = f"{len(task_id)}:{task_id}:{len(hotkey)}:{hotkey}".encode("utf-8")
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


def _counts(entries, field: str) -> tuple[public.FacetValue, ...]:
    return tuple(
        public.FacetValue(value=item.value, count=item.count)
        for item in conjectures.tally(entries, field)
    )


__all__ = ["CREDITS_PER_ATTEMPT", "PSEUDONYM_LENGTH", "router"]
