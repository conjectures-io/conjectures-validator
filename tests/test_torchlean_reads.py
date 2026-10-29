"""Dashboard history and source privacy against the restricted real PostgreSQL role."""

import asyncio
import hashlib
import json

import pytest
from sqlalchemy import text

from test_torchlean_intake import database as database, engines as engines
from test_torchlean_intake import execute, post, service

CONFIG = "c" * 64
OLD_CONFIG = "d" * 64
ROOT = hashlib.sha256(b"shipped").hexdigest()


def source(db, content):
    sha = hashlib.sha256(content).hexdigest()
    execute(
        db,
        "INSERT INTO sources(sha256,content) VALUES (:sha,:content) ON CONFLICT DO NOTHING",
        sha=sha,
        content=content,
    )
    return sha


def submit(db, name, state="proved", content=None):
    sha = source(db, content if content is not None else name.encode())
    sid = execute(
        db,
        """INSERT INTO submissions(hotkey,source_sha256,intake_mode,state)
                       VALUES (:name,:sha,'dev',:state) RETURNING id""",
        name=name,
        sha=sha,
        state=state,
    ).scalar_one()
    return sid, sha


def configure(db, kings=None, *, boards=None, config_hash=CONFIG, pause=False):
    source(db, b"shipped")
    names = boards if boards is not None else ["mlp", "resnet"]
    config = {
        "boards": {b: {"models": {b: 1}, "reference": {"sha256": ROOT}} for b in names},
        "environments": {
            "amd64": {"weight": 0.6, "identity": "a" * 64},
            "arm64": {"weight": 0.4, "identity": "b" * 64},
        },
        "admission": {"bar": 0.97, "guard": 1.1, "draws": 2000, "quantile": 0.05, "min_rounds": 5},
        "timing": {"rounds": 11, "inner": 2, "cell_ms": 100},
    }
    publication = {
        "version": "tensor-top2-v1",
        "rewards": {
            "board_weights": {b: 1 / len(names) for b in names},
            "rank_shares": [0.8, 0.2],
            "paused_policy": "treasury",
        },
        "boards": {
            b: {
                "config_hash": config_hash,
                "initial_reference": ROOT,
                "paused": pause,
                "kings": (kings or {}).get(b, []),
            }
            for b in names
        },
    }
    execute(
        db,
        """INSERT INTO coordinator_state(id,config_hash,config_evidence,publication,publication_hash)
                  VALUES (1,:hash,CAST(:config AS jsonb),CAST(:pub AS jsonb),:hash)
                  ON CONFLICT(id) DO UPDATE SET config_hash=excluded.config_hash,
                  config_evidence=excluded.config_evidence,publication=excluded.publication,
                  publication_hash=excluded.publication_hash""",
        hash=config_hash,
        config=json.dumps(config),
        pub=json.dumps(publication),
    )
    for board in names:
        execute(
            db,
            "INSERT INTO board_progress(board,activation_boundary) VALUES (:b,0) "
            "ON CONFLICT DO NOTHING",
            b=board,
        )


def evaluate(
    db,
    sid,
    board="mlp",
    ref=None,
    refhash=ROOT,
    *,
    state="completed",
    config=CONFIG,
    score=0.94,
    outcome="qualifies",
    generation=1,
):
    plan = {
        "cells": {f"{board}:b1:normal": 1},
        "timing": {"rounds": 11, "inner": 2},
        "admission": {"bar": 0.97, "guard": 1.1, "draws": 2000, "quantile": 0.05, "min_rounds": 5},
    }
    decision = {
        "score": score,
        "gain_lower": 0.01,
        "guard_passed": True,
        "outcome": outcome,
        "strata": [
            {
                "environment": "amd64",
                "seed": 42,
                "score": score,
                "guards": {f"{board}:b1:normal": score},
            }
        ],
    }
    return execute(
        db,
        """INSERT INTO evaluations(submission_id,board,generation,config_hash,
                   reference_promotion_id,reference_hash,plan,state,decision,completed_at)
                   VALUES (:sid,:board,:gen,:hash,:ref,:refhash,CAST(:plan AS jsonb),:state,
                           CAST(:decision AS jsonb),CASE WHEN :state='completed' THEN now() END)
                   RETURNING id""",
        sid=sid,
        board=board,
        gen=generation,
        hash=config,
        ref=ref,
        refhash=refhash,
        plan=json.dumps(plan),
        state=state,
        decision=json.dumps(decision) if state == "completed" else None,
    ).scalar_one()


def promote(db, sid, board="mlp", **kwargs):
    eid = evaluate(db, sid, board, **kwargs)
    return execute(
        db,
        "INSERT INTO promotions(board,evaluation_id) VALUES (:b,:e) RETURNING id",
        b=board,
        e=eid,
    ).scalar_one()


def king(pid, sid):
    return {"promotion_id": pid, "submission_id": sid, "hotkey": "hot"}


def get(db, path, **settings):
    return post(db, path=path, method="get", **settings)


def test_empty_policy_and_intake_remain_readable(database):
    sid, _ = submit(database, "queued", "queued")
    health = get(database, "/health")
    assert health.status_code == 200, health.text
    assert health.json()["queued"] == 1 and not health.json()["policy_ready"]
    assert get(database, "/boards").json()["boards"] == []
    assert get(database, "").json()["boards"] == []
    assert get(database, f"/submissions/{sid}").json()["source_available"] is False
    assert get(database, f"/submissions/{sid}/source").status_code == 403


def test_board_rules_and_shipped_reference(database):
    configure(database)
    boards = get(database, "/boards")
    assert boards.status_code == 200, boards.text
    board = boards.json()["boards"][0]
    assert board["id"] == "mlp" and board["reward_share"] == 0.5
    assert board["environments"][0]["weight"] == 0.6
    assert board["config_hash"] == CONFIG
    health = get(database, "/health").json()
    assert health["rank_shares"] == [0.8, 0.2] and health["competition_share"] is None
    response = get(database, "/leaderboard?board=mlp")
    assert response.status_code == 200, response.text
    assert response.json()["reference"] == {
        "promotion_id": None,
        "submission": None,
        "source_sha256": ROOT,
        "shipped": True,
    }
    assert response.json()["challengers"] == []


def test_history_keeps_reference_board_and_config_separate(database):
    configure(database)
    a, ah = submit(database, "old-king")
    pa = promote(database, a)
    b, bh = submit(database, "new-king")
    pb = promote(database, b, ref=pa, refhash=ah)
    c, _ = submit(database, "slow")
    evaluate(database, c, ref=pa, refhash=ah, score=0.99, outcome="above-bar")
    d, _ = submit(database, "new-challenge")
    evaluate(database, d, ref=pb, refhash=bh)
    old, _ = submit(database, "other-config")
    evaluate(database, old, ref=pa, refhash=ah, config=OLD_CONFIG)
    configure(database, {"mlp": [king(pb, b), king(pa, a)]})
    current = get(database, "/leaderboard?board=mlp").json()
    assert current["reference"]["promotion_id"] == pb
    assert [r["submission"] for r in current["challengers"]] == [d]
    historical = get(
        database, f"/leaderboard?board=mlp&promotion_id={pa}&config_hash={CONFIG}"
    ).json()
    assert [r["submission"] for r in historical["challengers"]] == [b, c]
    assert historical["challengers"][0]["promotion_id"] == pb
    assert historical["challengers"][1]["admission"] == "above-bar"
    latest = get(database, f"/leaderboard?board=mlp&promotion_id={pa}").json()
    assert latest["config_hash"] == OLD_CONFIG
    assert [r["submission"] for r in latest["challengers"]] == [old]
    assert get(database, f"/leaderboard?board=resnet&promotion_id={pa}").status_code == 404


def test_current_config_change_does_not_relabel_old_results(database):
    configure(database)
    a, ah = submit(database, "king")
    pa = promote(database, a)
    b, _ = submit(database, "old-challenge")
    evaluate(database, b, ref=pa, refhash=ah)
    configure(database, {"mlp": [king(pa, a)]}, config_hash=OLD_CONFIG)
    response = get(database, "/leaderboard?board=mlp").json()
    assert response["config_hash"] == OLD_CONFIG and response["challengers"] == []


def test_champion_ranks_disqualification_and_pause(database):
    configure(database)
    a, ah = submit(database, "old")
    pa = promote(database, a)
    b, bh = submit(database, "new")
    pb = promote(database, b, ref=pa, refhash=ah)
    configure(database, {"mlp": [king(pb, b), king(pa, a)]}, pause=True)
    data = get(database, "/champions?board=mlp").json()["champions"]
    assert [r["reward_rank"] for r in data] == [1, 2]
    assert data[0]["current"] and data[0]["competition_fraction"] == 0
    assert data[1]["rank_share"] == 0.2
    execute(
        database,
        """INSERT INTO source_disqualifications(source_hash,active,reason,actor)
                         VALUES (:sha,true,'operator reason','operator')""",
        sha=bh,
    )
    data = get(database, "/champions?board=mlp").json()["champions"]
    assert data[0]["source_disqualified"] and data[0]["reward_rank"] is None
    assert data[1]["current"]


def test_source_only_after_last_board_is_lost_and_by_hash(database):
    configure(database)
    a, ah = submit(database, "first")
    pa = promote(database, a)
    par = promote(database, a, "resnet")
    b, bh = submit(database, "second")
    promote(database, b, ref=pa, refhash=ah)
    alias, _ = submit(database, "different-hotkey", content=b"first")
    for sid in [a, alias, b]:
        response = get(database, f"/submissions/{sid}/source")
        assert response.status_code == 403, response.text
    promote(database, b, "resnet", ref=par, refhash=ah)
    response = get(database, f"/submissions/{a}/source")
    assert response.status_code == 200 and response.content == b"first"
    assert response.headers["cache-control"] == "no-store"
    assert get(database, f"/submissions/{alias}/source").content == b"first"
    # Falling back to a historical champion makes its source private again.
    execute(
        database,
        "UPDATE promotions SET disqualified_at=now() WHERE evaluation_id IN "
        "(SELECT id FROM evaluations WHERE submission_id=:id)",
        id=b,
    )
    assert get(database, f"/submissions/{a}/source").status_code == 403
    plain, _ = submit(database, "never-champion")
    assert get(database, f"/submissions/{plain}/source").status_code == 403


def job(db, eid, sha, *, environment="amd64", state="completed", request=None, error=None):
    result = {
        "provenance": {"identity": "a" * 64, "machine": "PRIVATE_HOST_PATH"},
        "repetitions": {"mlp:b1:normal": 10},
        "rounds": [
            {
                "reference_first": True,
                "cells": {
                    "mlp:b1:normal": {
                        "reference_ns": 100,
                        "candidate_ns": 94,
                        "reference_checksum": 7,
                        "candidate_checksum": 7,
                        "private": "SECRET",
                    }
                },
            }
        ],
        "token": "SECRET",
    }
    jid = execute(
        db,
        """INSERT INTO benchmark_jobs(request_hash,request,candidate_hash,reference_hash,
                 environment,lease_seconds,retry_seconds,state,result,error,owner,token,claimant,
                 lease_until)
                 VALUES (:hash,CAST(:request AS jsonb),:hash,:ref,:env,120,60,:state,CAST(:result AS jsonb),
                 CAST(:error AS jsonb),
                 CASE WHEN :state='running' THEN 'SECRET' END,
                 CASE WHEN :state='running' THEN 'SECRET' END,
                 CASE WHEN :state='running' THEN 'SECRET' END,
                 CASE WHEN :state='running' THEN now()+interval '1 hour' END) RETURNING id""",
        hash=sha,
        ref=ROOT,
        env=environment,
        state=state,
        result=json.dumps(result) if state == "completed" else None,
        request=json.dumps(request or {}),
        error=json.dumps(error) if error is not None else None,
    ).scalar_one()
    execute(
        db,
        "INSERT INTO evaluation_jobs(evaluation_id,job_id,environment,seed) VALUES (:e,:j,:env,42)",
        e=eid,
        j=jid,
        env=environment,
    )


def test_submission_list_pages_newest_first_with_signed_cursors(database):
    configure(database)
    ids = [submit(database, f"entry-{i}")[0] for i in range(5)]
    queued = submit(database, "waiting-proof", "queued")[0]
    first = get(database, "/submissions?limit=2")
    assert first.status_code == 200, first.text
    assert [s["id"] for s in first.json()["submissions"]] == [queued, ids[4]]
    cursor = first.json()["next_cursor"]
    second = get(database, f"/submissions?limit=2&cursor={cursor}").json()
    assert [s["id"] for s in second["submissions"]] == [ids[3], ids[2]]
    last = get(database, f"/submissions?limit=2&cursor={second['next_cursor']}").json()
    assert [s["id"] for s in last["submissions"]] == [ids[1], ids[0]]
    assert last["next_cursor"] is None
    # A filtered page is full when later rows are filtered out, and ends where the filter does.
    waiting = get(database, "/submissions?state=waiting&limit=4").json()
    assert [s["id"] for s in waiting["submissions"]] == ids[:0:-1]
    rest = get(database, f"/submissions?state=waiting&limit=4&cursor={waiting['next_cursor']}")
    assert [s["id"] for s in rest.json()["submissions"]] == [ids[0]]
    assert rest.json()["next_cursor"] is None
    assert get(database, "/submissions").json()["next_cursor"] is None
    tampered = get(database, f"/submissions?cursor={cursor[:-2]}xx")
    assert tampered.status_code == 400 and tampered.json()["reason_code"] == "INVALID_CURSOR"
    assert get(database, "/submissions?limit=101").status_code == 422


def test_progress_reports_and_queue_filters_without_worker_secrets(database):
    configure(database)
    sid, sha = submit(database, "candidate")
    assert get(database, f"/submissions/{sid}").json()["processing_state"] == "waiting"
    eid = evaluate(database, sid, state="pending")
    job(database, eid, sha)
    job(database, eid, sha, environment="arm64", state="running")
    detail = get(database, f"/submissions/{sid}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["processing_state"] == "benchmarking"
    assert detail.json()["evaluations"][0]["jobs"] == {
        "queued": 0,
        "running": 1,
        "completed": 1,
        "failed": 0,
        "cancelled": 0,
    }
    assert get(database, "/submissions?state=benchmarking").json()["submissions"][0]["id"] == sid
    assert get(database, "/submissions?state=queued").json()["submissions"] == []
    response = get(database, f"/submissions/{sid}/report")
    assert response.status_code == 200, response.text
    assert "SECRET" not in response.text and "PRIVATE_HOST_PATH" not in response.text
    measured = response.json()["evaluations"][0]["measurements"]
    assert measured[0]["rounds"][0]["cells"]["mlp:b1:normal"]["candidate_ns"] == 94
    assert measured[1]["rounds"] is None
    assert response.json()["evaluations"][0]["decision"] is None


@pytest.mark.parametrize(
    "path",
    [
        "/submissions/9999",
        "/submissions/9999/report",
        "/submissions/9999/source",
        "/champions?board=missing",
        "/leaderboard?board=missing",
    ],
)
def test_missing_resources(database, path):
    assert get(database, path).status_code == 404


def test_missing_read_grants_return_503(database):
    with database.owner.begin() as c:
        c.execute(text("REVOKE SELECT(config_evidence) ON coordinator_state FROM platform_api"))
    try:
        response = get(database, "/boards")
        assert response.status_code == 503, response.text
    finally:
        execute(database, "GRANT SELECT(config_evidence) ON coordinator_state TO platform_api")


def test_one_read_only_snapshot_survives_concurrent_promotion(database, monkeypatch):
    from submission_api import torchlean_reads

    configure(database)
    sid, sha = submit(database, "king")
    pid = promote(database, sid)
    configure(database, {"mlp": [king(pid, sid)]})
    original = torchlean_reads.state

    async def state_and_change(session):
        assert await session.scalar(text("SHOW transaction_isolation")) == "repeatable read"
        assert await session.scalar(text("SHOW transaction_read_only")) == "on"
        snap = await original(session)
        execute(database, "UPDATE promotions SET disqualified_at=now() WHERE id=:id", id=pid)
        return snap

    monkeypatch.setattr(torchlean_reads, "state", state_and_change)
    response = get(database, "/champions?board=mlp")
    assert response.status_code == 200, response.text
    assert response.json()["champions"][0]["current"] is True


def test_application_dispatch_and_validation(database):
    from submission_api.app import create_app
    from submission_api.dependencies import get_services
    from conftest_api import build_settings
    from httpx import ASGITransport, AsyncClient

    async def run():
        stub, engines = service(database)
        services = stub.dependency_overrides[get_services]()
        app = create_app(build_settings(), services=services)
        app.dependency_overrides[get_services] = lambda: services
        try:
            async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
                for path in ["/health", "/boards", "/submissions"]:
                    response = await client.get("/v1/competitions/torchlean" + path)
                    assert response.status_code == 200, response.text
                assert (
                    await client.get("/v1/competitions/torchlean/submissions/0")
                ).status_code == 400
                assert (
                    await client.get("/v1/competitions/torchlean/submissions?state=invalid")
                ).status_code == 400
                assert (await client.get("/v1/competitions/torchlean/pareto")).status_code == 404
        finally:
            for engine in engines:
                await engine.dispose()

    asyncio.run(run())


def test_report_preserves_stored_admission_instead_of_recomputing(database):
    configure(database)
    sid, sha = submit(database, "stored-score")
    eid = evaluate(database, sid, score=0.96, outcome="not-significant")
    job(database, eid, sha)
    response = get(database, f"/submissions/{sid}/report")
    assert response.status_code == 200, response.text
    evaluation = response.json()["evaluations"][0]
    assert evaluation["decision"]["score"] == 0.96
    assert evaluation["decision"]["outcome"] == "not-significant"
    assert evaluation["decision"]["gain_lower"] == 0.01
    assert evaluation["measurements"][0]["score"] == 0.96
    assert evaluation["measurements"][0]["guards"] == {"mlp:b1:normal": 0.96}


def test_report_names_the_workloads_a_multi_seed_job_timed(database):
    configure(database)
    sid, sha = submit(database, "multi-seed")
    eid = evaluate(database, sid)
    plan = [
        {"id": "s0", "seed": 11, "profile": "typical", "weight": 0.25, "private": "SECRET"},
        {"id": "s1", "seed": 12, "profile": "typical", "weight": 0.25},
    ]
    job(database, eid, sha, request={"workloads": plan, "cells": ["mlp:b1:normal"]})
    sid2, sha2 = submit(database, "single-seed")
    job(database, evaluate(database, sid2), sha2)
    response = get(database, f"/submissions/{sid}/report")
    assert response.status_code == 200, response.text
    measurement = response.json()["evaluations"][0]["measurements"][0]
    assert measurement["workloads"] == [
        {"id": "s0", "seed": 11, "profile": "typical", "weight": 0.25},
        {"id": "s1", "seed": 12, "profile": "typical", "weight": 0.25},
    ]
    assert "SECRET" not in response.text
    single = get(database, f"/submissions/{sid2}/report").json()
    assert single["evaluations"][0]["measurements"][0]["workloads"] is None


def decide(db, eid, **decision):
    # The decision exactly as the coordinator stores it, with no bootstrap strata.
    execute(
        db,
        "UPDATE evaluations SET decision=CAST(:d AS jsonb) WHERE id=:id",
        id=eid,
        d=json.dumps(decision),
    )


TIMEOUT = {
    "phase": "benchmark",
    "limit": "budget",
    "running": "reference",
    "allowance_seconds": 25.7,
    "budget_seconds": 1200,
    "elapsed_seconds": 1199.9,
    "used_seconds": {"reference": 410.2, "candidate": 789.7},
    "cell": "mlp:b1:normal",
    "cells_finished": 0,
    "cells_total": 1,
}


def test_a_timed_out_entry_reports_its_timeout_and_what_it_measured(database):
    configure(database)
    sid, sha = submit(database, "too-slow")
    eid = evaluate(database, sid)
    summary = TIMEOUT | {"environment": "amd64", "seed": 42, "rounds_completed": 1}
    decide(
        database,
        eid,
        outcome="timed-out",
        reason="benchmark-timeout: …",
        score=None,
        timeout=summary | {"x": "SECRET"},
    )
    observation = {
        "reference_ns": 100,
        "candidate_ns": 300,
        "reference_checksum": 7,
        "candidate_checksum": 7,
    }
    error = {
        "kind": "candidate",
        "reason": "benchmark-timeout: PRIVATE_HOST_PATH",
        "timeout": TIMEOUT | {"side": "candidate", "finished": {}, "private": "SECRET"},
        "partial": {
            "calibration": {"mlp:b1:normal": 101},
            "repetitions": {"mlp:b1:normal": 10},
            "rounds": [{"reference_first": True, "cells": {"mlp:b1:normal": observation}}],
            "round_in_progress": {"candidate": {"mlp:b1:normal": [300, 7]}},
            "rounds_planned": 11,
            "private": "SECRET",
        },
    }
    job(database, eid, sha, state="failed", error=error)
    detail = get(database, f"/submissions/{sid}")
    assert detail.status_code == 200, detail.text
    decision = detail.json()["evaluations"][0]["decision"]
    assert decision["outcome"] == "timed-out" and decision["score"] is None
    assert decision["timeout"] == summary | {"rounds_planned": None}
    response = get(database, f"/submissions/{sid}/report")
    assert response.status_code == 200, response.text
    assert "SECRET" not in response.text and "PRIVATE_HOST_PATH" not in response.text
    measured = response.json()["evaluations"][0]["measurements"][0]
    assert (measured["state"], measured["error_kind"]) == ("failed", "candidate")
    assert measured["timeout"] == TIMEOUT | {
        "environment": None,
        "seed": None,
        "rounds_completed": None,
        "rounds_planned": None,
    }
    assert measured["calibration"] == {"mlp:b1:normal": 101}
    assert measured["repetitions"] == {"mlp:b1:normal": 10}
    assert measured["rounds"][0]["cells"]["mlp:b1:normal"]["candidate_ns"] == 300
    assert measured["round_in_progress"] == {
        "candidate": {"mlp:b1:normal": {"ns": 300, "checksum": 7}}
    }
    assert measured["finished"] == {}
    assert measured["evidence_truncated"] is False


def test_a_failure_without_evidence_reports_none(database):
    configure(database)
    sid, sha = submit(database, "crashed")
    eid = evaluate(database, sid)
    decide(database, eid, outcome="benchmark-failed", reason="crash", score=None)
    job(
        database,
        eid,
        sha,
        state="failed",
        error={"kind": "candidate", "reason": "crash", "truncated": True},
    )
    response = get(database, f"/submissions/{sid}/report")
    assert response.status_code == 200, response.text
    evaluation = response.json()["evaluations"][0]
    assert evaluation["decision"]["timeout"] is None
    measured = evaluation["measurements"][0]
    assert measured["rounds"] is None and measured["timeout"] is None
    assert measured["round_in_progress"] is None and measured["finished"] is None
    assert measured["evidence_truncated"] is True


def test_a_paid_board_missing_from_the_weights_reports_no_fraction(database):
    configure(database)
    a, _ = submit(database, "unweighted-king")
    pa = promote(database, a)
    configure(database, {"mlp": [king(pa, a)]})
    execute(
        database,
        "UPDATE coordinator_state SET publication=publication #- '{rewards,board_weights,mlp}'",
    )
    response = get(database, "/champions?board=mlp")
    assert response.status_code == 200, response.text
    (champion,) = response.json()["champions"]
    assert champion["reward_rank"] == 1 and champion["competition_fraction"] is None


def test_removed_board_history_remains_readable(database):
    configure(database)
    a, ah = submit(database, "retired-board-king")
    pa = promote(database, a)
    b, _ = submit(database, "retired-board-challenger")
    evaluate(database, b, ref=pa, refhash=ah)
    configure(database, boards=["resnet"])
    champions = get(database, "/champions?board=mlp")
    assert champions.status_code == 200, champions.text
    assert champions.json()["active"] is False
    assert champions.json()["champions"][0]["rank_share"] is None
    response = get(database, f"/leaderboard?board=mlp&promotion_id={pa}")
    assert response.status_code == 200, response.text
    assert response.json()["challengers"][0]["submission"] == b


def test_operator_exclusion_is_not_reported_as_waiting_for_workers(database):
    configure(database)
    sid, sha = submit(database, "excluded")
    execute(
        database,
        """INSERT INTO source_disqualifications(source_hash,active,reason,actor)
                         VALUES (:sha,true,'operator reason','operator')""",
        sha=sha,
    )
    response = get(database, f"/submissions/{sid}")
    assert response.status_code == 200, response.text
    assert response.json()["proof_state"] == "proved"
    assert response.json()["processing_state"] == "disqualified"
    assert get(database, "/submissions?state=disqualified").json()["submissions"][0]["id"] == sid
