"""Full-entry uploads on isolated FloatLib and DEFLATE schemas, with real signatures."""

import asyncio
import hashlib
import time
from datetime import timedelta
from types import SimpleNamespace

import pytest
from bittensor.sp_core import Keypair
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from submission_api import competition_sig
from submission_api.competitions import Competition, CompetitionRegistry
from submission_api.dependencies import get_services, require_cookie_writer
from submission_api.errors import ApiError, api_error_handler
from submission_api.routers import competitions, floatlib, floatlib_intake
from submission_api.settings import SettingsError
from conftest_api import build_settings
from test_floatlib_api import SCHEMA, T0, execute, floatlib_db as fixture_db, floatlib_entry  # noqa: F401

floatlib_db = fixture_db

ALICE = Keypair.create_from_uri("//Alice")
KERNEL = b"import Contract\n-- upload fixture\n"


@pytest.fixture
def upload_db(floatlib_db):
    execute(floatlib_db, "SELECT setval('submissions_id_seq', (SELECT max(id) FROM submissions))")
    execute(
        floatlib_db,
        """
        CREATE TABLE submission_files (
            submission_id BIGINT REFERENCES submissions(id) ON DELETE CASCADE,
            name TEXT NOT NULL, content BYTEA NOT NULL, PRIMARY KEY (submission_id, name)
        );
        CREATE TABLE registrations (
            id BIGSERIAL PRIMARY KEY, uid INTEGER NOT NULL, ss58_hot TEXT NOT NULL,
            ss58_cold TEXT NOT NULL, block BIGINT NOT NULL, block_date TIMESTAMPTZ NOT NULL
        );
        CREATE TABLE rate_limit_windows (
            subject TEXT NOT NULL, window_start TIMESTAMPTZ NOT NULL, hits INTEGER NOT NULL,
            PRIMARY KEY(subject, window_start)
        );
        CREATE SCHEMA floatlib_deflate_fixture;
        CREATE TABLE floatlib_deflate_fixture.submissions (
            hotkey TEXT, baseline_key TEXT, submitted_at TIMESTAMPTZ, state TEXT
        );
    """,
    )
    try:
        yield floatlib_db
    finally:
        execute(floatlib_db, "DROP SCHEMA floatlib_deflate_fixture CASCADE")


def register(db, at=T0, block=100):
    execute(
        db,
        """
        INSERT INTO registrations (uid, ss58_hot, ss58_cold, block, block_date)
        VALUES (1, :hotkey, 'cold', :block, :at)
    """,
        hotkey=ALICE.ss58_address,
        block=block,
        at=at,
    )


def services_for(db, **overrides):
    engine = create_async_engine(db.dsn, connect_args={"options": f"-c search_path={SCHEMA}"})
    deflate_engine = create_async_engine(
        db.dsn, connect_args={"options": "-c search_path=floatlib_deflate_fixture"}
    )
    sessions = async_sessionmaker(engine)
    settings = SimpleNamespace(
        floatlib_submissions_enabled=True,
        floatlib_registration_cutoff=T0 - timedelta(seconds=1),
        floatlib_check_deflate_usage=False,
        deflate_submissions_enabled=False,
        submissions_paused=False,
        competition_signature_window_seconds=300,
        competition_ip_rate_per_minute=100,
        competition_rate_per_minute=100,
        trusted_proxy_hops=0,
        trusted_proxy_peers=(),
        cursor_secret="test",
    )
    for name, value in overrides.items():
        setattr(settings, name, value)
    return SimpleNamespace(
        settings=settings,
        competitions=CompetitionRegistry.of(
            Competition("deflate", "DEFLATE", 8.0, sessions=async_sessionmaker(deflate_engine)),
            floatlib_entry(engine=engine, sessions=sessions),
        ),
        competition_sessions=sessions,
    ), (engine, deflate_engine)


def app_for(services):
    app = FastAPI()
    app.include_router(floatlib.router)
    app.include_router(competitions.router)
    app.dependency_overrides[get_services] = lambda: services
    app.dependency_overrides[require_cookie_writer] = lambda: SimpleNamespace()
    app.add_exception_handler(ApiError, api_error_handler)
    return app


def signed(kernel=KERNEL, timestamp=None, board="floatlib"):
    timestamp = int(time.time()) if timestamp is None else timestamp
    message = competition_sig.submit_message(
        competition=board,
        digest=hashlib.sha256(kernel).hexdigest(),
        hotkey=ALICE.ss58_address,
        timestamp=timestamp,
    )
    return {
        "X-Conjectures-Hotkey": ALICE.ss58_address,
        "X-Conjectures-Timestamp": str(timestamp),
        "X-Conjectures-Signature": ALICE.sign(message.encode()).hex(),
    }


def post(db, kernel=KERNEL, *, headers=None, files=None, path="floatlib", **settings):
    async def run():
        services, engines = services_for(db, **settings)
        try:
            async with AsyncClient(
                transport=ASGITransport(app_for(services)), base_url="http://test"
            ) as c:
                return await c.post(
                    f"/v1/competitions/{path}/submissions",
                    headers=headers or signed(kernel),
                    files=files or {"Kernel.lean": ("Kernel.lean", kernel)},
                )
        finally:
            for engine in engines:
                await engine.dispose()

    return asyncio.run(run())


def test_upload_persists_file_and_duplicate_is_rejected(upload_db):
    register(upload_db)
    result = post(upload_db)
    assert result.status_code == 201, result.text
    sid = result.json()["submission"]
    with upload_db.sync.connect() as conn:
        from sqlalchemy import text

        content = conn.scalar(
            text(f"SELECT content FROM {SCHEMA}.submission_files WHERE submission_id=:id"),
            {"id": sid},
        )
    assert bytes(content) == KERNEL
    again = post(upload_db)
    assert again.status_code == 409 and again.json()["submission"] == sid
    assert again.json()["reason_code"] == "DUPLICATE_SUBMISSION"
    register(upload_db, T0 + timedelta(days=1), block=200)
    assert post(upload_db).status_code == 409  # duplicates remain duplicates after re-registration


@pytest.mark.parametrize("offset,allowed", [(-1, False), (0, False), (1, True)])
def test_cutoff_boundary(upload_db, offset, allowed):
    register(upload_db, T0 + timedelta(seconds=offset))
    result = post(upload_db, floatlib_registration_cutoff=T0)
    assert result.status_code == (201 if allowed else 402)


@pytest.mark.parametrize("state", ["queued", "verifying", "accepted", "rejected", "error"])
def test_any_prior_status_blocks_another_upload(upload_db, state):
    register(upload_db)
    execute(
        upload_db,
        """
        INSERT INTO submissions (hotkey, slot, digest, state, submitted_at)
        VALUES (:hotkey, 'add', :digest, :state, :at)
    """,
        hotkey=ALICE.ss58_address,
        digest="d" * 64,
        state=state,
        at=T0,
    )
    result = post(upload_db)
    assert result.status_code == 402 and result.json()["reason_code"] == "NO_ENTITLEMENT"
    register(upload_db, T0 + timedelta(seconds=1), block=200)
    assert post(upload_db).status_code == 201


def test_latest_registration_not_unused_history_grants_entry(upload_db):
    register(upload_db, T0 - timedelta(days=1), block=100)
    register(upload_db, T0 + timedelta(seconds=1), block=200)
    assert post(upload_db).status_code == 201
    assert post(upload_db, kernel=KERNEL + b"-- different\n").status_code == 402


@pytest.mark.parametrize("state", ["queued", "verifying", "accepted", "rejected", "error"])
def test_deflate_usage_blocks_every_state(upload_db, state):
    register(upload_db)
    execute(
        upload_db,
        """
        INSERT INTO floatlib_deflate_fixture.submissions VALUES (:hotkey, NULL, :at, :state)
    """,
        hotkey=ALICE.ss58_address,
        at=T0,
        state=state,
    )
    assert post(upload_db, floatlib_check_deflate_usage=True).status_code == 402


def test_deflate_overrides_cutoff_and_ignores_old_entries_and_baselines(upload_db):
    register(upload_db)
    execute(
        upload_db,
        """
        INSERT INTO floatlib_deflate_fixture.submissions VALUES
        (:hotkey, NULL, :old, 'accepted'), (:hotkey, 'seed', :at, 'accepted')
    """,
        hotkey=ALICE.ss58_address,
        old=T0 - timedelta(seconds=1),
        at=T0,
    )
    assert (
        post(
            upload_db,
            floatlib_check_deflate_usage=True,
            floatlib_registration_cutoff=T0 + timedelta(days=1),
        ).status_code
        == 201
    )


@pytest.mark.parametrize("eligible", [True, False])
def test_deflate_connection_failure_falls_back_to_cutoff(upload_db, monkeypatch, caplog, eligible):
    register(upload_db)

    async def unavailable(*args):
        raise OperationalError("connect", {}, ConnectionError("fixture unavailable"))

    monkeypatch.setattr(floatlib_intake, "used_in_deflate", unavailable)
    cutoff = T0 - timedelta(seconds=1) if eligible else T0
    result = post(upload_db, floatlib_check_deflate_usage=True, floatlib_registration_cutoff=cutoff)
    assert result.status_code == (201 if eligible else 402)
    assert "applying registration cutoff" in caplog.text


def test_query_defect_does_not_fall_back(upload_db, monkeypatch):
    register(upload_db)

    async def broken(*args):
        raise ProgrammingError("bad query", {}, RuntimeError("fixture schema defect"))

    monkeypatch.setattr(floatlib_intake, "used_in_deflate", broken)
    with pytest.raises(ProgrammingError):
        post(upload_db, floatlib_check_deflate_usage=True)


def test_two_simultaneous_uploads_cannot_use_same_registration(upload_db):
    register(upload_db)

    async def run():
        services, engines = services_for(upload_db)
        try:
            async with AsyncClient(
                transport=ASGITransport(app_for(services)), base_url="http://test"
            ) as c:

                async def upload(kernel):
                    return await c.post(
                        "/v1/competitions/floatlib/submissions",
                        headers=signed(kernel),
                        files={"Kernel.lean": ("Kernel.lean", kernel)},
                    )

                results = await asyncio.gather(upload(KERNEL), upload(KERNEL + b"-- second"))
                assert sorted(r.status_code for r in results) == [201, 402]
        finally:
            for engine in engines:
                await engine.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("body", [b"", b"\xff", b"x" * 256_001])
def test_invalid_kernel(upload_db, body):
    register(upload_db)
    assert post(upload_db, kernel=body).status_code in (400, 413)


def test_bad_signature_timestamp_shape_and_practice_upload(upload_db):
    register(upload_db)
    headers = signed()
    headers["X-Conjectures-Signature"] = "00" * 64
    assert post(upload_db, headers=headers).status_code == 401
    assert post(upload_db, headers=signed(timestamp=int(time.time()) - 1000)).status_code == 401
    assert (
        post(
            upload_db, files=[("Kernel.lean", ("a", KERNEL)), ("Kernel.lean", ("b", KERNEL))]
        ).status_code
        == 400
    )
    assert post(upload_db, path="floatlib-add").status_code == 404


def test_deflate_signed_upload_is_closed_before_body_parsing(upload_db):
    result = post(upload_db, path="deflate", files={"unexpected": ("file", b"x")})
    assert result.status_code == 403 and result.json()["reason_code"] == "SUBMISSIONS_CLOSED"


def test_deflate_session_upload_is_closed(upload_db):
    async def run():
        services, engines = services_for(upload_db)
        try:
            async with AsyncClient(
                transport=ASGITransport(app_for(services)), base_url="http://test"
            ) as c:
                r = await c.post(
                    "/v1/competitions/deflate/submissions/session",
                    headers={"X-Conjectures-Hotkey": ALICE.ss58_address},
                )
                assert r.status_code == 403 and r.json()["reason_code"] == "SUBMISSIONS_CLOSED"
        finally:
            for engine in engines:
                await engine.dispose()

    asyncio.run(run())


def test_upload_configuration_requires_cutoff_and_closed_deflate():
    args = dict(
        COMPETITIONS_ENABLED="1",
        COMPETITIONS="deflate,floatlib",
        COMPETITION_FLOATLIB_DATABASE_URL="postgresql+psycopg://u:p@db/floatlib",
        FLOATLIB_SUBMISSIONS_ENABLED="true",
    )
    with pytest.raises(SettingsError, match="CUTOFF"):
        build_settings(**args)
    args["FLOATLIB_REGISTRATION_CUTOFF"] = "2026-10-13T00:00:00Z"
    assert build_settings(**args).floatlib_registration_cutoff is not None
    with pytest.raises(SettingsError, match="DEFLATE"):
        build_settings(**args, FLOATLIB_CHECK_DEFLATE_USAGE="true")
    assert build_settings(
        **args, FLOATLIB_CHECK_DEFLATE_USAGE="true", DEFLATE_SUBMISSIONS_ENABLED="false"
    ).floatlib_check_deflate_usage
    args["FLOATLIB_REGISTRATION_CUTOFF"] = "2026-10-13T00:00:00"
    with pytest.raises(SettingsError, match="timezone"):
        build_settings(**args)


def test_floatlib_metadata_matches_upload_switch_and_practice_is_closed(upload_db):
    async def run():
        services, engines = services_for(upload_db)
        try:
            async with AsyncClient(
                transport=ASGITransport(app_for(services)), base_url="http://test"
            ) as c:
                full = await c.get("/v1/competitions/floatlib")
                practice = await c.get("/v1/competitions/floatlib-add")
                assert full.json()["submissions_open"] is True
                assert practice.json()["submissions_open"] is False
                services.settings.floatlib_submissions_enabled = False
                assert (await c.get("/v1/competitions/floatlib")).json()[
                    "submissions_open"
                ] is False
                assert (
                    await c.post("/v1/competitions/floatlib/submissions", headers=signed())
                ).status_code == 403
        finally:
            for engine in engines:
                await engine.dispose()

    asyncio.run(run())


def test_missing_registration_and_chunked_oversized_body_are_refused(upload_db):
    assert post(upload_db).json()["reason_code"] == "NOT_REGISTERED"

    async def run():
        services, engines = services_for(upload_db)
        try:

            async def chunks():
                for _ in range(6):
                    yield b"x" * 100000

            async with AsyncClient(
                transport=ASGITransport(app_for(services)), base_url="http://test"
            ) as c:
                r = await c.post(
                    "/v1/competitions/floatlib/submissions", headers=signed(), content=chunks()
                )
                assert r.status_code == 413
        finally:
            for engine in engines:
                await engine.dispose()

    asyncio.run(run())


def test_closed_competitions_refuse_even_without_upload_headers(upload_db):
    async def run():
        services, engines = services_for(upload_db, floatlib_submissions_enabled=False)
        try:
            async with AsyncClient(
                transport=ASGITransport(app_for(services)), base_url="http://test"
            ) as c:
                for path in (
                    "deflate/submissions",
                    "deflate/submissions/session",
                    "floatlib/submissions",
                ):
                    response = await c.post("/v1/competitions/" + path)
                    assert response.status_code == 403
                    assert response.json()["reason_code"] == "SUBMISSIONS_CLOSED"
        finally:
            for engine in engines:
                await engine.dispose()

    asyncio.run(run())
