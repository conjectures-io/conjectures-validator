"""FloatLib's public reads, from FloatLib's own database.

Every route here matches only FloatLib's slugs -- `floatlib`, the paid full entry, and
`floatlib-<slot>`, its practice boards -- through the `floatlib` path convertor, and the app
includes this router before DEFLATE's, whose `{slug}` routes would otherwise answer them. A
FloatLib slug on a route only DEFLATE has (`/pareto`, the session submit path, the operator queue) falls
through to DEFLATE's handler, which 404s it (`routers.competitions.resolve_competition`).

The shapes are the FloatLib wire contract (`schemas_floatlib`). What a board means is FloatLib's rule, restated in `floatlib_store`; nothing here re-runs
its scoring. Signed full-entry submissions are handled by `floatlib_intake`; practice boards are read-only.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Path, Query
from sqlalchemy.exc import InterfaceError, OperationalError
from starlette.convertors import Convertor, register_url_convertor

from conjectures_subnet.axiom import get_axiom
from submission_api import floatlib_store as store
from submission_api import schemas_compression as sc
from submission_api import schemas_floatlib as s
from submission_api.competitions import (
    FLOATLIB,
    FLOATLIB_MAIN_SLOT,
    FLOATLIB_PRACTICE_SLOTS,
    FLOATLIB_SLUG,
    Competition,
    floatlib_slug,
)
from submission_api.dependencies import CompetitionSessionDep, ServicesDep, competition_sessions
from submission_api.errors import NotFound, ServiceUnavailable
from submission_api.routers.competitions import resolve_competition


class FloatlibSlug(Convertor):
    """A path segment that is one of FloatLib's slugs, and nothing else."""

    regex = FLOATLIB_SLUG + "(?:-(?:" + "|".join(FLOATLIB_PRACTICE_SLOTS) + "))?"

    def convert(self, value: str) -> str:
        return value

    def to_string(self, value: str) -> str:
        return value


register_url_convertor("floatlib", FloatlibSlug())

router = APIRouter(prefix="/v1/competitions", tags=["competitions"])
# Named `board`, not `slug`: OpenAPI drops the convertor, and a `{slug}` here would document
# these routes as DEFLATE's own, which then replace them in the schema.
SLUG = "/{board:floatlib}"
BoardPath = Annotated[
    str,
    Path(
        pattern="^" + FloatlibSlug.regex + "$",
        description="`floatlib`, the paid full entry, or a practice board: `floatlib-<slot>`",
        examples=["floatlib", "floatlib-add"],
    ),
]

# The bar until the weight setter has published its own: FloatLib's default `PROMOTION_BAR`.
DEFAULT_PROMOTION_BAR = 0.97
# The one file a FloatLib submission is, and its cap there.
KERNEL_FILE = {"name": "Kernel.lean", "max_bytes": 256_000}
ADMISSIONS = frozenset({"leader", "qualifies", "not-significant", "above-bar", "no-evidence"})


def iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def board_of(services, slug: str) -> str:
    """The slot a FloatLib slug names, or 404 when FloatLib is not served here."""
    resolve_competition(services, slug, FLOATLIB)
    return services.competitions.board(slug)[1]


def policy_of(publication) -> dict:
    snapshot = publication[1] if publication else None
    policy = snapshot.get("policy") if isinstance(snapshot, dict) else None
    return policy if isinstance(policy, dict) else {}


def promotion_bar(policy: dict) -> float:
    bar = policy.get("promotion_bar")
    return float(bar) if isinstance(bar, int | float) else DEFAULT_PROMOTION_BAR


def pays(policy: dict, slot: str) -> bool:
    """Whether the slot's champion's author is paid: its share of the budget, as published."""
    shares = policy.get("slot_shares")
    if isinstance(shares, dict):
        share = shares.get(slot)
        return isinstance(share, int | float) and share > 0
    return slot == FLOATLIB_MAIN_SLOT


def rank_shares(policy: dict) -> list[float] | None:
    """Published reward fractions; unknown for older or malformed publications."""
    shares = policy.get("rank_shares")
    if not isinstance(shares, list) or not shares:
        return None
    if any(type(share) not in (int, float) or not 0 <= share <= 1 for share in shares):
        return None
    return [float(share) for share in shares]


def outcomes(publication, slot: str) -> dict[int, str]:
    """Each submission's published admission outcome on `slot`; empty for an unscored board."""
    snapshot = publication[1] if publication else None
    board = ((snapshot or {}).get("slots") or {}).get(slot) if isinstance(snapshot, dict) else None
    items = board.get("items") if isinstance(board, dict) else None
    return {
        int(item["submission_id"]): item["admission"]
        for item in items or ()
        if isinstance(item, dict)
        and isinstance(item.get("submission_id"), int)
        and item.get("admission") in ADMISSIONS
    }


def kernel_times(slot: str, classes) -> dict[str, float] | None:
    """Nanoseconds per operation for each kernel, summed over its timed input types.

    The full entry's input types carry their kernel (`add:near-one`, `bmm:relu`); a practice
    board times one kernel, so its input types are all the board's own.
    """
    if not isinstance(classes, dict) or not classes:
        return None
    times: dict[str, float] = {}
    for name, timing in classes.items():
        ns = timing.get("submission_ns") if isinstance(timing, dict) else None
        if not isinstance(ns, int | float):
            continue
        kernel = name.split(":", 1)[0] if ":" in name else slot
        times[kernel] = times.get(kernel, 0.0) + float(ns)
    return times or None


async def summary(competition: Competition, slot: str, session, services) -> sc.Competition:
    """One FloatLib board in the index's shape, so `/v1/competitions` lists it like DEFLATE."""
    await store.compatible(session)
    publication = await store.publication(session)
    policy = policy_of(publication)
    snapshot = publication[1] if publication else {}
    sid = str(publication[0]) if publication else None
    return sc.Competition(
        slug=floatlib_slug(slot),
        name=competition.name if slot == FLOATLIB_MAIN_SLOT else f"{competition.name} {slot}",
        submissions_open=slot == FLOATLIB_MAIN_SLOT
        and getattr(services.settings, "floatlib_submissions_enabled", False)
        and not services.settings.submissions_paused,
        queue_depth=await store.queue_depth(session),
        current_snapshot_id=sid,
        description="Formally verified float kernels, king of the hill",
        files=[KERNEL_FILE],
        metric_definitions=[
            {"key": "ratio", "label": "Time / champion", "unit": "ratio", "better": "lower"}
        ],
        policy=policy or None,
        policy_status="ready" if policy else "unknown",
        execution_limits={"benchmark_timeout_seconds": None, "gate_timeout_seconds": None},
        context=sc.Context(
            snapshot_id=sid,
            computed_at=snapshot.get("computed_at") if isinstance(snapshot, dict) else None,
            policy_version=policy.get("version"),
            status="ready" if publication else "not_ready",
            freshness="unknown",
            freshness_reason="Recorded scoring pass; the board itself is read live.",
        ),
    )


async def listed(competition: Competition, services) -> sc.Competition:
    """The index's entry for FloatLib, read from FloatLib's own database."""
    sessions = competition_sessions(services, competition.slug)
    if sessions is None:
        raise ServiceUnavailable(
            "competitions are not configured on this deployment",
            reason_code="COMPETITIONS_UNAVAILABLE",
        )
    async with sessions() as session:
        try:
            return await summary(competition, FLOATLIB_MAIN_SLOT, session, services)
        except (OperationalError, InterfaceError) as exc:
            raise ServiceUnavailable(
                "the competition database is unreachable",
                reason_code="COMPETITION_UNAVAILABLE",
            ) from exc


@router.get(SLUG, response_model=sc.Competition)
async def detail(board: BoardPath, services: ServicesDep, session: CompetitionSessionDep):
    competition = resolve_competition(services, board, FLOATLIB)
    return await summary(competition, board_of(services, board), session, services)


async def rules(session) -> s.Health:
    """FloatLib's queue and the rules its latest published scoring applied."""
    await store.compatible(session)
    policy = policy_of(await store.publication(session))
    share = policy.get("competition_share")
    rule = policy.get("admission")
    admission = (
        s.AdmissionRule(draws=rule["draws"], quantile=rule["quantile"])
        if isinstance(rule, dict)
        and isinstance(rule.get("draws"), int)
        and isinstance(rule.get("quantile"), int | float)
        else None
    )
    return s.Health(
        ok=True,
        queued=await store.queue_depth(session),
        promotion_bar=promotion_bar(policy),
        competition_share=float(share) if isinstance(share, int | float) else None,
        admission=admission,
        rank_shares=rank_shares(policy),
    )


@router.get(SLUG + "/health", response_model=s.Health)
async def health(board: BoardPath, services: ServicesDep, session: CompetitionSessionDep):
    """The FloatLib host's queue and the rules its latest published scoring applied."""
    board_of(services, board)
    return await rules(session)


# `/health` at the root, as FloatLib's own API serves it, so the website reads it from either.
root_router = APIRouter(tags=["competitions"])


@root_router.get("/health", response_model=s.Health)
async def root_health(services: ServicesDep):
    """FloatLib's `/health`: liveness, its queue and its rules, which must not fail with its store.

    Like FloatLib's own: always `ok`, with `queued` and the published rules null while FloatLib's
    database cannot be read or is not configured here, and the default promotion bar until it
    has published its own. `/v1/competitions/floatlib/health` is the strict version, which
    answers 404 or 503 instead. Not the platform's liveness probe: that is `/healthz`.
    """
    competition = next((c for c in services.competitions if c.kind == FLOATLIB), None)
    sessions = competition_sessions(services, competition.slug) if competition else None
    if sessions is not None:
        try:
            async with sessions() as session:
                return await rules(session)
        except Exception as exc:  # noqa: BLE001 - this answer must not fail with the store
            get_axiom().warn(
                source="api-health",
                event_type="floatlib_health_degraded",
                error=f"{type(exc).__name__}: {exc}",
            )
    return s.Health(ok=True, promotion_bar=DEFAULT_PROMOTION_BAR)


@router.get(SLUG + "/leaderboard", response_model=s.Leaderboard)
async def leaderboard(
    board: BoardPath,
    services: ServicesDep,
    session: CompetitionSessionDep,
    champion_id: Annotated[
        int | None, Query(ge=1, description="Champion submission ID from /champions")
    ] = None,
):
    """The current board, or challengers measured against a selected past champion."""
    slot = board_of(services, board)
    await store.compatible(session)
    history = await store.promotions(session, slot)
    current = store.standing(history)
    selected = current
    if champion_id is not None:
        selected = next((p for p in history if p["id"] == champion_id), None)
        if selected is None:
            raise NotFound("No such champion on this board")
    historical = selected is not None and selected != current
    leader = selected["id"] if selected else None
    champion_sha = (
        selected["promoted_sha256"]
        if selected
        else await store.champion_sha256(session, slot, history)
    )
    rows = await store.board(session, slot, champion_sha, leader, historical=historical)
    publication = await store.publication(session)
    policy = policy_of(publication)
    bar = promotion_bar(policy)
    published = outcomes(publication, slot)
    promotions = {p["id"]: p for p in history}
    if historical:
        snapshot = publication[1] if publication else None
        published_board = ((snapshot or {}).get("slots") or {}).get(slot)
        if not isinstance(published_board, dict) or published_board.get("leader") != leader:
            # Old scoring snapshots may have expired. Do not borrow current admission outcomes
            # or rerun statistics; a promotion record still proves an actual takeover.
            published = {}
        published = {sid: outcome for sid, outcome in published.items() if outcome != "leader"}
        published.update(
            {
                row["id"]: "qualifies"
                for row in rows
                if row["id"] != leader and row["id"] in promotions
            }
        )
    return s.Leaderboard(
        slot=slot,
        incumbent_sha256=await store.latest_incumbent(session, slot),
        champion_sha256=champion_sha,
        verifier_fingerprint=await store.latest_fingerprint(session),
        promotion_bar=bar,
        leader=leader,
        pays=pays(policy, slot),
        ranking=[
            s.Ranking(
                rank=index,
                submission=row["id"],
                hotkey=row["hotkey"],
                ratio=row["ratio"] or 0.0,
                qualifies=(row["ratio"] or 1.0) <= bar,
                # The champion is the leader by its promotion, on every board; the others'
                # outcomes are the published scoring's, which only scores the paid board.
                admission="leader" if row["id"] == leader else published.get(row["id"]),
                submitted_at=iso(row["submitted_at"]),
                kernels=kernel_times(slot, row["classes"]),
                promoted_at=(
                    iso(promotions[row["id"]]["promoted_at"])
                    if row["id"] == leader or (historical and row["id"] in promotions)
                    else None
                ),
            )
            for index, row in enumerate(rows, 1)
        ],
    )


@router.get(SLUG + "/champions", response_model=list[s.Champion])
async def champions(board: BoardPath, services: ServicesDep, session: CompetitionSessionDep):
    """Every champion the board has had, newest first, the one holding it marked `current`."""
    slot = board_of(services, board)
    await store.compatible(session)
    history = await store.promotions(session, slot)
    current = store.standing(history)
    policy = policy_of(await store.publication(session))
    shares = (rank_shares(policy) or []) if pays(policy, slot) else []
    eligible = [p for p in history if p["demoted_at"] is None][: len(shares)]
    ranks = {p["promotion_id"]: index for index, p in enumerate(eligible, 1)}
    speedups = store.speedups(history)
    return [
        s.Champion(
            submission=p["id"],
            hotkey=p["hotkey"],
            ratio=p["ratio"],
            speedup_vs_shipped=speedups.get(p["promotion_id"]),
            promoted_at=iso(p["promoted_at"]),
            demoted_at=iso(p["demoted_at"]) if p["demoted_at"] is not None else None,
            current=current is not None and p["promotion_id"] == current["promotion_id"],
            rank=ranks.get(p["promotion_id"]),
            rank_share=shares[ranks[p["promotion_id"]] - 1] if p["promotion_id"] in ranks else None,
        )
        for p in history
    ]


async def _row(services, board: str, session, submission_id: int) -> dict:
    slot = board_of(services, board)
    await store.compatible(session)
    row = await store.get_submission(session, submission_id)
    if row is None or row["slot"] != slot:
        raise NotFound("No such submission")
    return row


@router.get(SLUG + "/submissions/{submission_id}", response_model=s.SubmissionView)
async def submission(
    board: BoardPath,
    services: ServicesDep,
    session: CompetitionSessionDep,
    submission_id: int = Path(ge=1),
):
    """One submission's state and, once gated, its verdict, per-input-type timings included."""
    row = await _row(services, board, session, submission_id)
    accepted = row["state"] == "accepted"
    newest = await store.latest_fingerprint(session) if accepted else None
    return s.SubmissionView(
        id=row["id"],
        hotkey=row["hotkey"],
        slot=row["slot"],
        digest=row["digest"],
        submitted_at=iso(row["submitted_at"]),
        state=row["state"],
        exit_code=row["exit_code"],
        stage=row["stage"],
        reason=row["reason"],
        ratio=row["ratio"],
        classes=row["classes"],
        seed=row["seed"],
        incumbent_sha256=row["incumbent_sha256"],
        champion_sha256=row["champion_sha256"],
        verifier_fingerprint=row["verifier_fingerprint"],
        verification_current=(row["verifier_fingerprint"] == newest) if accepted else None,
    )


@router.get(SLUG + "/submissions/{submission_id}/report", response_model=s.Report)
async def report(
    board: BoardPath,
    services: ServicesDep,
    session: CompetitionSessionDep,
    submission_id: int = Path(ge=1),
):
    """The gate's verdict JSON in full; its own endpoint, since no listing should carry it."""
    await _row(services, board, session, submission_id)
    return s.Report(report=await store.report(session, submission_id))


# Include before the generic DEFLATE upload route in the application.
from .floatlib_intake import router as intake_router  # noqa: E402

router.include_router(intake_router)
