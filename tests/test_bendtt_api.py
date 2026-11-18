"""BendTT protocol and real PostgreSQL boundary tests, without Lean compilation."""

import asyncio
import hashlib
import os
import secrets
import time
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import psycopg
import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from psycopg.rows import dict_row
from psycopg.sql import SQL, Identifier
from sqlalchemy import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from submission_api import schemas_bendtt as s
from submission_api.competitions import Competition, CompetitionRegistry
from submission_api.errors import ApiError, api_error_handler, validation_error_handler
from submission_api.routers import bendtt
from submission_api.settings import Settings, SettingsError

H = "a" * 64
HOTKEY = "5" + "A" * 47
CONTENT = "-- Signed intake fixture; not proof evidence.\n"


def settings(**overrides):
    from conftest_api import build_settings

    return build_settings(
        COMPETITIONS_ENABLED="1",
        COMPETITIONS="bendtt",
        COMPETITION_BENDTT_DATABASE_URL="postgresql+psycopg://u:p@localhost/bendtt",
        BENDTT_SUBMISSIONS_ENABLED="true",
        BENDTT_DEV_SKIP_REGISTRATION="true",
        **overrides,
    )


def test_settings_and_registry():
    cfg = settings()
    assert cfg.bendtt_max_source_bytes == 4194304
    assert Competition("bendtt", "BendTT", 8.0, kind="bendtt").kind == "bendtt"
    with pytest.raises(ValueError):
        Competition("wrong", "BendTT", 8.0, kind="bendtt")
    from test_api_auth import production_env

    with pytest.raises(SettingsError, match="BENDTT_DEV_SKIP_REGISTRATION"):
        Settings.from_env(production_env(BENDTT_DEV_SKIP_REGISTRATION="true"))


def test_signature_binds_every_field():
    from bittensor.sp_core import Keypair
    from submission_api.login import verify_signature
    from submission_api.errors import Unauthorized

    key = Keypair.create_from_uri("//Alice")
    request = uuid4()
    message = bendtt.submit_message(H, key.ss58_address, 123, request)
    signature = key.sign(message)
    verify_signature(address=key.ss58_address, message=message, signature=signature)
    for altered in (
        message.replace("bendtt", "torchlean"),
        message.replace(H, "b" * 64),
        message.replace("123", "124"),
        message.replace(str(request), str(uuid4())),
    ):
        with pytest.raises(Unauthorized):
            verify_signature(address=key.ss58_address, message=altered, signature=signature)


def test_decimal_contract_rejects_precision_loss():
    assert s.decimal(2**63 - 1) == str(2**63 - 1)
    for value in (1.0, True, -1, 2**63, "1e9"):
        with pytest.raises(ValueError):
            s.decimal(value)


def test_all_planned_routes_have_response_schemas():
    app = FastAPI()
    app.include_router(bendtt.router)
    spec = app.openapi()
    assert len(spec["paths"]) == 21
    assert (
        "Submission.lean"
        in spec["paths"][bendtt.BASE + "/submissions"]["post"]["requestBody"]["content"][
            "multipart/form-data"
        ]["schema"]["properties"]
    )
    assert "Idempotency-Key" in [
        p["name"] for p in spec["paths"][bendtt.BASE + "/submissions"]["post"]["parameters"]
    ]


@dataclass
class Kit:
    dsn: str
    role: str
    password: str
    profile: dict
    competition: int

    def sql(self, query, params=()):
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            result = conn.execute(query, params, prepare=False)
            return result.fetchall() if result.description else []

    async def client(self, **overrides):
        cfg = replace(settings(), **overrides)
        url = make_url(self.dsn).set(drivername="postgresql+psycopg")
        engine = create_async_engine(url.set(username=self.role, password=self.password))
        central = create_async_engine(url)
        services = SimpleNamespace(
            settings=cfg,
            sessions=async_sessionmaker(central),
            competition_sessions=async_sessionmaker(engine),
            competitions=CompetitionRegistry.of(
                Competition(
                    "bendtt",
                    "BendTT",
                    8.0,
                    kind="bendtt",
                    engine=engine,
                    sessions=async_sessionmaker(engine),
                )
            ),
        )
        app = FastAPI()
        app.state.services = services
        app.add_exception_handler(ApiError, api_error_handler)
        app.add_exception_handler(RequestValidationError, validation_error_handler)
        app.include_router(bendtt.router)
        from submission_api.routers import competition_reads

        app.include_router(competition_reads.router)
        return (
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test"),
            engine,
            central,
        )


@pytest.fixture
def kit():
    admin = os.environ.get("BENDTT_TEST_ADMIN_DSN")
    if not admin:
        pytest.skip("BENDTT_TEST_ADMIN_DSN required for disposable PostgreSQL tests")
    url = make_url(admin)
    if url.host not in ("localhost", "127.0.0.1", "::1"):
        pytest.fail("BendTT test admin must be explicitly local")
    name = "bendtt_api_test_" + uuid4().hex
    role = "api_test_" + uuid4().hex[:20]
    password = secrets.token_urlsafe(24)
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(SQL("CREATE DATABASE {}").format(Identifier(name)))
        conn.execute(
            SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                Identifier(role), SQL("'" + password + "'")
            )
        )
    dsn = url.set(database=name).render_as_string(hide_password=False)
    try:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            for filename in ("0001_initial.sql", "0001_chain.sql", "0003_public_api.sql"):
                conn.execute(
                    (Path(__file__).parent / "fixtures/bendtt_schema" / filename).read_text(),
                    prepare=False,
                )
            cid = conn.execute(
                "INSERT INTO competitions(slug,name) VALUES('bendtt','BendTT') RETURNING id"
            ).fetchone()["id"]
            snapshot = conn.execute("SELECT put_snapshot('test',1,'{}'::jsonb)").fetchone()[
                "put_snapshot"
            ]
            policy = dict(
                profile_hash=H,
                coordinator_snapshot=snapshot,
                git_commit="c" * 40,
                git_dirty=False,
                proof_identity="b" * 64,
                execution_identity="c" * 64,
                scoring_identity="d" * 64,
                manifest_sha256="e" * 64,
                baseline_source_sha256="f" * 64,
                source_policy="former_champions_only",
                scoring=dict(
                    minimum_time_reduction=0.03,
                    regression_guard_enforced=False,
                    maximum_regression=0.05,
                ),
                tiers=[dict(id="fast", cases=1, process_units=1, rounds=3, batch_size=1)],
                targets=[
                    dict(
                        id="amd",
                        weight=1.0,
                        machine_identity=None,
                        limits=dict(
                            build_seconds_per_role=1200,
                            run_seconds=180,
                            measurement_seconds=3600,
                            attempt_seconds=7260,
                        ),
                    )
                ],
            )
            from psycopg.types.json import Jsonb

            conn.execute(
                "INSERT INTO api_profiles VALUES(%s,%s,%s,%s)", (H, cid, snapshot, Jsonb(policy))
            )
            conn.execute("INSERT INTO api_runtime VALUES(%s,%s,clock_timestamp())", (cid, H))
            conn.execute(
                "INSERT INTO api_intake_settings VALUES(%s,true,'finney',66,0,120,4194304,true)",
                (cid,),
            )
            conn.execute(
                "CREATE TABLE competition_intake_limits(subject text PRIMARY KEY,window_start timestamptz,hits integer)"
            )
            conn.execute(
                SQL("GRANT CONNECT ON DATABASE {} TO {}").format(Identifier(name), Identifier(role))
            )
            conn.execute(SQL("GRANT USAGE ON SCHEMA public TO {}").format(Identifier(role)))
            views = [
                "api_runtime",
                "api_profiles",
                "api_corpus",
                "api_target_summaries",
                "api_reward_policy",
                "api_intake_settings",
                "api_promotions",
                "api_submissions",
                "api_admissions",
                "api_phases",
                "api_evaluations",
                "api_requests",
                "api_attempts",
                "api_builds",
                "api_measurements",
                "api_members",
                "api_chain",
                "api_weight_sets",
                "api_allocations",
            ]
            for view in views:
                conn.execute(
                    SQL("GRANT SELECT ON {} TO {}").format(Identifier(view), Identifier(role))
                )
            conn.execute(
                SQL("GRANT SELECT(id,slug,name) ON competitions TO {}").format(Identifier(role))
            )
            for sig in (
                "api_enqueue(text,text,text,uuid,boolean)",
                "api_eligibility(text,text)",
                "api_source(bigint,bigint)",
            ):
                conn.execute(
                    SQL("GRANT EXECUTE ON FUNCTION {} TO {}").format(SQL(sig), Identifier(role))
                )
        yield Kit(dsn, role, password, policy, cid)
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(SQL("DROP DATABASE {} WITH (FORCE)").format(Identifier(name)))
            conn.execute(SQL("DROP ROLE {}").format(Identifier(role)))


async def request(kit, method, path, **kwargs):
    overrides = kwargs.pop("settings", {})
    client, engine, central = await kit.client(**overrides)
    try:
        async with client:
            return await client.request(method, bendtt.BASE + path, **kwargs)
    finally:
        await engine.dispose()
        await central.dispose()


def signed(content=CONTENT, request_id=None):
    from bittensor.sp_core import Keypair

    key = Keypair.create_from_uri("//Alice")
    now = int(time.time())
    rid = request_id or uuid4()
    message = bendtt.submit_message(
        hashlib.sha256(content.encode()).hexdigest(), key.ss58_address, now, rid
    )
    return dict(
        headers={
            "X-Conjectures-Hotkey": key.ss58_address,
            "X-Conjectures-Timestamp": str(now),
            "X-Conjectures-Signature": key.sign(message).hex(),
            "Idempotency-Key": str(rid),
        },
        files={"Submission.lean": ("Submission.lean", content.encode(), "text/plain")},
    )


def test_upload_retry_and_private_source(kit):
    async def scenario():
        payload = signed()
        first = await request(kit, "POST", "/submissions", **payload)
        assert first.status_code == 201, first.text
        sid = first.json()["submission"]
        assert isinstance(sid, str)
        again = await request(kit, "POST", "/submissions", **payload)
        assert again.status_code == 200 and again.json()["replayed"] is True
        assert again.json()["submission"] == sid
        bad = signed(CONTENT + "-- changed", UUID(payload["headers"]["Idempotency-Key"]))
        conflict = await request(kit, "POST", "/submissions", **bad)
        assert conflict.status_code == 409, conflict.text
        source = await request(kit, "GET", f"/submissions/{sid}/source")
        assert source.status_code == 403
        proof = await request(kit, "GET", f"/submissions/{sid}/proof")
        assert proof.status_code == 200 and proof.json()["state"] == "queued"
        stored = kit.sql("SELECT intake,hotkey,registration_id FROM submissions")
        assert stored[0]["intake"] == "dev" and stored[0]["registration_id"] is None

    from uuid import UUID

    asyncio.run(scenario())


def test_pagination_scope_and_filters(kit):
    async def scenario():
        for _ in range(3):
            result = await request(kit, "POST", "/submissions", **signed())
            assert result.status_code == 201, result.text
        first = await request(kit, "GET", "/submissions?limit=1")
        assert first.status_code == 200, first.text
        cursor = first.json()["next_cursor"]
        assert cursor
        for suffix in (
            "/submissions?limit=1&state=queued&cursor=" + cursor,
            "/champions?cursor=" + cursor,
            "/submissions?cursor=" + cursor + "x",
            "/submissions?surprise=1",
            "/submissions?limit=1&limit=2",
        ):
            result = await request(kit, "GET", suffix)
            assert result.status_code == 400, (suffix, result.text)
        added = await request(kit, "POST", "/submissions", **signed())
        assert added.status_code == 201
        second = await request(kit, "GET", "/submissions?limit=1&cursor=" + cursor)
        assert (
            second.status_code == 200
            and second.json()["items"][0]["id"] != first.json()["items"][0]["id"]
        )
        filtered = await request(kit, "GET", "/submissions?state=queued&intake_mode=dev")
        assert filtered.status_code == 200 and len(filtered.json()["items"]) == 4, filtered.text

    asyncio.run(scenario())


def test_overview_policy_empty_collections_and_secrecy(kit):
    async def scenario():
        client, engine, central = await kit.client()
        try:
            async with client:
                catalog = await client.get("/v1/competitions")
                assert catalog.status_code == 200, catalog.text
                assert catalog.json()["items"][0]["slug"] == "bendtt"
        finally:
            await engine.dispose()
            await central.dispose()

        for path in (
            "",
            "/policy",
            "/champions",
            "/submissions",
            "/weight-sets",
            "/rewards",
            "/corpus",
            "/health",
            "/eligibility?hotkey=" + HOTKEY,
        ):
            response = await request(kit, "GET", path)
            assert response.status_code == 200, (path, response.text)
            for forbidden in (
                "lease_token",
                "evidence_uri",
                "/opt/",
                "DB_PASSWORD",
                "coordinator_token",
            ):
                assert forbidden not in response.text
        missing = await request(kit, "GET", "/attempts/99999")
        assert missing.status_code == 404
        unknown = await request(kit, "GET", "/submissions?state=bogus")
        assert unknown.status_code == 400

    asyncio.run(scenario())


def test_registered_entitlement_survives_coldkey_change_and_stale_chain(kit):
    kit.sql("UPDATE api_intake_settings SET dev_mode=false")
    snap = kit.sql("SELECT put_snapshot('test',1,'{}'::jsonb) AS hash")[0]["hash"]
    from bittensor.sp_core import Keypair

    hotkey = Keypair.create_from_uri("//Alice").ss58_address
    kit.sql(
        "INSERT INTO chain_state VALUES('finney',66,100,'block',clock_timestamp(),%s,clock_timestamp())",
        (snap,),
    )
    kit.sql(
        "INSERT INTO registrations(network,netuid,uid,hotkey,coldkey,registered_block,observed_block) VALUES('finney',66,42,%s,'cold-one',99,100)",
        (hotkey,),
    )

    async def scenario():
        cfg = dict(bendtt_dev_skip_registration=False)
        first = await request(kit, "POST", "/submissions", settings=cfg, **signed())
        assert first.status_code == 201, first.text
        kit.sql("UPDATE registrations SET active=false")
        kit.sql(
            "INSERT INTO registrations(network,netuid,uid,hotkey,coldkey,registered_block,observed_block) VALUES('finney',66,42,%s,'cold-two',99,100)",
            (hotkey,),
        )
        repeat = await request(kit, "POST", "/submissions", settings=cfg, **signed())
        assert repeat.status_code == 402 and repeat.json()["reason_code"] == "NO_ENTITLEMENT", (
            repeat.text
        )
        kit.sql("UPDATE chain_state SET observed_at=clock_timestamp()-interval '1 hour'")
        stale = await request(kit, "POST", "/submissions", settings=cfg, **signed())
        assert stale.status_code == 503 and stale.json()["reason_code"] == "CHAIN_STALE"

    asyncio.run(scenario())


def test_concurrent_identical_requests_have_one_receipt(kit):
    async def scenario():
        payload = signed()
        replies = await asyncio.gather(
            *(request(kit, "POST", "/submissions", **payload) for _ in range(4))
        )
        assert sorted(r.status_code for r in replies) == [200, 200, 200, 201], [
            r.text for r in replies
        ]
        assert len({r.json()["submission"] for r in replies}) == 1

    asyncio.run(scenario())
    assert kit.sql("SELECT count(*) AS count FROM submissions")[0]["count"] == 1


def test_api_database_role_cannot_mutate_competition(kit):
    url = make_url(kit.dsn).set(username=kit.role, password=kit.password)
    for sql in (
        "SELECT content FROM sources",
        "SELECT coordinator_token FROM competitions",
        "UPDATE promotions SET promoted_at=clock_timestamp()",
        "SELECT worker_claim('x','x','" + H + "','" + H + "',30)",
        "SELECT put_snapshot('bad',1,'{}'::jsonb)",
    ):
        with psycopg.connect(url.render_as_string(hide_password=False)) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(sql)


def populated(kit, suffix="", previous=None):
    """Relational protocol fixture only: inserts no claimed real proof/native evidence."""
    from psycopg.types.json import Jsonb

    source = CONTENT + suffix
    digest = hashlib.sha256(source.encode()).hexdigest()
    kit.sql(
        "INSERT INTO sources(sha256,content) VALUES(%s,%s) ON CONFLICT DO NOTHING", (digest, source)
    )
    snap = kit.sql("SELECT put_snapshot('test',1,%s) AS hash", (Jsonb(dict(profile=H)),))[0]["hash"]
    sid = kit.sql(
        "INSERT INTO submissions(competition_id,source_sha256,submitter,hotkey) VALUES(%s,%s,'fixture',%s) RETURNING id",
        (kit.competition, digest, HOTKEY),
    )[0]["id"]
    aid = kit.sql(
        "INSERT INTO admissions(source_sha256,proof_identity,snapshot_sha256,state,started_at,finished_at,elapsed_seconds) VALUES(%s,%s,%s,'passed',clock_timestamp(),clock_timestamp(),1.0) ON CONFLICT(source_sha256,proof_identity) DO UPDATE SET state='passed' RETURNING id",
        (digest, "b" * 64, snap),
    )[0]["id"]
    eid = kit.sql(
        "INSERT INTO evaluations(competition_id,submission_id,admission_id,coordinator_snapshot_sha256,baseline_source_sha256,incumbent_promotion_id,expected_targets) VALUES(%s,%s,%s,%s,%s,%s,ARRAY['amd']) RETURNING id",
        (kit.competition, sid, aid, snap, digest, previous),
    )[0]["id"]
    rid = kit.sql(
        "INSERT INTO benchmark_requests(evaluation_id,target,snapshot_sha256,schema_version,specification,state) VALUES(%s,'amd',%s,1,%s,'running') RETURNING id",
        (eid, snap, Jsonb(dict(rounds=3))),
    )[0]["id"]
    attempt = kit.sql(
        "INSERT INTO benchmark_attempts(request_id,attempt_number,worker_key,snapshot_sha256,lease_token,lease_expires_at) VALUES(%s,1,%s,%s,%s,clock_timestamp()+interval '1 hour') RETURNING id",
        (rid, uuid4().hex, snap, uuid4()),
    )[0]["id"]
    for role in ("baseline", "incumbent", "challenger"):
        kit.sql(
            "INSERT INTO benchmark_builds(attempt_id,role,source_sha256,status,started_at,finished_at,elapsed_seconds,returncode,artifact_sha256,cache_hit) VALUES(%s,%s,%s,'completed',clock_timestamp(),clock_timestamp(),0.1,0,%s,true)",
            (attempt, role, digest, digest),
        )
    return sid, eid, rid, attempt, digest


def finish_promotion(kit, ids):
    sid, eid, rid, attempt, _ = ids
    kit.sql(
        "UPDATE benchmark_attempts SET state='completed',finished_at=clock_timestamp() WHERE id=%s",
        (attempt,),
    )
    kit.sql(
        "UPDATE benchmark_requests SET state='completed',completed_attempt_id=%s,finished_at=clock_timestamp(),speedup_estimate=1.06,speedup_lower=1.04,speedup_upper=1.08 WHERE id=%s",
        (attempt, rid),
    )
    kit.sql(
        "UPDATE evaluations SET state='completed',outcome='promoted',completed_at=clock_timestamp(),speedup_estimate=1.06,speedup_lower=1.04,speedup_upper=1.08,guard_passed=true WHERE id=%s",
        (eid,),
    )
    return kit.sql(
        "INSERT INTO promotions(competition_id,evaluation_id) VALUES(%s,%s) RETURNING id",
        (kit.competition, eid),
    )[0]["id"]


def test_partial_timeout_evidence_reports_and_scoping(kit):
    sid, eid, rid, attempt, digest = populated(kit)
    for role in ("incumbent", "challenger"):
        kit.sql(
            "INSERT INTO measurements(attempt_id,round_index,case_id,group_name,input_sha256,role,status,started_at,finished_at,wall_ns,cpu_ns,peak_rss_kib,check_and_render_ns,parse_ns,sandbox_seconds,returncode,outcome) VALUES(%s,0,'case','math',%s,%s,'completed',clock_timestamp(),clock_timestamp(),1000000000,900000000,500,800000000,10000000,1,0,'accepted')",
            (attempt, digest, role),
        )
    mid = kit.sql(
        "INSERT INTO measurements(attempt_id,round_index,case_id,group_name,input_sha256,role,status,started_at,finished_at,sandbox_seconds,returncode,evidence_uri,diagnostic) VALUES(%s,1,'batch','math',%s,'challenger','timeout',clock_timestamp(),clock_timestamp(),180,124,'/secret/worker.env','private source') RETURNING id",
        (attempt, digest),
    )[0]["id"]
    kit.sql(
        "INSERT INTO measurement_members VALUES(%s,0,'first-book',%s,1,2,3,'accepted',null)",
        (mid, digest),
    )
    kit.sql(
        "UPDATE benchmark_attempts SET state='failed',failure_kind='candidate',reason='secret raw reason',finished_at=clock_timestamp() WHERE id=%s",
        (attempt,),
    )
    kit.sql(
        "UPDATE benchmark_requests SET state='failed',finished_at=clock_timestamp() WHERE id=%s",
        (rid,),
    )
    kit.sql(
        "UPDATE evaluations SET state='completed',outcome='benchmark_failed',completed_at=clock_timestamp() WHERE id=%s",
        (eid,),
    )
    kit.sql("INSERT INTO competitions(slug,name) VALUES('other','Other') RETURNING id")
    kit.sql("UPDATE evaluations SET competition_id=competition_id WHERE id=%s", (eid,))

    async def scenario():
        for path in (
            f"/submissions/{sid}",
            f"/submissions/{sid}/proof",
            f"/submissions/{sid}/evaluations",
            f"/submissions/{sid}/report",
            f"/evaluations/{eid}",
            f"/evaluations/{eid}/report",
            f"/evaluations/{eid}/attempts",
            f"/attempts/{attempt}",
            f"/attempts/{attempt}/measurements",
            f"/measurements/{mid}/members",
        ):
            result = await request(kit, "GET", path)
            assert result.status_code == 200, (path, result.text)
            assert "secret" not in result.text and "private source" not in result.text
        partial = await request(kit, "GET", f"/measurements/{mid}/members")
        assert partial.json()["items"][0]["case_id"] == "first-book"
        report = await request(kit, "GET", f"/evaluations/{eid}/report")
        assert report.json()["decision"]["outcome"] == "benchmark_failed"
        assert report.json()["decision"]["speedup"] is None
        measured = await request(kit, "GET", f"/attempts/{attempt}/measurements?status=timeout")
        assert measured.json()["items"][0]["wall_ns"] is None
        conflict = await request(
            kit, "GET", f"/attempts/{attempt}/measurements?cursor=bad&after_id=0"
        )
        assert conflict.status_code == 400
        assert (await request(kit, "GET", f"/evaluations/{eid}/attempts?state=failed")).json()[
            "items"
        ]

    asyncio.run(scenario())


def test_champion_source_becomes_public_only_after_replacement(kit):
    first = populated(kit)
    old = finish_promotion(kit, first)

    async def scenario():
        current = await request(kit, "GET", f"/submissions/{first[0]}/source")
        assert current.status_code == 403
        second = populated(kit, "-- second", old)
        finish_promotion(kit, second)
        source = await request(kit, "GET", f"/submissions/{first[0]}/source")
        assert source.status_code == 200 and source.text == CONTENT
        assert source.headers["cache-control"] == "no-store"
        champions = await request(kit, "GET", "/champions?limit=1")
        assert champions.status_code == 200, champions.text
        assert champions.json()["items"][0]["current"] is True
        assert champions.json()["next_cursor"]

    asyncio.run(scenario())


def test_rewards_history_and_allocations(kit):
    from psycopg.types.json import Jsonb

    snap = kit.sql("SELECT put_snapshot('test',1,'{}'::jsonb) AS hash")[0]["hash"]
    kit.sql(
        "INSERT INTO api_reward_policy VALUES(%s,%s,%s,clock_timestamp())",
        (
            kit.competition,
            snap,
            Jsonb(
                dict(
                    rank_shares=[1.0],
                    competition_share=0.8,
                    reign_full_days=None,
                    reign_zero_days=None,
                )
            ),
        ),
    )
    wid = kit.sql(
        "INSERT INTO weight_sets(competition_id,network,netuid,validator_hotkey,mode,block,block_hash,epoch_block,snapshot_sha256,status,reason,finished_at) VALUES(%s,'fixture',66,'validator','dry_run',100,'block',90,%s,'dry_run','secret SDK message',clock_timestamp()) RETURNING id",
        (kit.competition, snap),
    )[0]["id"]
    kit.sql("INSERT INTO weight_allocations VALUES(%s,42,%s,1.0,'miner')", (wid, HOTKEY))

    async def scenario():
        for path in ("/rewards", "/weight-sets?mode=dry_run", f"/weight-sets/{wid}/allocations"):
            result = await request(kit, "GET", path)
            assert result.status_code == 200, (path, result.text)
            assert "secret SDK" not in result.text
        response = await request(kit, "GET", "/rewards")
        assert response.json()["measured_emission"] is None
        assert response.json()["policy"]["rank_shares"] == [1.0]

    asyncio.run(scenario())


def test_upload_validation_and_signature_errors(kit):
    async def scenario():
        payload = signed()
        payload["headers"]["X-Conjectures-Signature"] = "00" * 64
        invalid = await request(kit, "POST", "/submissions", **payload)
        assert invalid.status_code == 401
        for files, code in (
            ({"other.lean": ("anything.lean", b"x")}, 400),
            ({"Submission.lean": ("anything.lean", b"")}, 422),
            ({"Submission.lean": ("anything.lean", b"\xff")}, 422),
            ({"Submission.lean": ("anything.lean", b"x\0")}, 422),
            ({"Submission.lean": ("anything.lean", b"x" * (4194304 + 1))}, 413),
        ):
            payload = signed()
            payload["files"] = files
            response = await request(kit, "POST", "/submissions", **payload)
            assert response.status_code == code, response.text
        payload = signed()
        payload["files"] = {"Submission.lean": ("arbitrary-local-name.lean", CONTENT.encode())}
        assert (await request(kit, "POST", "/submissions", **payload)).status_code == 201
        closed = await request(
            kit, "POST", "/submissions", settings=dict(bendtt_submissions_enabled=False), **signed()
        )
        assert closed.status_code == 403

    asyncio.run(scenario())


@pytest.mark.integration
def test_signed_upload_to_real_coordinator_and_two_native_workers(kit, tmp_path):
    """Opt-in complete local flow. No remote deployment or real registration/payment."""
    import json
    import signal
    import subprocess

    root_raw = os.environ.get("BENDTT_NATIVE_ROOT")
    if not root_raw:
        pytest.skip("BENDTT_NATIVE_ROOT required for native end-to-end test")
    root = Path(root_raw)
    if not (root / ".lake/build/bin/observe").exists():
        pytest.fail("Build BendTT trusted tools first")
    cpus = sorted(os.sched_getaffinity(0))
    if len(cpus) < 2:
        pytest.skip("two available CPUs required")
    url = make_url(kit.dsn)
    env = dict(
        os.environ,
        DB_NAME=url.database,
        DB_HOST=url.host,
        DB_PORT=str(url.port),
        DB_USER=url.username,
        DB_PASSWORD=url.password,
        DB_ADMIN_PASSWORD=url.password,
        DB_SSLMODE="disable",
        BENDTT_COMPETITION="bendtt",
        BENDTT_BASELINE="submissions/shipped-slow-double.lean",
        BENDTT_SUITE="smoke",
        BENDTT_TIERS="[]",
        BENDTT_TIERS_FILE="",
        BENDTT_BATCH_SIZE="1",
        BENDTT_TARGETS=json.dumps(
            [dict(name="local", rounds=3, weight=1), dict(name="peer", rounds=3, weight=1)]
        ),
        BENDTT_POLL_SECONDS="0.1",
        BENDTT_LEASE_SECONDS="30",
        BENDTT_MEMORY_MB="0",
        BENDTT_CGROUP_ROOT="",
        BENDTT_CPU=str(cpus[0]),
        BENDTT_HOST_LOCK=str(tmp_path / "coordinator.lock"),
    )
    processes = []
    roles = []
    try:
        for index, target in enumerate(("local", "peer")):
            role = "native_api_" + uuid4().hex[:16]
            roles.append(role)
            credential = tmp_path / (target + ".env")
            subprocess.run(
                [
                    str(root / ".venv/bin/python"),
                    "-m",
                    "validator.cli.worker_login",
                    role,
                    "--output",
                    str(credential),
                    "--target",
                    target,
                    "--device",
                    target + "-native",
                ],
                cwd=root,
                env=env,
                check=True,
                capture_output=True,
            )
            child_env = dict(
                env,
                BENDTT_CPU=str(cpus[index]),
                BENDTT_HOST_LOCK=str(tmp_path / (target + ".lock")),
            )
            with (tmp_path / (target + ".log")).open("wb") as log:
                processes.append(
                    subprocess.Popen(
                        [
                            str(root / ".venv/bin/python"),
                            "-m",
                            "validator.cli.competition",
                            "worker",
                            "--env-file",
                            str(credential),
                        ],
                        cwd=root,
                        env=child_env,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                )
        with (tmp_path / "coordinator.log").open("wb") as log:
            processes.append(
                subprocess.Popen(
                    [
                        str(root / ".venv/bin/python"),
                        "-m",
                        "validator.cli.competition",
                        "coordinator",
                    ],
                    cwd=root,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            )

        async def scenario():
            started = time.monotonic()
            deadline = started + 300
            while time.monotonic() < deadline:
                assert all(p.poll() is None for p in processes), "service exited; inspect " + str(
                    tmp_path
                )
                current = kit.sql(
                    "SELECT profile_hash FROM api_runtime WHERE competition_id=%s",
                    (kit.competition,),
                )[0]["profile_hash"]
                if current != H:
                    break
                await asyncio.sleep(0.2)
            else:
                pytest.fail("coordinator did not publish")
            upload = await request(
                kit,
                "POST",
                "/submissions",
                **signed((root / "submissions/shipped.lean").read_text()),
            )
            assert upload.status_code == 201, upload.text
            sid = upload.json()["submission"]
            while time.monotonic() < deadline:
                assert all(p.poll() is None for p in processes), "service exited; inspect " + str(
                    tmp_path
                )
                result = await request(kit, "GET", f"/submissions/{sid}")
                assert result.status_code == 200, result.text
                if result.json()["processing_state"] in ("completed", "failed", "rejected"):
                    break
                await asyncio.sleep(0.5)
            else:
                pytest.fail("native workflow exceeded 300 seconds; inspect " + str(tmp_path))
            assert result.json()["processing_state"] == "completed", result.text
            report = await request(kit, "GET", f"/submissions/{sid}/report")
            assert report.status_code == 200, report.text
            assert len(report.json()["targets"]) == 2
            quality_usable = all(
                t["quality"]["status"] == "usable" for t in report.json()["targets"]
            )
            assert report.json()["decision"]["outcome"] == (
                "promoted" if quality_usable else "inconclusive"
            ), report.text
            proof = await request(kit, "GET", f"/submissions/{sid}/proof")
            assert proof.json()["state"] == "passed"

            assert all(
                t["baseline_comparison"]["challenger_speedup"] is not None
                for t in report.json()["targets"]
            )
            print("Native API flow seconds:", round(time.monotonic() - started, 2))
            evidence = root / "artifacts/api-smoke" / uuid4().hex
            evidence.mkdir(parents=True)
            (evidence / "api-result.json").write_text(json.dumps(report.json(), indent=2))
            (evidence / "proof.json").write_text(json.dumps(proof.json(), indent=2))
            print("Native API evidence:", evidence)

        asyncio.run(scenario())
    finally:
        for process in processes:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
        for role in roles:
            kit.sql(SQL("DROP OWNED BY {}").format(Identifier(role)))
            kit.sql(SQL("DROP ROLE {}").format(Identifier(role)))
