"""Liveness and readiness.

`/healthz` says the process is up. `/readyz` touches the database and the task pool, so a
deployment that boots but cannot serve is not reported ready.

Both are exempt from the per-request Axiom events, because the orchestrator polls them on a fixed
interval and reporting every probe would bury everything else. That leaves one thing that has to be
reported explicitly: an *unready* answer. A replica whose database has gone away removes itself from
service, and without the event below the only trace would be a `503` nothing records and a
traceback this handler deliberately swallows.
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from conjectures_subnet.axiom import get_axiom
from submission_api import __version__, schemas
from submission_api.dependencies import ServicesDep, SessionDep
from submission_api.errors import ServiceUnavailable

router = APIRouter(tags=["operations"])


@router.get("/healthz", response_model=schemas.Health, summary="Liveness")
async def healthz(services: ServicesDep) -> schemas.Health:
    return schemas.Health(
        status="ok", version=__version__, app_mode=services.settings.app_mode
    )


@router.get("/readyz", response_model=schemas.Readiness, summary="Readiness")
async def readyz(services: ServicesDep, session: SessionDep) -> schemas.Readiness:
    database_error: str | None = None
    try:
        await session.execute(text("SELECT 1"))
        database = True
    except Exception as exc:  # noqa: BLE001 - readiness must not raise
        database = False
        # Kept, so the event can say *why* the probe failed. The response still must not: an
        # unauthenticated endpoint does not describe the database it could not reach.
        database_error = f"{type(exc).__name__}: {exc}"
    tasks = len(services.catalog.entries)
    ready = database and tasks > 0
    payload = schemas.Readiness(
        status="ok" if ready else "unavailable",
        database=database,
        task_pool=tasks > 0,
        tasks=tasks,
    )
    if not ready:
        # `error`: this replica is out of service. Emitted on every failing probe rather than only
        # on the transition, because the handler holds no state between requests and a probe that
        # stops arriving is itself a signal an operator wants to see the shape of.
        get_axiom().error(
            source="api-health",
            event_type="readiness_degraded",
            database=database,
            task_pool=tasks > 0,
            tasks=tasks,
            database_error=database_error,
        )
        raise ServiceUnavailable("service is not ready", extra=payload.model_dump())
    return payload
