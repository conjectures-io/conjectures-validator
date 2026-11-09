"""What keeps a burst of competition reads from emptying the competition database's pool.

On 2026-11-08 the website's server rendered ~110 DEFLATE submission pages in two minutes. Every
read ran the scoring-pass head query (~550 ms on production), so 15 pooled connections served a
few dozen requests a second at best, and 338 requests waited out SQLAlchemy's 30 s pool timeout
and failed as 500s. These tests pin the four things that changed: the head query is shared and
remembered, the schema check is remembered, an unknown submission is refused before any pass is
loaded, and an exhausted pool answers a prompt 503.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import TimeoutError as PoolTimeout

from conftest_api import build_settings
from test_compression_api import point
from conjectures_subnet.db.engine import create_async_db_engine
from submission_api import compression_store as store
from submission_api import errors
from submission_api.app import COMPETITION_POOL_TIMEOUT_SECONDS, build_competitions
from submission_api.competitions import Competition, CompetitionRegistry
from submission_api.dependencies import get_competition_session, get_services
from submission_api.errors import ApiError, ServiceUnavailable, api_error_handler
from submission_api.routers import competition_reads as reads


class _Bind:
    """Stands in for an Engine: the memos key on the session's bind, nothing else."""


def _session():
    return SimpleNamespace(bind=_Bind())


@pytest.fixture
def heads(monkeypatch):
    """`_query_head` replaced by a counting stub, and a clock the test moves by hand."""
    now = [1000.0]
    calls: list[object] = []
    state = {"gate": None, "fail": None, "answer": {"id": 1, "version": "v1"}}

    async def query(session, sid):
        calls.append(sid)
        if state["gate"] is not None:
            await state["gate"].wait()
        if state["fail"] is not None:
            raise state["fail"]
        return state["answer"]

    monkeypatch.setattr(store, "_query_head", query)
    monkeypatch.setattr(store, "_clock", lambda: now[0])
    monkeypatch.setattr(store, "HEAD_TTL_SECONDS", 5.0)
    return SimpleNamespace(now=now, calls=calls, state=state)


def test_a_burst_of_reads_runs_the_head_query_once(heads):
    session = _session()

    async def scenario():
        heads.state["gate"] = asyncio.Event()
        readers = [asyncio.create_task(store._head(session, None)) for _ in range(50)]
        await asyncio.sleep(0)
        heads.state["gate"].set()
        return await asyncio.gather(*readers)

    results = asyncio.run(scenario())
    assert heads.calls == [None]
    assert all(result == {"id": 1, "version": "v1"} for result in results)


def test_the_head_is_remembered_for_the_ttl_and_then_asked_again(heads):
    session = _session()

    async def scenario():
        first = await store._head(session, None)
        heads.state["answer"] = {"id": 2, "version": "v2"}
        heads.now[0] += 4.9
        within = await store._head(session, None)
        heads.now[0] += 0.2
        after = await store._head(session, None)
        return first, within, after

    first, within, after = asyncio.run(scenario())
    assert first["id"] == 1 and within["id"] == 1 and after["id"] == 2
    assert heads.calls == [None, None]


def test_no_published_pass_is_remembered_too(heads):
    session = _session()
    heads.state["answer"] = None

    async def scenario():
        return [await store._head(session, None) for _ in range(3)]

    assert asyncio.run(scenario()) == [None, None, None]
    assert heads.calls == [None]


def test_heads_are_per_snapshot_id_and_per_engine(heads):
    one, other = _session(), _session()

    async def scenario():
        await store._head(one, None)
        await store._head(one, 7)
        await store._head(one, 7)
        await store._head(other, None)

    asyncio.run(scenario())
    assert heads.calls == [None, 7, None]


def test_remembered_heads_are_bounded(heads, monkeypatch):
    monkeypatch.setattr(store, "_HEADS_KEPT", 4)
    session = _session()

    async def scenario():
        for sid in range(1, 20):
            await store._head(session, sid)

    asyncio.run(scenario())
    assert len(store._HEADS[session.bind]) <= 4
    assert store._HEADS_INFLIGHT[session.bind] == {}


def test_a_failed_head_query_fails_its_waiters_and_is_not_remembered(heads):
    session = _session()
    heads.state["fail"] = RuntimeError("database went away")

    async def scenario():
        heads.state["gate"] = asyncio.Event()
        readers = [asyncio.create_task(store._head(session, None)) for _ in range(3)]
        await asyncio.sleep(0)
        heads.state["gate"].set()
        outcomes = await asyncio.gather(*readers, return_exceptions=True)
        heads.state["fail"] = None
        heads.state["gate"] = None
        return outcomes, await store._head(session, None)

    outcomes, recovered = asyncio.run(scenario())
    assert all(isinstance(o, RuntimeError) for o in outcomes)
    assert recovered == {"id": 1, "version": "v1"}
    assert heads.calls == [None, None]


def test_a_cancelled_leader_hands_the_query_to_a_waiter(heads):
    session = _session()

    async def scenario():
        heads.state["gate"] = asyncio.Event()
        leader = asyncio.create_task(store._head(session, None))
        await asyncio.sleep(0)
        follower = asyncio.create_task(store._head(session, None))
        await asyncio.sleep(0)
        leader.cancel()
        await asyncio.sleep(0)
        heads.state["gate"].set()
        with pytest.raises(asyncio.CancelledError):
            await leader
        return await follower

    assert asyncio.run(scenario()) == {"id": 1, "version": "v1"}
    assert heads.calls == [None, None]


def test_a_zero_ttl_or_an_unbound_session_always_queries(heads, monkeypatch):
    async def scenario(session):
        await store._head(session, None)
        await store._head(session, None)

    asyncio.run(scenario(SimpleNamespace()))
    monkeypatch.setattr(store, "HEAD_TTL_SECONDS", 0)
    asyncio.run(scenario(_session()))
    assert heads.calls == [None] * 4


class _Columns:
    """A session whose information_schema answer the test controls, counting the asks."""

    def __init__(self, columns):
        self.bind = _Bind()
        self.columns = columns
        self.asked = 0

    async def execute(self, *_args, **_kwargs):
        self.asked += 1
        columns = self.columns
        return SimpleNamespace(scalars=lambda: list(columns))


MODERN = [
    "submission_files.content",
    "submissions.verification_attempt",
    "submissions.account_id",
    "weight_sets.api_snapshot",
]


def test_a_compatible_schema_is_checked_once_per_engine():
    session = _Columns(MODERN)

    async def scenario():
        for _ in range(3):
            await store.compatible(session)

    asyncio.run(scenario())
    assert session.asked == 1
    other = _Columns(MODERN)
    asyncio.run(store.compatible(other))
    assert other.asked == 1


def test_an_incompatible_schema_is_asked_again_so_a_migration_is_noticed():
    session = _Columns(MODERN[:-1])

    async def scenario():
        for _ in range(2):
            with pytest.raises(ServiceUnavailable):
                await store.compatible(session)
        session.columns = MODERN
        await store.compatible(session)
        await store.compatible(session)

    asyncio.run(scenario())
    assert session.asked == 3


# --- the routes -------------------------------------------------------------------------------


class _Reports:
    """A session answering the one statement `/report` runs itself."""

    async def execute(self, *_args, **_kwargs):
        return SimpleNamespace(scalar_one=lambda: "verified in 1.2 s")


@pytest.fixture
def routes(monkeypatch):
    loaded: list[object] = []
    item = point(1, "miner")
    known = {1: item["submission"]}

    async def snapshot(session, sid=None):
        loaded.append(sid)
        return None

    async def compatible(session):
        pass

    async def get_submission(session, sid):
        return known.get(sid)

    async def evidence(session, sub):
        return item

    monkeypatch.setattr(store, "snapshot", snapshot)
    monkeypatch.setattr(store, "compatible", compatible)
    monkeypatch.setattr(store, "get_submission", get_submission)
    monkeypatch.setattr(store, "evidence", evidence)

    app = FastAPI()
    app.include_router(reads.router)
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(PoolTimeout, errors.pool_timeout_handler)
    services = SimpleNamespace(
        settings=SimpleNamespace(cursor_secret="test-secret", submissions_paused=False),
        competitions=CompetitionRegistry.of(Competition("deflate", "Compression", 8)),
    )

    async def injected_services():
        return services

    async def injected_session():
        return _Reports()

    app.dependency_overrides[get_services] = injected_services
    app.dependency_overrides[get_competition_session] = injected_session

    def get(path):
        async def request():
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                return await client.get(path)

        return asyncio.run(request())

    return SimpleNamespace(get=get, loaded=loaded)


@pytest.mark.parametrize("suffix", ["", "/admission", "/report", "/source"])
def test_an_unknown_submission_is_refused_before_any_pass_is_loaded(routes, suffix):
    response = routes.get(f"/v1/competitions/deflate/submissions/999{suffix}")
    assert response.status_code == 404
    assert routes.loaded == []


def test_a_report_never_loads_a_scoring_pass(routes):
    response = routes.get("/v1/competitions/deflate/submissions/1/report")
    assert response.status_code == 200
    assert response.json()["submission_id"] == "1"
    assert routes.loaded == []


def test_a_detail_still_reads_the_current_pass(routes):
    assert routes.get("/v1/competitions/deflate/submissions/1").status_code == 200
    assert routes.loaded == [None]


def test_an_exhausted_pool_is_a_prompt_503_with_retry_after(routes, monkeypatch):
    async def exhausted(session, sid):
        raise PoolTimeout("QueuePool limit of size 5 overflow 10 reached")

    monkeypatch.setattr(store, "get_submission", exhausted)
    response = routes.get("/v1/competitions/deflate/submissions/1")
    assert response.status_code == 503
    assert response.headers["retry-after"] == str(errors.DATABASE_BUSY_RETRY_SECONDS)
    assert response.json()["reason_code"] == errors.REASON_DATABASE_BUSY


def test_the_app_maps_a_pool_timeout_to_its_handler():
    from submission_api.app import create_app

    app = create_app(settings=build_settings())
    assert app.exception_handlers[PoolTimeout] is errors.pool_timeout_handler


# --- the pools --------------------------------------------------------------------------------


def test_competition_engines_wait_seconds_not_half_a_minute_for_a_connection():
    settings = build_settings(
        COMPETITIONS_ENABLED="1",
        COMPETITIONS="deflate,floatlib",
        DATABASE_URL="postgresql+psycopg://u:p@db:5432/conjectures",
        COMPETITION_DEFLATE_DATABASE_URL="postgresql+psycopg://u:p@db:5432/deflate",
        COMPETITION_FLOATLIB_DATABASE_URL="postgresql+psycopg://u:p@db:5432/floatlib",
    )
    registry = build_competitions(settings)
    try:
        assert {e.pool.timeout() for e in registry.engines()} == {COMPETITION_POOL_TIMEOUT_SECONDS}
    finally:
        for engine in registry.engines():
            asyncio.run(engine.dispose())


def test_other_engines_keep_sqlalchemys_default_wait():
    engine = create_async_db_engine("postgresql+psycopg://u:p@db:5432/conjectures")
    try:
        assert engine.pool.timeout() == 30.0
    finally:
        asyncio.run(engine.dispose())
