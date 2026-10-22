import copy
import datetime as dt
import hashlib
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.trajectory_assets import build_package, example_document
from submission_api.settings import Settings, SettingsError
from submission_api.trajectory_schema import (
    ArtifactEntry, EventRecord, Manifest, PackageError, package_schema, parse_package,
)
from submission_api.trajectory_terms import ASSENT_STATEMENTS_SHA256, TERMS_SHA256, TERMS_VERSION
from submission_api.trajectories import eligibility_reason

ROOT = Path(__file__).resolve().parents[1]


def example():
    """The synthetic package in its one-object form (manifest, events, artifacts, files...)."""
    return copy.deepcopy(example_document())


def consent_params(**overrides):
    return {"terms_version": TERMS_VERSION, "terms_sha256": TERMS_SHA256,
            "assent_statements_sha256": ASSENT_STATEMENTS_SHA256, "accepted": "true", **overrides}


def check(document, **kwargs):
    return parse_package(build_package(document, **kwargs))


def test_published_schema_example_and_pinned_terms():
    summary = check(example())
    assert (summary.events, summary.artifacts) == (10, 1)
    assert summary.proof_first_seen == dt.datetime(2026, 10, 12, 12, 0, 6, tzinfo=dt.UTC)
    assert summary.manifest.synthetic
    published = (ROOT / "docs/trajectories/example-package.zip").read_bytes()
    assert published == build_package(example()) and parse_package(published).events == 10
    schema = json.loads((ROOT / "docs/trajectories/chat-log-package.schema.json").read_text())
    assert {k: v for k, v in schema.items() if k not in ("$schema", "$id")} == package_schema()
    assert TERMS_SHA256 == "sha256:" + hashlib.sha256(
        (ROOT / "docs/trajectories/chat-log-terms-v5.md").read_bytes()).hexdigest()


@pytest.mark.parametrize("change", [
    lambda p: p["manifest"].update(format="conjectures-trajectory/v1"),
    lambda p: p["manifest"].update(final_proof_sha256="sha256:" + "1" * 64),
    lambda p: p["manifest"].update(exported_at="2026-10-12T12:30:00"),
    lambda p: p["manifest"].pop("task_id"),
    lambda p: p["events"][1].update(role="internal_reasoning"),
    lambda p: p["events"][1].update(id="event-1"),
    lambda p: p["events"][1].update(timestamp="2026-10-12T13:00:00Z"),  # After exported_at.
    lambda p: p.update(events=[e for e in p["events"] if e["role"] != "user"]),
    lambda p: p["artifacts"][0].update(event_ids=["event-404"]),
    lambda p: p["artifacts"][0].update(sha256="sha256:" + "2" * 64),
    lambda p: p["artifacts"][0].update(kind="code"),
    lambda p: p["files"].update({"artifacts/Solution.lean": b"tampered"}),
    lambda p: p["artifacts"][0].update(path="../../etc/passwd"),
    # The chat must date the proof: its linked event needs a real timestamp.
    lambda p: p["events"][5].update(timestamp=None),
    lambda p: p["artifacts"][0].update(event_ids=[]),
])
def test_refuses_invalid_or_misleading_packages(change):
    p = example()
    change(p)
    with pytest.raises(PackageError):
        check(p)


def test_required_files_paths_and_zip_safety():
    raw = build_package(example())
    with pytest.raises(PackageError, match="missing a required file") as error:
        parse_package(_without(raw, "README.md"))
    assert error.value.file == "README.md"
    assert check(example(), folder="").events == 10  # Zipped without a top folder.
    with pytest.raises(PackageError, match="upload a .zip package"):
        parse_package(b'{"not": "a zip"}')
    for name in ("../escape.txt", "/abs.txt"):
        with pytest.raises(PackageError, match="unsafe path"):
            parse_package(_with(raw, name, b"x"))
    with pytest.raises(PackageError, match="exceeds 256 MiB"):
        class Oversized(bytes):
            def __len__(self):
                return 256 * 1024 * 1024 + 1
        parse_package(Oversized())


def test_archive_size_boundaries(monkeypatch):
    from submission_api import trajectory_schema as schema
    assert schema.MAX_PACKAGE_BYTES == 256 * 1024 * 1024
    assert schema.MAX_UNPACKED_BYTES == 2 * 1024 * 1024 * 1024
    raw = build_package(example())
    monkeypatch.setattr(schema, "MAX_PACKAGE_BYTES", len(raw))
    assert parse_package(raw).events == 10
    monkeypatch.setattr(schema, "MAX_PACKAGE_BYTES", len(raw) - 1)
    with pytest.raises(PackageError, match="exceeds 256 MiB"):
        parse_package(raw)
    info = zipfile.ZipInfo("supporting-file.bin")
    info.file_size = schema.MAX_UNPACKED_BYTES
    archive = SimpleNamespace(infolist=lambda: [info])
    assert schema._entries(archive)[info.filename] == info
    info.file_size += 1
    with pytest.raises(PackageError, match="more than 2 GiB"):
        schema._entries(archive)


def _without(raw: bytes, suffix: str) -> bytes:
    source, out = zipfile.ZipFile(io.BytesIO(raw)), io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            if not info.filename.endswith("/" + suffix):
                target.writestr(info, source.read(info))
    return out.getvalue()


def _with(raw: bytes, name: str, data: bytes) -> bytes:
    source, out = zipfile.ZipFile(io.BytesIO(raw)), io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            target.writestr(info, source.read(info))
        target.writestr(zipfile.ZipInfo(name), data)
    return out.getvalue()


def test_fingerprint_ignores_repackaging_but_hash_tracks_exact_package():
    p = example()
    other = example()
    other["manifest"]["submission_id"] = "00000000-0000-4000-8000-000000000002"
    assert check(p).fingerprint == check(other).fingerprint
    assert build_package(p) != build_package(other)


def test_extra_fields_are_kept_and_strict_json_is_enforced():
    p = example()
    p["events"][0]["model_settings"] = {"temperature": 1}
    p["manifest"]["usage"] = {"input_tokens": 10}
    assert check(p).manifest.usage == {"input_tokens": 10}
    p["files"]["artifacts/notes.txt"] = b"scratch"
    assert check(p).events == 10
    raw = build_package(example())
    events = zipfile.ZipFile(io.BytesIO(raw)).read("chat-log-package/events.jsonl")
    doubled = events.replace(b'"role": "user"', b'"role": "user", "role": "user"', 1)
    with pytest.raises(PackageError, match="duplicate JSON object key"):
        parse_package(_with(_without(raw, "events.jsonl"), "chat-log-package/events.jsonl", doubled))


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


def test_terms_assent_and_example_are_plain_and_pinned():
    assert "sha256:" + hashlib.sha256((ROOT / "docs/trajectories/assent-statements-v2.json").read_bytes()).hexdigest() == ASSENT_STATEMENTS_SHA256
    terms = (ROOT / "docs/trajectories/chat-log-terms-v5.md").read_text()
    assert "You have removed" not in terms
    assert "Anything from other people is included with\ntheir permission." in terms
    for promise in ("Every open problem requires logs", "before you submit the\nproof",
                    "up to three versions in all", "within 14 days of the request",
                    "reasoning wherever\nyour tool saved it as readable text", "do not try to decrypt or recreate it",
                    "`conjectures logs upload`", "original chat logs that produced the proof",
                    "not a\nrecreation", "sell and license the logs to buyers",
                    "publish the logs openly, and buyers may not either", "within one year of that moment",
                    "earlier problems keep the schedule they opened with",
                    "approved for its full bounty", "paid in Subnet 66 Alpha", "this right ends"):
        assert promise in terms, promise
    assert example()["manifest"]["synthetic"] is True


def test_agent_instructions_cover_every_checked_field_and_stay_neutral():
    # Contributors hand this file to an AI agent; it must keep up with what the server checks.
    guide = (ROOT / "docs/trajectories/agent-instructions.md").read_text()
    fields = set(Manifest.model_fields) | set(EventRecord.model_fields) | set(ArtifactEntry.model_fields)
    assert not {f for f in fields - {"synthetic"} if f"`{f}`" not in guide}
    for name in ("README.md", "events.jsonl", "episodes.jsonl", "sample.html", "artifact-index.json",
                 "manifest.json", "validation.json", "chat-log-package.zip", "conjectures-chat-log/v2"):
        assert name in guide, name
    for rule in ("[REDACTED]", "real timestamp", "hidden chain of thought", "256 MiB", "readable text",
                 "encrypted or withheld"):
        assert rule in guide, rule
    for word in ("owner", "licens", "permission", "legal", "broker", "sell", "buyer", "Codex", "Claude"):
        assert word.lower() not in guide.lower(), word


def test_consent_and_safe_error_locations():
    from submission_api.errors import BadRequest
    from submission_api.trajectory_intake import Consent, check_consent, package_errors
    assert check_consent(Consent(**{**consent_params(), "accepted": True}))["accepted"]
    for key in ("terms_version", "terms_sha256", "assent_statements_sha256", "accepted"):
        with pytest.raises(BadRequest):
            check_consent(Consent(**{**consent_params(), "accepted": True, key: False if key == "accepted" else "stale"}))
    p = example()
    p["events"][2]["role"] = "SECRET_ROLE_VALUE"
    with pytest.raises(PackageError) as error:
        check(p)
    result = package_errors(error.value)
    assert result[0]["file"] == "events.jsonl" and result[0]["line"] == 3
    assert "SECRET" not in json.dumps(result)
    p = example()
    p["artifacts"][0]["sha256"] = "sha256:" + "3" * 64
    p["manifest"]["final_proof_sha256"] = "sha256:" + "3" * 64
    with pytest.raises(PackageError) as error:
        check(p)
    assert package_errors(error.value) == [{"file": "artifact-index.json", "line": 1, "path": [],
                                            "type": "value_error", "message": "artifact SHA-256 does not match the file"}]


def test_production_launch_needs_the_pinned_approved_terms_digest():
    assert "draft" not in TERMS_VERSION
    with pytest.raises(SettingsError, match="owner-approved terms digest"):
        Settings.from_env({"APP_MODE": "PROD", "TRAJECTORY_BONUS_ENABLED": "true",
            "TRAJECTORY_LAUNCH_APPROVED_TERMS_SHA256": "sha256:" + "0" * 64})
    with pytest.raises(SettingsError) as later:  # Other production settings are still required.
        Settings.from_env({"APP_MODE": "PROD", "TRAJECTORY_BONUS_ENABLED": "true",
            "TRAJECTORY_LAUNCH_APPROVED_TERMS_SHA256": TERMS_SHA256})
    assert "chat-log" not in str(later.value)


def test_chat_log_launch_must_publish_the_terms_version_it_names():
    from test_api_auth import production_env
    root = Path(__file__).resolve().parents[1]
    launch = {"TRAJECTORY_BONUS_ENABLED": "true", "TRAJECTORY_LAUNCH_APPROVED_TERMS_SHA256": TERMS_SHA256,
              "SUBMISSION_TERMS_VERSION": "v8", "SUBMISSION_TERMS_EFFECTIVE_FROM": "2026-10-13"}
    v8 = str(root / "docs/policies/submission-terms-v8.md")
    assert Settings.from_env(production_env(**launch, SUBMISSION_TERMS_PATH=v8)).trajectory_bonus_enabled
    v9 = {**launch, "SUBMISSION_TERMS_VERSION": "v9"}
    path = str(root / "docs/policies/submission-terms-v9.md")
    assert Settings.from_env(production_env(**v9, SUBMISSION_TERMS_PATH=path)).trajectory_bonus_enabled
    v10 = {**launch, "SUBMISSION_TERMS_VERSION": "v10", "SUBMISSION_TERMS_EFFECTIVE_FROM": "2026-10-14"}
    path = str(root / "docs/policies/submission-terms-v10.md")
    assert Settings.from_env(production_env(**v10, SUBMISSION_TERMS_PATH=path)).trajectory_bonus_enabled
    v11 = {**v10, "SUBMISSION_TERMS_VERSION": "v11"}
    path = str(root / "docs/policies/submission-terms-v11.md")
    assert Settings.from_env(production_env(**v11, SUBMISSION_TERMS_PATH=path)).trajectory_bonus_enabled
    v12 = {**v11, "SUBMISSION_TERMS_VERSION": "v12", "SUBMISSION_TERMS_EFFECTIVE_FROM": "2026-10-21"}
    path = str(root / "docs/policies/submission-terms-v12.md")
    assert Settings.from_env(production_env(**v12, SUBMISSION_TERMS_PATH=path)).trajectory_bonus_enabled
    assert f"../trajectories/{TERMS_VERSION}.md" in Path(path).read_text()
    # The v7 file labelled v8 is refused rather than published under the wrong version.
    with pytest.raises(SettingsError, match="must state the configured"):
        Settings.from_env(production_env(**launch, SUBMISSION_TERMS_PATH=str(root / "docs/SUBMISSION_TERMS.md")))


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
