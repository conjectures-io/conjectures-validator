"""BendTT signed intake and bounded public reads in the centralized API."""

import hashlib
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response
from sqlalchemy import text
from sqlalchemy.exc import InterfaceError, OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from conjectures_subnet.axiom import get_axiom
from submission_api import schemas_bendtt as s
from submission_api.bendtt.store import BASE, Store, rows, speedup
from submission_api.competitions import BENDTT, Competition
from submission_api.dependencies import ServicesDep
from submission_api.errors import (
    BadRequest,
    Forbidden,
    ServiceUnavailable,
    Unauthorized,
    UnprocessableEntity,
)
from submission_api.kernel_upload import read_kernel
from submission_api.login import verify_signature
from submission_api.middleware import client_address
from submission_api.routers.competitions import refuse_if_paused, resolve_competition
from submission_api.routers.torchlean import spend

Identifier = Annotated[int, Path(ge=1, le=2**63 - 1)]
Limit = Annotated[int, Query(ge=1, le=100)]
Cursor = Annotated[str | None, Query(max_length=256)]
Hotkey = Annotated[str, Query(pattern=r"^[1-9A-HJ-NP-Za-km-z]{46,64}$")]
Digest = Annotated[str | None, Query(pattern=r"^[0-9a-f]{64}$")]


def submit_message(digest: str, hotkey: str, timestamp: int, request_id: UUID) -> str:
    return "\n".join(
        (
            "conjectures-competition-submit-v2",
            "competition: bendtt",
            f"digest: {digest}",
            f"hotkey: {hotkey}",
            f"timestamp: {timestamp}",
            f"request_id: {request_id}",
        )
    )


def query_check(request: Request) -> None:
    allowed = {param.alias for param in request.scope["route"].dependant.query_params}
    if set(request.query_params) - allowed or any(
        len(request.query_params.getlist(key)) > 1 for key in request.query_params
    ):
        raise BadRequest("Unknown or repeated query parameter", reason_code="INVALID_FILTER")


@asynccontextmanager
async def database_session(services: ServicesDep, *, readonly: bool) -> AsyncIterator[AsyncSession]:
    competition = resolve_competition(services, BENDTT, BENDTT)
    if competition.sessions is None:
        raise ServiceUnavailable(
            "BendTT database not configured", reason_code="COMPETITION_UNAVAILABLE"
        )
    async with competition.sessions() as db:
        try:
            if readonly:
                await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            yield db
        except ProgrammingError as exc:
            if getattr(exc.orig, "sqlstate", None) not in ("42501", "42703", "42P01", "42883"):
                raise
            raise ServiceUnavailable(
                "BendTT schema or API grants unavailable",
                reason_code="COMPETITION_SCHEMA_UNAVAILABLE",
            ) from exc
        except (OperationalError, InterfaceError) as exc:
            raise ServiceUnavailable(
                "BendTT database unreachable", reason_code="COMPETITION_UNAVAILABLE"
            ) from exc


async def session(services: ServicesDep, request: Request) -> AsyncIterator[AsyncSession]:
    async with database_session(services, readonly=request.method == "GET") as db:
        yield db


async def store(services: ServicesDep, db: Annotated[AsyncSession, Depends(session)]) -> Store:
    return await Store.open(db, services.settings)


Read = Annotated[Store, Depends(store)]
router = APIRouter(prefix=BASE, tags=["bendtt"], dependencies=[Depends(query_check)])


async def listed(competition: Competition, services: ServicesDep):
    from submission_api import schemas_compression as catalog

    async with database_session(services, readonly=True) as db:
        view = await Store.open(db, services.settings)
        item = await view.overview()
    return catalog.Competition(
        slug="bendtt",
        name=item.name,
        submissions_open=item.submissions_open,
        queue_depth=sum(item.queue.model_dump().values()),
        current_snapshot_id=item.profile_hash,
        description="Proved-correct optimization of BendTT proof checking",
        files=[v.model_dump() for v in item.files],
        metric_definitions=[
            dict(key="speedup", label="Incumbent / challenger time", unit="ratio", better="higher")
        ],
        policy=None if item.scoring is None else item.scoring.model_dump(),
        policy_status=item.status,
        execution_limits={},
        context=catalog.Context(snapshot_id=item.profile_hash, status=item.status),
    )


@router.get("", response_model=s.Overview)
async def overview(view: Read):
    return await view.overview()


@router.get("/policy", response_model=s.Policy)
async def policy(view: Read):
    if view.policy is None:
        raise ServiceUnavailable("Coordinator policy not published", reason_code="POLICY_NOT_READY")
    rewards = await view.rewards()
    return view.policy.model_copy(update=dict(as_of=view.now, rewards=rewards.policy))


@router.get("/eligibility", response_model=s.Eligibility)
async def eligibility(view: Read, hotkey: Hotkey):
    return await view.eligibility(hotkey)


@router.post(
    "/submissions",
    status_code=201,
    response_model=s.Accepted,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["Submission.lean"],
                        "additionalProperties": False,
                        "properties": {"Submission.lean": {"type": "string", "format": "binary"}},
                    }
                }
            },
        }
    },
)
async def submit(
    request: Request,
    response: Response,
    services: ServicesDep,
    view: Read,
    hotkey: Annotated[str, Header(alias="X-Conjectures-Hotkey")],
    timestamp: Annotated[int, Header(alias="X-Conjectures-Timestamp")],
    signature: Annotated[str, Header(alias="X-Conjectures-Signature")],
    request_id: Annotated[UUID, Header(alias="Idempotency-Key")],
):
    settings = services.settings
    if not settings.bendtt_submissions_enabled:
        raise Forbidden("BendTT submissions are closed", reason_code="SUBMISSIONS_CLOSED")
    refuse_if_paused(services)
    if view.dev and settings.production:
        raise ServiceUnavailable("Development bypass forbidden in production")
    address = client_address(
        request.scope, settings.trusted_proxy_hops, settings.trusted_proxy_peers
    )
    await spend(services, "ip:" + address, settings.competition_ip_rate_per_minute, BENDTT)
    if abs(int(time.time()) - timestamp) > settings.competition_signature_window_seconds:
        raise Unauthorized("Signed timestamp expired", reason_code="SIGNATURE_EXPIRED")
    try:
        signed = bytes.fromhex(signature.removeprefix("0x"))
        if len(signed) != 64 or not 2 <= len(hotkey) <= 64:
            raise ValueError("invalid signature shape")
    except ValueError as exc:
        raise Unauthorized("Malformed signature") from exc
    content = await read_kernel(
        request,
        max_file_bytes=settings.bendtt_max_source_bytes,
        filename="Submission.lean",
        typed_errors=True,
    )
    if b"\0" in content:
        raise UnprocessableEntity("Source contains NUL", reason_code="SUBMISSION_POLICY_VIOLATION")
    digest = hashlib.sha256(content).hexdigest()
    verify_signature(
        address=hotkey,
        message=submit_message(digest, hotkey, timestamp, request_id),
        signature=signed,
    )
    await spend(services, "hotkey:" + hotkey, settings.competition_rate_per_minute, BENDTT)
    sid, replayed = await view.enqueue(hotkey, content.decode("utf-8"), str(request_id))
    summary = await view.submission(await view.get("submission", sid))
    response.status_code = 200 if replayed else 201
    get_axiom().info(
        source="api-competitions",
        event_type="submission_accepted",
        competition="bendtt",
        submission_id=sid,
        hotkey=hotkey,
        source_sha256=digest,
        intake_mode=summary.intake_mode,
        replayed=replayed,
    )
    return s.Accepted(
        submission=str(sid),
        source_sha256=digest,
        proof_state=summary.proof.state,
        processing_state=summary.processing_state,
        intake_mode=summary.intake_mode,
        replayed=replayed,
        status_url=f"{BASE}/submissions/{sid}",
    )


@router.get("/submissions", response_model=s.Page[s.Submission])
async def submissions(
    view: Read,
    limit: Limit = 25,
    cursor: Cursor = None,
    hotkey: Annotated[str | None, Query(max_length=64)] = None,
    state: s.ProcessingState | None = None,
    outcome: s.Outcome | None = None,
    intake_mode: s.Mode | None = None,
):
    result = await view.collection(
        "submissions",
        None,
        dict(hotkey=hotkey, state=state, outcome=outcome, intake_mode=intake_mode),
        limit,
        cursor,
    )
    return s.Page[s.Submission](
        items=[await view.submission(v) for v in result.rows],
        next_cursor=result.next_cursor,
        as_of=view.now,
    )


@router.get("/submissions/{submission_id}", response_model=s.Submission)
async def submission(submission_id: Identifier, view: Read):
    return await view.submission(await view.get("submission", submission_id))


@router.get("/submissions/{submission_id}/proof", response_model=s.Proof)
async def proof(submission_id: Identifier, view: Read):
    return await view.proof(await view.get("submission", submission_id))


@router.get("/submissions/{submission_id}/evaluations", response_model=s.Page[s.EvaluationSummary])
async def evaluations(
    submission_id: Identifier, view: Read, limit: Limit = 25, cursor: Cursor = None
):
    await view.get("submission", submission_id)
    result = await view.collection("evaluations", submission_id, {}, limit, cursor)
    return s.Page[s.EvaluationSummary](
        items=[await view.evaluation_summary(v) for v in result.rows],
        next_cursor=result.next_cursor,
        as_of=view.now,
    )


@router.get("/evaluations/{evaluation_id}", response_model=s.Evaluation)
async def evaluation(evaluation_id: Identifier, view: Read):
    return await view.evaluation(await view.get("evaluation", evaluation_id))


@router.get("/evaluations/{evaluation_id}/report", response_model=s.Report)
async def evaluation_report(evaluation_id: Identifier, view: Read):
    return await view.report(await view.get("evaluation", evaluation_id))


@router.get("/submissions/{submission_id}/report", response_model=s.Report)
async def submission_report(submission_id: Identifier, view: Read):
    await view.get("submission", submission_id)
    found = await rows(
        view.session,
        "SELECT * FROM api_evaluations WHERE submission_id=:id AND competition_id=:c ORDER BY id DESC LIMIT 1",
        dict(id=submission_id, c=view.competition),
    )
    return await view.report(found[0]) if found else s.Report(submission_id=str(submission_id))


@router.get("/evaluations/{evaluation_id}/attempts", response_model=s.Page[s.Attempt])
async def attempts(
    evaluation_id: Identifier,
    view: Read,
    limit: Limit = 25,
    cursor: Cursor = None,
    target: Annotated[str | None, Query(max_length=128)] = None,
    state: s.AttemptState | None = None,
):
    await view.get("evaluation", evaluation_id)
    result = await view.collection(
        "attempts", evaluation_id, dict(target=target, state=state), limit, cursor
    )
    return s.Page[s.Attempt](
        items=[await view.attempt(v) for v in result.rows],
        next_cursor=result.next_cursor,
        as_of=view.now,
    )


@router.get("/attempts/{attempt_id}", response_model=s.Attempt)
async def attempt(attempt_id: Identifier, view: Read):
    return await view.attempt(await view.get("attempt", attempt_id))


@router.get("/attempts/{attempt_id}/measurements", response_model=s.Page[s.Measurement])
async def measurements(
    attempt_id: Identifier,
    view: Read,
    limit: Limit = 25,
    cursor: Cursor = None,
    role: s.Role | None = None,
    phase: s.Phase | None = None,
    status: s.MeasurementStatus | None = None,
    case_id: Annotated[str | None, Query(max_length=256)] = None,
    round_index: Annotated[int | None, Query(ge=0, le=10000)] = None,
    after_id: Annotated[int | None, Query(ge=0, le=2**63 - 1)] = None,
):
    await view.get("attempt", attempt_id)
    result = await view.collection(
        "measurements",
        attempt_id,
        dict(role=role, phase=phase, status=status, case_id=case_id, round_index=round_index),
        limit,
        cursor,
        after_id,
    )
    values = []
    for row in result.rows:
        members = await view.session.scalar(
            text("SELECT count(*) FROM api_members WHERE measurement_id=:id"), dict(id=row["id"])
        )
        values.append(
            s.Measurement.model_validate(
                {
                    **{k: v for k, v in row.items() if k not in ("group_name", "outcome")},
                    "group": row["group_name"],
                    "checker_outcome": row["outcome"],
                    "members_recorded": members,
                    "links": dict(members=f"{BASE}/measurements/{row['id']}/members"),
                }
            )
        )
    return s.Page[s.Measurement](items=values, next_cursor=result.next_cursor, as_of=view.now)


@router.get("/measurements/{measurement_id}/members", response_model=s.Page[s.Member])
async def members(measurement_id: Identifier, view: Read, limit: Limit = 25, cursor: Cursor = None):
    await view.get("measurement", measurement_id)
    result = await view.collection("members", measurement_id, {}, limit, cursor)
    return s.Page[s.Member](
        items=[
            s.Member.model_validate(
                dict(
                    index=v["member_index"],
                    case_id=v["case_id"],
                    input_sha256=v["input_sha256"],
                    parse_ns=v["parse_ns"],
                    check_and_render_ns=v["check_and_render_ns"],
                    output_bytes=v["output_bytes"],
                    checker_outcome=v["outcome"],
                )
            )
            for v in result.rows
        ],
        next_cursor=result.next_cursor,
        as_of=view.now,
    )


@router.get("/champions", response_model=s.Page[s.Champion])
async def champions(view: Read, limit: Limit = 25, cursor: Cursor = None):
    result = await view.collection("champions", None, {}, limit, cursor)
    values = []
    for row in result.rows:
        sub = await view.get("submission", int(str(row["submission_id"])))
        values.append(
            s.Champion.model_validate(
                dict(
                    promotion_id=row["id"],
                    submission_id=row["submission_id"],
                    hotkey=row["hotkey"],
                    source_sha256=row["source_sha256"],
                    promoted_at=row["promoted_at"],
                    current=row["current"],
                    evaluation_id=row["evaluation_id"],
                    profile_hash=row["profile_hash"],
                    speedup_over_predecessor=speedup(row),
                    source_visibility="public" if sub["source_available"] else "withheld",
                )
            )
        )
    return s.Page[s.Champion](items=values, next_cursor=result.next_cursor, as_of=view.now)


@router.get(
    "/submissions/{submission_id}/source",
    response_class=Response,
    responses={200: {"content": {"text/plain": {"schema": {"type": "string"}}}}},
)
async def source(submission_id: Identifier, view: Read):
    sub = await view.get("submission", submission_id)
    if not sub["source_available"]:
        raise Forbidden("Source is withheld", reason_code="SOURCE_WITHHELD")
    content = await view.session.scalar(
        text("SELECT api_source(:c,:id)"), dict(c=view.competition, id=submission_id)
    )
    if content is None:
        raise Forbidden("Source is withheld", reason_code="SOURCE_WITHHELD")
    return Response(
        content,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="Submission.lean"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/corpus", response_model=s.CorpusPage)
async def corpus(
    view: Read,
    limit: Limit = 25,
    cursor: Cursor = None,
    profile_hash: Digest = None,
    tier: Annotated[str | None, Query(max_length=128)] = None,
    group: Annotated[str | None, Query(max_length=128)] = None,
):
    digest = profile_hash or (view.policy.profile_hash if view.policy else None)
    if digest is None or await view.profile(digest) is None:
        raise ServiceUnavailable(
            "Published corpus profile unavailable", reason_code="POLICY_NOT_READY"
        )
    result = await view.collection(
        "corpus", None, dict(profile_hash=digest, tier=tier, group=group), limit, cursor
    )
    return s.CorpusPage(
        profile_hash=digest,
        items=[
            s.CorpusCase.model_validate(
                dict(
                    id=v["id"],
                    title=v["title"],
                    group=v["group_name"],
                    tier=v["tier"],
                    rounds=v["rounds"],
                    input_sha256=v["input_sha256"],
                    expected_outcome=v["expected_outcome"],
                    source=dict(project=v["source_project"], url=v["source_url"]),
                    description=v["title"],
                )
            )
            for v in result.rows
        ],
        next_cursor=result.next_cursor,
        as_of=view.now,
    )


@router.get("/rewards", response_model=s.Rewards)
async def rewards(view: Read):
    return await view.rewards()


@router.get("/weight-sets", response_model=s.Page[s.WeightSet])
async def weight_sets(
    view: Read,
    limit: Limit = 25,
    cursor: Cursor = None,
    mode: Annotated[str | None, Query(pattern=r"^(dry_run|live)$")] = None,
    status: s.WeightStatus | None = None,
):
    result = await view.collection(
        "weight-sets", None, dict(mode=mode, status=status), limit, cursor
    )
    return s.Page[s.WeightSet](
        items=[await view.weight(v) for v in result.rows],
        next_cursor=result.next_cursor,
        as_of=view.now,
    )


@router.get("/weight-sets/{weight_set_id}/allocations", response_model=s.Page[s.Allocation])
async def allocations(
    weight_set_id: Identifier, view: Read, limit: Limit = 25, cursor: Cursor = None
):
    await view.get("weight-set", weight_set_id)
    result = await view.collection("allocations", weight_set_id, {}, limit, cursor)
    return s.Page[s.Allocation](
        items=[
            s.Allocation.model_validate({k: v for k, v in row.items() if k != "weight_set_id"})
            for row in result.rows
        ],
        next_cursor=result.next_cursor,
        as_of=view.now,
    )


@router.get("/health", response_model=s.Health)
async def health(view: Read, response: Response):
    result = await view.health()
    if result.status == "not_ready":
        response.status_code = 503
    return result


@router.get("/progress", response_model=s.Page[s.ProgressPoint])
async def progress(view: Read, limit: Limit = 100, cursor: Cursor = None):
    result = await view.collection("progress", None, {}, limit, cursor)
    return s.Page[s.ProgressPoint](
        items=[s.ProgressPoint.model_validate(row) for row in result.rows],
        next_cursor=result.next_cursor,
        as_of=view.now,
    )


@router.get("/statistics", response_model=s.Statistics)
async def statistics(view: Read):
    return await view.statistics()


@router.get("/history", response_model=s.Page[s.HistoryEntry])
async def history(view: Read, limit: Limit = 50, cursor: Cursor = None):
    return await view.history(limit, cursor)
