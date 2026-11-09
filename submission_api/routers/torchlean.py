"""Signed kernel-competition intake and the minimal polling surface returned by intake.

TorchLean and Tensor kernels run the same competition code against their own databases, so one
set of routes serves both: `build_router(slug)` mounts it under `/v1/competitions/<slug>`, with
that slug's database, intake settings (`<prefix>_submissions_enabled` and friends, see
`competitions.kernel_settings_prefix`) and signed-message competition.
"""

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
from submission_api.competitions import (
    KERNEL_COMPETITIONS,
    TORCHLEAN,
    Competition,
    kernel_settings_prefix,
)
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


class TorchLeanAccepted(BaseModel):
    competition: str = TORCHLEAN
    submission: int
    source_sha256: str
    proof_state: Literal["queued"] = "queued"
    status_url: str
    intake_mode: Literal["subnet", "dev"]


def intake_setting(settings, slug: str, name: str):
    """One of a kernel competition's intake settings, e.g. `submissions_enabled`."""
    return getattr(settings, f"{kernel_settings_prefix(slug)}_{name}")


def open_check(slug: str):
    """The dependency refusing uploads while `slug`'s intake is closed or all writes are paused."""

    def require_open(services: ServicesDep):
        resolve_competition(services, slug, TORCHLEAN)
        if not intake_setting(services.settings, slug, "submissions_enabled"):
            raise Forbidden(
                "Submissions for this competition are closed.", reason_code="SUBMISSIONS_CLOSED"
            )
        refuse_if_paused(services)

    return require_open


def competition_session(slug: str):
    """The dependency opening `slug`'s own database, with its failures as problem responses."""
    name = KERNEL_COMPETITIONS[slug]

    async def session(services: ServicesDep):
        competition = resolve_competition(services, slug, TORCHLEAN)
        if competition.sessions is None:
            raise ServiceUnavailable(f"{name} database is not configured")
        async with competition.sessions() as session:
            try:
                yield session
            except ProgrammingError as exc:
                if getattr(exc.orig, "sqlstate", None) not in ("42501", "42703", "42P01"):
                    raise
                raise ServiceUnavailable(
                    f"{name} schema or API grants need updating.",
                    reason_code="COMPETITION_SCHEMA_UNAVAILABLE",
                ) from exc
            except (OperationalError, InterfaceError) as exc:
                raise ServiceUnavailable(
                    f"The {name} database is unreachable.", reason_code="COMPETITION_UNAVAILABLE"
                ) from exc

    return session


# TorchLean's, under the names this module has always had.
require_open = open_check(TORCHLEAN)
torchlean_session = competition_session(TORCHLEAN)
TorchLeanSession = Annotated[AsyncSession, Depends(torchlean_session)]


async def spend(services, subject, limit, slug=TORCHLEAN):
    # Request limits are central-API storage, never tables in the competition's worker database.
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
            # Each competition has its own budget: an upload to one never spends the other's.
            {"subject": f"{slug}:{subject}", "cap": limit + 1},
        )
        await session.commit()
    if hits > limit:
        raise TooManyRequests(
            f"{KERNEL_COMPETITIONS[slug]} submission request limit exceeded.",
            reason_code="RATE_LIMITED",
        )


UPLOAD_BODY = {
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
}


def build_router(slug: str) -> APIRouter:
    """Every route of the kernel competition served as `slug`."""
    name = KERNEL_COMPETITIONS[slug]
    base = f"/v1/competitions/{slug}"
    session_dependency = competition_session(slug)
    Session = Annotated[AsyncSession, Depends(session_dependency)]
    router = APIRouter(tags=[slug])

    @router.post(
        f"{base}/submissions",
        status_code=201,
        response_model=TorchLeanAccepted,
        dependencies=[Depends(open_check(slug))],
        openapi_extra=UPLOAD_BODY,
    )
    async def submit(
        request: Request,
        services: ServicesDep,
        session: Session,
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
            await spend(services, "ip:" + address, settings.competition_ip_rate_per_minute, slug)
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
            kernel = await read_kernel(
                request, max_file_bytes=intake_setting(settings, slug, "max_source_bytes")
            )
            digest = hashlib.sha256(kernel).hexdigest()
            attempt.update(source_sha256=digest, kernel_bytes=len(kernel))
            # The slug is in the signed message, so a signature for one competition is
            # refused by the other.
            verify_signature(
                address=hotkey,
                message=competition_sig.submit_message(
                    competition=slug, digest=digest, hotkey=hotkey, timestamp=timestamp
                ),
                signature=signed,
            )
            await spend(services, "hotkey:" + hotkey, settings.competition_rate_per_minute, slug)
            await store.compatible(session)
            dev = intake_setting(settings, slug, "dev_skip_registration")
            # Backstop for injected service graphs as well as environment validation.
            if dev and settings.production:
                raise ServiceUnavailable(
                    f"{name} dev registration bypass is unavailable in production"
                )
            sid = await store.enqueue(
                session,
                hotkey=hotkey,
                kernel=kernel,
                digest=digest,
                cutoff=intake_setting(settings, slug, "registration_cutoff"),
                dev=dev,
            )
            # After `enqueue` committed. Ids, hotkey and digest, never the file.
            get_axiom().info(
                source="api-competitions",
                event_type="submission_accepted",
                competition=slug,
                submission_id=sid,
                intake_mode="dev" if dev else "subnet",
                **attempt,
            )
            return TorchLeanAccepted(
                competition=slug,
                submission=sid,
                source_sha256=digest,
                intake_mode="dev" if dev else "subnet",
                status_url=f"{base}/submissions/{sid}",
            )
        except ApiError as problem:
            # Refusals raised before the handler runs (closed, paused, database unreachable) are
            # on `request_completed` only; these are the ones that got far enough to name a file.
            # Ours (5xx) are errors, the miner's (4xx) warnings, as on the request stream.
            (get_axiom().error if problem.status_code >= 500 else get_axiom().warn)(
                source="api-competitions",
                event_type="submission_rejected",
                competition=slug,
                reason_code=problem.reason_code,
                http_status=problem.status_code,
                **attempt,
            )
            raise

    from submission_api.routers.torchlean_public import build_router as build_public_router

    router.include_router(build_public_router(slug, session_dependency), prefix=base)
    return router


async def listed(competition: Competition, services):
    from submission_api.routers.torchlean_public import listed as public_listing

    return await public_listing(competition, services)


# The complete surface of every kernel competition, registered before legacy slug routes.
router = APIRouter()
for _slug in KERNEL_COMPETITIONS:
    router.include_router(build_router(_slug))
