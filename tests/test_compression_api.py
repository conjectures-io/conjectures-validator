"""Contract tests for the read adapter, without running scoring in the API."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from submission_api import compression_views as view
from submission_api import compression_store as store
from submission_api.competitions import Competition, CompetitionRegistry
from submission_api.dependencies import get_services, get_competition_session
from submission_api.errors import ApiError, api_error_handler
from submission_api.routers import competition_reads as reads, competitions as intake


def point(sid=1, hotkey="miner", outcome="passed", ratio=32, time=1):
    return {
        "submission": {
            "id": sid,
            "hotkey": hotkey,
            "baseline_key": "lazy" if hotkey is None else None,
            "baseline_active": hotkey is None,
            "submitted_at": "2026-09-24T00:00:00+00:00",
            "state": "accepted",
            "aggregation_id": sid,
            "admission_check_id": sid,
            "digest": "a" * 64,
            "source_sha256": "b" * 64,
            "static_verified_at": "2026-09-24T00:00:00+00:00",
            "lean_verified_at": "2026-09-24T00:00:00+00:00",
        },
        "aggregation": {
            "id": sid,
            "raw_bytes": 1000,
            "bytes": 320,
            "parse_seconds": 1.2,
            "compression_seconds": 2,
            "statistics": {
                "relative_timing": {"time_ratio": time},
                "compression": {"ratio_pct": ratio},
                "intervals": {"balanced_time_ratio": [time * 0.98, time * 1.02]},
                "confidence": 0.95,
                "method": "recorded-bootstrap",
                "draws": 2000,
            },
        },
        "admission": {
            "outcome": outcome,
            "reason_code": "recorded-reason",
            "policy_version": "fixed-corpus-speed-bounds-v2",
            "reference": None,
        },
    }


def score(sid, hotkey, weight, reason=None):
    return {
        "submission_id": sid,
        "hotkey": hotkey,
        "pareto_weight": weight,
        "improvement_weight": 0.0,
        "combined_weight": weight,
        "payable_weight": weight if reason is None else 0.0,
        "on_frontier": True,
        "burn_reason": reason,
    }


@pytest.fixture
def setup(monkeypatch):
    snap = {
        "id": 42,
        "created_at": "2026-09-24T00:00:00Z",
        "dry_run": True,
        "accepted": False,
        "api_snapshot": {
            "schema_version": 1,
            "policy": {
                "version": "test",
                "competition_share": 0.2,
                "max_balanced_time_ratio": 10.0,
                "max_mean_file_compression_pct": 40.0,
            },
            "items": [point(1, "a"), point(2, "b"), point(3, None)],
        },
        "scores": [score(1, "a", 0.1), score(2, "b", 0.3), score(3, None, 0.6, "baseline")],
    }
    state = {"snap": snap, "latest": 42}

    async def snapshot(session, sid=None):
        if sid is not None and sid != 42:
            return None
        return deepcopy(state["snap"])

    async def compatible(session):
        pass

    async def get_submission(session, sid):
        return next(
            (
                deepcopy(i["submission"])
                for i in snap["api_snapshot"]["items"]
                if i["submission"]["id"] == sid
            ),
            None,
        )

    async def evidence(session, sub):
        return deepcopy(snap["api_snapshot"]["items"][sub["id"] - 1])

    monkeypatch.setattr(store, "snapshot", snapshot)
    monkeypatch.setattr(store, "compatible", compatible)
    monkeypatch.setattr(store, "get_submission", get_submission)
    monkeypatch.setattr(store, "evidence", evidence)
    app = FastAPI()
    app.include_router(intake.router)
    app.include_router(reads.router)
    app.add_exception_handler(ApiError, api_error_handler)
    services = SimpleNamespace(
        settings=SimpleNamespace(cursor_secret="test-secret", submissions_paused=False),
        competitions=CompetitionRegistry.of(Competition("miniz-oxide", "Compression", 8)),
    )

    async def injected_services():
        return services

    async def injected_session():
        return object()

    app.dependency_overrides[get_services] = injected_services
    app.dependency_overrides[get_competition_session] = injected_session

    class Client:
        def get(self, path, **kwargs):
            async def request():
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    return await client.get(path, **kwargs)

            return asyncio.run(request())

    return Client(), state, services


BASE = "/v1/competitions/miniz-oxide"


def test_weights_are_persisted_fractions_not_renormalized(setup):
    client, _, _ = setup
    body = client.get(BASE + "/weights/current").json()
    assert body["weights"] == {"a": 0.1, "b": 0.3}
    assert body["competition_share"] == 0.2
    assert body["unpaid_competition_weight"] == pytest.approx(0.6)
    assert body["context"]["computed_at"] == "2026-09-24T00:00:00Z"
    assert body["dry_run"] is True and body["chain_accepted"] is False


def test_ranking_and_pareto_keep_baseline_and_weights(setup):
    client, state, _ = setup
    body = client.get(BASE + "/leaderboard").json()
    assert [r["hotkey"] for r in body["ranking"]] == ["b", "a"]
    body = client.get(BASE + "/pareto").json()
    assert len(body["items"]) == 3
    baseline = body["items"][2]
    assert baseline["score"]["combined_weight"] == 0.6
    assert baseline["score"]["payable_weight"] == 0
    assert baseline["timing_interval"]["lower"] == 0.98
    assert body["bounds"]["max_balanced_time_ratio"] == 10


def test_cursor_pins_snapshot_and_rejects_cross_route_or_slug(setup):
    client, _, _ = setup
    first = client.get(BASE + "/pareto?limit=1").json()
    cursor = first["next_cursor"]
    second = client.get(BASE + "/pareto", params={"limit": 1, "cursor": cursor}).json()
    assert second["items"][0]["id"] == "2"
    assert second["context"]["snapshot_id"] == "42"
    assert client.get(BASE + "/leaderboard", params={"cursor": cursor}).status_code == 400
    assert (
        client.get(BASE + "/pareto", params={"cursor": cursor, "snapshot_id": 43}).status_code
        == 400
    )


def test_missing_snapshot_and_pending_do_not_fabricate_results(setup):
    client, state, _ = setup
    assert client.get(BASE + "/pareto?snapshot_id=999").status_code == 404
    state["snap"] = None
    body = client.get(BASE + "/weights/current").json()
    assert body["weights"] == {}
    assert body["payable_competition_weight"] is None
    assert body["context"]["status"] == "not_ready"
    candidate = point()
    candidate["admission"] = None
    assert view.decision(candidate) is None
    assert view.admission(candidate).admitted is None


def test_admission_graph_uses_one_sided_test_and_separate_display_interval():
    candidate = point()
    candidate["admission"].update(
        {
            "reference": {"submission_id": 2, "aggregation_id": 20},
            "statistics": {
                "reference_time": 2.0,
                "candidate_time": 1.98,
                "gain_pct": 1.0,
                "lower_pct": -0.2,
                "upper_pct": 2.4,
                "confidence": 0.95,
                "threshold_pct": 0.0,
                "method_version": "stored-test",
                "draws": 2000,
                "files": [{}, {}],
                "limitations": "host drift excluded",
            },
            "outcome": "inconclusive",
        }
    )
    result = view.decision(candidate)
    assert result.admitted is False
    assert result.speed_test.lower_confidence_bound == -0.002
    assert result.speed_test.gain_display_interval.confidence_level == 0.90
    assert result.speed_test.confidence_level == 0.95
    assert result.speed_test.reference_timing is None
    assert result.speed_test.comparison_range.lower == pytest.approx(1.952)
    assert result.speed_test.comparison_range.upper == pytest.approx(2.004)


def test_excluded_is_not_a_failed_statistical_test():
    candidate = point(outcome="excluded")
    candidate["admission"]["scoring_bounds"] = {
        "eligible": False,
        "max_time_ratio": 10,
        "max_compression_pct": 40,
        "time_ratio": 11,
        "compression_pct": 32,
        "violations": ["time-ratio-limit"],
    }
    result = view.decision(candidate)
    assert result.speed_test is None
    assert result.bounds.violations == ["time-ratio-limit"]
    assert result.admitted is False


def test_private_diagnostics_are_sanitized():
    report = view.sanitized_report(
        "/home/secret/project/foo.rs password=hidden https://private/token"
    )
    assert "hidden" not in report and "/home" not in report and "https://" not in report


def test_openapi_documents_upload_and_response_shapes(setup):
    client, _, _ = setup
    schema = client.get("/openapi.json").json()
    upload = (
        schema["paths"][BASE + "/submissions"]
        if BASE + "/submissions" in schema["paths"]
        else schema["paths"]["/v1/competitions/{slug}/submissions"]
    )
    assert upload["post"]["requestBody"]["content"]["multipart/form-data"]["schema"][
        "required"
    ] == ["parse.rs", "Parse.lean"]
    assert "lower_confidence_bound" in schema["components"]["schemas"]["SpeedTest"]["properties"]


def test_intake_locks_before_entitlement_and_returns_same_id(monkeypatch, setup):
    _, _, services = setup
    calls = []

    async def compatible(session):
        calls.append("schema")

    async def lock(session, hotkey):
        calls.append("lock")

    async def existing(session, **kw):
        calls.append("idempotency")
        return SimpleNamespace(id=7, state="accepted")

    async def entitlement(session, hotkey):
        calls.append("entitlement")
        return False, 0, 0

    monkeypatch.setattr(store, "compatible", compatible)
    monkeypatch.setattr(store, "lock_hotkey", lock)
    monkeypatch.setattr(store, "find_submission", existing)
    monkeypatch.setattr(store, "may_queue", entitlement)
    result = asyncio.run(
        intake._queue(
            competition=services.competitions.get("miniz-oxide"),
            session=object(),
            services=services,
            hotkey="a",
            rust=b"x",
            lean=b"y",
            digest="digest",
        )
    )
    assert calls == ["schema", "lock", "idempotency", "entitlement"]
    assert result.submission == "7" and result.created is False


def test_partial_payment_preserves_eligibility_and_unpaid_reason():
    allocation = score(1, "miner", 0.2, "duplicate-hotkey")
    allocation["improvement_weight"] = 0.05
    allocation["payable_weight"] = 0.05
    result = view.submission(point(), allocation, 42)
    assert result.score.payment_eligible is True
    assert result.score.payable_weight == 0.05
    assert result.score.unpaid_reason == "duplicate-hotkey"


def test_frontier_source_is_withheld(setup):
    # Submission 1 is on the snapshot's frontier: publishing it would let anyone copy the best.
    client, _, _ = setup
    for path in ("/submissions/1/source", "/submissions/1/source/parse.rs"):
        response = client.get(BASE + path)
        assert response.status_code == 403
        assert response.json()["reason_code"] == "SOURCE_WITHHELD"


def test_unscored_source_is_withheld_until_a_pass_places_it(setup):
    # No snapshot yet: the next pass may put the submission on the frontier, so fail closed.
    client, state, _ = setup
    state["snap"] = None
    response = client.get(BASE + "/submissions/2/source")
    assert response.status_code == 403
    assert response.json()["reason_code"] == "SOURCE_WITHHELD"


@pytest.mark.parametrize(
    ("baseline_key", "on_frontier", "withheld"),
    [
        (None, True, True),
        (None, False, False),
        ("optimal", True, False),
        ("optimal", False, False),
    ],
)
def test_withhold_frontier_miners_only(baseline_key, on_frontier, withheld):
    sub = {"id": 7, "baseline_key": baseline_key}
    score = {"on_frontier": on_frontier}
    if withheld:
        with pytest.raises(ApiError) as caught:
            reads.withhold(sub, score)
        assert caught.value.status_code == 403
        assert caught.value.reason_code == "SOURCE_WITHHELD"
    else:
        reads.withhold(sub, score)


def test_unscored_baseline_source_stays_public():
    reads.withhold({"id": 3, "baseline_key": "lazy"}, None)
