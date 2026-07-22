import json
import shutil
from pathlib import Path

import pytest

from verifier.errors import ReasonCode, VerifierError
from verifier.task_loader import load_task, verify_trusted_hashes


ROOT = Path(__file__).resolve().parent.parent


def copied_task(tmp_path: Path) -> Path:
    destination = tmp_path / "task"
    shutil.copytree(ROOT / "examples/simple-direct/task-positive", destination)
    return destination


def test_load_task_rejects_non_deterministic_id(tmp_path):
    task = copied_task(tmp_path)
    path = task / "manifest.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**value, "task_id": "tampered"}), encoding="utf-8")
    with pytest.raises(VerifierError) as error:
        load_task(task)
    assert error.value.reason == ReasonCode.INVALID_MANIFEST


def test_trusted_config_cannot_drift_from_manifest(tmp_path):
    task = copied_task(tmp_path)
    manifest = load_task(task)
    (task / "comparator-config.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(VerifierError) as error:
        verify_trusted_hashes(task, manifest)
    assert error.value.reason == ReasonCode.TRUSTED_FILE_MODIFIED
