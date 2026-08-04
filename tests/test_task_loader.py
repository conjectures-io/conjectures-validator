import json
import os
import shutil
from pathlib import Path

import pytest

from verifier.errors import ReasonCode, VerifierError
from verifier.hashing import sha256_file
from verifier.repository import tasks_repository_root
from verifier.task_loader import load_task, load_task_bundle, verify_trusted_hashes


ROOT = Path(__file__).resolve().parent.parent
TASKS_ROOT = tasks_repository_root(ROOT)


def copied_task(tmp_path: Path) -> Path:
    destination = tmp_path / "task"
    shutil.copytree(TASKS_ROOT / "fixtures/simple-direct/task-positive", destination)
    return destination


def test_load_task_rejects_non_deterministic_id(tmp_path):
    task = copied_task(tmp_path)
    path = task / "manifest.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**value, "task_id": "tampered"}), encoding="utf-8")
    with pytest.raises(VerifierError) as error:
        load_task(task)
    assert error.value.reason == ReasonCode.INVALID_MANIFEST


def test_load_task_rejects_duplicate_json_keys(tmp_path):
    task = copied_task(tmp_path)
    path = task / "manifest.json"
    text = path.read_text(encoding="utf-8").replace(
        '"schema_version": 1,',
        '"schema_version": 1,\n  "schema_version": 1,',
    )
    path.write_text(text, encoding="utf-8")
    with pytest.raises(VerifierError) as error:
        load_task_bundle(task)
    assert error.value.reason == ReasonCode.INVALID_MANIFEST

def test_load_task_rejects_unknown_or_coerced_manifest_fields(tmp_path):
    task = copied_task(tmp_path)
    path = task / "manifest.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**value, "ignored_security_policy": True}), encoding="utf-8")
    with pytest.raises(VerifierError) as error:
        load_task_bundle(task)
    assert error.value.reason == ReasonCode.INVALID_MANIFEST

    value["source_theorem"] = 123
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(VerifierError) as error:
        load_task_bundle(task)
    assert error.value.reason == ReasonCode.INVALID_MANIFEST


def test_trusted_config_cannot_drift_from_manifest(tmp_path):
    task = copied_task(tmp_path)
    manifest = load_task(task)
    (task / "comparator-config.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(VerifierError) as error:
        verify_trusted_hashes(task, manifest)
    assert error.value.reason == ReasonCode.TRUSTED_FILE_MODIFIED


def test_task_bundle_rejects_extra_files_and_excessive_limits(tmp_path):
    task = copied_task(tmp_path)
    (task / "hidden.lean").write_text("theorem forged : False := by sorry\n", encoding="utf-8")
    with pytest.raises(VerifierError) as error:
        load_task_bundle(task)
    assert error.value.reason == ReasonCode.INVALID_MANIFEST

    (task / "hidden.lean").unlink()
    path = task / "manifest.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["max_submission_bytes"] = 1_000_001
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(VerifierError) as error:
        load_task_bundle(task)
    assert error.value.reason == ReasonCode.INVALID_MANIFEST


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX FIFOs")
def test_task_fifo_is_rejected_without_blocking(tmp_path):
    task = copied_task(tmp_path)
    challenge = task / "Challenge.lean"
    challenge.unlink()
    os.mkfifo(challenge)
    with pytest.raises(VerifierError) as error:
        load_task_bundle(task)
    assert error.value.reason == ReasonCode.INVALID_MANIFEST


def test_task_bundle_has_external_commitment_digest(tmp_path):
    task = copied_task(tmp_path)
    digest = load_task_bundle(task).sha256
    assert digest.startswith("sha256:") and len(digest) == 71


def test_answer_policy_cannot_be_removed_from_numeric_task(tmp_path):
    task = tmp_path / "task"
    shutil.copytree(TASKS_ROOT / "fixtures/numeric-answer/task-answer", task)
    path = task / "manifest.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["answer_policy"] = {}
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(VerifierError) as error:
        load_task_bundle(task)
    assert error.value.reason == ReasonCode.INVALID_MANIFEST


def test_hashed_but_non_generated_challenge_is_rejected(tmp_path):
    task = copied_task(tmp_path)
    challenge = task / "Challenge.lean"
    challenge.write_text(challenge.read_text(encoding="utf-8") + "\n#check True\n", encoding="utf-8")
    digest = sha256_file(challenge)
    for name in ("manifest.json", "trusted-hashes.json"):
        path = task / name
        value = json.loads(path.read_text(encoding="utf-8"))
        target = value["trusted_file_hashes"] if name == "manifest.json" else value
        target["Challenge.lean"] = digest
        path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(VerifierError) as error:
        load_task_bundle(task)
    assert error.value.reason == ReasonCode.TRUSTED_FILE_MODIFIED
