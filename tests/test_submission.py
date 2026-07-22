import os

import pytest

from verifier.errors import ReasonCode, VerifierError
from verifier.submission import load_submission


def test_submission_rejects_invalid_utf8_nul_and_size(tmp_path):
    invalid = tmp_path / "Invalid.lean"
    invalid.write_bytes(b"\xff")
    with pytest.raises(VerifierError) as error:
        load_submission(invalid, 10)
    assert error.value.reason == ReasonCode.SUBMISSION_NOT_UTF8

    nul = tmp_path / "Nul.lean"
    nul.write_bytes(b"theorem\x00")
    with pytest.raises(VerifierError) as error:
        load_submission(nul, 20)
    assert error.value.reason == ReasonCode.SUBMISSION_POLICY_VIOLATION

    large = tmp_path / "Large.lean"
    large.write_text("12345", encoding="utf-8")
    with pytest.raises(VerifierError) as error:
        load_submission(large, 4)
    assert error.value.reason == ReasonCode.SUBMISSION_TOO_LARGE


def test_submission_rejects_symlink(tmp_path):
    target = tmp_path / "Target.lean"
    target.write_text("theorem target : True := by trivial", encoding="utf-8")
    link = tmp_path / "Main.lean"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(VerifierError) as error:
        load_submission(link, 1000)
    assert error.value.reason == ReasonCode.SUBMISSION_POLICY_VIOLATION


def test_submission_rejects_non_lean_payload(tmp_path):
    archive = tmp_path / "payload.zip"
    archive.write_bytes(b"not a Lean source")
    with pytest.raises(VerifierError) as error:
        load_submission(archive, 1000)
    assert error.value.reason == ReasonCode.SUBMISSION_POLICY_VIOLATION
