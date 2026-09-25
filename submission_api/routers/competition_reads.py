"""Read-only frontend API for compression. All scoring comes from persisted passes."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Path, Query, Response
from sqlalchemy import text

from submission_api import compression_store as store, compression_views as view
from submission_api import schemas_compression as s
from submission_api.dependencies import CompetitionSessionDep, ServicesDep, PrincipalDep
from submission_api.errors import BadRequest, Conflict, Forbidden, NotFound
from submission_api.pagination import decode_parts, encode_parts
from submission_api.routers.competitions import resolve_competition
from submission_api.settings import MAX_COMPETITION_FILE_BYTES

router = APIRouter(prefix="/v1/competitions", tags=["competitions"])


def binding(filters):
    return hashlib.sha256(json.dumps(filters, sort_keys=True).encode()).hexdigest()[:20]


def page_state(services, cursor, kind, filters, snapshot_id):
    if cursor is None:
        return snapshot_id, 0
    version = "compression-" + kind
    saved, offset, digest = decode_parts(
        services.settings.cursor_secret, cursor, version=version, count=3
    )
    selected = None if saved == "none" else int(saved)
    if digest != binding(filters) or (snapshot_id is not None and selected != snapshot_id):
        raise BadRequest("Cursor filters or snapshot changed", reason_code="INVALID_CURSOR")
    return selected, int(offset) if offset.isdecimal() else offset


def next_page(services, kind, filters, sid, offset, more):
    if not more:
        return None
    return encode_parts(
        services.settings.cursor_secret,
        version="compression-" + kind,
        parts=(str(sid) if sid is not None else "none", str(offset), binding(filters)),
    )


async def selected_snapshot(session, sid):
    await store.compatible(session)
    snap = await store.snapshot(session, sid)
    if sid is not None and snap is None:
        raise NotFound("No completed scoring snapshot", reason_code="SNAPSHOT_NOT_FOUND")
    return snap


def context(snap):
    if snap is None:
        return s.Context()
    return s.Context(
        snapshot_id=str(snap["id"]),
        computed_at=snap["api_snapshot"].get("computed_at", view.timestamp(snap["created_at"])),
        policy_version=snap["api_snapshot"]["policy"].get("version"),
        status="ready",
        freshness=snap.get("freshness", "unknown"),
        freshness_reason=snap.get(
            "freshness_reason", "Recorded scoring pass; live policy changes require a new pass."
        ),
    )


def items(snap):
    if not snap:
        return []
    scores = {str(row["submission_id"]): row for row in snap["scores"]}
    return [
        (item, scores.get(str(item["submission"]["id"]))) for item in snap["api_snapshot"]["items"]
    ]


async def summary(competition, services, session):
    snap = await selected_snapshot(session, None)
    ctx = context(snap)
    return s.Competition(
        slug=competition.slug,
        name=competition.name,
        submissions_open=not services.settings.submissions_paused,
        queue_depth=await store.queue_depth(session),
        current_snapshot_id=ctx.snapshot_id,
        description="Formally verified compression competition",
        files=[
            {"name": name, "max_bytes": MAX_COMPETITION_FILE_BYTES}
            for name in ("parse.rs", "Parse.lean")
        ],
        metric_definitions=[
            {
                "key": "balanced_time_ratio",
                "label": "Mean file time / incumbent",
                "unit": "ratio",
                "better": "lower",
            },
            {
                "key": "mean_file_compression_pct",
                "label": "Mean file compressed size",
                "unit": "percent",
                "better": "lower",
            },
        ],
        policy=snap["api_snapshot"]["policy"] if snap else None,
        policy_status="ready" if snap else "unknown",
        execution_limits={"benchmark_timeout_seconds": None, "gate_timeout_seconds": None},
        context=ctx,
    )


@router.get("", response_model=s.Index)
async def index(services: ServicesDep, session: CompetitionSessionDep):
    return s.Index(
        items=[await summary(c, services, session) for c in services.competitions.competitions]
    )


@router.get("/{slug}", response_model=s.Competition)
async def competition_detail(slug: str, services: ServicesDep, session: CompetitionSessionDep):
    return await summary(resolve_competition(services, slug), services, session)


async def feed(
    slug,
    services,
    session,
    limit,
    cursor,
    hotkey,
    kind,
    gate_status,
    admission_outcome,
    on_frontier,
    snapshot_id,
    account_id=None,
):
    resolve_competition(services, slug)
    filters = [slug, hotkey, kind, gate_status, admission_outcome, on_frontier, account_id]
    sid, before = page_state(services, cursor, "feed", filters, snapshot_id)
    snap = await selected_snapshot(session, sid) if not (cursor and sid is None) else None
    sid = snap["id"] if snap else None
    if (admission_outcome is not None or on_frontier is not None) and snap is None:
        raise Conflict("Scoring is not ready", reason_code="SCORING_NOT_READY")
    chosen = {str(item["submission"]["id"]): (item, score) for item, score in items(snap)}
    predicates = [store.PUBLIC]
    params = {"limit": limit + 1}
    if before:
        micros, row_id = str(before).split("_")
        predicates.append("(submitted_at,id) < (:before_time,:before_id)")
        params["before_time"] = datetime.fromtimestamp(int(micros) / 1_000_000, UTC)
        params["before_id"] = int(row_id)
    for key, value in (("hotkey", hotkey), ("account_id", account_id)):
        if value is not None:
            predicates.append(f"{key}=:{key}")
            params[key] = value
    if kind:
        predicates.append("baseline_key IS " + ("NOT NULL" if kind == "baseline" else "NULL"))
    if gate_status:
        states = [key for key, value in view.GATE.items() if value == gate_status]
        predicates.append("state = ANY(:states)")
        params["states"] = states
    if admission_outcome is not None or on_frontier is not None:
        ids = [
            int(key)
            for key, (item, score) in chosen.items()
            if (
                admission_outcome is None
                or (item.get("admission") or {}).get("outcome") == admission_outcome
            )
            and (on_frontier is None or (score is not None and score["on_frontier"] == on_frontier))
        ]
        predicates.append("id = ANY(:ids)")
        params["ids"] = ids
    rows = list(
        (
            await session.execute(
                text(
                    "SELECT "
                    + store.SUB_FIELDS
                    + " FROM submissions WHERE "
                    + " AND ".join(predicates)
                    + " ORDER BY submitted_at DESC, id DESC LIMIT :limit"
                ),
                params,
            )
        ).mappings()
    )
    result = []
    for sub in rows[:limit]:
        item, score = chosen.get(str(sub["id"]), (None, None))
        if item is None:
            item = await store.evidence(session, dict(sub))
        rendered = view.submission(item, score, sid)
        rendered.gate_status = view.GATE.get(sub["state"], "unknown")
        result.append(rendered)
    return s.SubmissionPage(
        context=context(snap),
        items=result,
        next_cursor=next_page(
            services,
            "feed",
            filters,
            sid,
            str(int(rows[limit - 1]["submitted_at"].timestamp() * 1_000_000))
            + "_"
            + str(rows[limit - 1]["id"]),
            len(rows) > limit,
        )
        if len(rows) > limit
        else None,
    )


@router.get("/{slug}/submissions", response_model=s.SubmissionPage)
async def submissions(
    slug: str,
    services: ServicesDep,
    session: CompetitionSessionDep,
    limit: int = Query(25, ge=1, le=100),
    cursor: str | None = Query(None, max_length=256),
    hotkey: str | None = None,
    kind: Literal["miner", "baseline"] | None = None,
    gate_status: Literal["queued", "running", "passed", "failed", "error", "unknown"] | None = None,
    admission_outcome: Literal["passed", "not_required", "inconclusive", "dominated", "excluded"]
    | None = None,
    on_frontier: bool | None = None,
    snapshot_id: int | None = Query(None, ge=1),
):
    return await feed(
        slug,
        services,
        session,
        limit,
        cursor,
        hotkey,
        kind,
        gate_status,
        admission_outcome,
        on_frontier,
        snapshot_id,
    )


@router.get("/{slug}/me/submissions", response_model=s.SubmissionPage)
async def mine(
    slug: str,
    services: ServicesDep,
    session: CompetitionSessionDep,
    principal: PrincipalDep,
    limit: int = Query(25, ge=1, le=100),
    cursor: str | None = Query(None, max_length=256),
    gate_status: Literal["queued", "running", "passed", "failed", "error", "unknown"] | None = None,
    admission_outcome: Literal["passed", "not_required", "inconclusive", "dominated", "excluded"]
    | None = None,
    on_frontier: bool | None = None,
    snapshot_id: int | None = Query(None, ge=1),
):
    return await feed(
        slug,
        services,
        session,
        limit,
        cursor,
        None,
        None,
        gate_status,
        admission_outcome,
        on_frontier,
        snapshot_id,
        str(principal.account.id),
    )


@router.get("/{slug}/pareto", response_model=s.ParetoPage)
async def pareto(
    slug: str,
    services: ServicesDep,
    session: CompetitionSessionDep,
    limit: int = Query(25, ge=1, le=100),
    cursor: str | None = Query(None, max_length=256),
    snapshot_id: int | None = Query(None, ge=1),
):
    resolve_competition(services, slug)
    sid, offset = page_state(services, cursor, "pareto", [slug], snapshot_id)
    snap = await selected_snapshot(session, sid)
    points = [
        s.ParetoPoint(
            **view.submission(item, score, snap["id"]).model_dump(),
            timing_interval=view.interval(item.get("aggregation")),
            bounds=view.bounds(item.get("admission")),
        )
        for item, score in items(snap)
    ]
    frontier = sorted(
        [p for p in points if p.score and p.score.on_frontier],
        key=lambda p: (
            p.metrics.balanced_time_ratio,
            p.metrics.mean_file_compression_pct,
            int(p.id),
        ),
    )
    for index, point in enumerate(frontier):
        point.frontier_order = index
    policy = snap["api_snapshot"]["policy"] if snap else {}
    return s.ParetoPage(
        context=context(snap),
        axes={"x": "balanced_time_ratio", "y": "mean_file_compression_pct"},
        bounds=s.Bounds(
            max_balanced_time_ratio=policy.get("max_balanced_time_ratio"),
            max_mean_file_compression_pct=policy.get("max_mean_file_compression_pct"),
        ),
        items=points[offset : offset + limit],
        next_cursor=next_page(
            services,
            "pareto",
            [slug],
            snap["id"] if snap else None,
            offset + limit,
            offset + limit < len(points),
        ),
    )


def rankings(snap):
    result = {}
    for _, score in items(snap):
        if score is None or score["hotkey"] is None:
            continue
        key = score["hotkey"]
        row = result.setdefault(
            key,
            dict(
                rank=0,
                hotkey=key,
                submission_ids=[],
                pareto_weight=0.0,
                improvement_weight=0.0,
                combined_weight=0.0,
                payable_weight=0.0,
                bounty_earned_alpha=None,
            ),
        )
        row["submission_ids"].append(str(score["submission_id"]))
        for field in ("pareto_weight", "improvement_weight", "combined_weight", "payable_weight"):
            row[field] += score[field]
        earned = view.alpha(score.get("bounty_rao"))
        if earned is not None:
            row["bounty_earned_alpha"] = (row["bounty_earned_alpha"] or 0.0) + earned
    rows = sorted(result.values(), key=lambda row: (-row["payable_weight"], row["hotkey"]))
    for index, row in enumerate(rows, 1):
        row["rank"] = index
    return rows


@router.get("/{slug}/leaderboard", response_model=s.Leaderboard)
async def leaderboard(
    slug: str,
    services: ServicesDep,
    session: CompetitionSessionDep,
    limit: int = Query(25, ge=1, le=100),
    cursor: str | None = Query(None, max_length=256),
    snapshot_id: int | None = Query(None, ge=1),
):
    resolve_competition(services, slug)
    sid, offset = page_state(services, cursor, "rank", [slug], snapshot_id)
    snap = await selected_snapshot(session, sid)
    rows = rankings(snap)
    bounty = (snap["api_snapshot"].get("bounty") or {}) if snap else {}
    return s.Leaderboard(
        context=context(snap),
        bounty_limit_alpha=bounty.get("limit_alpha"),
        ranking=rows[offset : offset + limit],
        next_cursor=next_page(
            services,
            "rank",
            [slug],
            snap["id"] if snap else None,
            offset + limit,
            offset + limit < len(rows),
        ),
    )


@router.get("/{slug}/weights/current", response_model=s.Weights)
async def weights(
    slug: str,
    services: ServicesDep,
    session: CompetitionSessionDep,
    snapshot_id: int | None = Query(None, ge=1),
):
    resolve_competition(services, slug)
    snap = await selected_snapshot(session, snapshot_id)
    vector = {
        row["hotkey"]: row["payable_weight"] for row in rankings(snap) if row["payable_weight"] > 0
    }
    return s.Weights(
        competition=slug,
        context=context(snap),
        weights=vector,
        payable_competition_weight=sum(vector.values()) if snap else None,
        unpaid_competition_weight=max(0, 1 - sum(vector.values())) if snap else None,
        competition_share=snap["api_snapshot"]["policy"]["competition_share"] if snap else None,
        weight_set_id=str(snap["id"]) if snap else None,
        dry_run=snap["dry_run"] if snap else None,
        chain_accepted=snap["accepted"] if snap else None,
    )


async def load_submission(slug, sid, services, session, snapshot_id=None):
    resolve_competition(services, slug)
    snap = await selected_snapshot(session, snapshot_id)
    sub = await store.get_submission(session, sid)
    if sub is None:
        raise NotFound("No such submission")
    chosen = next(
        ((i, score) for i, score in items(snap) if str(i["submission"]["id"]) == str(sid)), None
    )
    if snapshot_id is not None and chosen is None:
        raise NotFound("Submission is not in this snapshot", reason_code="NOT_IN_SNAPSHOT")
    item, score = chosen if chosen else (await store.evidence(session, sub), None)
    return sub, item, score, snap


@router.get("/{slug}/submissions/{submission_id}", response_model=s.Detail)
async def detail(
    slug: str,
    services: ServicesDep,
    session: CompetitionSessionDep,
    submission_id: int = Path(ge=1),
    snapshot_id: int | None = Query(None, ge=1),
):
    sub, item, score, snap = await load_submission(
        slug, submission_id, services, session, snapshot_id
    )
    prefix = f"/v1/competitions/{slug}/submissions/{submission_id}"
    rendered = view.submission(item, score, snap["id"] if snap else None)
    rendered.gate_status = view.GATE.get(sub["state"], "unknown")
    return s.Detail(
        submission=rendered,
        digest=sub["digest"],
        source_sha256=sub["source_sha256"],
        pipeline_observed_at=datetime.now(UTC).isoformat(),
        pipeline=view.pipeline(sub),
        current_evidence={
            "aggregation_id": view.identifier(sub["aggregation_id"]),
            "admission_check_id": view.identifier(sub["admission_check_id"]),
        },
        bounds=view.bounds(item.get("admission")),
        context=context(snap),
        links={
            "report": prefix + "/report",
            "source": prefix + "/source",
            "admission": prefix + "/admission",
        },
    )


@router.get("/{slug}/submissions/{submission_id}/admission", response_model=s.AdmissionResponse)
async def admission(
    slug: str,
    services: ServicesDep,
    session: CompetitionSessionDep,
    submission_id: int = Path(ge=1),
    snapshot_id: int | None = Query(None, ge=1),
):
    sub, item, _, snap = await load_submission(slug, submission_id, services, session, snapshot_id)
    if snapshot_id is None:
        item = await store.evidence(session, sub)
    return s.AdmissionResponse(
        context=context(snap) if snapshot_id else None,
        admission=view.admission(item),
        decision=view.decision(item),
    )


@router.get("/{slug}/submissions/{submission_id}/report")
async def report(
    slug: str,
    services: ServicesDep,
    session: CompetitionSessionDep,
    submission_id: int = Path(ge=1),
):
    await load_submission(slug, submission_id, services, session)
    report = (
        await session.execute(
            text("SELECT report FROM submissions WHERE id=:id"), {"id": submission_id}
        )
    ).scalar_one()
    return {"submission_id": str(submission_id), "report": view.sanitized_report(report)}


SOURCE_WITHHELD = "SOURCE_WITHHELD"


def withhold(sub, score):
    """Keep a miner's source private while it is, or may be, the current best.

    Publishing the frontier would let anyone resubmit the leading parser under their own
    hotkey, so a submission on the latest snapshot's Pareto frontier is withheld and is
    published once another submission beats it. A submission the latest snapshot has not
    scored yet is withheld too: it may be about to land on the frontier, and the next pass
    decides. Baselines are exempt, being the public miner/examples already.
    """
    if sub.get("baseline_key"):
        return
    if score is None:
        raise Forbidden(
            "Source is withheld until a scoring pass has placed this submission",
            reason_code=SOURCE_WITHHELD,
        )
    if score["on_frontier"]:
        raise Forbidden(
            "Source is withheld while this submission is on the Pareto frontier; "
            "it is published once another submission beats it",
            reason_code=SOURCE_WITHHELD,
        )


async def sources(slug, sid, services, session):
    sub, _, score, _ = await load_submission(slug, sid, services, session)
    if sub["state"] != "accepted":
        raise NotFound("No published source")
    withhold(sub, score)
    rows = (
        await session.execute(
            text("SELECT name, content FROM submission_files WHERE submission_id=:id"), {"id": sid}
        )
    ).all()
    return {name: bytes(content) for name, content in rows}


@router.get("/{slug}/submissions/{submission_id}/source/{filename}", response_class=Response)
async def source_file(
    slug: str,
    services: ServicesDep,
    session: CompetitionSessionDep,
    filename: Literal["parse.rs", "Parse.lean"],
    submission_id: int = Path(ge=1),
):
    files = await sources(slug, submission_id, services, session)
    if filename not in files:
        raise NotFound("Source is unavailable")
    return Response(files[filename], media_type="text/plain; charset=utf-8")


@router.get("/{slug}/submissions/{submission_id}/source")
async def source_pair(
    slug: str,
    services: ServicesDep,
    session: CompetitionSessionDep,
    submission_id: int = Path(ge=1),
):
    files = await sources(slug, submission_id, services, session)
    if len(files) != 2:
        raise NotFound("Source is unavailable")
    return {
        "id": str(submission_id),
        "parse_rs": files["parse.rs"].decode(),
        "proof_lean": files["Parse.lean"].decode(),
    }
