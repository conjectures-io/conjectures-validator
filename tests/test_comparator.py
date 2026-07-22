from pathlib import Path

from verifier.comparator import (
    COMPARATOR_MEMORY_BYTES,
    missing_tools,
    production_sandbox_available,
    rejection_reason,
    resolve_tools,
)
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


def test_nested_process_exhaustion_is_reported_as_a_resource_limit():
    thread_failure = failed(
        stdout="Building Challenge\n",
        stderr="lean::exception: failed to create thread\nChild exited with 139",
    )
    process_failure = failed(
        stdout="Building Challenge\nExporting declarations\nBuilding Solution\n",
        stderr="resource exhausted (error code: 11, resource temporarily unavailable)",
    )
    assert rejection_reason(thread_failure, False) == ReasonCode.RESOURCE_LIMIT
    assert rejection_reason(process_failure, False) == ReasonCode.RESOURCE_LIMIT


def test_environment_cannot_redirect_trusted_tools(monkeypatch):
    monkeypatch.setenv("VERIFIER_COMPARATOR", "/usr/bin/true")
    monkeypatch.setenv("COMPARATOR_LEAN4EXPORT", "/usr/bin/true")
    tools = resolve_tools(Path(__file__).resolve().parent.parent)
    assert tools.comparator != Path("/usr/bin/true")
    assert tools.lean4export != Path("/usr/bin/true")


def test_sandbox_helpers_keep_default_call_compatibility():
    tools = resolve_tools(Path(__file__).resolve().parent.parent)
    assert isinstance(missing_tools(tools), tuple)
    assert isinstance(production_sandbox_available(tools), bool)


def test_comparator_memory_budget_covers_large_kernel_checked_certificates():
    assert COMPARATOR_MEMORY_BYTES == 64 * 1024 * 1024 * 1024
