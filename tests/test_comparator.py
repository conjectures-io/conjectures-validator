from verifier.comparator import rejection_reason
from verifier.errors import ReasonCode
from verifier.models import ProcessResult


def failed(stdout: str = "", stderr: str = "") -> ProcessResult:
    return ProcessResult(("comparator",), 1, stdout, stderr, 1)


def test_comparator_reason_mapping_matches_upstream_messages():
    build = failed("Building Challenge\nExporting declarations\nBuilding Solution\n", "Child exited with 1")
    axiom = failed(stderr="Illegal axiom detected: 'sorryAx'")
    mismatch = failed(stderr="Solution theorem statement does not match")
    assert rejection_reason(build, False) == ReasonCode.SOLUTION_BUILD_FAILED
    assert rejection_reason(axiom, False) == ReasonCode.UNPERMITTED_AXIOM
    assert rejection_reason(mismatch, False) == ReasonCode.STATEMENT_MISMATCH
