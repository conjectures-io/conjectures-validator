"""Signed torchlean intake and the minimal polling surface returned by intake."""

from __future__ import annotations

import hashlib
import time
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import InterfaceError, OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from conjectures_subnet.axiom import get_axiom
from submission_api import competition_sig, torchlean_store as store
from submission_api.competitions import TORCHLEAN, Competition
from submission_api.dependencies import ServicesDep
from submission_api.errors import (
    ApiError,
    Forbidden,
    ServiceUnavailable,
    TooManyRequests,
    Unauthorized,
)
from submission_api.login import verify_signature
from submission_api.middleware import client_address
from submission_api.routers.competitions import refuse_if_paused, resolve_competition
from submission_api.kernel_upload import read_kernel

router = APIRouter(tags=["torchlean"])


class TorchLeanAccepted(BaseModel):
    competition: str = "torchlean"
    submission: int
    source_sha256: str
    proof_state: Literal["queued"] = "queued"
    status_url: str
    intake_mode: Literal["subnet", "dev"]


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
        except ProgrammingError as exc:
            if getattr(exc.orig, "sqlstate", None) not in ("42501", "42703", "42P01"):
                raise
            raise ServiceUnavailable(
                "TorchLean schema or API grants need updating.",
                reason_code="COMPETITION_SCHEMA_UNAVAILABLE",
            ) from exc
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
    "/v1/competitions/torchlean/submissions",
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
    # What a refusal can name: the claimed hotkey is unverified until the signature checks.
    attempt = {"hotkey": hotkey, "source_sha256": None, "kernel_bytes": None}
    try:
        settings = services.settings
        address = client_address(
            request.scope, settings.trusted_proxy_hops, settings.trusted_proxy_peers
        )
        await spend(services, "ip:" + address, settings.competition_ip_rate_per_minute)
        if abs(int(time.time()) - timestamp) > settings.competition_signature_window_seconds:
            raise Unauthorized(
                "The signed timestamp is outside the freshness window.",
                reason_code="SIGNATURE_EXPIRED",
            )
        try:
            signed = bytes.fromhex(signature.removeprefix("0x"))
            if len(signed) != 64 or not 2 <= len(hotkey) <= 64:
                raise ValueError("invalid signature")
        except ValueError as exc:
            raise Unauthorized("Malformed hotkey signature.") from exc
        kernel = await read_kernel(request)
        digest = hashlib.sha256(kernel).hexdigest()
        attempt.update(source_sha256=digest, kernel_bytes=len(kernel))
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
            raise ServiceUnavailable(
                "TorchLean dev registration bypass is unavailable in production"
            )
        sid = await store.enqueue(
            session,
            hotkey=hotkey,
            kernel=kernel,
            digest=digest,
            cutoff=settings.torchlean_registration_cutoff,
            dev=dev,
        )
        # After `enqueue` committed. Ids, hotkey and digest, never the file.
        get_axiom().info(
            source="api-competitions",
            event_type="submission_accepted",
            competition="torchlean",
            submission_id=sid,
            intake_mode="dev" if dev else "subnet",
            **attempt,
        )
        return TorchLeanAccepted(
            submission=sid,
            source_sha256=digest,
            intake_mode="dev" if dev else "subnet",
            status_url=f"/v1/competitions/torchlean/submissions/{sid}",
        )
    except ApiError as problem:
        # Refusals raised before the handler runs (closed, paused, database unreachable) are on
        # `request_completed` only; these are the ones that got far enough to name a file.
        # Ours (5xx) are errors, the miner's (4xx) warnings, as on the request stream.
        (get_axiom().error if problem.status_code >= 500 else get_axiom().warn)(
            source="api-competitions",
            event_type="submission_rejected",
            competition="torchlean",
            reason_code=problem.reason_code,
            http_status=problem.status_code,
            **attempt,
        )
        raise


async def listed(competition: Competition, services):
    from submission_api.routers.torchlean_public import listed as public_listing

    return await public_listing(competition, services)


# This router owns the complete TorchLean surface and is registered before legacy slug routes.
from submission_api.routers.torchlean_public import router as public_router  # noqa: E402

router.include_router(public_router, prefix="/v1/competitions/torchlean")
