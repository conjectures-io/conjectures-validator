"""Internal-only ASGI app for binding manual review decisions.

It intentionally exposes none of the miner submission routes and constructs no
authenticator, payment reader, task catalog, or verifier dispatcher. Deploy it
with the least-privilege reviewer database role on a private listener.
"""

from __future__ import annotations

import os
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Mapping

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from conjectures_subnet.db import async_session_factory, create_async_db_engine, database_url
from conjectures_subnet.db.errors import DatabaseError
from submission_api import errors
from submission_api.routers.reviews import router as review_router


IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@._:-]{0,254}$")


@dataclass(frozen=True)
class ReviewSettings:
    database_url: str
    review_api_token: str
    reviewer_identity: str
    production: bool

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "ReviewSettings":
        env = os.environ if environ is None else environ
        token = env.get("REVIEW_API_TOKEN", "").strip()
        if len(token) < 32:
            raise RuntimeError("REVIEW_API_TOKEN must contain at least 32 characters")
        identity = env.get("REVIEWER_IDENTITY", "operator").strip()
        if IDENTITY.fullmatch(identity) is None:
            raise RuntimeError("REVIEWER_IDENTITY contains invalid characters")
        return cls(
            database_url=env.get("DATABASE_URL", "").strip() or database_url(),
            review_api_token=token,
            reviewer_identity=identity,
            production=env.get("APP_MODE", "PROD").strip().upper() == "PROD",
        )


@dataclass(frozen=True)
class ReviewServices:
    settings: ReviewSettings
    engine: object
    sessions: object


def create_review_app(settings: ReviewSettings | None = None) -> FastAPI:
    resolved = settings or ReviewSettings.from_env()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        engine = create_async_db_engine(resolved.database_url)
        application.state.services = ReviewServices(
            settings=resolved,
            engine=engine,
            sessions=async_session_factory(engine),
        )
        try:
            yield
        finally:
            await engine.dispose()

    application = FastAPI(
        title="conjectures.io internal review service",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if resolved.production else "/docs",
        redoc_url=None,
        openapi_url=None if resolved.production else "/openapi.json",
    )
    application.add_exception_handler(errors.ApiError, errors.api_error_handler)
    application.add_exception_handler(DatabaseError, errors.database_error_handler)
    application.add_exception_handler(RequestValidationError, errors.validation_error_handler)
    application.add_exception_handler(Exception, errors.unhandled_error_handler)
    application.include_router(review_router)
    return application
