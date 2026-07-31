"""Connection plumbing: resolve the database URL and hand out sessions.

The URL is resolved once, here, so every process — the API and each worker — talks to the
database the deployment configured. `DATABASE_URL` wins; otherwise it is assembled from the
`POSTGRES_*` variables that `docker-compose.db.yml` and `.env.example` already define, so a
host-side script needs no extra configuration.

PostgreSQL only, deliberately. The schema uses domains, native enums, JSONB, INET, partial
indexes and a plpgsql trigger; there is no portable subset to fall back to, and pretending
otherwise would let a test pass against a database the service will never run on.

Neither variant uses AUTOCOMMIT: idempotency, payment claiming, and multi-row verdict
recording all depend on real transactions plus the unique constraints in the migration.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

DRIVER = "postgresql+psycopg"


def database_url() -> str:
    """The SQLAlchemy URL for this deployment.

    Prefers `DATABASE_URL`; otherwise assembles it from `POSTGRES_USER`, `POSTGRES_PASSWORD`,
    `POSTGRES_HOST`, `POSTGRES_PORT` and `POSTGRES_DB`.
    """
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        return url
    user = os.getenv("POSTGRES_USER", "conjectures")
    password = os.getenv("POSTGRES_PASSWORD", "conjectures")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    database = os.getenv("POSTGRES_DB", "conjectures")
    return f"{DRIVER}://{user}:{password}@{host}:{port}/{database}"


def create_db_engine(url: str | None = None, *, echo: bool = False) -> Engine:
    """A sync Engine with a pre-ping pool, for workers and operator tooling."""
    return create_engine(
        url or database_url(), pool_pre_ping=True, echo=echo, future=True
    )


def session_factory(engine: Engine) -> sessionmaker[Session]:
    """`expire_on_commit=False` keeps returned rows readable after the unit of work closes."""
    return sessionmaker(
        bind=engine, expire_on_commit=False, autoflush=False, future=True
    )


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on error, always close."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_async_db_engine(
    url: str | None = None, *, echo: bool = False
) -> AsyncEngine:
    """An async Engine with a pre-ping pool, for the submission API."""
    return create_async_engine(url or database_url(), pool_pre_ping=True, echo=echo)


def async_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@asynccontextmanager
async def async_session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Async transactional scope: commit on success, roll back on error, always close."""
    session = factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
