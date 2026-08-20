from types import SimpleNamespace

import pytest

from verifier import cli
from verifier.errors import ReasonCode, VerifierError
from verifier.preflight import verify_proof_bundle_bytes


TASK_ID = "fc-test-formalized-v1"
TASK_DIGEST = "sha256:" + "ab" * 32
HOTKEY = "5" * 48
PROOF = b"theorem target : True := by trivial\n"


def test_bundle_preflight_runs_the_production_adapter_on_the_admitted_bytes(
    monkeypatch, tmp_path
):
    task = SimpleNamespace(
        sha256=TASK_DIGEST,
        manifest=SimpleNamespace(task_id=TASK_ID, max_submission_bytes=1_000_000),
    )
    claimed = SimpleNamespace(manifest=SimpleNamespace(miner_hotkey=HOTKEY))
    admitted = SimpleNamespace(proof=SimpleNamespace(raw=PROOF))
    report = SimpleNamespace(accepted=True)
    calls = []

    monkeypatch.setattr("verifier.preflight.load_task_bundle", lambda path: task)
    monkeypatch.setattr("verifier.preflight.load_proof_bundle", lambda *args, **kwargs: claimed)

    def admit(raw, **kwargs):
        calls.append(("admit", raw, kwargs))
        return admitted

    class Adapter:
        def __init__(self, **kwargs):
            calls.append(("adapter", kwargs))

        def verify_bytes(self, **kwargs):
            calls.append(("verify", kwargs))
            return report

    monkeypatch.setattr("verifier.preflight.admit_proof_bundle", admit)
    monkeypatch.setattr("verifier.preflight.ProductionVerifierAdapter", Adapter)

    result = verify_proof_bundle_bytes(
        raw=b"bundle",
        task_dir=tmp_path / "task",
        project_root=tmp_path,
        expected_task_id=TASK_ID,
        expected_task_sha256=TASK_DIGEST,
        expected_hotkey=HOTKEY,
    )

    assert result.raw == b"bundle"
    assert result.bundle is admitted
    assert result.report is report
    assert calls == [
        (
            "admit",
            b"bundle",
            {
                "task_manifest": task.manifest,
                "expected_task_sha256": TASK_DIGEST,
                "expected_hotkey": HOTKEY,
            },
        ),
        (
            "adapter",
            {
                "project_root": tmp_path,
                "allow_insecure_development": False,
            },
        ),
        (
            "verify",
            {
                "task_dir": tmp_path / "task",
                "submission": PROOF,
                "expected_task_sha256": TASK_DIGEST,
            },
        ),
    ]


@pytest.mark.parametrize(
    ("task_id", "task_digest", "reason"),
    [
        ("wrong-task", TASK_DIGEST, ReasonCode.INVALID_ARGUMENT),
        (TASK_ID, "sha256:" + "cd" * 32, ReasonCode.TASK_COMMITMENT_MISMATCH),
    ],
)
def test_bundle_preflight_refuses_cli_task_mismatches_before_reading_the_proof(
    monkeypatch, tmp_path, task_id, task_digest, reason
):
    task = SimpleNamespace(
        sha256=TASK_DIGEST,
        manifest=SimpleNamespace(task_id=TASK_ID, max_submission_bytes=1_000_000),
    )
    monkeypatch.setattr("verifier.preflight.load_task_bundle", lambda path: task)
    monkeypatch.setattr(
        "verifier.preflight.load_proof_bundle",
        lambda *args, **kwargs: pytest.fail("proof bundle should not be read"),
    )

    with pytest.raises(VerifierError) as failure:
        verify_proof_bundle_bytes(
            raw=b"bundle",
            task_dir=tmp_path / "task",
            project_root=tmp_path,
            expected_task_id=task_id,
            expected_task_sha256=task_digest,
        )
    assert failure.value.reason == reason


def test_bundle_verify_command_reports_the_full_verifier_result(monkeypatch, tmp_path):
    report = SimpleNamespace(
        accepted=True,
        reason_code=ReasonCode.VERIFIED,
        to_dict=lambda: {"accepted": True, "checks": {"lean_kernel_passed": True}},
    )
    calls = []
    printed = []
    monkeypatch.setattr(
        cli,
        "verify_proof_bundle_file",
        lambda **kwargs: calls.append(kwargs) or SimpleNamespace(report=report),
    )
    monkeypatch.setattr(cli, "_print", printed.append)

    exit_code = cli._run(
        SimpleNamespace(
            command="bundle",
            bundle_command="verify",
            bundle=tmp_path / "submission.zip",
            task=tmp_path / "task",
            allow_insecure_development=False,
        )
    )

    assert exit_code == 0
    assert calls == [
        {
            "bundle_path": tmp_path / "submission.zip",
            "task_dir": tmp_path / "task",
            "project_root": cli.PROJECT_ROOT,
            "allow_insecure_development": False,
        }
    ]
    assert printed == [
        {"accepted": True, "checks": {"lean_kernel_passed": True}}
    ]
