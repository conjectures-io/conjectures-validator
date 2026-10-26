"""Real signed uploads against the torchlean schema and its restricted platform role."""

import asyncio
import hashlib
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from bittensor.sp_core import Keypair
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from conftest import competition_dsn, postgres_dsn
from conftest_api import build_settings
from submission_api import competition_sig
from submission_api.app import build_competitions
from submission_api.competitions import Competition, CompetitionRegistry, TORCHLEAN
from submission_api.dependencies import get_services
from submission_api.errors import ApiError, api_error_handler
from submission_api.routers import torchlean
from submission_api.settings import SettingsError

ALICE = Keypair.create_from_uri("//Alice")
BOB = Keypair.create_from_uri("//Bob")
T0 = datetime(2026, 10, 26, tzinfo=UTC)
KERNEL = b"import ContractExact\n-- fixture\n"
COMP = Path(__file__).parent / "fixtures/tensor_schema"


@pytest.fixture(scope="module")
def engines():
    comp, central = competition_dsn(), postgres_dsn()
    if not comp or not central:
        pytest.skip("requires a declared private PostgreSQL fixture")
    owner, api = sa.create_engine(comp), sa.create_engine(central)
    with owner.begin() as c:
        c.exec_driver_sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        for name in [
            "0001_schema.sql",
            "0001_job_protocol.sql",
            "0001_worker_roles.sql",
            "0002_dev_intake.sql",
        ]:
            c.exec_driver_sql((COMP / name).read_text(), execution_options={"no_parameters": True})
        c.exec_driver_sql(
            """DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='platform_api') THEN CREATE ROLE platform_api; END IF; END $$"""
        )
        c.exec_driver_sql("ALTER ROLE platform_api LOGIN PASSWORD 'private_torchlean_api_test'")
        c.exec_driver_sql((COMP / "platform_api.sql").read_text())
    with api.begin() as c:
        c.exec_driver_sql("DROP TABLE IF EXISTS competition_intake_limits")
        c.exec_driver_sql(
            (
                Path(__file__).parents[1] / "deploy/migrate/sql/V054__competition_intake_limits.sql"
            ).read_text()
        )
    url = sa.engine.make_url(comp).set(username="platform_api", password="private_torchlean_api_test")
    yield SimpleNamespace(
        owner=owner, api=api, comp=url.render_as_string(hide_password=False), central=central
    )
    with owner.begin() as c:
        c.exec_driver_sql("DROP OWNED BY platform_api; DROP ROLE platform_api")
    owner.dispose()
    api.dispose()


@pytest.fixture
def database(engines):
    with engines.owner.begin() as c:
        c.exec_driver_sql("TRUNCATE sources,registrations,submissions,coordinator_state CASCADE")
    with engines.api.begin() as c:
        c.exec_driver_sql("TRUNCATE competition_intake_limits")
    return engines


def execute(database, sql, **params):
    with database.owner.begin() as c:
        return c.execute(sa.text(sql), params)


def register(database, hotkey=ALICE.ss58_address, block=100, at=T0, uid=1):
    return execute(
        database,
        "INSERT INTO registrations(uid,ss58_hot,ss58_cold,block,block_date) VALUES (:uid,:hotkey,'cold',:block,:at) RETURNING id",
        uid=uid,
        hotkey=hotkey,
        block=block,
        at=at,
    ).scalar_one()


def headers(kernel=KERNEL, slug="torchlean", timestamp=None, key=ALICE):
    timestamp = int(time.time()) if timestamp is None else timestamp
    msg = competition_sig.submit_message(
        competition=slug,
        digest=hashlib.sha256(kernel).hexdigest(),
        hotkey=key.ss58_address,
        timestamp=timestamp,
    )
    return {
        "X-Conjectures-Hotkey": key.ss58_address,
        "X-Conjectures-Timestamp": str(timestamp),
        "X-Conjectures-Signature": key.sign(msg.encode()).hex(),
    }


def service(database, **settings):
    c = create_async_engine(database.comp)
    a = create_async_engine(database.central)
    config = SimpleNamespace(
        torchlean_submissions_enabled=True,
        torchlean_dev_skip_registration=False,
        torchlean_registration_cutoff=T0 - timedelta(seconds=1),
        submissions_paused=False,
        production=False,
        competition_signature_window_seconds=300,
        competition_ip_rate_per_minute=100,
        competition_rate_per_minute=100,
        trusted_proxy_hops=0,
        trusted_proxy_peers=(),
    )
    for k, v in settings.items():
        setattr(config, k, v)
    services = SimpleNamespace(
        settings=config,
        sessions=async_sessionmaker(a),
        competitions=CompetitionRegistry.of(
            Competition(
                "torchlean", "TorchLean", 8, kind=TORCHLEAN, engine=c, sessions=async_sessionmaker(c)
            )
        ),
    )
    app = FastAPI()
    app.include_router(torchlean.router)
    app.dependency_overrides[get_services] = lambda: services
    app.add_exception_handler(ApiError, api_error_handler)
    return app, (c, a)


async def request(
    database,
    kernel=KERNEL,
    *,
    provided=None,
    files=None,
    path="/submissions",
    method="post",
    **settings,
):
    app, engines = service(database, **settings)
    try:
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
            if method == "get":
                return await client.get("/v1/competitions/torchlean" + path)
            return await client.post(
                "/v1/competitions/torchlean" + path,
                headers=headers(kernel) if provided is None else provided,
                files=files if files is not None else {"Kernel.lean": ("Kernel.lean", kernel)},
            )
    finally:
        for engine in engines:
            await engine.dispose()


def post(database, *args, **kwargs):
    return asyncio.run(request(database, *args, **kwargs))


def test_upload_source_status_and_duplicate(database):
    rid = register(database)
    r = post(database)
    assert r.status_code == 201, r.text
    sid = r.json()["submission"]
    assert r.json()["proof_state"] == "queued"
    row = execute(
        database,
        "SELECT registration_id,intake_mode,source_sha256 FROM submissions WHERE id=:id",
        id=sid,
    ).one()
    assert row.registration_id == rid and row.intake_mode == "subnet"
    assert row.source_sha256 == hashlib.sha256(KERNEL).hexdigest()
    assert bytes(execute(database, "SELECT content FROM sources").scalar_one()) == KERNEL
    status = post(database, path=f"/submissions/{sid}", method="get")
    assert status.status_code == 200, status.text
    assert status.json()["evaluations"] == [] and status.json()["proof_state"] == "queued"
    duplicate = post(database)
    assert duplicate.status_code == 409
    assert duplicate.json()["submission"] == sid
    register(database, block=200, at=T0 + timedelta(days=1))
    assert post(database).status_code == 409


@pytest.mark.parametrize("offset,status", [(-1, 402), (0, 402), (1, 201)])
def test_cutoff(database, offset, status):
    register(database, at=T0 + timedelta(seconds=offset))
    assert post(database, torchlean_registration_cutoff=T0).status_code == status


@pytest.mark.parametrize("state", ["queued", "running", "proved", "rejected"])
def test_every_proof_state_spends_registration(database, state):
    register(database)
    assert post(database).status_code == 201
    execute(
        database,
        "UPDATE submissions SET state=:state,owner=CASE WHEN :state='running' THEN 'test' END, token=CASE WHEN :state='running' THEN 'token' END,lease_until=CASE WHEN :state='running' THEN now()+interval '1 hour' END",
        state=state,
    )
    r = post(database, KERNEL + b"-- another\n")
    assert r.status_code == 402
    assert r.json()["reason_code"] == "NO_ENTITLEMENT"


def test_reregistration_uses_latest_only(database):
    register(database, block=100, at=T0 - timedelta(days=1))
    latest = register(database, block=200, at=T0)
    r = post(database)
    assert r.status_code == 201, r.text
    assert execute(database, "SELECT registration_id FROM submissions").scalar_one() == latest
    assert post(database, KERNEL + b"-- changed").status_code == 402


def test_new_registration_after_spent_one_is_allowed(database):
    register(database)
    assert post(database).status_code == 201
    latest = register(database, block=200, at=T0 + timedelta(days=1))
    assert post(database, KERNEL + b"-- changed").status_code == 201
    assert (
        execute(
            database, "SELECT count(*) FROM submissions WHERE registration_id=:id", id=latest
        ).scalar_one()
        == 1
    )


def test_removed_hotkey_is_not_currently_registered(database):
    register(database)
    register(database, hotkey=BOB.ss58_address, block=200)
    r = post(database)
    assert r.status_code == 402 and r.json()["reason_code"] == "NOT_REGISTERED"


@pytest.mark.parametrize("slug", ["floatlib", "deflate", "tensor"])
def test_cross_competition_signature_rejected(database, slug):
    register(database)
    r = post(database, provided=headers(slug=slug))
    assert r.status_code == 401
    assert execute(database, "SELECT count(*) FROM submissions").scalar_one() == 0


@pytest.mark.parametrize("change", ["stale", "signature", "bytes"])
def test_bad_auth_does_not_store_work(database, change):
    register(database)
    h = headers(timestamp=1) if change == "stale" else headers()
    if change == "signature":
        h["X-Conjectures-Signature"] = "00" * 64
    r = post(database, KERNEL + b"changed" if change == "bytes" else KERNEL, provided=h)
    assert r.status_code == 401
    assert execute(database, "SELECT count(*) FROM sources").scalar_one() == 0


@pytest.mark.parametrize("kernel,status", [(b"", 400), (b"\xff", 400), (b"x" * 256001, 413)])
def test_invalid_kernel(database, kernel, status):
    register(database)
    assert post(database, kernel).status_code == status


def test_wrong_parts(database):
    register(database)
    r = post(database, files={"wrong": ("wrong", KERNEL)})
    assert r.status_code == 400


@pytest.mark.parametrize(
    "settings,status",
    [({"torchlean_submissions_enabled": False}, 403), ({"submissions_paused": True}, 503)],
)
def test_closed_before_required_headers(database, settings, status):
    assert post(database, provided={}, **settings).status_code == status


def test_dev_bypass_is_signed_tagged_and_does_not_fake_registration(database):
    r = post(database, torchlean_dev_skip_registration=True, torchlean_registration_cutoff=None)
    assert r.status_code == 201, r.text
    row = execute(database, "SELECT registration_id,intake_mode FROM submissions").one()
    assert row.registration_id is None and row.intake_mode == "dev"
    assert execute(database, "SELECT count(*) FROM registrations").scalar_one() == 0
    assert post(database, KERNEL + b"-- new", torchlean_dev_skip_registration=True).status_code == 201
    assert post(database, torchlean_dev_skip_registration=True).status_code == 409
    assert (
        post(
            database,
            KERNEL + b"-- bad",
            provided=headers(slug="floatlib"),
            torchlean_dev_skip_registration=True,
        ).status_code
        == 401
    )


def test_runtime_backstop_for_dev_in_production(database):
    r = post(database, torchlean_dev_skip_registration=True, production=True)
    assert r.status_code == 503
    assert execute(database, "SELECT count(*) FROM submissions").scalar_one() == 0


def test_registration_required_by_default(database):
    r = post(database)
    assert r.status_code == 402 and r.json()["reason_code"] == "NOT_REGISTERED"


@pytest.mark.parametrize("kind", ["ip", "hotkey"])
def test_rate_limits_use_central_db(database, kind):
    register(database)
    setting = {"competition_" + ("ip_" if kind == "ip" else "") + "rate_per_minute": 1}
    assert post(database, **setting).status_code == 201
    r = post(database, KERNEL + b"-- second", **setting)
    assert r.status_code == 429, r.text
    assert execute(database, "SELECT to_regclass('rate_limit_windows')").scalar_one() is None


def test_parallel_uploads_charge_registration_once(database):
    register(database)

    async def run():
        return await asyncio.gather(request(database), request(database, KERNEL + b"-- racing"))

    responses = asyncio.run(run())
    assert sorted(r.status_code for r in responses) == [201, 402]
    assert execute(database, "SELECT count(*) FROM submissions").scalar_one() == 1
    assert execute(database, "SELECT count(*) FROM sources").scalar_one() == 1


def test_missing_schema_fails_closed(database):
    execute(database, "ALTER TABLE submissions RENAME COLUMN intake_mode TO hidden_intake_mode")
    try:
        assert post(database, torchlean_dev_skip_registration=True).status_code == 503
    finally:
        execute(database, "ALTER TABLE submissions RENAME COLUMN hidden_intake_mode TO intake_mode")


def test_detail_and_not_found(database):
    r = post(database, path="", method="get")
    assert r.status_code == 200, r.text
    assert r.json()["slug"] == "torchlean"
    assert post(database, path="/submissions/999999", method="get").status_code == 404


def test_settings_and_registry_support_torchlean_only():
    settings = build_settings(
        COMPETITIONS_ENABLED="1",
        COMPETITIONS="torchlean",
        COMPETITION_TORCHLEAN_DATABASE_URL="postgresql+psycopg://u:p@db:5432/torchlean",
        TORCHLEAN_SUBMISSIONS_ENABLED="1",
        TORCHLEAN_REGISTRATION_CUTOFF="2026-10-26T00:00:00Z",
    )
    registry = build_competitions(settings)
    assert registry.get("torchlean").kind == TORCHLEAN
    for engine in registry.engines():
        asyncio.run(engine.dispose())


@pytest.mark.parametrize(
    "options,pattern",
    [
        ({"TORCHLEAN_SUBMISSIONS_ENABLED": "1"}, "requires TorchLean"),
        ({"TORCHLEAN_REGISTRATION_CUTOFF": "2026-10-26"}, "timezone"),
        (
            {
                "TORCHLEAN_SUBMISSIONS_ENABLED": "1",
                "COMPETITIONS_ENABLED": "1",
                "COMPETITIONS": "torchlean",
                "COMPETITION_TORCHLEAN_DATABASE_URL": "postgresql+psycopg://u:p@db/torchlean",
            },
            "CUTOFF",
        ),
    ],
)
def test_invalid_settings(options, pattern):
    with pytest.raises(SettingsError, match=pattern):
        build_settings(**options)


def test_production_configuration_refuses_dev_registration_bypass():
    with pytest.raises(SettingsError, match="TORCHLEAN_DEV_SKIP_REGISTRATION"):
        build_settings(APP_MODE="PROD", TORCHLEAN_DEV_SKIP_REGISTRATION="1")


def test_stored_source_hash_is_enforced_by_database(database):
    with pytest.raises(sa.exc.DBAPIError):
        execute(
            database,
            "INSERT INTO sources(sha256,content) VALUES (:sha,:content)",
            sha="0" * 64,
            content=KERNEL,
        )


def test_platform_role_cannot_forge_a_proof_or_promotion(database):
    with database.owner.connect() as c:
        for table in ["promotions", "benchmark_jobs"]:
            assert not c.scalar(
                sa.text("SELECT has_table_privilege('platform_api',:table,'INSERT')"),
                {"table": table},
            )
        assert not c.scalar(
            sa.text("SELECT has_column_privilege('platform_api','submissions','state','INSERT')")
        )


def test_dev_constraint_prevents_untagged_registrationless_intake(database):
    from sqlalchemy.exc import IntegrityError

    execute(
        database,
        "INSERT INTO sources(sha256,content) VALUES (:sha,:content)",
        sha=hashlib.sha256(KERNEL).hexdigest(),
        content=KERNEL,
    )
    with pytest.raises(IntegrityError):
        execute(
            database,
            "INSERT INTO submissions(hotkey,source_sha256) VALUES (:hotkey,:sha)",
            hotkey=ALICE.ss58_address,
            sha=hashlib.sha256(KERNEL).hexdigest(),
        )


def test_full_application_dispatches_torchlean_before_legacy_routes(database):
    from submission_api.app import create_app

    register(database)

    async def run():
        stub, engines = service(database)
        services = stub.dependency_overrides[get_services]()
        app = create_app(build_settings(), services=services)
        app.dependency_overrides[get_services] = lambda: services
        try:
            async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
                response = await client.post(
                    "/v1/competitions/torchlean/submissions",
                    headers=headers(),
                    files={"Kernel.lean": ("Kernel.lean", KERNEL)},
                )
            assert response.status_code == 201, response.text
            post = app.openapi()["paths"]["/v1/competitions/torchlean/submissions"]["post"]
            assert "201" in post["responses"]
            assert "multipart/form-data" in post["requestBody"]["content"]
        finally:
            for engine in engines:
                await engine.dispose()

    asyncio.run(run())
