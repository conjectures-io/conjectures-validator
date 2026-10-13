import copy
import datetime as dt
import hashlib
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from submission_api.settings import Settings, SettingsError
from submission_api.trajectory_schema import TrajectoryPackage, canonical_bytes, evidence_digest, parse_package, proof_first_seen
from submission_api.trajectory_terms import ASSENT_STATEMENTS_SHA256, TERMS_SHA256, TERMS_VERSION
from submission_api.trajectories import eligibility_reason

ROOT = Path(__file__).resolve().parents[1]


def example():
    return json.loads((ROOT / "docs/trajectories/synthetic-example.json").read_text())


def envelope_body(package, state_version=None, **consent):
    return {"package_json": json.dumps(package), "expected_state_version": state_version, "consent": {
        "terms_version": TERMS_VERSION, "terms_sha256": TERMS_SHA256,
        "assent_statements_sha256": ASSENT_STATEMENTS_SHA256, "accepted": True, **consent}}


def test_published_schema_example_and_pinned_terms():
    p = TrajectoryPackage.model_validate(example())
    assert parse_package(canonical_bytes(p)) == p
    schema = json.loads((ROOT / "docs/trajectories/trajectory.schema.json").read_text())
    generated = TrajectoryPackage.model_json_schema()
    assert {k: v for k, v in schema.items() if k not in ("$schema", "$id")} == generated
    assert TERMS_SHA256 == "sha256:" + hashlib.sha256(
        (ROOT / "docs/trajectories/chat-log-terms-v1.md").read_bytes()).hexdigest()
    assert proof_first_seen(p) == dt.datetime(2026, 10, 12, 12, 0, 6, tzinfo=dt.UTC)


@pytest.mark.parametrize("change", [
    lambda p: p.update(hidden_chain_of_thought="secret"),
    lambda p: p["events"][1].update(kind="internal_reasoning"),
    lambda p: p["events"][1].update(visibility="hidden"),
    lambda p: p["events"][1].update(sequence=4),
    lambda p: p["events"][1].update(run_id="nonexistent"),
    lambda p: p["events"][1].update(artifact_ids=["nonexistent"]),
    lambda p: p["events"][1]["timestamp"].update(value="2026-10-12T11:00:00Z"),
    lambda p: p["events"][1]["timestamp"].update(value="2026-10-12T12:00:00"),
    lambda p: p["events"][3].update(tool_call_id="missing-call"),
    lambda p: p["events"][3].update(tool_name="different-tool"),
    lambda p: p["events"][3].update(run_id=None),
    lambda p: p["runs"][0]["version"].update(unknown_reason=None),
    lambda p: p["artifacts"][0].update(content="tampered proof"),
    lambda p: p["artifacts"][0].update(path="../../secrets"),
    lambda p: p.update(final_proof_sha256="sha256:" + "1" * 64),
    lambda p: p.update(completeness="complete_export"),
    lambda p: p["permissions"].update(allowed_to_share="true"),
    lambda p: p.update(commercial_election={"election": "decline"}),
    # The chat must date the proof: no timestamped event may be left showing it.
    lambda p: [e.update(artifact_ids=[]) for e in p["events"]],
    lambda p: [e["timestamp"].update(value=None, unknown_reason="Not exported") for e in p["events"]],
])
def test_refuses_invalid_or_misleading_evidence(change):
    p = example()
    change(p)
    with pytest.raises(ValueError):
        parse_package(json.dumps(p).encode())


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b"[" * 25 + b"]" * 25, b'{"x": NaN}', b"\xff"])
def test_strict_json(raw):
    with pytest.raises(ValueError):
        parse_package(raw)


def test_fingerprint_ignores_repackaging_but_hash_tracks_exact_package():
    p = TrajectoryPackage.model_validate(example())
    other = p.model_copy(update={"submission_id": uuid.UUID("00000000-0000-4000-8000-000000000002")})
    assert evidence_digest(p) == evidence_digest(other)
    assert canonical_bytes(p) != canonical_bytes(other)


def test_feature_is_off_by_default_and_the_cutoff_is_not_configuration():
    from conftest_api import build_settings
    assert build_settings().trajectory_bonus_enabled is False
    # The bonus cutoff is stamped once in the database when intake opens, never read from env.
    enabled = build_settings(TRAJECTORY_BONUS_ENABLED="true", TRAJECTORY_BONUS_START_AT="2020-01-01T00:00:00Z")
    assert enabled.trajectory_bonus_enabled and not hasattr(enabled, "trajectory_bonus_start_at")
    now = dt.datetime.now(dt.UTC)
    sub = SimpleNamespace(created_at=now, verification_status="VERIFIED", manual_review_status="APPROVED",
                          reward_status="ELIGIBLE", review_policy_version="v3")
    decision = SimpleNamespace(kind="HUMAN", decision="APPROVED", reason_code="REVIEW_APPROVED")
    assert eligibility_reason(sub, decision, cutoff=now + dt.timedelta(days=1), now=now) is None
    decision.reason_code = "FORMALIZATION_DEFECT_AWARD"
    assert eligibility_reason(sub, decision, cutoff=now + dt.timedelta(days=1), now=now)
    decision.reason_code = "REVIEW_APPROVED"
    decision.kind = "AUTOMATIC"
    assert eligibility_reason(sub, decision, cutoff=now + dt.timedelta(days=1), now=now)


def test_terms_assent_and_template_are_plain_and_pinned():
    assert "sha256:" + hashlib.sha256((ROOT / "docs/trajectories/assent-statements-v2.json").read_bytes()).hexdigest() == ASSENT_STATEMENTS_SHA256
    terms = (ROOT / "docs/trajectories/chat-log-terms-v1.md").read_text()
    for promise in ("upload the logs before you submit the proof", "original chat logs that produced the proof",
                    "not a\nrecreation", "sell and license the logs to buyers",
                    "publish the logs openly, and buyers may not either", "within one year of that\nmoment",
                    "approved for its full bounty", "paid in Subnet 66 Alpha", "this right ends"):
        assert promise in terms, promise
    assert not example()["permissions"]["authentic_logs_that_produced_this_proof"]
    blank = json.loads((ROOT / "docs/trajectories/template.json").read_text())
    assert not blank["runs"] and not blank["events"] and not blank["artifacts"]
    assert "REPLACE_" in blank["exported_at"] and blank["submission_id"] is None
    assert blank["reproduction"]["lean_toolchain"]["value"] is None
    assert "commercial_election" not in blank


def test_agent_instructions_cover_every_schema_field():
    # Contributors hand this file to an AI agent; it must keep up with the schema it targets.
    guide = (ROOT / "docs/trajectories/agent-instructions.md").read_text()
    schema = TrajectoryPackage.model_json_schema()
    fields = set(schema["properties"])
    for name in ("ModelRun", "Event", "Artifact", "Reproduction"):
        fields |= set(schema["$defs"][name]["properties"])
    assert not {f for f in fields if f"`{f}`" not in guide}
    for rule in ("verbatim", "thinking", "[REDACTED]", "authoritative_submission", "real timestamp"):
        assert rule in guide


def test_empty_output_and_branch_linkage_preserve_export():
    p = example()
    p["events"][3].update(content="", empty_content=True)
    p["events"][5]["parent_event_id"] = "event-2"
    assert TrajectoryPackage.model_validate(p).events[3].content == ""
    p["events"][5]["parent_event_id"] = "event-9"
    with pytest.raises(ValueError):
        TrajectoryPackage.model_validate(p)


def test_raw_envelope_consent_duplicate_keys_and_safe_locations():
    from pydantic import ValidationError
    from submission_api.trajectory_intake import prepare_upload, safe_errors
    p = example()
    body = envelope_body(p)
    assert prepare_upload(json.dumps(body).encode())[0].permissions.license_to_sell_once_accepted
    for key in ("terms_version", "terms_sha256", "assent_statements_sha256", "accepted"):
        changed = copy.deepcopy(body)
        changed["consent"][key] = False if key == "accepted" else "stale"
        with pytest.raises(ValueError):
            prepare_upload(json.dumps(changed).encode())
    body["package_json"] = body["package_json"].replace('"synthetic": true', '"synthetic": false, "synthetic": true')
    with pytest.raises(ValueError):
        prepare_upload(json.dumps(body).encode())
    p["SECRET_UNKNOWN_FIELD"] = "SECRET_VALUE"
    try:
        TrajectoryPackage.model_validate(p)
    except ValidationError as error:
        result = json.dumps(safe_errors(error))
        assert "SECRET" not in result and "unknown-field" in result and "extra_forbidden" in result


def test_production_launch_needs_the_pinned_approved_terms_digest():
    assert "draft" not in TERMS_VERSION
    with pytest.raises(SettingsError, match="owner-approved terms digest"):
        Settings.from_env({"APP_MODE": "PROD", "TRAJECTORY_BONUS_ENABLED": "true",
            "TRAJECTORY_LAUNCH_APPROVED_TERMS_SHA256": "sha256:" + "0" * 64})
    with pytest.raises(SettingsError) as later:  # Other production settings are still required.
        Settings.from_env({"APP_MODE": "PROD", "TRAJECTORY_BONUS_ENABLED": "true",
            "TRAJECTORY_LAUNCH_APPROVED_TERMS_SHA256": TERMS_SHA256})
    assert "chat-log" not in str(later.value)


def test_upload_budget_prunes_expired_accounts_and_bounds_failures(monkeypatch):
    import asyncio
    from submission_api.trajectory_intake import UploadBudget
    from submission_api.errors import TooManyRequests
    async def scenario():
        budget = UploadBudget()
        budget.failures.max_clients = 2
        for key in ("old-a", "old-b"):
            budget.failures.check(key, 0)
        monkeypatch.setattr("submission_api.trajectory_intake.time.monotonic", lambda: 1000)
        async with budget.hold("new-account"):
            with pytest.raises(TooManyRequests):
                async with budget.hold("new-account"):
                    pass
        for _ in range(6):
            with pytest.raises(ValueError):
                async with budget.hold("new-account"):
                    raise ValueError()
        with pytest.raises(TooManyRequests):
            async with budget.hold("new-account"):
                pass
    asyncio.run(scenario())


def test_semantic_messages_are_allowlisted_and_original_content_can_contain_template_words():
    from pydantic import ValidationError
    from submission_api.trajectory_intake import prepare_upload, safe_errors
    p = example()
    p["events"][3]["tool_name"] = "mismatched-tool"
    with pytest.raises(ValidationError) as error:
        TrajectoryPackage.model_validate(p)
    detail = safe_errors(error.value)
    assert detail[0]["message"] == "tool result must match the call's model run and tool name"
    p = example()
    p["synthetic"] = False
    p["runs"][0]["source_export_unavailable_reason"] = "Fixture only"
    p["events"][0]["content"] += " Authentic log with a REPLACE_ME variable."
    body = envelope_body(p)
    assert "REPLACE_ME" in prepare_upload(json.dumps(body).encode())[0].events[0].content
    with pytest.raises(ValueError, match="nesting"):
        prepare_upload(b"[" * 25 + b"]" * 25)


def test_success_budget_bounds_exact_retries_and_timeout_does_not_spend_failure_budget():
    import asyncio
    from submission_api.trajectory_intake import UploadBudget
    from submission_api.errors import ApiError, TooManyRequests
    async def scenario():
        budget = UploadBudget()
        with pytest.raises(ApiError):
            async with budget.hold("account"):
                raise ApiError("timed out", status_code=408)
        assert budget.failures.tracked_clients == 0
        for _ in range(3):
            async with budget.hold("account"):
                pass
        with pytest.raises(TooManyRequests):
            async with budget.hold("account"):
                pass
    asyncio.run(scenario())


def test_published_v7_bytes_remain_identical_to_the_archived_contract():
    assert (ROOT / 'docs/SUBMISSION_TERMS.md').read_bytes() == (ROOT / 'docs/policies/submission-terms-v7.md').read_bytes()
