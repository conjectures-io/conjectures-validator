"""Application factory.

Expensive, immutable state is built once during lifespan. Interactive docs are exposed only
outside production; leaving the schema and Swagger UI reachable on a live miner-facing endpoint
is a mistake worth not repeating.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from conjectures_subnet.bounty import FlatBountyPricer
from conjectures_subnet.db import (
    async_session_factory,
    create_async_db_engine,
    database_url,
)
from conjectures_subnet.db.errors import DatabaseError
from submission_api import __version__, errors
from submission_api.auth import build_authenticator
from submission_api.dependencies import Services
from submission_api.payments import build_payment_verifier
from submission_api.routers import health, submissions, tasks
from submission_api.settings import Settings
from submission_api.taskpool import TaskCatalog
from submission_api.verification import build_dispatcher
from verifier.errors import VerifierError

DESCRIPTION = """
Paid Lean-proof submission API for conjectures.io, Bittensor Subnet 66.

A miner pays for one verification attempt, then submits a `conjectures-submission/v1` bundle
for one allowlisted task. Payment buys an attempt; it never changes the Lean verdict and does
not guarantee a reward.
""".strip()


def build_services(
    settings: Settings, *, catalog: TaskCatalog | None = None
) -> Services:
    """Assemble the service graph. Tests inject a catalog directly."""
    resolved_catalog = catalog or TaskCatalog.load(
        allowlist_path=settings.task_allowlist_path,
        pool_root=settings.task_pool_root,
    )
    # The URL comes from conjectures_subnet.db, so the API, the workers and Flyway can never
    # disagree about which database they are talking to.
    engine = create_async_db_engine(settings.database_url or database_url())
    return Services(
        settings=settings,
        engine=engine,
        sessions=async_session_factory(engine),
        catalog=resolved_catalog,
        authenticator=build_authenticator(settings),
        payments=build_payment_verifier(settings),
        dispatcher=build_dispatcher(settings),
        pricing=FlatBountyPricer(
            amount_rao=settings.bounty_amount_rao,
            policy_version=settings.bounty_policy_version,
        ),
    )


def create_app(
    settings: Settings | None = None, *, services: Services | None = None
) -> FastAPI:
    resolved_settings = settings or (
        services.settings if services else Settings.from_env()
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if not hasattr(application.state, "services"):
            application.state.services = build_services(resolved_settings)
        try:
            yield
        finally:
            # Only dispose an engine this app created; an injected one belongs to the caller.
            if services is None:
                await application.state.services.engine.dispose()

    application = FastAPI(
        title="conjectures.io Subnet 66 submission API",
        description=DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if resolved_settings.expose_docs else None,
        redoc_url="/redoc" if resolved_settings.expose_docs else None,
        openapi_url="/openapi.json" if resolved_settings.expose_docs else None,
    )
    if services is not None:
        # Injected services are already constructed, so make them reachable without requiring
        # the caller to drive lifespan (in-process test clients do not).
        application.state.services = services
    application.add_exception_handler(errors.ApiError, errors.api_error_handler)
    application.add_exception_handler(VerifierError, errors.verifier_error_handler)
    application.add_exception_handler(DatabaseError, errors.database_error_handler)
    application.add_exception_handler(
        RequestValidationError, errors.validation_error_handler
    )
    application.add_exception_handler(Exception, errors.unhandled_error_handler)
    application.include_router(health.router)
    application.include_router(tasks.router)
    application.include_router(submissions.router)
    return application
