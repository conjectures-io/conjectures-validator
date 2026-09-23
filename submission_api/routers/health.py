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

import asyncio

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
    competitions = await _competition_databases(services)
    # Competition databases are reported, never required. Every replica reads the same ones, so
    # gating readiness on them would take the whole proofs platform out of rotation because one
    # competition's database is down -- a failure its own routes already answer with a 503.
    ready = database and tasks > 0
    payload = schemas.Readiness(
        status="ok" if ready else "unavailable",
        database=database,
        task_pool=tasks > 0,
        tasks=tasks,
        competitions=competitions,
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


# A probe that waits on a hung competition database would time the whole readiness check out
# and take the replica out of rotation after all, which is the thing reporting-not-requiring
# exists to avoid. So each competition gets a short, independent budget.
COMPETITION_PROBE_SECONDS = 2.0


async def _competition_databases(services: ServicesDep) -> dict[str, bool]:
    results: dict[str, bool] = {}
    for competition in services.competitions:
        error: str | None = None
        try:
            results[competition.slug] = await asyncio.wait_for(
                competition.ping(), COMPETITION_PROBE_SECONDS
            )
        except Exception as exc:  # noqa: BLE001 - readiness must not raise
            results[competition.slug] = False
            error = f"{type(exc).__name__}: {exc}"
        if not results[competition.slug]:
            get_axiom().warn(
                source="api-health",
                event_type="competition_database_unreachable",
                competition=competition.slug,
                database_error=error,
            )
    return results
