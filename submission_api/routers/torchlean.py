"""Signed torchlean intake and the minimal polling surface returned by intake."""

from __future__ import annotations

import hashlib
import time
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from submission_api import competition_sig, torchlean_store as store
from submission_api.competitions import TORCHLEAN, Competition
from submission_api.dependencies import ServicesDep
from submission_api.errors import (
    Forbidden,
    NotFound,
    ServiceUnavailable,
    TooManyRequests,
    Unauthorized,
)
from submission_api.login import verify_signature
from submission_api.middleware import client_address
from submission_api.routers.competitions import refuse_if_paused, resolve_competition
from submission_api.kernel_upload import MAX_FILE_BYTES, read_kernel
from submission_api import schemas_compression as catalog

router = APIRouter(prefix="/v1/competitions/torchlean", tags=["torchlean"])


class TorchLeanAccepted(BaseModel):
    competition: str = "torchlean"
    submission: int
    source_sha256: str
    proof_state: Literal["queued"] = "queued"
    status_url: str
    intake_mode: Literal["subnet", "dev"]


class TorchLeanSubmissionView(BaseModel):
    id: int
    hotkey: str
    source_sha256: str
    submitted_at: datetime
    proof_state: Literal["queued", "running", "proved", "rejected"]
    stage: str | None
    reason: str | None
    intake_mode: Literal["subnet", "dev"]
    evaluations: list[dict]


def require_open(services: ServicesDep):
    resolve_competition(services, "torchlean", TORCHLEAN)
    if not services.settings.torchlean_submissions_enabled:
        raise Forbidden(
            "Submissions for this competition are closed.", reason_code="SUBMISSIONS_CLOSED"
        )
    refuse_if_paused(services)


async def torchlean_session(services: ServicesDep):
    competition = resolve_competition(services, "torchlean", TORCHLEAN)
    if competition.sessions is None:
        raise ServiceUnavailable("TorchLean database is not configured")
    async with competition.sessions() as session:
        try:
            yield session
        except (OperationalError, InterfaceError) as exc:
            raise ServiceUnavailable(
                "The TorchLean database is unreachable.", reason_code="COMPETITION_UNAVAILABLE"
            ) from exc


TorchLeanSession = Annotated[AsyncSession, Depends(torchlean_session)]


async def spend(services, subject, limit):
    # Request limits are central-API storage, never tables in the torchlean worker database.
    async with services.sessions() as session:
        hits = await session.scalar(
            text("""
            INSERT INTO competition_intake_limits(subject,window_start,hits)
            VALUES (:subject,date_trunc('minute',statement_timestamp()),1)
            ON CONFLICT(subject) DO UPDATE SET
              window_start=date_trunc('minute',statement_timestamp()),
              hits=CASE WHEN competition_intake_limits.window_start=
                date_trunc('minute',statement_timestamp())
                THEN least(competition_intake_limits.hits+1,:cap) ELSE 1 END
            RETURNING hits
        """),
            {"subject": "torchlean:" + subject, "cap": limit + 1},
        )
        await session.commit()
    if hits > limit:
        raise TooManyRequests(
            "TorchLean submission request limit exceeded.", reason_code="RATE_LIMITED"
        )


@router.post(
    "/submissions",
    status_code=201,
    response_model=TorchLeanAccepted,
    dependencies=[Depends(require_open)],
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["Kernel.lean"],
                        "additionalProperties": False,
                        "properties": {"Kernel.lean": {"type": "string", "format": "binary"}},
                    }
                }
            },
        }
    },
)
async def submit(
    request: Request,
    services: ServicesDep,
    session: TorchLeanSession,
    hotkey: str = Header(..., alias="X-Conjectures-Hotkey"),
    timestamp: int = Header(..., alias="X-Conjectures-Timestamp"),
    signature: str = Header(..., alias="X-Conjectures-Signature"),
) -> TorchLeanAccepted:
    settings = services.settings
    address = client_address(
        request.scope, settings.trusted_proxy_hops, settings.trusted_proxy_peers
    )
    await spend(services, "ip:" + address, settings.competition_ip_rate_per_minute)
    if abs(int(time.time()) - timestamp) > settings.competition_signature_window_seconds:
        raise Unauthorized(
            "The signed timestamp is outside the freshness window.", reason_code="SIGNATURE_EXPIRED"
        )
    try:
        signed = bytes.fromhex(signature.removeprefix("0x"))
        if len(signed) != 64 or not 2 <= len(hotkey) <= 64:
            raise ValueError("invalid signature")
    except ValueError as exc:
        raise Unauthorized("Malformed hotkey signature.") from exc
    kernel = await read_kernel(request)
    digest = hashlib.sha256(kernel).hexdigest()
    verify_signature(
        address=hotkey,
        message=competition_sig.submit_message(
            competition="torchlean", digest=digest, hotkey=hotkey, timestamp=timestamp
        ),
        signature=signed,
    )
    await spend(services, "hotkey:" + hotkey, settings.competition_rate_per_minute)
    await store.compatible(session)
    dev = settings.torchlean_dev_skip_registration
    # Backstop for injected service graphs as well as environment validation.
    if dev and settings.production:
        raise ServiceUnavailable("TorchLean dev registration bypass is unavailable in production")
    sid = await store.enqueue(
        session,
        hotkey=hotkey,
        kernel=kernel,
        digest=digest,
        cutoff=settings.torchlean_registration_cutoff,
        dev=dev,
    )
    return TorchLeanAccepted(
        submission=sid,
        source_sha256=digest,
        intake_mode="dev" if dev else "subnet",
        status_url=f"/v1/competitions/torchlean/submissions/{sid}",
    )


@router.get("/submissions/{submission_id}", response_model=TorchLeanSubmissionView)
async def submission(submission_id: int, services: ServicesDep, session: TorchLeanSession):
    await store.compatible(session)
    row = (
        (
            await session.execute(
                text("""
        SELECT id,hotkey,source_sha256,submitted_at,state AS proof_state,stage,reason,intake_mode
        FROM submissions WHERE id=:id
    """),
                {"id": submission_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise NotFound("No such TorchLean submission")
    evaluations = (
        (
            await session.execute(
                text("""
        SELECT id,board,generation,state,reference_promotion_id,reference_hash,config_hash,
               decision,completed_at FROM evaluations WHERE submission_id=:id ORDER BY id
    """),
                {"id": submission_id},
            )
        )
        .mappings()
        .all()
    )
    return TorchLeanSubmissionView(**dict(row), evaluations=[dict(e) for e in evaluations])


async def listed(competition: Competition, services):
    if competition.sessions is None:
        raise ServiceUnavailable("TorchLean database is not configured")
    try:
        async with competition.sessions() as session:
            await store.compatible(session)
            queued = await session.scalar(
                text("""
                SELECT count(*) FROM submissions s WHERE s.state IN ('queued','running')
                OR (s.state='proved' AND EXISTS(SELECT 1 FROM evaluations e
                    WHERE e.submission_id=s.id AND e.state='pending'))
            """)
            )
    except (OperationalError, InterfaceError) as exc:
        raise ServiceUnavailable(
            "The TorchLean database is unreachable.", reason_code="COMPETITION_UNAVAILABLE"
        ) from exc
    return catalog.Competition(
        slug="torchlean",
        name=competition.name,
        description="Proved exact torchlean kernels, evaluated on model-family boards",
        submissions_open=services.settings.torchlean_submissions_enabled
        and not services.settings.submissions_paused,
        queue_depth=queued,
        current_snapshot_id=None,
        policy=None,
        execution_limits={},
        files=[{"name": "Kernel.lean", "max_bytes": MAX_FILE_BYTES}],
        metric_definitions=[
            {"key": "ratio", "label": "Time / champion", "unit": "ratio", "better": "lower"}
        ],
        policy_status="unknown",
        context=catalog.Context(status="not_ready", freshness="unknown"),
    )


@router.get("", response_model=catalog.Competition)
async def detail(services: ServicesDep):
    return await listed(resolve_competition(services, "torchlean", TORCHLEAN), services)
