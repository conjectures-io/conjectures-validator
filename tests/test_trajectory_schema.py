import copy
import datetime as dt
import hashlib
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from submission_api.settings import Settings, SettingsError
from submission_api.trajectory_schema import TrajectoryPackage, canonical_bytes, evidence_digest, parse_package
from submission_api.trajectory_terms import COMMERCIAL_TERMS_SHA256
from submission_api.trajectories import commercial_clearance_reason, eligibility_reason

ROOT = Path(__file__).resolve().parents[1]


def example():
    return json.loads((ROOT / "docs/trajectories/synthetic-example.json").read_text())


def test_published_schema_example_and_pinned_license():
    p = TrajectoryPackage.model_validate(example())
    assert parse_package(canonical_bytes(p)) == p
    schema = json.loads((ROOT / "docs/trajectories/trajectory.schema.json").read_text())
    generated = TrajectoryPackage.model_json_schema()
    assert {k: v for k, v in schema.items() if k not in ("$schema", "$id")} == generated
    assert COMMERCIAL_TERMS_SHA256 == "sha256:" + hashlib.sha256(
        (ROOT / "docs/trajectories/commercial-license-draft-v1.md").read_bytes()).hexdigest()


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
    lambda p: p["permissions"].update(private_review_and_storage_only="true"),
    lambda p: p["commercial_election"].update(election="conditional_grant", explicit_assent=False),
    lambda p: p["commercial_election"].update(purposes=["training"]),
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


def test_optional_license_unknown_rights_block_only_commercial_clearance():
    election = copy.deepcopy(example()["commercial_election"])
    assert commercial_clearance_reason(election)
    election.update(election="conditional_grant", explicit_assent=True, purposes=["training"], sources=[{
        "rights_status": "unknown", "agreement_or_license": {"value": None},
        "evidence_reference": {"value": None}, "allowed_purposes": [],
    }])
    assert commercial_clearance_reason(election)
    election["sources"][0].update(rights_status="confirmed", agreement_or_license={"value": "Own code"},
                                  evidence_reference={"value": "license evidence"}, allowed_purposes=["training"])
    assert commercial_clearance_reason(election) is None


def test_feature_requires_explicit_launch_cutoff_and_preserves_recorded_policy():
    from conftest_api import build_settings
    assert build_settings().trajectory_bonus_enabled is False
    with pytest.raises(SettingsError):
        Settings.from_env({"TRAJECTORY_BONUS_ENABLED": "true"})
    with pytest.raises(SettingsError):
        Settings.from_env({"TRAJECTORY_BONUS_START_AT": "2026-01-01"})
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


def test_review_terms_assents_and_template_are_truthful_and_pinned():
    from submission_api.trajectory_terms import REVIEW_TERMS_SHA256, ASSENT_STATEMENTS_SHA256
    for name, digest in [("review-terms-v2.md", REVIEW_TERMS_SHA256), ("assent-statements-v1.json", ASSENT_STATEMENTS_SHA256)]:
        assert "sha256:" + hashlib.sha256((ROOT / "docs/trajectories" / name).read_bytes()).hexdigest() == digest
    assert not example()["permissions"]["authentic_user_visible_export"]
    blank = json.loads((ROOT / "docs/trajectories/template.json").read_text())
    assert not blank["runs"] and not blank["events"] and not blank["artifacts"]
    assert "REPLACE_" in blank["exported_at"]
    assert blank["reproduction"]["lean_toolchain"]["value"] is None
    grant = TrajectoryPackage.model_validate(json.loads((ROOT / "docs/trajectories/synthetic-rights-example.json").read_text()))
    assert commercial_clearance_reason(grant.commercial_election.model_dump())


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
    from submission_api.trajectory_terms import REVIEW_TERMS_VERSION, REVIEW_TERMS_SHA256, ASSENT_STATEMENTS_SHA256
    p = example()
    body = {"package_json": json.dumps(p), "commercial_election": p["commercial_election"], "consent": {
        "review_terms_version": REVIEW_TERMS_VERSION, "review_terms_sha256": REVIEW_TERMS_SHA256,
        "assent_statements_sha256": ASSENT_STATEMENTS_SHA256, "accepted": True}}
    assert prepare_upload(json.dumps(body).encode())[0].permissions.authentic_user_visible_export
    for key in ("review_terms_version", "review_terms_sha256", "assent_statements_sha256", "accepted"):
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


def test_sublicensing_needs_actual_permitted_use():
    from submission_api.trajectory_schema import CommercialElection
    value = example()["commercial_election"]
    value.update(election="conditional_grant", purposes=["dataset_redistribution", "sublicensing"], explicit_assent=True)
    with pytest.raises(ValueError):
        CommercialElection.model_validate(value)


def test_production_refuses_draft_launch_even_with_digest():
    with pytest.raises(SettingsError, match="draft terms"):
        Settings.from_env({"APP_MODE": "PROD", "TRAJECTORY_BONUS_ENABLED": "true",
            "TRAJECTORY_BONUS_START_AT": "2026-01-01T00:00:00Z",
            "TRAJECTORY_LAUNCH_APPROVED_TERMS_SHA256": COMMERCIAL_TERMS_SHA256})


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
    from submission_api.trajectory_terms import REVIEW_TERMS_VERSION, REVIEW_TERMS_SHA256, ASSENT_STATEMENTS_SHA256
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
    body = {"package_json": json.dumps(p), "commercial_election": p["commercial_election"], "consent": {
        "review_terms_version": REVIEW_TERMS_VERSION, "review_terms_sha256": REVIEW_TERMS_SHA256,
        "assent_statements_sha256": ASSENT_STATEMENTS_SHA256, "accepted": True}}
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
