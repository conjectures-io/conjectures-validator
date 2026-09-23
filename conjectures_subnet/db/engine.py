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


COMPETITION_DATABASE = "conjectures_competition"


def competition_database_url() -> str:
    """The SQLAlchemy URL for the competition database.

    A second database in the same cluster, not a second cluster and not a second schema.
    The competition schema is owned by Alembic while this one is owned by Flyway, and two
    migration tools sharing a database would each see the other's objects as drift; separate
    databases give each tool a history table it alone writes.

    The separation is also what makes the two key models coexist. V035 retired the miner
    hotkey here and enforces it with triggers, while a competition entitlement is inherently
    per-hotkey — one subnet registration buys one accepted submission. Those triggers do not
    reach across a database boundary, so neither model has to bend to the other.

    The cost is that nothing links the two: PostgreSQL has no cross-database foreign key and
    no cross-database transaction. A competition row names an account by a plain `account_id`
    that this process is responsible for, and no write may assume both databases moved
    together.

    Prefers `COMPETITION_DATABASE_URL`; otherwise assembles it from the same `POSTGRES_*`
    credentials as `database_url`, differing only in `COMPETITION_POSTGRES_DB`. Deliberately
    not derived from `DATABASE_URL`: that variable names one database, and rewriting its path
    component to reach another is the kind of guess that silently points a migration at
    production.
    """
    url = os.getenv("COMPETITION_DATABASE_URL", "").strip()
    if url:
        return url
    user = os.getenv("POSTGRES_USER", "conjectures")
    password = os.getenv("POSTGRES_PASSWORD", "conjectures")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    database = os.getenv("COMPETITION_POSTGRES_DB", COMPETITION_DATABASE)
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
