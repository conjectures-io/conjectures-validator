"""FloatLib served beside DEFLATE: the registry, its configuration, and FloatLib's reads.

The reads run against a real Postgres schema with the columns FloatLib's migrations (0016, the
`promotions` table with demotion) give it -- `conjectures-optimisation-float-kernels`,
`deploy/platform/CONTRACT.md` -- in a schema of its own inside the competition fixture database,
so DEFLATE's tables there are untouched.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi", reason="submission API tests need the service extra")
pytest.importorskip("psycopg", reason="submission API tests need the db extra")

import sqlalchemy as sa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from conftest import COMPETITION_SKIP_REASON, competition_dsn
from conftest_api import build_settings
from submission_api.app import build_competitions
from submission_api.competitions import (
    DEFLATE,
    FLOATLIB,
    Competition,
    CompetitionRegistry,
    UnknownCompetition,
)
from submission_api.dependencies import get_services
from submission_api.errors import ApiError, api_error_handler
from submission_api.routers import competition_reads, competitions as intake, floatlib
from submission_api.settings import SettingsError

SCHEMA = "floatlib_api_test"
T0 = datetime(2026, 10, 8, 10, 0, tzinfo=UTC)
SHIPPED = "c" * 64
ALICE_DIGEST = "a" * 64
FINGERPRINT = "f" * 64

# ── the registry and its configuration ────────────────────────────────────


def floatlib_entry(**kwargs) -> Competition:
    return Competition("floatlib", "FloatLib", 8.0, kind=FLOATLIB, **kwargs)


def test_every_floatlib_board_resolves_to_the_one_floatlib_competition():
    registry = CompetitionRegistry.of(Competition("deflate", "DEFLATE", 8.0), floatlib_entry())
    assert registry.board("floatlib") == (registry.get("floatlib"), "full")
    assert registry.board("floatlib-bmm")[1] == "bmm"
    assert registry.get("floatlib-add").kind == FLOATLIB
    assert registry.board("deflate") == (registry.get("deflate"), None)
    with pytest.raises(UnknownCompetition):
        registry.get("floatlib-nope")


def test_floatlib_is_served_under_its_own_slug_only():
    # Its practice boards' slugs derive from it, and miners sign them.
    with pytest.raises(ValueError, match="FloatLib is served as"):
        Competition("kernels", "FloatLib", 8.0, kind=FLOATLIB)


def test_without_competitions_the_deployment_serves_its_one_slug_as_before():
    settings = build_settings(COMPETITIONS_ENABLED="1")
    assert settings.competition_slugs == ("deflate",)
    assert settings.competition_database_urls == ()


def test_floatlib_needs_a_database_of_its_own_named():
    with pytest.raises(SettingsError, match="COMPETITION_FLOATLIB_DATABASE_URL"):
        build_settings(COMPETITIONS_ENABLED="1", COMPETITIONS="deflate,floatlib")


def test_an_unknown_competition_is_refused_at_startup():
    with pytest.raises(SettingsError, match="cannot serve"):
        build_settings(COMPETITIONS_ENABLED="1", COMPETITIONS="deflate,rust")


def test_each_competition_gets_its_own_engine_and_kind():
    settings = build_settings(
        COMPETITIONS_ENABLED="1",
        COMPETITIONS="deflate,floatlib",
        DATABASE_URL="postgresql+psycopg://u:p@db:5432/conjectures",
        COMPETITION_DEFLATE_DATABASE_URL="postgresql+psycopg://u:p@db:5432/deflate",
        COMPETITION_FLOATLIB_DATABASE_URL="postgresql+psycopg://u:p@db:5432/floatlib",
    )
    registry = build_competitions(settings)
    try:
        assert [(c.slug, c.kind) for c in registry] == [
            ("deflate", DEFLATE),
            ("floatlib", FLOATLIB),
        ]
        engines = registry.engines()
        assert len({id(e) for e in engines}) == 2
        assert {e.url.database for e in engines} == {"deflate", "floatlib"}
    finally:
        for engine in registry.engines():
            asyncio.run(engine.dispose())


def test_floatlib_on_the_proofs_database_is_refused():
    url = "postgresql+psycopg://u:p@db:5432/conjectures"
    settings = build_settings(
        COMPETITIONS_ENABLED="1",
        COMPETITIONS="floatlib",
        DATABASE_URL=url,
        COMPETITION_FLOATLIB_DATABASE_URL=url,
    )
    with pytest.raises(SettingsError, match="proofs database"):
        build_competitions(settings)


def test_kernel_times_sum_each_kernels_input_types():
    classes = {
        "add:near-one": {"submission_ns": 2.0},
        "add:wide-gap": {"submission_ns": 3.0},
        "mul:tiny": {"submission_ns": 4.0},
        "sum:relu": {"submission_ns": 5.0},
        "bmm:dense": {"submission_ns": 6.0},
    }
    assert floatlib.kernel_times("full", classes) == {
        "add": 5.0,
        "mul": 4.0,
        "sum": 5.0,
        "bmm": 6.0,
    }
    # A practice board times one kernel, so its unprefixed input types are all that kernel's.
    assert floatlib.kernel_times("mul", {"a": {"submission_ns": 1}, "b": {"submission_ns": 2}}) == {
        "mul": 3.0
    }
    assert floatlib.kernel_times("add", None) is None


# ── the reads, against a FloatLib-shaped schema ───────────────────────────

DDL = """
CREATE TABLE submissions (
    id BIGSERIAL PRIMARY KEY, hotkey TEXT NOT NULL, slot TEXT NOT NULL DEFAULT 'full',
    digest TEXT NOT NULL, submitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    state TEXT NOT NULL DEFAULT 'queued', exit_code INTEGER, stage TEXT, reason TEXT,
    ratio DOUBLE PRECISION, classes JSONB, rounds JSONB, seed BIGINT, incumbent_sha256 TEXT,
    champion_sha256 TEXT, verifier_fingerprint TEXT, gate_seconds DOUBLE PRECISION, report TEXT,
    account_id TEXT, worker_id TEXT, claimed_at TIMESTAMPTZ, verification_attempt TEXT,
    gate_errors INTEGER NOT NULL DEFAULT 0, finished_at TIMESTAMPTZ
);
CREATE TABLE promotions (
    id BIGSERIAL PRIMARY KEY, slot TEXT NOT NULL,
    submission_id BIGINT NOT NULL UNIQUE REFERENCES submissions(id) ON DELETE CASCADE,
    champion_sha256 TEXT NOT NULL, promoted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    failures INTEGER NOT NULL DEFAULT 0, demoted_at TIMESTAMPTZ
);
CREATE TABLE weight_sets (
    id BIGSERIAL PRIMARY KEY, netuid INTEGER NOT NULL, block BIGINT NOT NULL,
    uids JSONB NOT NULL, weights JSONB NOT NULL, summary TEXT, accepted BOOLEAN NOT NULL,
    error TEXT, dry_run BOOLEAN NOT NULL DEFAULT false, scoring_content_sha256 TEXT,
    api_snapshot JSONB, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

FULL_CLASSES = {
    f"{kernel}:{kind}": {
        "incumbent_ns": 40.0,
        "submission_ns": ns,
        "ratio": ns / 40.0,
        "spread_inc": 1.01,
        "spread_sub": 1.02,
    }
    for kernel, kind, ns in (
        ("add", "near-one", 30.0),
        ("add", "wide-gap", 25.0),
        ("mul", "tiny", 20.0),
        ("sum", "relu", 19.5),
        ("bmm", "dense", 46.0),
    )
}

PUBLICATION = {
    "competition": "floatlib",
    "computed_at": "2026-10-08T12:00:00+00:00",
    "policy": {
        "version": "auto-promotion-full-entry-v1",
        "competition_share": 0.2,
        "slot_shares": {"full": 1.0},
        "winner_share": 1.0,
        "promotion_bar": 0.97,
        "admission": {"version": "paired-median-bootstrap-v1", "draws": 2000, "quantile": 0.05},
    },
    "slots": {
        "full": {
            "share": 1.0,
            "leader": 1,
            "items": [
                {
                    "hotkey": "alice",
                    "submission_id": 1,
                    "ratio": 0.8,
                    "weight": 1.0,
                    "admission": "leader",
                },
                {
                    "hotkey": "bob",
                    "submission_id": 2,
                    "ratio": 0.99,
                    "weight": 0.0,
                    "admission": "above-bar",
                },
            ],
        }
    },
}


def submission(
    id,
    hotkey,
    *,
    slot="full",
    state="accepted",
    ratio=None,
    champion=None,
    classes=None,
    digest=None,
    at=0,
):
    return {
        "id": id,
        "hotkey": hotkey,
        "slot": slot,
        "digest": digest or f"{id:064x}",
        "submitted_at": T0 + timedelta(minutes=at),
        "finished_at": T0 + timedelta(minutes=at + 5) if state != "queued" else None,
        "state": state,
        "ratio": ratio,
        "classes": json.dumps(classes) if classes is not None else None,
        "champion_sha256": champion,
        "incumbent_sha256": "1" * 64 if state == "accepted" else None,
        "verifier_fingerprint": FINGERPRINT if state != "queued" else None,
        "report": json.dumps({"stage": "score"}) if state != "queued" else None,
    }


ROWS = [
    # Alice beat the shipped champion and was promoted.
    submission(1, "alice", ratio=0.8, champion=SHIPPED, classes=FULL_CLASSES, digest=ALICE_DIGEST),
    # Bob, timed against Alice, sits above the bar.
    submission(2, "bob", ratio=0.99, champion=ALICE_DIGEST, classes=FULL_CLASSES, at=10),
    # Carol was timed against the shipped champion, before Alice's promotion: not comparable now.
    submission(3, "carol", ratio=0.95, champion=SHIPPED, classes=FULL_CLASSES, at=1),
    submission(4, "dave", state="queued", at=20),
    # A practice board with no promotion yet.
    submission(
        5,
        "eve",
        slot="add",
        ratio=0.9,
        champion="e" * 64,
        classes={"near-one": {"submission_ns": 27.0}},
        at=2,
    ),
]


@pytest.fixture
def floatlib_db():
    dsn = competition_dsn()
    if dsn is None:
        pytest.skip(COMPETITION_SKIP_REASON)
    sync = sa.create_engine(dsn)
    with sync.begin() as conn:
        conn.execute(sa.text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        conn.execute(sa.text(f"CREATE SCHEMA {SCHEMA}"))
        conn.execute(sa.text(f"SET LOCAL search_path TO {SCHEMA}"))
        conn.execute(sa.text(DDL))
        insert = sa.text(
            "INSERT INTO submissions (id, hotkey, slot, digest, submitted_at, finished_at, state, "
            "ratio, classes, champion_sha256, incumbent_sha256, verifier_fingerprint, report) "
            "VALUES (:id, :hotkey, :slot, :digest, :submitted_at, :finished_at, :state, :ratio, "
            "CAST(:classes AS jsonb), :champion_sha256, :incumbent_sha256, "
            ":verifier_fingerprint, :report)"
        )
        for row in ROWS:
            conn.execute(insert, row)
        conn.execute(
            sa.text(
                "INSERT INTO promotions (slot, submission_id, champion_sha256, promoted_at) "
                "VALUES ('full', 1, :digest, :at)"
            ),
            {"digest": ALICE_DIGEST, "at": T0 + timedelta(minutes=5)},
        )
    try:
        yield SimpleNamespace(dsn=dsn, sync=sync)
    finally:
        with sync.begin() as conn:
            conn.execute(sa.text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        sync.dispose()


def execute(db, sql, **params):
    with db.sync.begin() as conn:
        conn.execute(sa.text(f"SET LOCAL search_path TO {SCHEMA}"))
        conn.execute(sa.text(sql), params)


def publish(db, snapshot=PUBLICATION):
    execute(
        db,
        "INSERT INTO weight_sets (netuid, block, uids, weights, accepted, api_snapshot) "
        "VALUES (66, 1, '[]', '[]', true, CAST(:snapshot AS jsonb))",
        snapshot=json.dumps(snapshot),
    )


def read(db, *paths, deflate=True):
    """GET each path against an app serving DEFLATE and FloatLib, FloatLib from the fixture.

    DEFLATE has no database here, so a FloatLib request that fell back to it would fail. With
    `deflate=False` the app serves FloatLib alone, which is then its primary competition.
    """

    async def scenario():
        engine = create_async_engine(db.dsn, connect_args={"options": f"-c search_path={SCHEMA}"})
        app = FastAPI()
        app.include_router(floatlib.root_router)
        app.include_router(floatlib.router)
        app.include_router(intake.router)
        app.include_router(competition_reads.router)
        app.add_exception_handler(ApiError, api_error_handler)
        sessions = async_sessionmaker(engine)
        entry = floatlib_entry(engine=engine, sessions=sessions)
        services = SimpleNamespace(
            settings=SimpleNamespace(cursor_secret="test-secret", submissions_paused=False),
            competitions=(
                CompetitionRegistry.of(Competition("deflate", "DEFLATE", 8.0), entry)
                if deflate
                else CompetitionRegistry.of(entry)
            ),
            # As `build_services` sets it: DEFLATE's sessions, or the only competition's.
            competition_sessions=None if deflate else sessions,
        )
        app.dependency_overrides[get_services] = lambda: services
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                return [await c.get(path) for path in paths]
        finally:
            await engine.dispose()

    return asyncio.run(scenario())


def test_the_full_board_leads_with_its_champion_and_carries_kernel_times(floatlib_db):
    publish(floatlib_db)
    (response,) = read(floatlib_db, "/v1/competitions/floatlib/leaderboard")
    assert response.status_code == 200, response.text
    board = response.json()
    assert board["slot"] == "full" and board["pays"] is True and board["leader"] == 1
    assert board["champion_sha256"] == ALICE_DIGEST and board["promotion_bar"] == 0.97
    assert board["verifier_fingerprint"] == FINGERPRINT
    champion, bob = board["ranking"]
    assert champion["submission"] == 1 and champion["admission"] == "leader"
    assert champion["promoted_at"] == "2026-10-08T10:05:00Z"
    assert champion["kernels"] == {"add": 55.0, "mul": 20.0, "sum": 19.5, "bmm": 46.0}
    assert bob["submission"] == 2 and bob["admission"] == "above-bar"
    assert bob["qualifies"] is False and bob["promoted_at"] is None
    assert [r["rank"] for r in board["ranking"]] == [1, 2]


def test_a_practice_board_reads_its_own_slot_and_pays_nothing(floatlib_db):
    publish(floatlib_db)
    (response,) = read(floatlib_db, "/v1/competitions/floatlib-add/leaderboard")
    board = response.json()
    assert board["slot"] == "add" and board["pays"] is False and board["leader"] is None
    (row,) = board["ranking"]
    # The published scoring scores only the paid board, so a practice row has no outcome.
    assert row["submission"] == 5 and row["admission"] is None
    assert row["kernels"] == {"add": 27.0} and row["qualifies"] is True


def test_health_states_the_queue_and_the_published_rules(floatlib_db):
    (before,) = read(floatlib_db, "/v1/competitions/floatlib/health")
    assert before.json() == {
        "ok": True,
        "queued": 1,
        "promotion_bar": 0.97,
        "competition_share": None,
        "admission": None,
    }
    publish(floatlib_db)
    (after,) = read(floatlib_db, "/v1/competitions/floatlib/health")
    assert after.json()["competition_share"] == 0.2
    assert after.json()["admission"] == {"draws": 2000, "quantile": 0.05}


def test_root_health_is_floatlibs_health_as_its_own_api_serves_it(floatlib_db):
    publish(floatlib_db)
    root, scoped = read(floatlib_db, "/health", "/v1/competitions/floatlib/health")
    assert root.status_code == 200 and root.json() == scoped.json()
    assert root.json() == {
        "ok": True,
        "queued": 1,
        "promotion_bar": 0.97,
        "competition_share": 0.2,
        "admission": {"draws": 2000, "quantile": 0.05},
    }


def root_health(competitions):
    """`/health` on an app serving `competitions`, none of them with a reachable database."""

    async def scenario():
        app = FastAPI()
        app.include_router(floatlib.root_router)
        services = SimpleNamespace(competitions=competitions, competition_sessions=None)
        app.dependency_overrides[get_services] = lambda: services
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            return await c.get("/health")

    return asyncio.run(scenario())


NO_STORE = {
    "ok": True,
    "queued": None,
    "promotion_bar": 0.97,
    "competition_share": None,
    "admission": None,
}


def test_root_health_answers_without_floatlib_configured():
    response = root_health(CompetitionRegistry.of(Competition("deflate", "DEFLATE", 8.0)))
    assert response.status_code == 200 and response.json() == NO_STORE


def test_root_health_does_not_fail_with_floatlibs_database():
    engine = create_async_engine("postgresql+psycopg://nobody:nothing@127.0.0.1:1/absent")
    try:
        entry = floatlib_entry(engine=engine, sessions=async_sessionmaker(engine))
        response = root_health(CompetitionRegistry.of(entry))
        assert response.status_code == 200 and response.json() == NO_STORE
    finally:
        asyncio.run(engine.dispose())


def test_champions_lists_every_champion_and_marks_the_one_holding_the_board(floatlib_db):
    execute(
        floatlib_db,
        "INSERT INTO promotions (slot, submission_id, champion_sha256, promoted_at) "
        "VALUES ('full', 2, :digest, :at)",
        digest="2" * 64,
        at=T0 + timedelta(hours=1),
    )
    (both,) = read(floatlib_db, "/v1/competitions/floatlib/champions")
    assert [(c["submission"], c["current"], c["demoted_at"]) for c in both.json()] == [
        (2, True, None),
        (1, False, None),
    ]
    # Bob's own bench fails too often: demoted, and the board goes back to Alice.
    execute(
        floatlib_db,
        "UPDATE promotions SET demoted_at = :at, failures = 3 WHERE submission_id = 2",
        at=T0 + timedelta(hours=2),
    )
    (after, practice) = read(
        floatlib_db,
        "/v1/competitions/floatlib/champions",
        "/v1/competitions/floatlib-add/champions",
    )
    assert [(c["submission"], c["current"]) for c in after.json()] == [(2, False), (1, True)]
    assert after.json()[0]["demoted_at"] == "2026-10-08T12:00:00Z"
    assert after.json()[1] == {
        "submission": 1,
        "hotkey": "alice",
        "ratio": 0.8,
        "promoted_at": "2026-10-08T10:05:00Z",
        "demoted_at": None,
        "current": True,
    }
    assert practice.json() == []


def test_a_submission_reads_in_floatlibs_shape_and_only_under_its_own_slot(floatlib_db):
    view, report, elsewhere = read(
        floatlib_db,
        "/v1/competitions/floatlib/submissions/1",
        "/v1/competitions/floatlib/submissions/1/report",
        "/v1/competitions/floatlib-add/submissions/1",
    )
    body = view.json()
    assert body["slot"] == "full" and body["ratio"] == 0.8
    assert body["classes"]["add:near-one"]["submission_ns"] == 30.0
    assert body["verification_current"] is True
    assert json.loads(report.json()["report"]) == {"stage": "score"}
    assert elsewhere.status_code == 404


def test_floatlib_is_listed_and_detailed_beside_deflate_from_its_own_database(floatlib_db):
    publish(floatlib_db)
    (detail,) = read(floatlib_db, "/v1/competitions/floatlib-mul")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["slug"] == "floatlib-mul" and body["submissions_open"] is False
    assert body["queue_depth"] == 1 and body["policy"]["competition_share"] == 0.2
    assert body["files"] == [{"name": "Kernel.lean", "max_bytes": 256_000}]


def test_the_index_lists_floatlib_once_from_its_own_database(floatlib_db):
    publish(floatlib_db)
    (index,) = read(floatlib_db, "/v1/competitions", deflate=False)
    assert index.status_code == 200, index.text
    body = index.json()
    assert body["total"] == 1
    (entry,) = body["items"]
    assert entry["slug"] == "floatlib" and entry["name"] == "FloatLib"
    assert entry["current_snapshot_id"] is not None and entry["context"]["status"] == "ready"


def test_deflate_routes_and_unknown_boards_404_a_floatlib_slug(floatlib_db):
    *deflate_only, unknown = read(
        floatlib_db,
        "/v1/competitions/floatlib/pareto",
        "/v1/competitions/floatlib/submissions",
        "/v1/competitions/floatlib/weights/current",
        "/v1/competitions/floatlib-nope/leaderboard",
    )
    assert [r.status_code for r in deflate_only] == [404, 404, 404]
    # Not a FloatLib board, so FloatLib's routes do not match it and DEFLATE's answer: here,
    # where DEFLATE has no database, as every unserved slug is answered, rather than by reading
    # FloatLib's.
    assert unknown.status_code == 503
    assert unknown.json()["reason_code"] == "COMPETITIONS_UNAVAILABLE"


def test_a_schema_without_promotions_is_an_outage_not_a_500(floatlib_db):
    execute(floatlib_db, "DROP TABLE promotions")
    (response,) = read(floatlib_db, "/v1/competitions/floatlib/leaderboard")
    assert response.status_code == 503
    assert response.json()["reason_code"] == "COMPETITION_SCHEMA_UNAVAILABLE"
