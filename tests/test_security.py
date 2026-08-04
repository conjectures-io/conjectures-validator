import os
from pathlib import Path

import pytest

from verifier.cli import _absolute_without_resolving
from verifier.environment import trusted_environment
from verifier.errors import ReasonCode, VerifierError
from verifier.repository import repository_commit, tasks_repository_root
from verifier.submission import load_submission
from verifier.verification import verify


ROOT = Path(__file__).resolve().parent.parent
TASKS_ROOT = tasks_repository_root(ROOT)


def test_cli_preserves_final_symlink_for_no_follow_validation(tmp_path):
    target = tmp_path / "Target.lean"
    target.write_text("theorem target : True := by trivial\n", encoding="utf-8")
    link = tmp_path / "Main.lean"
    link.symlink_to(target)
    assert _absolute_without_resolving(link).is_symlink()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX FIFOs")
def test_submission_fifo_is_rejected_without_blocking(tmp_path):
    submission = tmp_path / "Main.lean"
    os.mkfifo(submission)
    with pytest.raises(VerifierError) as error:
        load_submission(submission, 1000)
    assert error.value.reason == ReasonCode.SUBMISSION_POLICY_VIOLATION


def test_insecure_development_sandbox_fails_closed_by_default():
    report = verify(
        task_dir=TASKS_ROOT / "fixtures/simple-direct/task-positive",
        submission_path=ROOT / "examples/valid-submission/Main.lean",
        project_root=ROOT,
        allow_test_task=True,
    )
    assert not report.accepted
    assert report.reason_code == ReasonCode.INSECURE_SANDBOX


def test_external_task_commitment_mismatch_fails_before_lean():
    report = verify(
        task_dir=TASKS_ROOT / "fixtures/simple-direct/task-positive",
        submission_path=ROOT / "examples/valid-submission/Main.lean",
        project_root=ROOT,
        expected_task_sha256="sha256:" + "0" * 64,
        allow_test_task=True,
    )
    assert not report.accepted
    assert report.reason_code == ReasonCode.TASK_COMMITMENT_MISMATCH


def test_verification_environment_does_not_inherit_injection_variables(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin")
    monkeypatch.setenv("LEAN_PATH", str(tmp_path))
    monkeypatch.setenv("LD_PRELOAD", str(tmp_path / "payload.so"))
    home = tmp_path / "home"
    (home / ".tmp").mkdir(parents=True)
    environment = trusted_environment(ROOT, home)
    assert str(tmp_path) not in environment["PATH"]
    assert environment["LEAN_NUM_THREADS"] == "1"
    assert "LEAN_PATH" not in environment
    assert "LD_PRELOAD" not in environment


def test_repository_pin_check_ignores_ambient_path(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    commit = repository_commit(ROOT)
    assert len(commit) == 40 and all(char in "0123456789abcdef" for char in commit)
