"""Liveness and readiness.

`/healthz` says the process is up. `/readyz` touches the database and the task pool, so a
deployment that boots but cannot serve is not reported ready.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from submission_api import __version__, schemas
from submission_api.dependencies import Services, get_services, get_session
from submission_api.errors import ServiceUnavailable


router = APIRouter(tags=["operations"])


@router.get("/healthz", response_model=schemas.Health, summary="Liveness")
async def healthz(services: Services = Depends(get_services)) -> schemas.Health:
    return schemas.Health(
        status="ok", version=__version__, app_mode=services.settings.app_mode
    )


@router.get("/readyz", response_model=schemas.Readiness, summary="Readiness")
async def readyz(
    services: Services = Depends(get_services),
    session: AsyncSession = Depends(get_session),
) -> schemas.Readiness:
    try:
        await session.execute(text("SELECT 1"))
        database = True
    except Exception:  # noqa: BLE001 - readiness must not raise
        database = False
    tasks = len(services.catalog.entries)
    ready = database and tasks > 0
    payload = schemas.Readiness(
        status="ok" if ready else "unavailable",
        database=database,
        task_pool=tasks > 0,
        tasks=tasks,
    )
    if not ready:
        raise ServiceUnavailable("service is not ready", extra=payload.model_dump())
    return payload
