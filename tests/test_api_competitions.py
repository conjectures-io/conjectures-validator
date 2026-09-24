"""The competition surface's wiring: a second database, or none at all.

Two databases in one process is a shape worth testing directly, because both of its failure
modes are quiet. A deployment that was never given a competition database must refuse that
surface rather than fall through to the proofs engine and write competition rows into it; and
a deployment that *was* given one, and cannot reach it, must leave rotation rather than serve
half its surface as 503s while reporting ready.

The routes themselves are not here yet. What is here is everything they will stand on.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("fastapi", reason="submission API tests need the service extra")
pytest.importorskip("sqlalchemy", reason="submission API tests need the db extra")
pytest.importorskip("httpx", reason="submission API tests need the service extra")
pytest.importorskip("psycopg", reason="submission API tests need the db extra")

from conftest import COMPETITION_SKIP_REASON, competition_dsn
from conftest_api import (
    build_settings,
    harness,
    postgres_dsn,
)
from test_api_auth import production_env

from conjectures_subnet.db import create_async_db_engine
from submission_api.app import competition_url
from submission_api.dependencies import get_competition_session
from submission_api.errors import ServiceUnavailable
from submission_api.settings import Settings, SettingsError

pytestmark = pytest.mark.skipif(postgres_dsn() is None, reason="no database")


def run(coro):
    return asyncio.run(coro)


async def _client(kit):
    from httpx import ASGITransport, AsyncClient

    return AsyncClient(transport=ASGITransport(app=kit.app), base_url="http://validator.test")


# ── configuration ──────────────────────────────────────────────────────────


def test_production_refuses_the_competition_surface_without_an_explicit_url():
    # The fallback assembles a URL from POSTGRES_*, which is a guess. A guess about which
    # database to write to is not one to ship, so production says so at startup. Built from
    # test_api_auth.production_env so this asserts the competition refusal specifically,
    # rather than whichever production requirement happens to be checked first.
    with pytest.raises(SettingsError, match="COMPETITION_DATABASE_URL"):
        Settings.from_env(production_env(COMPETITIONS_ENABLED="1"))


def test_production_accepts_the_competition_surface_with_an_explicit_url():
    settings = Settings.from_env(
        production_env(
            COMPETITIONS_ENABLED="1",
            COMPETITION_DATABASE_URL="postgresql+psycopg://u:p@db:5432/competition",
        )
    )
    assert settings.competitions_enabled
    assert settings.competition_database_url.endswith("/competition")


def test_development_may_leave_the_competition_url_to_the_resolver():
    settings = build_settings(COMPETITIONS_ENABLED="1")
    assert settings.competitions_enabled
    assert settings.competition_database_url == ""


def test_competitions_are_off_unless_asked_for():
    assert build_settings().competitions_enabled is False


def test_the_competition_database_may_not_be_the_proofs_database():
    """The guard that keeps the split true at runtime rather than by convention.

    The resolvers default to different names, so this only fires when someone has pointed
    them at the same place by hand -- which would put Alembic's schema into Flyway's database
    and would otherwise be discovered by a migration, not by a startup check.
    """
    shared = postgres_dsn()
    assert shared is not None
    settings = build_settings(
        COMPETITIONS_ENABLED="1",
        DATABASE_URL=shared,
        COMPETITION_DATABASE_URL=shared,
    )
    with pytest.raises(SettingsError, match="must not be the proofs database"):
        competition_url(settings)


def test_a_distinct_competition_url_is_returned_unchanged():
    settings = build_settings(
        COMPETITIONS_ENABLED="1",
        DATABASE_URL="postgresql+psycopg://u:p@db:5432/conjectures",
        COMPETITION_DATABASE_URL="postgresql+psycopg://u:p@db:5432/conjectures_competition",
    )
    assert competition_url(settings).endswith("/conjectures_competition")


def test_a_second_competition_is_refused_at_startup():
    """Two competitions would share every table, so the registry will not hold them.

    Every handler resolves its competition from the slug in the path, so nothing at request
    time would notice: both slugs would resolve and both would read and write the same rows,
    because the competition schema has no slug column to tell them apart. The guard is on
    construction so it holds for every route at once, rather than for whichever handler
    remembered to check.
    """
    from submission_api.competitions import Competition, CompetitionRegistry

    one = Competition(slug="miniz-oxide", name="miniz", speed_floor=8.0)
    two = Competition(slug="rust-competition", name="rust", speed_floor=8.0)
    assert len(CompetitionRegistry.of(one)) == 1
    with pytest.raises(ValueError, match="no slug column"):
        CompetitionRegistry((one, two))


# ── the dependency ─────────────────────────────────────────────────────────


def test_an_unconfigured_competition_session_refuses_rather_than_falling_back():
    """The important half: it raises, rather than handing back the proofs session.

    A fallback here would be silent and would write competition rows into the database
    Flyway owns, so the absence is an outage of this surface and not a reason to improvise.
    """

    async def scenario():
        kit = await harness().setup()
        try:
            assert kit.services.competition_sessions is None
            assert kit.services.competition_engine is None
            agen = get_competition_session(kit.services)
            with pytest.raises(ServiceUnavailable) as raised:
                await agen.__anext__()
            assert raised.value.reason_code == "COMPETITIONS_UNAVAILABLE"
        finally:
            await kit.teardown()

    run(scenario())


# ── readiness ──────────────────────────────────────────────────────────────


def test_readiness_reports_no_competition_database_as_absent_not_broken():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await _client(kit) as client:
                ready = await client.get("/readyz")
            assert ready.status_code == 200
            assert ready.json()["competition_database"] is None
        finally:
            await kit.teardown()

    run(scenario())


@pytest.mark.skipif(competition_dsn() is None, reason=COMPETITION_SKIP_REASON)
def test_readiness_refuses_the_legacy_competition_schema():
    async def scenario():
        engine = create_async_db_engine(competition_dsn())
        kit = await harness(competition_engine=engine).setup()
        try:
            async with await _client(kit) as client:
                ready = await client.get("/readyz")
            assert ready.status_code == 503
            assert ready.json()["competition_database"] is False
        finally:
            await kit.teardown()
            await engine.dispose()

    run(scenario())


def test_an_unreachable_competition_database_takes_the_replica_out_of_rotation():
    """Configured and unreachable is a 503, not a degraded 200.

    One process serves one port on one origin, so a replica that cannot answer
    `/v1/competitions/*` should leave rotation rather than serve that half as errors while
    telling the orchestrator it is fine.
    """

    async def scenario():
        # A URL that parses and resolves to nothing listening. Refused synchronously, so the
        # test does not wait on a connect timeout.
        engine = create_async_db_engine("postgresql+psycopg://nobody:nothing@127.0.0.1:1/absent")
        kit = await harness(competition_engine=engine).setup()
        try:
            async with await _client(kit) as client:
                ready = await client.get("/readyz")
            assert ready.status_code == 503
            # `extra` merges into the problem document at the top level; `detail` is the
            # human-readable string. See ApiError.problem.
            body = ready.json()
            assert body["competition_database"] is False
            # The proofs database is fine; only the competition half failed.
            assert body["database"] is True
        finally:
            await kit.teardown()
            await engine.dispose()

    run(scenario())


# Public route contracts now live in test_compression_api.py and
# test_compression_postgres.py, using the compression-owned schema. The former
# byte-ranking/inline-source assertions no longer describe the public API.
