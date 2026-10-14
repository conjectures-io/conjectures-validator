"""Signed full-entry intake; eligibility is consumed when the submission is recorded."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import InterfaceError, OperationalError, TimeoutError as PoolTimeout
from starlette.requests import Request as MultipartRequest

from submission_api import competition_sig, floatlib_store as store, schemas_floatlib as schemas
from submission_api.competitions import FLOATLIB
from submission_api.dependencies import ServicesDep
from submission_api.errors import (
    BadRequest,
    Conflict,
    Forbidden,
    PayloadTooLarge,
    PaymentRequired,
    ServiceUnavailable,
    Unauthorized,
)
from submission_api.login import verify_signature
from submission_api.routers.competitions import (
    _spend,
    _spend_ip,
    refuse_if_paused,
    resolve_competition,
)

router = APIRouter()
logger = logging.getLogger(__name__)
MAX_FILE_BYTES = 256_000
MAX_BODY_BYTES = 2 * MAX_FILE_BYTES
DEFLATE_TIMEOUT_SECONDS = 3


async def full_session(services: ServicesDep):
    competition = resolve_competition(services, "floatlib", FLOATLIB)
    if competition.sessions is None:
        raise ServiceUnavailable("FloatLib database is not configured")
    async with competition.sessions() as session:
        try:
            yield session
        except (OperationalError, InterfaceError) as exc:
            raise ServiceUnavailable(
                "The FloatLib database is unreachable.", reason_code="COMPETITION_UNAVAILABLE"
            ) from exc


FloatlibSessionDep = Annotated[AsyncSession, Depends(full_session)]


async def read_kernel(request: Request) -> bytes:
    # Bound the actual stream, including multipart overhead, before the parser spools files.
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY_BYTES:
            raise PayloadTooLarge("FloatLib upload body is too large")
        body.extend(chunk)

    async def receive():
        return {"type": "http.request", "body": bytes(body), "more_body": False}

    parsed = MultipartRequest(request.scope, receive=receive)
    try:
        async with parsed.form(max_files=1, max_fields=0) as form:
            if len(form.multi_items()) != 1 or set(form) != {"Kernel.lean"}:
                raise BadRequest("Expected exactly one file, Kernel.lean")
            part = form["Kernel.lean"]
            if not hasattr(part, "read"):
                raise BadRequest("Kernel.lean must be a file")
            kernel = await part.read(MAX_FILE_BYTES + 1)
    except (BadRequest, PayloadTooLarge):
        raise
    except Exception as exc:  # noqa: BLE001 - malformed multipart input
        raise BadRequest("Expected exactly one file, Kernel.lean") from exc
    if len(kernel) > MAX_FILE_BYTES:
        raise PayloadTooLarge(f"Kernel.lean must be at most {MAX_FILE_BYTES} bytes")
    if not kernel:
        raise BadRequest("Kernel.lean must be non-empty")
    try:
        kernel.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BadRequest("Kernel.lean must be UTF-8 text") from exc
    return kernel


async def used_in_deflate(services, hotkey, registered_at) -> bool:
    # Explicitly use DEFLATE's factory; never fall back to FloatLib's database.
    sessions = services.competitions.get("deflate").sessions
    if sessions is None:
        raise ServiceUnavailable("DEFLATE usage connection is not configured")
    async with sessions() as session:
        return bool(
            await session.scalar(
                text("""
                    SELECT EXISTS (
                        SELECT 1 FROM submissions
                        WHERE hotkey = :hotkey AND baseline_key IS NULL
                          AND submitted_at >= :registered_at
                    )
                """),
                {"hotkey": hotkey, "registered_at": registered_at},
            )
        )


def connection_failure(exc) -> bool:
    if isinstance(exc, InterfaceError):
        return True
    if isinstance(exc, OperationalError):
        code = getattr(exc.orig, "sqlstate", None)
        return code is None or code.startswith("08") or code in {"57P01", "57P02", "57P03", "53300"}
    return isinstance(exc, (TimeoutError, PoolTimeout))


async def eligible(services, hotkey, registered_at) -> None:
    settings = services.settings
    if settings.floatlib_check_deflate_usage:
        try:
            async with asyncio.timeout(DEFLATE_TIMEOUT_SECONDS):
                used = await used_in_deflate(services, hotkey, registered_at)
        except (OperationalError, InterfaceError, TimeoutError, PoolTimeout) as exc:
            if not connection_failure(exc):
                raise
            # Do not include DSNs or passwords from exception strings in the warning.
            logger.warning(
                "[floatlib] DEFLATE usage unavailable; applying registration cutoff for hotkey %s",
                hotkey,
            )
        else:
            if used:
                raise PaymentRequired(
                    "This registration has already been used for a DEFLATE submission.",
                    reason_code="NO_ENTITLEMENT",
                )
            return
    cutoff = settings.floatlib_registration_cutoff
    if cutoff is None:
        raise ServiceUnavailable("FloatLib registration cutoff is not configured")
    if registered_at <= cutoff:
        raise PaymentRequired(
            "The latest registration must be after the FloatLib registration cutoff.",
            reason_code="REGISTRATION_BEFORE_CUTOFF",
        )


async def enqueue(session, services, hotkey: str, kernel: bytes, digest: str) -> schemas.Accepted:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:hotkey, 0))"), {"hotkey": hotkey}
    )
    duplicate = (
        await session.execute(
            text(
                "SELECT id, state FROM submissions WHERE hotkey=:hotkey AND slot='full' AND digest=:digest"
            ),
            {"hotkey": hotkey, "digest": digest},
        )
    ).first()
    if duplicate is not None:
        raise Conflict(
            "Duplicate submission: this hotkey has already uploaded the same file.",
            reason_code="DUPLICATE_SUBMISSION",
            extra={"submission": duplicate.id, "state": duplicate.state},
        )
    registered_at = await session.scalar(
        text(
            "SELECT block_date FROM registrations WHERE ss58_hot=:hotkey ORDER BY block DESC, id DESC LIMIT 1"
        ),
        {"hotkey": hotkey},
    )
    if registered_at is None:
        raise PaymentRequired(
            "This hotkey is not registered on the subnet.", reason_code="NOT_REGISTERED"
        )
    already_used = await session.scalar(
        text(
            "SELECT EXISTS(SELECT 1 FROM submissions WHERE hotkey=:hotkey AND submitted_at >= :registered_at)"
        ),
        {"hotkey": hotkey, "registered_at": registered_at},
    )
    if already_used:
        raise PaymentRequired(
            "This registration has already been used for a FloatLib submission.",
            reason_code="NO_ENTITLEMENT",
        )
    await eligible(services, hotkey, registered_at)
    sid = await session.scalar(
        text("""
            INSERT INTO submissions (hotkey, slot, digest, state, submitted_at)
            VALUES (:hotkey, 'full', :digest, 'queued', clock_timestamp()) RETURNING id
        """),
        {"hotkey": hotkey, "digest": digest},
    )
    await session.execute(
        text(
            "INSERT INTO submission_files (submission_id, name, content) VALUES (:id, 'Kernel.lean', :content)"
        ),
        {"id": sid, "content": kernel},
    )
    await session.commit()
    return schemas.Accepted(submission=sid, digest=digest)


def require_open_intake(services: ServicesDep) -> None:
    resolve_competition(services, "floatlib", FLOATLIB)
    if not getattr(services.settings, "floatlib_submissions_enabled", False):
        raise Forbidden(
            "Submissions for this competition are closed.", reason_code="SUBMISSIONS_CLOSED"
        )
    refuse_if_paused(services)


@router.post(
    "/floatlib/submissions",
    status_code=201,
    dependencies=[Depends(require_open_intake)],
    response_model=schemas.Accepted,
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
    session: FloatlibSessionDep,
    hotkey: str = Header(..., alias="X-Conjectures-Hotkey"),
    timestamp: int = Header(..., alias="X-Conjectures-Timestamp"),
    signature: str = Header(..., alias="X-Conjectures-Signature"),
) -> schemas.Accepted:
    resolve_competition(services, "floatlib", FLOATLIB)
    if not getattr(services.settings, "floatlib_submissions_enabled", False):
        raise Forbidden(
            "Submissions for this competition are closed.", reason_code="SUBMISSIONS_CLOSED"
        )
    refuse_if_paused(services)
    await store.compatible(session)
    await _spend_ip(request, session, services)
    settings = services.settings
    if abs(int(time.time()) - timestamp) > settings.competition_signature_window_seconds:
        raise Unauthorized(
            "The signed timestamp is outside the freshness window.", reason_code="SIGNATURE_EXPIRED"
        )
    try:
        signature_bytes = bytes.fromhex(signature.removeprefix("0x"))
        if len(signature_bytes) != 64 or not 2 <= len(hotkey) <= 64:
            raise ValueError("invalid signature or address length")
    except ValueError as exc:
        raise Unauthorized("Malformed hotkey signature.") from exc
    kernel = await read_kernel(request)
    digest = hashlib.sha256(kernel).hexdigest()
    verify_signature(
        address=hotkey,
        message=competition_sig.submit_message(
            competition="floatlib", digest=digest, hotkey=hotkey, timestamp=timestamp
        ),
        signature=signature_bytes,
    )
    await _spend(
        session, f"hotkey:{hotkey}", limit=settings.competition_rate_per_minute, what="hotkey"
    )
    return await enqueue(session, services, hotkey, kernel, digest)
