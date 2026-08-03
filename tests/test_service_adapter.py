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
    # The exact argument set, so a development override cannot be added to the production path
    # without this test being changed on purpose. allow_insecure_development is passed explicitly
    # as False rather than omitted: the default must be visible here, not inherited from verify().
    assert calls == [
        {
            "task_dir": tmp_path / "task",
            "submission_path": calls[0]["submission_path"],
            "project_root": tmp_path,
            "expected_task_sha256": TASK_DIGEST,
            "allow_insecure_development": False,
        }
    ]


def test_service_adapter_forwards_an_insecure_sandbox_only_when_asked(
    monkeypatch, tmp_path
):
    calls = []
    monkeypatch.setattr(
        "verifier.service_adapter.verify", lambda **kwargs: calls.append(kwargs)
    )

    ProductionVerifierAdapter(
        project_root=tmp_path, allow_insecure_development=True
    ).verify_bytes(
        task_dir=tmp_path / "task", submission=PROOF, expected_task_sha256=TASK_DIGEST
    )

    assert calls[0]["allow_insecure_development"] is True


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
