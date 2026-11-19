"""Exact integer kernels: the kernel competition routes under a third slug, with a C upload and
an instruction-count report, against the same schema and restricted platform role."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from conftest_api import build_settings
from test_torchlean_intake import database as database, engines as engines
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


# --- Reads ------------------------------------------------------------------------------------

CONFIG = "c" * 64
INCUMBENT = b"/* the shipped incumbent */\n"
ROOT = hashlib.sha256(INCUMBENT).hexdigest()
MEASUREMENT = "e" * 64
VERIFIER = "f" * 64
BOARDS = {
    "rv32im": {"rv32im": 1},
    "thumbv6m": {"thumbv6m": 1},
    "overall": {"rv32im": 0.5, "thumbv6m": 0.5},
}
FUNCTIONS = ("exp", "sigmoid", "tanh")


def source(db, content):
    sha = hashlib.sha256(content).hexdigest()
    execute(
        db,
        "INSERT INTO sources(sha256,content) VALUES (:sha,:content) ON CONFLICT DO NOTHING",
        sha=sha,
        content=content,
    )
    return sha


def submit(db, name, content=None, state="proved", stage=None, reason=None):
    sha = source(db, content if content is not None else name.encode())
    sid = execute(
        db,
        """INSERT INTO submissions(hotkey,source_sha256,intake_mode,state,stage,reason)
           VALUES (:name,:sha,'dev',:state,:stage,:reason) RETURNING id""",
        name=name,
        sha=sha,
        state=state,
        stage=stage,
        reason=reason,
    ).scalar_one()
    return sid, sha


def configure(db, kings=None):
    """`config_evidence` and `publication` as CONTRACT.md describes them."""
    source(db, INCUMBENT)
    fullcheck = {"shards_per_function": 8, "require_targets": ["rv32im"], "targetcheck_shards": 8}
    config = {
        "competition": EIK,
        "boards": {
            board: {
                "models": models,
                "reference": {"sha256": ROOT},
                "environments": {"eik-compare-x86": 1.0},
            }
            for board, models in BOARDS.items()
        },
        "environments": {
            "eik-compare-x86": {"identity": MEASUREMENT, "weight": 1.0},
            "eik-check": {"identity": VERIFIER, "weight": 1.0},
        },
        "admission": {"bar": 0.97, "guard": None},
        "fullcheck": fullcheck,
    }
    publication = {
        "version": "eik-top1-v1",
        "rewards": {
            "board_weights": {"rv32im": 0.25, "thumbv6m": 0.25, "overall": 0.5},
            "rank_shares": [1.0],
            "paused_policy": "treasury",
        },
        "boards": {
            board: {
                "config_hash": CONFIG,
                "initial_reference": ROOT,
                "paused": False,
                "kings": (kings or {}).get(board, []),
            }
            for board in BOARDS
        },
    }
    execute(
        db,
        """INSERT INTO coordinator_state(id,config_hash,config_evidence,publication,publication_hash)
           VALUES (1,:hash,CAST(:config AS jsonb),CAST(:pub AS jsonb),:hash)
           ON CONFLICT(id) DO UPDATE SET config_hash=excluded.config_hash,
           config_evidence=excluded.config_evidence,publication=excluded.publication,
           publication_hash=excluded.publication_hash""",
        hash=CONFIG,
        config=json.dumps(config),
        pub=json.dumps(publication),
    )
    for board in BOARDS:
        execute(
            db,
            "INSERT INTO board_progress(board,activation_boundary) VALUES (:b,0) "
            "ON CONFLICT DO NOTHING",
            b=board,
        )


MERGED = {
    "verdict": "passed",
    "functions": {fn: {"chunks_covered": 4096, "words_checked": 1 << 32} for fn in FUNCTIONS},
    "targets": {
        "rv32im": {fn: {"chunks_covered": 4096, "words_checked": 1 << 32} for fn in FUNCTIONS}
    },
    "required_targets": ["rv32im"],
}


def qualifies(board, refhash=ROOT):
    return {
        "outcome": "qualifies",
        "score": 0.5,
        "reason": None,
        "entry_S": 100.0,
        "champion_S": 200.0,
        "champion_sha256": refhash,
        "seed": 20261118,
        "fullcheck": MERGED,
    }


def evaluate(db, sid, board, *, decision=None, ref=None, refhash=ROOT, state="completed"):
    plan = {
        "kind": "eik-v1",
        "seed": 20261118,
        "champion_sha256": refhash,
        "takeover_ratio": 0.97,
        "measurement_identity": MEASUREMENT,
        "verifier_identity": VERIFIER,
        "fullcheck": {"shards_per_function": 8, "require_targets": ["rv32im"]},
    }
    return execute(
        db,
        """INSERT INTO evaluations(submission_id,board,generation,config_hash,
           reference_promotion_id,reference_hash,plan,state,decision,completed_at)
           VALUES (:sid,:board,1,:hash,:ref,:refhash,CAST(:plan AS jsonb),:state,
                   CAST(:decision AS jsonb),CASE WHEN :state='completed' THEN now() END)
           RETURNING id""",
        sid=sid,
        board=board,
        hash=CONFIG,
        ref=ref,
        refhash=refhash,
        plan=json.dumps(plan),
        state=state,
        decision=json.dumps(decision if decision is not None else qualifies(board, refhash))
        if state == "completed"
        else None,
    ).scalar_one()


def promote(db, sid, board, **kwargs):
    eid = evaluate(db, sid, board, **kwargs)
    return execute(
        db,
        "INSERT INTO promotions(board,evaluation_id) VALUES (:b,:e) RETURNING id",
        b=board,
        e=eid,
    ).scalar_one()


def job(db, evaluations, sha, request, *, seed, state="completed", result=None, environment=None):
    """One job linked to every evaluation in `evaluations`, as the coordinator plans it."""
    environment = environment or (
        "eik-compare-x86" if request["kind"] == "compare" else "eik-check"
    )
    jid = execute(
        db,
        """INSERT INTO benchmark_jobs(request_hash,request,candidate_hash,reference_hash,
           environment,lease_seconds,retry_seconds,state,result,owner,token,claimant,lease_until)
           VALUES (:hash,CAST(:request AS jsonb),:sha,:ref,:env,120,60,:state,
                   CAST(:result AS jsonb),
                   CASE WHEN :state='running' THEN 'SECRET' END,
                   CASE WHEN :state='running' THEN 'SECRET' END,
                   CASE WHEN :state='running' THEN 'SECRET' END,
                   CASE WHEN :state='running' THEN now()+interval '1 hour' END) RETURNING id""",
        hash=hashlib.sha256(json.dumps(request).encode()).hexdigest(),
        request=json.dumps(request),
        sha=sha,
        ref=request.get("reference_hash", sha),
        env=environment,
        state=state,
        result=json.dumps(result) if result is not None else None,
    ).scalar_one()
    for eid in evaluations:
        execute(
            db,
            "INSERT INTO evaluation_jobs(evaluation_id,job_id,environment,seed) "
            "VALUES (:e,:j,:env,:seed)",
            e=eid,
            j=jid,
            env=environment,
            seed=seed,
        )


FINGERPRINT = {"measurement": MEASUREMENT, "sandbox": "SECRET-SANDBOX", "verifier": VERIFIER}


def target_score(s, size, functions=True, **extra):
    row = {"S": s, "bytes": size, "size_factor": 1.05, **extra}
    if functions:
        row["functions"] = {
            fn: {"geo_mean_instr": 1000.0 + i, "tail_factor": 1.01, "private": "SECRET"}
            for i, fn in enumerate(FUNCTIONS)
        }
    return row


def compare_result(sha, refhash=ROOT):
    """`eik compare`'s report, shaped like build/prod-compare.json, with private extras."""
    measure = {
        "schema": "eik-report-1",
        "kind": "measure",
        "sha256": sha,
        "seed": 20261118,
        "inputs_sha256": "1" * 64,
        "hard_pool": {"source": "hardscan", "samples": 67108864, "secret": "SECRET"},
        "images": {"rv32im": "2" * 64, "thumbv6m": "3" * 64, "thumbv7em": "4" * 64},
        "bytes": {"rv32im": 16600, "thumbv6m": 15476, "thumbv7em": 13480},
        "fingerprint": FINGERPRINT,
        "sampled": True,
        "verdict": "passed",
        "stage": None,
        "reasons": [],
        "scores": {
            "rv32im": target_score(12326.18, 16600),
            "thumbv6m": target_score(17990.92, 15476),
            "thumbv7em": target_score(6830.42, 13480, reported_only=True),
            "overall": {"S": 14891.59},
        },
    }
    boards = {
        board: {
            "beats": True,
            "champion_S": 2 * entry,
            "champion_sha256": refhash,
            "entry_S": entry,
            "ratio": 0.5,
        }
        for board, entry in (("rv32im", 12326.18), ("thumbv6m", 17990.92), ("overall", 14891.59))
    }
    return {
        "schema": "eik-report-1",
        "kind": "compare",
        "competition": EIK,
        "sha256": sha,
        "seed": 20261118,
        "measure": measure,
        # The champions' own measurements: never published.
        "champions": {refhash: {"images": {"rv32im": "SECRET-CHAMPION-IMAGE"}}},
        "decision": {"kind": "decision", "boards": boards, "fingerprint": FINGERPRINT},
        "fingerprint": FINGERPRINT,
    }


def check_result(sha, fn, lo, hi, *, kind="fullcheck", target=None, rejected=False):
    result = {
        "schema": "eik-report-1",
        "kind": kind,
        "competition": EIK,
        "sha256": sha,
        "function": fn,
        "chunk_lo": lo,
        "chunk_hi": hi,
        "budget_seconds": 30720,
        "seconds": 4378.0,
        "fingerprint": FINGERPRINT,
        "exit": 0,
        "quick": False,
        "words_checked": (hi - lo) << 20,
        "mismatches": 0,
        "examples": [],
        "verdict": "passed",
        "stage": None,
        "reasons": [],
        "complete": False,
        "range_complete": True,
    }
    if target:
        result |= {"target": target, "image_sha256": "SECRET-IMAGE", "jobs": 8}
    if rejected:
        result |= {
            "verdict": "rejected",
            "stage": "fullcheck:outputs",
            "reasons": ["1 wrong outputs of exp"],
            "mismatches": 1,
            "examples": [{"input": "bf317218", "got": "3f000001", "want": "3f000000"}],
            "words_checked": 1 << 20,
            "seconds": 20.4,
            "range_complete": False,
        }
    return result


def eik_get(db, path, **settings):
    return post(db, path=path, method="get", slug=EIK, **settings)


def test_overview_names_entry_c_and_its_limit(database):
    configure(database)
    overview = eik_get(database, "")
    assert overview.status_code == 200, overview.text
    body = overview.json()
    assert body["slug"] == EIK and body["name"] == "Exact integer kernels"
    assert body["files"] == [{"name": "entry.c", "max_bytes": CAP}]
    assert body["max_file_bytes"] == CAP
    assert body["metric_definitions"][0]["label"] == "Score / champion"
    assert body["execution_limits"] == {}
    assert body["environment_limits"] == {"eik-compare-x86": {}, "eik-check": {}}
    assert set(body["boards"]) == set(BOARDS)
    # Tensor kernels and TorchLean still name Kernel.lean.
    tensor = post(database, path="", method="get", slug=TENSOR_KERNELS).json()
    assert tensor["files"][0]["name"] == "Kernel.lean"
    assert tensor["metric_definitions"][0]["label"] == "Time / reference"


def test_boards_leaderboard_champions_and_health_read_its_shapes(database):
    configure(database)
    boards = eik_get(database, "/boards")
    assert boards.status_code == 200, boards.text
    by_id = {b["id"]: b for b in boards.json()["boards"]}
    assert set(by_id) == set(BOARDS)
    assert by_id["overall"]["models"] == {"rv32im": 0.5, "thumbv6m": 0.5}
    assert by_id["overall"]["reward_share"] == 0.5
    assert by_id["rv32im"]["promotion_bar"] == 0.97 and by_id["rv32im"]["guard_limit"] is None
    assert by_id["rv32im"]["environments"] == [
        {"id": "eik-compare-x86", "weight": 1.0, "identity": MEASUREMENT}
    ]
    health = eik_get(database, "/health")
    assert health.status_code == 200 and health.json()["policy_ready"], health.text

    a, ah = submit(database, "first")
    pids = {board: promote(database, a, board) for board in BOARDS}
    configure(
        database,
        kings={
            b: [{"promotion_id": p, "submission_id": a, "hotkey": "first"}] for b, p in pids.items()
        },
    )
    b, _ = submit(database, "challenger")
    above = {
        "outcome": "above-bar",
        "score": 0.99,
        "reason": "not within 0.97 of the champion",
        "entry_S": 99.0,
        "champion_S": 100.0,
        "champion_sha256": ah,
        "seed": 1,
        "fullcheck": None,
    }
    evaluate(database, b, "overall", decision=above, ref=pids["overall"], refhash=ah)
    leaderboard = eik_get(database, "/leaderboard?board=overall")
    assert leaderboard.status_code == 200, leaderboard.text
    assert leaderboard.json()["reference"]["submission"] == a
    (challenger,) = leaderboard.json()["challengers"]
    assert challenger["ratio"] == 0.99 and challenger["admission"] == "above-bar"
    champions = eik_get(database, "/champions?board=overall")
    assert champions.status_code == 200, champions.text
    (champion,) = champions.json()["champions"]
    assert champion["current"] and champion["reward_rank"] == 1
    assert champion["competition_fraction"] == 0.5 and champion["ratio"] == 0.5
    # A current champion's source is never public.
    assert eik_get(database, f"/submissions/{a}/source").status_code == 403


def test_a_rejected_entry_lists_with_its_gate_stage(database):
    configure(database)
    sid, _ = submit(
        database,
        "rejected",
        state="rejected",
        stage="ast",
        reason="FloatingLiteral is not allowed (in eik_sigmoid); ...",
    )
    page = eik_get(database, "/submissions?state=rejected")
    assert page.status_code == 200, page.text
    (row,) = page.json()["submissions"]
    assert row["id"] == sid and row["stage"] == "ast" and row["processing_state"] == "rejected"
    report = eik_get(database, f"/submissions/{sid}/report")
    assert report.status_code == 200, report.text
    assert report.json()["evaluations"] == []


def test_report_projects_instruction_counts_and_checks(database):
    configure(database)
    sid, sha = submit(database, "entry", content=ENTRY)
    evaluations = {board: evaluate(database, sid, board) for board in BOARDS}
    every = list(evaluations.values())
    compare = {
        "kind": "compare",
        "identity": MEASUREMENT,
        "seed": 20261118,
        "candidate_hash": sha,
        "reference_hash": ROOT,
        "comparisons": {board: {"reference_hash": ROOT} for board in BOARDS},
    }
    job(database, every, sha, compare, seed=20261118, result=compare_result(sha))
    host = {"kind": "fullcheck", "identity": VERIFIER, "function": "exp", "chunks": [0, 512]}
    job(
        database,
        every,
        sha,
        host | {"reference_hash": sha},
        seed=0,
        result=check_result(sha, "exp", 0, 512),
    )
    on_target = host | {"kind": "targetcheck", "target": "rv32im", "chunks": [512, 1024]}
    job(
        database,
        every,
        sha,
        on_target | {"reference_hash": sha},
        seed=(1 << 24) + 512,
        result=check_result(sha, "exp", 512, 1024, kind="targetcheck", target="rv32im"),
    )
    queued = host | {"function": "tanh", "chunks": [0, 512]}
    job(database, every, sha, queued | {"reference_hash": sha}, seed=2 << 20, state="queued")

    detail = eik_get(database, f"/submissions/{sid}")
    assert detail.status_code == 200, detail.text
    decision = detail.json()["evaluations"][0]["decision"]
    assert decision["outcome"] == "qualifies" and decision["score"] == 0.5

    response = eik_get(database, f"/submissions/{sid}/report")
    assert response.status_code == 200, response.text
    for private in ("SECRET", "champions", "fingerprint", "hard_pool", "rounds", "image_sha256"):
        assert private not in response.text, private
    reports = {e["board"]: e for e in response.json()["evaluations"]}
    assert set(reports) == set(BOARDS)
    overall = reports["overall"]
    assert overall["seed"] == 20261118
    assert overall["jobs"]["completed"] == 3 and overall["jobs"]["queued"] == 1
    assert overall["decision"] == qualifies("overall") | {"stage": None, "reasons": None}
    assert overall["decision"]["fullcheck"]["targets"]["rv32im"]["exp"]["chunks_covered"] == 4096

    first, host_shard, target_shard, waiting = overall["job_results"]
    assert (first["kind"], first["state"], first["check"]) == ("compare", "completed", None)
    measured = first["compare"]
    assert measured["inputs_sha256"] == "1" * 64
    assert measured["images"] == {"rv32im": "2" * 64, "thumbv6m": "3" * 64, "thumbv7em": "4" * 64}
    assert measured["scores"]["overall"] == {
        "S": 14891.59,
        "bytes": None,
        "size_factor": None,
        "functions": None,
        "reported_only": False,
    }
    rv = measured["scores"]["rv32im"]
    assert rv["S"] == 12326.18 and rv["bytes"] == 16600 and not rv["reported_only"]
    assert rv["functions"]["exp"] == {"geo_mean_instr": 1000.0, "tail_factor": 1.01}
    assert measured["scores"]["thumbv7em"]["reported_only"] is True
    assert measured["boards"]["rv32im"] == {
        "entry_S": 12326.18,
        "champion_S": 24652.36,
        "champion_sha256": ROOT,
        "ratio": 0.5,
        "beats": True,
    }

    assert host_shard["kind"] == "fullcheck" and host_shard["compare"] is None
    assert (host_shard["function"], host_shard["target"], host_shard["chunks"]) == (
        "exp",
        None,
        [0, 512],
    )
    assert host_shard["check"] == {
        "verdict": "passed",
        "stage": None,
        "reasons": [],
        "words_checked": 512 << 20,
        "mismatches": 0,
        "examples": [],
        "seconds": 4378.0,
        "budget_seconds": 30720.0,
    }
    assert target_shard["kind"] == "targetcheck" and target_shard["target"] == "rv32im"
    assert target_shard["chunks"] == [512, 1024] and target_shard["check"]["verdict"] == "passed"
    assert waiting["state"] == "queued" and waiting["check"] is None and waiting["compare"] is None


def test_report_of_a_failed_check_names_the_wrong_outputs(database):
    configure(database)
    sid, sha = submit(database, "wrong", content=ENTRY + b"/* wrong */\n")
    failed = {
        "outcome": "check-failed",
        "score": None,
        "reason": "1 wrong outputs of exp",
        "stage": "fullcheck:outputs",
        "reasons": ["1 wrong outputs of exp"],
        "entry_S": 100.0,
        "champion_S": 200.0,
        "champion_sha256": ROOT,
        "seed": 20261118,
        "fullcheck": {
            "verdict": "rejected",
            "functions": {},
            "targets": None,
            "required_targets": None,
        },
    }
    eid = evaluate(database, sid, "rv32im", decision=failed)
    request = {
        "kind": "fullcheck",
        "function": "exp",
        "chunks": [3059, 3060],
        "reference_hash": sha,
    }
    job(
        database,
        [eid],
        sha,
        request,
        seed=3059,
        result=check_result(sha, "exp", 3059, 3060, rejected=True),
    )
    response = eik_get(database, f"/submissions/{sid}/report")
    assert response.status_code == 200, response.text
    (evaluation,) = response.json()["evaluations"]
    assert evaluation["decision"]["outcome"] == "check-failed"
    assert evaluation["decision"]["stage"] == "fullcheck:outputs"
    assert evaluation["decision"]["fullcheck"]["verdict"] == "rejected"
    (shard,) = evaluation["job_results"]
    assert shard["check"]["verdict"] == "rejected" and shard["check"]["mismatches"] == 1
    assert shard["check"]["examples"] == [
        {"input": "bf317218", "got": "3f000001", "want": "3f000000"}
    ]


def test_report_of_a_rejected_compare_has_no_board_calls(database):
    configure(database)
    sid, sha = submit(database, "slow", content=ENTRY + b"/* slow */\n")
    failed = {
        "outcome": "benchmark-failed",
        "score": None,
        "reason": "run:rv32im",
        "stage": "run:rv32im",
        "reasons": ["timeout"],
        "fullcheck": None,
    }
    eid = evaluate(database, sid, "overall", decision=failed)
    result = compare_result(sha)
    del result["decision"]
    result["measure"] |= {"verdict": "rejected", "stage": "run:rv32im", "reasons": ["timeout"]}
    del result["measure"]["scores"]
    request = {"kind": "compare", "seed": 7, "reference_hash": ROOT}
    job(database, [eid], sha, request, seed=7, result=result)
    response = eik_get(database, f"/submissions/{sid}/report")
    assert response.status_code == 200, response.text
    (evaluation,) = response.json()["evaluations"]
    assert evaluation["decision"]["entry_S"] is None and evaluation["decision"]["fullcheck"] is None
    (compare,) = evaluation["job_results"]
    assert compare["compare"]["scores"] is None and compare["compare"]["boards"] is None
    assert compare["compare"]["images"]["rv32im"] == "2" * 64


def test_a_former_champion_source_is_served_as_entry_c(database):
    configure(database)
    a, ah = submit(database, "first", content=ENTRY)
    pids = {board: promote(database, a, board) for board in BOARDS}
    b, _ = submit(database, "second")
    for board in BOARDS:
        assert eik_get(database, f"/submissions/{a}/source").status_code == 403
        promote(database, b, board, ref=pids[board], refhash=ah)
    response = eik_get(database, f"/submissions/{a}/source")
    assert response.status_code == 200, response.text
    assert response.content == ENTRY
    assert response.headers["content-type"] == "text/x-c; charset=utf-8"
    assert response.headers["content-disposition"] == 'attachment; filename="entry.c"'
    assert response.headers["cache-control"] == "no-store"
    # The current champion's is not.
    assert eik_get(database, f"/submissions/{b}/source").status_code == 403
