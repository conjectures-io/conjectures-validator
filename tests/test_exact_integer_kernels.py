"""Exact integer kernels: the kernel competition routes under a third slug, with a C upload and
an instruction-count report, against the competition's own schema and `platform_api` grants
(`fixtures/eik_schema`) and the rows its coordinator writes."""

import asyncio
import base64
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient

from database_guard import _migrate_competition
from conftest import competition_dsn, postgres_dsn
from conftest_api import build_settings
from test_torchlean_intake import roles_lock as roles_lock
from test_torchlean_intake import execute, headers, post, register, service

from submission_api.app import build_competitions
from submission_api.competitions import (
    EXACT_INTEGER_KERNELS,
    TENSOR_KERNELS,
    TORCHLEAN,
    Competition,
    kernel_upload,
)
from submission_api.dependencies import get_services
from submission_api.settings import SettingsError

EIK = EXACT_INTEGER_KERNELS
ENTRY = b"#include <stdint.h>\nuint32_t eik_exp(uint32_t x) { return x; }\n"
CAP = 262_144
SCHEMA = Path(__file__).parent / "fixtures/eik_schema"


@pytest.fixture(scope="module")
def engines(roles_lock):
    """The competition database as `coordinator.migrate` leaves it, with its API grants."""
    comp, central = competition_dsn(), postgres_dsn()
    if not comp or not central:
        pytest.skip("requires a declared private PostgreSQL fixture")
    pinned = json.loads((SCHEMA / "sha256.json").read_text())
    for name, digest in pinned.items():
        assert hashlib.sha256((SCHEMA / name).read_bytes()).hexdigest() == digest, name
    owner, api = sa.create_engine(comp), sa.create_engine(central)
    with owner.begin() as c:
        c.exec_driver_sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        # As `coordinator.migrate` runs it: one transaction, no parameters.
        for migration in ("0001_schema.sql", "0002_source_embargoes.sql"):
            c.exec_driver_sql(
                (SCHEMA / migration).read_text(), execution_options={"no_parameters": True}
            )
        c.exec_driver_sql(
            "DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='platform_api') "
            "THEN CREATE ROLE platform_api; END IF; END $$"
        )
        c.exec_driver_sql("ALTER ROLE platform_api LOGIN PASSWORD 'private_eik_api_test'")
        c.exec_driver_sql((SCHEMA / "platform_api.sql").read_text())
    with api.begin() as c:
        c.exec_driver_sql("DROP TABLE IF EXISTS competition_intake_limits")
        c.exec_driver_sql(
            (
                Path(__file__).parents[1] / "deploy/migrate/sql/V054__competition_intake_limits.sql"
            ).read_text()
        )
    url = sa.engine.make_url(comp).set(username="platform_api", password="private_eik_api_test")
    yield SimpleNamespace(
        owner=owner, api=api, comp=url.render_as_string(hide_password=False), central=central
    )
    with owner.begin() as c:
        # Roles are cluster-wide; DROP OWNED also takes back the database-level CONNECT grant.
        c.exec_driver_sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        c.exec_driver_sql("DROP OWNED BY platform_api, eik_worker_access")
        c.exec_driver_sql("DROP ROLE platform_api; DROP ROLE eik_worker_access")
    owner.dispose()
    api.dispose()
    # This worker's competition database belongs to the other competition tests too (the
    # registration watcher's, which sort after this module): give it back migrated to head, as
    # `database_guard.isolate_worker` made it, not empty.
    _migrate_competition(comp)


@pytest.fixture
def database(engines):
    with engines.owner.begin() as c:
        c.exec_driver_sql(
            "TRUNCATE sources,registrations,submissions,coordinator_state CASCADE; "
            "INSERT INTO coordinator_state(id) VALUES (1)"
        )
    with engines.api.begin() as c:
        c.exec_driver_sql("TRUNCATE competition_intake_limits")
    return engines


def eik(database, *args, **kwargs):
    return post(database, *args, slug=EIK, **kwargs)


# --- Intake -----------------------------------------------------------------------------------


def test_upload_is_one_entry_c_signed_for_its_own_slug(database):
    register(database)
    r = eik(database, ENTRY)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["competition"] == EIK and body["intake_mode"] == "subnet"
    assert body["status_url"] == f"/v1/competitions/{EIK}/submissions/{body['submission']}"
    assert bytes(execute(database, "SELECT content FROM sources").scalar_one()) == ENTRY
    status = eik(database, path=f"/submissions/{body['submission']}", method="get")
    assert status.status_code == 200, status.text
    assert status.json()["proof_state"] == "queued"


@pytest.mark.parametrize(
    "slug,field", [(EIK, "Kernel.lean"), (TENSOR_KERNELS, "entry.c"), (TORCHLEAN, "entry.c")]
)
def test_each_slug_takes_only_its_own_file(database, slug, field):
    register(database)
    r = post(database, ENTRY, files={field: (field, ENTRY)}, slug=slug)
    assert r.status_code == 400, r.text
    assert kernel_upload(slug).name in r.json()["detail"]
    assert execute(database, "SELECT count(*) FROM submissions").scalar_one() == 0


@pytest.mark.parametrize("size,status", [(CAP, 201), (CAP + 1, 413)])
def test_upload_limit_is_the_gates(database, size, status):
    register(database)
    assert eik(database, b"/" * size).status_code == status


@pytest.mark.parametrize("kernel", [b"", b"\xff\xfe"])
def test_empty_or_non_utf8_entry_is_refused(database, kernel):
    register(database)
    assert eik(database, kernel).status_code == 400


@pytest.mark.parametrize("signed_for", [TORCHLEAN, TENSOR_KERNELS, "exact-integer-kernel"])
def test_a_signature_for_another_competition_is_refused(database, signed_for):
    register(database)
    r = eik(database, ENTRY, provided=headers(ENTRY, slug=signed_for))
    assert r.status_code == 401, r.text
    assert execute(database, "SELECT count(*) FROM submissions").scalar_one() == 0


def test_its_signature_is_refused_by_tensor_kernels(database):
    register(database)
    r = post(database, ENTRY, provided=headers(ENTRY, slug=EIK), slug=TENSOR_KERNELS)
    assert r.status_code == 401, r.text


def test_intake_has_its_own_switch_and_budget(database):
    register(database)
    closed = eik(database, ENTRY, exact_integer_kernels_submissions_enabled=False)
    assert closed.status_code == 403 and closed.json()["reason_code"] == "SUBMISSIONS_CLOSED"
    opened = eik(
        database,
        ENTRY,
        torchlean_submissions_enabled=False,
        tensor_kernels_submissions_enabled=False,
    )
    assert opened.status_code == 201, opened.text
    with database.api.begin() as c:
        subjects = set(c.exec_driver_sql("SELECT subject FROM competition_intake_limits").scalars())
    assert subjects and all(s.startswith(f"{EIK}:") for s in subjects)


def test_dev_bypass_and_its_production_backstop(database):
    dev = {"exact_integer_kernels_dev_skip_registration": True}
    r = eik(database, ENTRY, **dev)
    assert r.status_code == 201 and r.json()["intake_mode"] == "dev", r.text
    assert eik(database, ENTRY + b"//", production=True, **dev).status_code == 503
    # Another competition's bypass is not this one's.
    other = eik(database, ENTRY + b"//", tensor_kernels_dev_skip_registration=True)
    assert other.status_code == 402


# --- Settings ---------------------------------------------------------------------------------


EIK_URL = "postgresql+psycopg://u:p@db:5432/exact_integer_kernels"


def test_settings_and_registry_serve_it_beside_the_other_kernel_competitions():
    settings = build_settings(
        COMPETITIONS_ENABLED="1",
        COMPETITIONS="torchlean,tensor-kernels,exact-integer-kernels",
        COMPETITION_TORCHLEAN_DATABASE_URL="postgresql+psycopg://u:p@db:5432/torchlean",
        COMPETITION_TENSOR_KERNELS_DATABASE_URL="postgresql+psycopg://u:p@db:5432/tensor_kernels",
        COMPETITION_EXACT_INTEGER_KERNELS_DATABASE_URL=EIK_URL,
        EXACT_INTEGER_KERNELS_SUBMISSIONS_ENABLED="1",
        EXACT_INTEGER_KERNELS_REGISTRATION_CUTOFF="2026-11-23T01:00:00+01:00",
    )
    assert settings.exact_integer_kernels_submissions_enabled
    assert not settings.tensor_kernels_submissions_enabled
    assert settings.exact_integer_kernels_registration_cutoff == datetime(2026, 11, 23, tzinfo=UTC)
    assert settings.exact_integer_kernels_max_source_bytes == CAP
    # The others' defaults are unchanged.
    assert settings.tensor_kernels_max_source_bytes == settings.torchlean_max_source_bytes
    assert settings.torchlean_max_source_bytes == 1024 * 1024
    registry = build_competitions(settings)
    competition = registry.get(EIK)
    assert competition.kind == TORCHLEAN and competition.name == "Exact integer kernels"
    assert len({c.engine for c in registry}) == 3
    for engine in registry.engines():
        asyncio.run(engine.dispose())


def test_upload_limit_may_be_lowered_but_not_raised():
    assert (
        build_settings(
            EXACT_INTEGER_KERNELS_MAX_SOURCE_BYTES="4096"
        ).exact_integer_kernels_max_source_bytes
        == 4096
    )
    with pytest.raises(SettingsError, match="EXACT_INTEGER_KERNELS_MAX_SOURCE_BYTES"):
        build_settings(EXACT_INTEGER_KERNELS_MAX_SOURCE_BYTES=str(CAP + 1))
    # Tensor kernels keeps its 4 MiB ceiling.
    assert build_settings(TENSOR_KERNELS_MAX_SOURCE_BYTES=str(4 * 1024 * 1024))


@pytest.mark.parametrize(
    "options,pattern",
    [
        ({"EXACT_INTEGER_KERNELS_SUBMISSIONS_ENABLED": "1"}, "requires Exact integer kernels"),
        (
            {"EXACT_INTEGER_KERNELS_REGISTRATION_CUTOFF": "2026-11-23"},
            "EXACT_INTEGER_KERNELS_REGISTRATION_CUTOFF",
        ),
        (
            {
                "EXACT_INTEGER_KERNELS_SUBMISSIONS_ENABLED": "1",
                "COMPETITIONS_ENABLED": "1",
                "COMPETITIONS": EIK,
                "COMPETITION_EXACT_INTEGER_KERNELS_DATABASE_URL": EIK_URL,
            },
            "EXACT_INTEGER_KERNELS_REGISTRATION_CUTOFF is required",
        ),
        (
            {"COMPETITIONS_ENABLED": "1", "COMPETITIONS": EIK},
            "COMPETITION_EXACT_INTEGER_KERNELS_DATABASE_URL is required",
        ),
        (
            {"APP_MODE": "PROD", "EXACT_INTEGER_KERNELS_DEV_SKIP_REGISTRATION": "1"},
            "EXACT_INTEGER_KERNELS_DEV_SKIP_REGISTRATION is forbidden in production",
        ),
    ],
)
def test_invalid_settings(options, pattern):
    with pytest.raises(SettingsError, match=pattern):
        build_settings(**options)


def test_it_is_served_only_under_its_own_slug():
    Competition(EIK, "Exact integer kernels", 8, kind=TORCHLEAN)
    with pytest.raises(ValueError, match="kernel competition"):
        Competition("exact-integer-kernel", "x", 8, kind=TORCHLEAN)


# --- The full application ---------------------------------------------------------------------


def test_openapi_names_each_slugs_own_file_and_report(database):
    from submission_api.app import create_app

    stub, engines_ = service(database, EIK)
    services = stub.dependency_overrides[get_services]()
    app = create_app(build_settings(), services=services)
    schema = app.openapi()
    paths = schema["paths"]

    def upload(slug):
        body = paths[f"/v1/competitions/{slug}/submissions"]["post"]["requestBody"]
        return body["content"]["multipart/form-data"]["schema"]

    def report(slug):
        response = paths[f"/v1/competitions/{slug}/submissions/{{submission_id}}/report"]["get"]
        return response["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]

    assert upload(EIK)["required"] == ["entry.c"]
    assert upload(TENSOR_KERNELS)["required"] == upload(TORCHLEAN)["required"] == ["Kernel.lean"]
    assert report(EIK).endswith("/EikReport")
    assert report(TENSOR_KERNELS).endswith("/TorchLeanReport")
    source = paths[f"/v1/competitions/{EIK}/submissions/{{submission_id}}/source"]["get"]
    assert list(source["responses"]["200"]["content"]) == ["text/x-c"]

    async def go():
        app.dependency_overrides[get_services] = lambda: services
        register(database)
        try:
            async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
                return await client.post(
                    f"/v1/competitions/{EIK}/submissions",
                    headers=headers(ENTRY, slug=EIK),
                    files={"entry.c": ("entry.c", ENTRY)},
                )
        finally:
            for engine in engines_:
                await engine.dispose()

    response = asyncio.run(go())
    assert response.status_code == 201, response.text


# --- Reads, of the rows the coordinator writes -------------------------------------------------

ROWS = json.loads((SCHEMA / "coordinator_rows.json").read_text())
JSONB = {
    "proof_report",
    "plan",
    "decision",
    "request",
    "result",
    "error",
    "config_evidence",
    "publication",
}
SERIALS = ("registrations", "submissions", "evaluations", "benchmark_jobs", "promotions")
# Foreign keys first.
TABLES = (
    "registrations",
    "sources",
    "submissions",
    "board_progress",
    "evaluations",
    "benchmark_jobs",
    "evaluation_jobs",
    "promotions",
    "coordinator_state",
)
# The six entries, in submission order (generate_rows.ENTRIES).
FIRST, GATE_REJECTED, COMPARE_REJECTED, CHECK_REJECTED, SECOND, IN_PROGRESS = range(1, 7)
BOARDS = ("rv32im", "thumbv6m", "overall")
# What a worker's report carries that the API must not publish.
PRIVATE = (
    "champions",
    "fingerprint",
    "hard_pool",
    "sandbox",
    "image_sha256",
    "candidate_boards",
    "needs_fullcheck",
    "lease",
    "owner",
    "token",
)


def value(column, v):
    if isinstance(v, dict) and set(v) == {"base64"}:
        return base64.b64decode(v["base64"])
    if column in JSONB:
        return json.dumps(v) if v is not None else None
    return v


def load(db):
    """Every table the API reads, as the coordinator left it (fixtures/eik_schema/README.md)."""
    with db.owner.begin() as c:
        c.exec_driver_sql("DELETE FROM coordinator_state")
        for table in TABLES:
            for row in ROWS[table]:
                # Promotions and the evaluations planned against them refer to each other.
                row = {k: v for k, v in row.items() if k != "reference_promotion_id"}
                names = ",".join(row)
                values = ",".join(f"CAST(:{k} AS jsonb)" if k in JSONB else f":{k}" for k in row)
                c.execute(
                    sa.text(f"INSERT INTO {table}({names}) VALUES ({values})"),
                    {k: value(k, v) for k, v in row.items()},
                )
        for row in ROWS["evaluations"]:
            if row["reference_promotion_id"] is not None:
                c.execute(
                    sa.text("UPDATE evaluations SET reference_promotion_id=:p WHERE id=:id"),
                    {"p": row["reference_promotion_id"], "id": row["id"]},
                )
        for table in SERIALS:
            c.exec_driver_sql(
                f"SELECT setval(pg_get_serial_sequence('{table}','id'),(SELECT max(id) FROM {table}))"
            )


def eik_get(db, path, **settings):
    return post(db, path=path, method="get", slug=EIK, **settings)


def ok(db, path):
    response = eik_get(db, path)
    assert response.status_code == 200, response.text
    return response.json()


def report(db, sid):
    response = eik_get(db, f"/submissions/{sid}/report")
    assert response.status_code == 200, response.text
    for private in PRIVATE:
        assert f'"{private}"' not in response.text, private
    return {e["board"]: e for e in response.json()["evaluations"]}


def source_of(sid):
    sha = next(s["source_sha256"] for s in ROWS["submissions"] if s["id"] == sid)
    row = next(r for r in ROWS["sources"] if r["sha256"] == sha)
    return base64.b64decode(row["content"]["base64"])


def test_the_fixture_is_the_coordinators_eik_top2_publication():
    publication = ROWS["coordinator_state"][0]["publication"]
    assert publication["version"] == "eik-top2-v1"
    assert publication["rewards"]["rank_shares"] == [1.0, 0.0]
    assert [k["submission_id"] for k in publication["boards"]["rv32im"]["kings"]] == [SECOND, FIRST]


def test_platform_role_reads_and_writes_only_what_the_grants_allow(database):
    with database.owner.connect() as c:

        def can(table, privilege, column=None):
            if column:
                return c.scalar(
                    sa.text("SELECT has_column_privilege('platform_api',:t,:c,:p)"),
                    {"t": table, "c": column, "p": privilege},
                )
            return c.scalar(
                sa.text("SELECT has_table_privilege('platform_api',:t,:p)"),
                {"t": table, "p": privilege},
            )

        assert can("submissions", "SELECT") and can("sources", "INSERT")
        assert can("submissions", "INSERT", "intake_mode")
        assert not can("submissions", "INSERT", "state")
        assert not can("coordinator_state", "SELECT", "leader_token")
        for table in ("promotions", "benchmark_jobs", "evaluations", "worker_logins"):
            assert not can(table, "INSERT"), table


def test_overview_boards_and_health(database):
    load(database)
    overview = ok(database, "")
    assert overview["slug"] == EIK and overview["name"] == "Exact integer kernels"
    assert overview["files"] == [{"name": "entry.c", "max_bytes": CAP}]
    assert overview["max_file_bytes"] == CAP
    assert overview["metric_definitions"][0]["label"] == "Score / champion"
    assert overview["execution_limits"] == {}
    assert overview["environment_limits"] == {"eik-compare-x86": {}, "eik-check": {}}
    assert set(overview["boards"]) == set(BOARDS)
    assert overview["policy_status"] == "ready" and overview["queue_depth"] == 1
    # Tensor kernels still names Kernel.lean.
    tensor = post(database, path="", method="get", slug=TENSOR_KERNELS).json()
    assert tensor["files"][0]["name"] == "Kernel.lean"
    assert tensor["metric_definitions"][0]["label"] == "Time / reference"

    boards = {b["id"]: b for b in ok(database, "/boards")["boards"]}
    assert set(boards) == set(BOARDS)
    assert boards["overall"]["models"] == {"rv32im": 0.5, "thumbv6m": 0.5}
    assert boards["overall"]["reward_share"] == 0.5 and boards["rv32im"]["reward_share"] == 0.25
    assert boards["rv32im"]["promotion_bar"] == 0.97 and boards["rv32im"]["guard_limit"] is None
    (environment,) = boards["rv32im"]["environments"]
    assert environment["id"] == "eik-compare-x86" and environment["weight"] == 1.0
    assert (
        environment["identity"]
        == ROWS["coordinator_state"][0]["config_evidence"]["environments"]["eik-compare-x86"][
            "identity"
        ]
    )
    assert boards["rv32im"]["current_promotion_id"] == 3 and boards["rv32im"]["paused"] is False

    health = ok(database, "/health")
    assert health["policy_ready"] and health["rank_shares"] == [1.0, 0.0]
    assert health["benchmarking"] == 1 and health["queued"] == 0


def test_champions_and_leaderboard(database):
    load(database)
    champions = ok(database, "/champions?board=overall")["champions"]
    assert [c["submission"] for c in champions] == [SECOND, FIRST]
    second, first = champions
    assert second["current"] and second["reward_rank"] == 1 and second["rank_share"] == 1.0
    assert second["competition_fraction"] == 0.5 and second["ratio"] == 0.9
    # The previous champion keeps rank 2 in the publication, at a share of nothing.
    assert not first["current"] and first["reward_rank"] == 2
    assert first["competition_fraction"] == 0.0
    assert first["source_available"] and not second["source_available"]
    assert ok(database, "/champions?board=thumbv6m")["champions"][0]["submission"] == SECOND

    board = ok(database, "/leaderboard?board=overall")
    assert board["reference"]["submission"] == SECOND and not board["reference"]["shipped"]
    (challenger,) = board["challengers"]
    assert challenger["submission"] == IN_PROGRESS and challenger["state"] == "pending"
    assert challenger["ratio"] is None
    # The previous reference's group: everyone measured against the first champion.
    old = ok(database, "/leaderboard?board=overall&promotion_id=2")
    outcomes = {c["submission"]: c["admission"] for c in old["challengers"]}
    assert outcomes == {
        COMPARE_REJECTED: "benchmark-failed",
        CHECK_REJECTED: "check-failed",
        SECOND: "qualifies",
    }


def test_submissions_list_their_states(database):
    load(database)
    rows = {s["id"]: s for s in ok(database, "/submissions")["submissions"]}
    assert rows[IN_PROGRESS]["processing_state"] == "benchmarking"
    rejected = rows[GATE_REJECTED]
    assert rejected["proof_state"] == "rejected" and rejected["stage"] == "ast"
    assert rejected["reason"].startswith("FloatingLiteral is not allowed")
    assert all(rows[s]["processing_state"] == "completed" for s in (FIRST, SECOND, CHECK_REJECTED))
    page = ok(database, "/submissions?state=rejected")["submissions"]
    assert [s["id"] for s in page] == [GATE_REJECTED]
    decisions = {e["board"]: e["decision"] for e in rows[FIRST]["evaluations"]}
    assert decisions["thumbv6m"]["outcome"] == "above-bar" and decisions["rv32im"]["score"] == 0.5


def test_report_of_a_takeover(database):
    load(database)
    boards = report(database, FIRST)
    assert set(boards) == set(BOARDS)
    rv32im, thumbv6m = boards["rv32im"], boards["thumbv6m"]
    assert rv32im["seed"] == 20261118
    decision = rv32im["decision"]
    assert decision["outcome"] == "qualifies" and decision["score"] == 0.5
    assert decision["entry_S"] / decision["champion_S"] == pytest.approx(0.5)
    assert decision["champion_sha256"] == rv32im["reference_hash"]
    merged = decision["fullcheck"]
    assert merged["verdict"] == "passed" and merged["required_targets"] == ["rv32im"]
    assert merged["functions"]["exp"] == {"chunks_covered": 4096, "words_checked": 1 << 32}
    assert merged["targets"]["rv32im"]["tanh"]["chunks_covered"] == 4096

    compare, *checks = rv32im["job_results"]
    assert compare["kind"] == "compare" and compare["state"] == "completed"
    measured = compare["compare"]
    assert set(measured["scores"]) == {"rv32im", "thumbv6m", "thumbv7em", "overall"}
    rv = measured["scores"]["rv32im"]
    assert rv["S"] == pytest.approx(12326.18) and rv["bytes"] == 16600
    assert set(rv["functions"]) == {"exp", "sigmoid", "tanh"}
    assert rv["functions"]["exp"]["tail_factor"] == 1.0 and not rv["reported_only"]
    assert measured["scores"]["thumbv7em"]["reported_only"] is True
    assert measured["scores"]["overall"]["bytes"] is None
    assert set(measured["images"]) == {"rv32im", "thumbv6m", "thumbv7em"}
    assert len(measured["inputs_sha256"]) == 64
    assert measured["boards"]["rv32im"]["beats"] and not measured["boards"]["thumbv6m"]["beats"]
    # Two host shards per function, then one QEMU shard per function on the required target, in
    # eik's function order.
    assert [(c["kind"], c["function"], c["target"], c["chunks"]) for c in checks] == [
        ("fullcheck", fn, None, chunks)
        for fn in ("sigmoid", "tanh", "exp")
        for chunks in ([0, 2048], [2048, 4096])
    ] + [("targetcheck", fn, "rv32im", [0, 4096]) for fn in ("sigmoid", "tanh", "exp")]
    assert all(c["state"] == "completed" and c["compare"] is None for c in checks)
    assert checks[0]["check"] == {
        "verdict": "passed",
        "stage": None,
        "reasons": [],
        "words_checked": 2048 << 20,
        "mismatches": 0,
        "examples": [],
        "seconds": 2189.0,
        "budget_seconds": 15360.0,
    }
    assert rv32im["jobs"]["completed"] == 10

    # Above the bar on thumbv6m: decided from the compare alone, no checks.
    assert thumbv6m["decision"]["outcome"] == "above-bar"
    assert thumbv6m["decision"]["fullcheck"] is None
    assert [j["kind"] for j in thumbv6m["job_results"]] == ["compare"]


def test_report_of_a_check_rejection(database):
    load(database)
    boards = report(database, CHECK_REJECTED)
    decision = boards["rv32im"]["decision"]
    assert decision["outcome"] == "check-failed" and decision["stage"] == "fullcheck:outputs"
    assert decision["reasons"] == [decision["reason"]]
    assert decision["fullcheck"]["verdict"] == "rejected"
    jobs = boards["rv32im"]["job_results"]
    (rejected,) = [j for j in jobs if j["check"] and j["check"]["verdict"] == "rejected"]
    assert rejected["check"]["mismatches"] == 1
    assert rejected["check"]["examples"] == [
        {"input": "bf317218", "got": "3f000001", "want": "3f000000"}
    ]
    # The shards the rejection stopped carry no result.
    cancelled = [j for j in jobs if j["state"] == "cancelled"]
    assert cancelled and all(j["check"] is None for j in cancelled)


def test_report_of_a_compare_rejection_and_of_work_in_progress(database):
    load(database)
    boards = report(database, COMPARE_REJECTED)
    decision = boards["overall"]["decision"]
    assert decision["outcome"] == "benchmark-failed" and decision["stage"] == "outputs:rv32im"
    assert decision["entry_S"] is None and decision["fullcheck"] is None
    (compare,) = boards["overall"]["job_results"]
    assert compare["compare"]["scores"] is None and compare["compare"]["boards"] is None
    assert set(compare["compare"]["images"]) == {"rv32im", "thumbv6m", "thumbv7em"}

    pending = report(database, IN_PROGRESS)
    assert {e["state"] for e in pending.values()} == {"pending"}
    (queued,) = pending["rv32im"]["job_results"]
    assert queued == {
        "kind": "compare",
        "function": None,
        "target": None,
        "chunks": None,
        "state": "queued",
        "compare": None,
        "check": None,
    }
    assert ok(database, f"/submissions/{GATE_REJECTED}/report")["evaluations"] == []


def test_a_source_that_discloses_a_current_champion_is_withheld(database):
    # The coordinator embargoes a dethroned source that contains a current champion's code.
    load(database)
    first = next(s["source_sha256"] for s in ROWS["submissions"] if s["id"] == FIRST)
    with database.owner.begin() as c:
        c.execute(
            sa.text(
                "INSERT INTO source_embargoes(source_hash,protects,containment) VALUES (:h,:h,1)"
            ),
            {"h": first},
        )
    response = eik_get(database, f"/submissions/{FIRST}/source")
    assert response.status_code == 403, response.text
    assert response.json()["reason_code"] == "SOURCE_WITHHELD"
    champions = ok(database, "/champions?board=overall")["champions"]
    assert not next(c for c in champions if c["submission"] == FIRST)["source_available"]
    assert not ok(database, f"/submissions/{FIRST}")["source_available"]
    with database.owner.begin() as c:
        c.exec_driver_sql("DELETE FROM source_embargoes")
    assert eik_get(database, f"/submissions/{FIRST}/source").status_code == 200


def test_source_of_a_former_champion_only(database):
    load(database)
    response = eik_get(database, f"/submissions/{FIRST}/source")
    assert response.status_code == 200, response.text
    assert response.content == source_of(FIRST)
    assert response.headers["content-type"] == "text/x-c; charset=utf-8"
    assert response.headers["content-disposition"] == 'attachment; filename="entry.c"'
    assert response.headers["cache-control"] == "no-store"
    for sid in (SECOND, CHECK_REJECTED, IN_PROGRESS):
        assert eik_get(database, f"/submissions/{sid}/source").status_code == 403, sid
