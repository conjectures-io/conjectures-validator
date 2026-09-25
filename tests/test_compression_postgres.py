"""Integration against compression-owned migrations, isolated from both application DBs.

The gate's expensive execution is a fixture; queue claims, raw import, aggregation,
admission, scoring, snapshot writing and API reads are real. No chain calls occur.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from bittensor.sp_core import Keypair

from conftest import postgres_dsn
from submission_api import competition_sig
from submission_api.competitions import Competition, CompetitionRegistry
from submission_api.dependencies import (
    get_services,
    get_competition_session,
    require_principal,
    require_cookie_writer,
)
from submission_api.errors import ApiError, api_error_handler
from submission_api.routers import competitions, competition_reads

REPO = Path(__file__).resolve().parents[1]
COMPRESSION = Path(
    os.environ.get("COMPRESSION_REPO", str(REPO.parent / "conjectures-optimisation-miniz-oxide"))
)
BASE = "/v1/competitions/miniz-oxide"


@pytest.fixture
def owned_db():
    base = postgres_dsn()
    if not base:
        pytest.skip("dedicated Postgres test cluster unavailable")
    if not (COMPRESSION / "deploy/migrate/alembic/versions/0011_api_snapshot.py").exists():
        pytest.skip("compression checkout with API migration required (COMPRESSION_REPO)")
    name = "compression_api_" + uuid.uuid4().hex[:12]
    admin = sa.create_engine(base.rsplit("/", 1)[0] + "/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    url = base.rsplit("/", 1)[0] + "/" + name
    env = {
        **os.environ,
        "DATABASE_URL": url,
        "PYTHONPATH": str(COMPRESSION / "validator"),
        "PYTHONPYCACHEPREFIX": "/tmp/compression-api-integration-pycache",
    }
    try:
        migration = subprocess.run(
            [
                str(COMPRESSION / ".venv/bin/python"),
                "-m",
                "alembic",
                "-c",
                "alembic.ini",
                "upgrade",
                "head",
            ],
            cwd=COMPRESSION / "deploy/migrate",
            env=env,
            capture_output=True,
            text=True,
        )
        assert migration.returncode == 0, migration.stdout + migration.stderr
        yield url, env
    finally:
        with admin.connect() as conn:
            conn.execute(
                sa.text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:name"
                ),
                {"name": name},
            )
            conn.execute(sa.text(f'DROP DATABASE "{name}"'))
        admin.dispose()


async def app_for(url):
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(competitions.router)
    app.include_router(competition_reads.router)
    app.add_exception_handler(ApiError, api_error_handler)
    services = SimpleNamespace(
        settings=SimpleNamespace(
            cursor_secret="integration-secret",
            submissions_paused=False,
            competition_rate_per_minute=1000,
            competition_signature_window_seconds=300,
        ),
        competitions=CompetitionRegistry.of(Competition("miniz-oxide", "Compression", 8)),
    )

    async def svc():
        return services

    async def session():
        async with sessions() as s:
            yield s

    app.dependency_overrides[get_services] = svc
    app.dependency_overrides[get_competition_session] = session
    return app, engine, sessions, services


async def register(engine, kp, uid=1, slots=1):
    async with engine.begin() as conn:
        for index in range(slots):
            await conn.execute(
                sa.text("""INSERT INTO registrations(uid, ss58_hot, ss58_cold, block, block_date)
                VALUES (:uid,:hot,'browser-cold',:block,now())"""),
                {"uid": uid, "hot": kp.ss58_address, "block": 100 + index},
            )


def signed(kp, rust=b"parser", proof=b"proof"):
    stamp = int(time.time())
    message = competition_sig.submit_message(
        competition="miniz-oxide",
        digest=competition_sig.digest_of(rust, proof),
        hotkey=kp.ss58_address,
        timestamp=stamp,
    )
    return {
        "headers": {
            "X-Conjectures-Hotkey": kp.ss58_address,
            "X-Conjectures-Timestamp": str(stamp),
            "X-Conjectures-Signature": kp.sign(message).hex(),
        },
        "files": {"parse.rs": ("parse.rs", rust), "Parse.lean": ("Parse.lean", proof)},
    }


WORKER = r"""
import hashlib, json, os, subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path.cwd()/"validator/tests"))
from unittest.mock import patch
import db
from db import models
from db.aggregation import aggregate, publish
from bench.storage import import_file
from service import worker
from service.settings import Settings
from verifier.identity import fingerprint
from test_bench_storage import evidence
from test_weight_setter import FakeChain, PARAMS, at_epoch_boundary
from workers.weight_setter import WeightSetterConfig, step
from scoring import ScoringConfig
store = db.connect(os.environ["DATABASE_URL"])

def fixture_gate(directory, results, claim=None, attempt=None):
    sid = int(directory.name)
    source = (directory/"parse.rs").read_bytes()
    proof = (directory/"Parse.lean").read_bytes()
    token, _ = store.verification.begin(sid, source, proof, fingerprint(), expected_claim=claim, expected_attempt=attempt)
    for milestone in ("static", "lean", "measured"):
        store.verification.publish(sid, token, milestone)
    run_ids = []
    for corpus in ("corpus-stage1", "corpus-stage2"):
        raw = evidence()
        raw[0]["corpus"] = corpus
        raw[0]["methods"]["candidate"]["source_sha256"] = hashlib.sha256(source).hexdigest()
        raw[0]["measured_rounds"] = 3
        raw[0]["benchmark_provenance"] = {"engine_sha256":"1"*64,"template_sha256":"2"*64,"host_sha256":"3"*64}
        for offset, name in enumerate(("incumbent", "candidate")):
            method = raw[1]["methods"][name]
            method["output_bytes"] = 35 if name == "incumbent" else 32
            method["reps"] = [{"phase":"measured", "order_index": 2*i+offset,
                "time_s": .9 if source == b"faster" and name == "candidate" else 1., "encode_s": .1,
                "total_s": 1. if source == b"faster" and name == "candidate" else 1.1} for i in range(3)]
        path = results.parent/(corpus+".jsonl")
        path.write_text("\n".join(json.dumps(x) for x in raw)+"\n")
        run_ids.append(import_file(store.engine, path))
    with store.sessions.begin() as session:
        rows = [session.get(models.BenchmarkRun, rid) for rid in run_ids]
        result = aggregate(session, rows)
        publish(session, sid, result.id)
    # Production gate has already published aggregate evidence; finish preserves it.
    results.write_text(json.dumps({"candidates":["candidate"], "raw_bytes":200,
        "methods":{"candidate":{"output_bytes":64,"parse_s":1.8 if source == b"faster" else 2.,"total_s":2. if source == b"faster" else 2.2},
                   "incumbent":{"output_bytes":70,"parse_s":2.,"total_s":2.2}}}))
    return subprocess.CompletedProcess([], 0, "ACCEPTED (fixture gate)", "")
with tempfile.TemporaryDirectory(prefix="api-gate-fixture-") as tmp:
    with patch.object(worker, "run_gate", fixture_gate):
        assert worker.drain(store, Settings(files=Path(tmp), worker_id="api-test")) >= 1
with store.sessions.begin() as session:
    hotkeys = {row.hotkey: 1 for row in session.query(models.Submission) if row.hotkey}
chain = FakeChain(block=at_epoch_boundary(), hotkeys=hotkeys)
result = step(chain, store, WeightSetterConfig(netuid=66, dry_run=True), ScoringConfig(), PARAMS)
assert result.scoring is not None and result.scoring.api_snapshot is not None
assert chain.submitted == []
print(result.weight_set_id)
store.close()
"""


def test_cli_queue_worker_evidence_scoring_and_reads(owned_db):
    url, env = owned_db

    async def scenario():
        app, engine, sessions, services = await app_for(url)
        kp = Keypair.create_from_uri("//Alice")
        try:
            await register(engine, kp)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                empty = await client.get(BASE + "/weights/current")
                assert empty.json()["context"]["status"] == "not_ready"
                first = await client.post(BASE + "/submissions", **signed(kp))
                assert first.status_code == 201, first.text
                sid = first.json()["submission"]
                again = await client.post(BASE + "/submissions", **signed(kp))
                assert again.status_code == 200 and again.json()["submission"] == sid
                assert (
                    await client.get(BASE + f"/submissions/{sid}/source/parse.rs")
                ).status_code == 404
                async with engine.connect() as conn:
                    assert (
                        await conn.execute(sa.text("SELECT account_id FROM submissions"))
                    ).scalar_one() is None
                    assert (
                        await conn.execute(sa.text("SELECT count(*) FROM submission_files"))
                    ).scalar_one() == 2
                proc = await asyncio.to_thread(
                    subprocess.run,
                    [str(COMPRESSION / ".venv/bin/python"), "-c", WORKER],
                    cwd=COMPRESSION,
                    env=env,
                    capture_output=True,
                    text=True,
                )
                assert proc.returncode == 0, proc.stderr
                detail = (await client.get(BASE + f"/submissions/{sid}")).json()
                assert detail["submission"]["gate_status"] == "passed"
                assert all(v["status"] == "passed" for v in detail["pipeline"].values())
                assert detail["submission"]["admission"]["outcome"] == "not_required"
                weights = (await client.get(BASE + "/weights/current")).json()
                assert weights["dry_run"] is True and weights["chain_accepted"] is False
                assert weights["weights"][kp.ss58_address] > 0
                snapshot_id = weights["context"]["snapshot_id"]
                chart = (
                    await client.get(BASE + "/pareto", params={"snapshot_id": snapshot_id})
                ).json()
                assert chart["items"][0]["timing_interval"] is not None
                assert chart["items"][0]["metrics"]["mean_file_compression_pct"] == 32
                # The only scored submission is the frontier, so its source stays private.
                withheld = await client.get(BASE + f"/submissions/{sid}/source/parse.rs")
                assert withheld.status_code == 403
                assert withheld.json()["reason_code"] == "SOURCE_WITHHELD"
                async with engine.begin() as conn:
                    await conn.execute(
                        sa.text("UPDATE submissions SET state='error' WHERE id=:id"),
                        {"id": int(sid)},
                    )
                    await conn.execute(
                        sa.text("INSERT INTO submissions(digest) VALUES (:digest)"),
                        {"digest": "e" * 64},
                    )
                stable = (
                    await client.get(BASE + "/pareto", params={"snapshot_id": snapshot_id})
                ).json()
                assert stable["items"] == chart["items"]
                feed = (await client.get(BASE + "/submissions")).json()
                assert len(feed["items"]) == 1
                assert feed["items"][0]["gate_status"] == "error"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_concurrent_entitlement_and_browser_attribution(owned_db):
    url, _ = owned_db

    async def scenario():
        app, engine, sessions, services = await app_for(url)
        kp = Keypair.create_from_uri("//Alice")
        browser = Keypair.create_from_uri("//Bob")
        account = SimpleNamespace(id=uuid.uuid4(), submission_coldkey="browser-cold")

        async def principal():
            return SimpleNamespace(account=account)

        app.dependency_overrides[require_principal] = principal
        app.dependency_overrides[require_cookie_writer] = principal
        try:
            await register(engine, kp)
            await register(engine, browser, uid=2)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                one, two = await asyncio.gather(
                    client.post(BASE + "/submissions", **signed(kp, b"one")),
                    client.post(BASE + "/submissions", **signed(kp, b"two")),
                )
                assert sorted([one.status_code, two.status_code]) == [201, 402]
                posted = await client.post(
                    BASE + "/submissions/session",
                    headers={"X-Conjectures-Hotkey": browser.ss58_address},
                    files={
                        "parse.rs": ("parse.rs", b"browser"),
                        "Parse.lean": ("Parse.lean", b"proof"),
                    },
                )
                assert posted.status_code == 201, posted.text
                mine = await client.get(BASE + "/me/submissions")
                assert [r["id"] for r in mine.json()["items"]] == [posted.json()["submission"]]
                bad = signed(kp)
                bad["headers"]["X-Conjectures-Signature"] = "00" * 64
                assert (await client.post(BASE + "/submissions", **bad)).status_code in (401, 403)
                services.settings.submissions_paused = True
                assert (await client.post(BASE + "/submissions", **signed(kp))).status_code == 503
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_stored_speed_comparison_supplies_frontend_plot_data(owned_db):
    url, env = owned_db

    async def scenario():
        app, engine, _, _ = await app_for(url)
        alice, bob = Keypair.create_from_uri("//Alice"), Keypair.create_from_uri("//Bob")
        try:
            await register(engine, alice)
            await register(engine, bob, uid=2)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                first = await client.post(BASE + "/submissions", **signed(alice))
                second = await client.post(BASE + "/submissions", **signed(bob, b"faster"))
                assert first.status_code == second.status_code == 201
                proc = await asyncio.to_thread(
                    subprocess.run,
                    [str(COMPRESSION / ".venv/bin/python"), "-c", WORKER],
                    cwd=COMPRESSION,
                    env=env,
                    capture_output=True,
                    text=True,
                )
                assert proc.returncode == 0, proc.stderr
                sid = second.json()["submission"]
                response = await client.get(BASE + f"/submissions/{sid}/admission")
                assert response.status_code == 200, response.text
                result = response.json()
                assert result["admission"]["outcome"] == "passed"
                test = result["decision"]["speed_test"]
                assert test["lower_confidence_bound"] > 0
                assert test["threshold"] == 0
                assert test["confidence_level"] == 0.95
                assert test["gain_display_interval"]["confidence_level"] == 0.90
                assert len(test["corpora"]) == 2
                assert test["reference"]["submission_id"] == int(first.json()["submission"])
                assert test["frontier_before"]
                assert (
                    test["comparison_range"]["method"] == "reference-anchored-relative-comparison"
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_intake_security_and_shape_checks(owned_db):
    url, _ = owned_db

    async def scenario():
        app, engine, _, services = await app_for(url)
        kp = Keypair.create_from_uri("//Alice")
        try:
            await register(engine, kp)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                assert (await client.get("/v1/competitions/unknown/pareto")).status_code == 404
                stale = signed(kp)
                stale["headers"]["X-Conjectures-Timestamp"] = "1"
                assert (await client.post(BASE + "/submissions", **stale)).json()[
                    "reason_code"
                ] == "SIGNATURE_EXPIRED"
                changed = signed(kp)
                changed["files"]["parse.rs"] = ("parse.rs", b"different bytes")
                assert (await client.post(BASE + "/submissions", **changed)).status_code in (
                    401,
                    403,
                )
                stranger = Keypair.create_from_uri("//Charlie")
                assert (await client.post(BASE + "/submissions", **signed(stranger))).json()[
                    "reason_code"
                ] == "NOT_REGISTERED"
                assert (
                    await client.post(BASE + "/submissions", **signed(kp, b""))
                ).status_code == 400
                assert (
                    await client.post(BASE + "/submissions", **signed(kp, b"x" * (512 * 1024 + 1)))
                ).status_code == 413
                extra = signed(kp)
                extra["files"]["baseline"] = ("baseline", b"yes")
                assert (await client.post(BASE + "/submissions", **extra)).status_code == 400
                wrong_competition = signed(kp)
                stamp = int(wrong_competition["headers"]["X-Conjectures-Timestamp"])
                wrong_competition["headers"]["X-Conjectures-Signature"] = kp.sign(
                    competition_sig.submit_message(
                        competition="other",
                        digest=competition_sig.digest_of(b"parser", b"proof"),
                        hotkey=kp.ss58_address,
                        timestamp=stamp,
                    )
                ).hex()
                assert (
                    await client.post(BASE + "/submissions", **wrong_competition)
                ).status_code in (401, 403)
                async with engine.connect() as conn:
                    assert (
                        await conn.execute(sa.text("SELECT count(*) FROM submissions"))
                    ).scalar_one() == 0
                services.settings.competition_rate_per_minute = 1
                assert (await client.post(BASE + "/submissions", **signed(kp))).status_code == 429
        finally:
            await engine.dispose()

    asyncio.run(scenario())
