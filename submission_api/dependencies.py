"""Request-scoped wiring.

Everything expensive — settings, engine, task catalog, authenticator, payment verifier — is
built once during lifespan and stored on the application state. Routers reach it through these
dependencies rather than through module-level globals.

The database belongs to `conjectures_subnet.db`, the validator's shared durable store; this
module only borrows a session from it per request.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from submission_api.auth import Authenticator
from submission_api.payments import PaymentVerifier
from submission_api.settings import Settings
from submission_api.taskpool import TaskCatalog
from submission_api.verification import VerificationDispatcher


@dataclass(frozen=True)
class Services:
    settings: Settings
    engine: AsyncEngine
    sessions: async_sessionmaker
    catalog: TaskCatalog
    authenticator: Authenticator
    payments: PaymentVerifier
    dispatcher: VerificationDispatcher


def get_services(request: Request) -> Services:
    return request.app.state.services


def get_settings(services: Services = Depends(get_services)) -> Settings:
    return services.settings


async def get_session(services: Services = Depends(get_services)) -> AsyncIterator[AsyncSession]:
    """One session per request, always closed.

    The handler commits explicitly, so a request that fails mid-write rolls back rather than
    leaving a partial submission behind.
    """
    async with services.sessions() as session:
        try:
            yield session
        finally:
            await session.close()
