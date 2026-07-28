from __future__ import annotations

from pathlib import Path

import pytest

from verifier.service_adapter import ProductionVerifierAdapter


TASK_DIGEST = "sha256:" + "ef" * 32
PROOF = b"theorem Bounty.target : True := by\n  trivial\n"


def test_service_adapter_passes_only_production_verifier_arguments(
    monkeypatch, tmp_path
):
    calls = []

    def fake_verify(**kwargs):
        calls.append(kwargs)
        assert Path(kwargs["submission_path"]).read_bytes() == PROOF
        return "verified"

    monkeypatch.setattr("verifier.service_adapter.verify", fake_verify)
    adapter = ProductionVerifierAdapter(project_root=tmp_path)

    assert (
        adapter.verify_bytes(
            task_dir=tmp_path / "task",
            submission=PROOF,
            expected_task_sha256=TASK_DIGEST,
        )
        == "verified"
    )
    assert calls == [
        {
            "task_dir": tmp_path / "task",
            "submission_path": calls[0]["submission_path"],
            "project_root": tmp_path,
            "expected_task_sha256": TASK_DIGEST,
        }
    ]


def test_service_adapter_rejects_bad_task_digest_before_verification(
    monkeypatch, tmp_path
):
    called = False

    def fake_verify(**kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr("verifier.service_adapter.verify", fake_verify)
    adapter = ProductionVerifierAdapter(project_root=tmp_path)

    with pytest.raises(ValueError, match="expected task digest"):
        adapter.verify_bytes(
            task_dir=tmp_path / "task",
            submission=PROOF,
            expected_task_sha256="not-a-digest",
        )
    assert called is False
