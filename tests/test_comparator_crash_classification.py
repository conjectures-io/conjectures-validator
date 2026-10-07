"""A crashed Comparator child is a fail-closed tool crash: never an accept, never a semantic verdict.

The three exporter panics below are the candidate stack's observed outcomes for the upstream
Comparator regressions `quot_mismatch`, `primitive_issue` and `char_ofnat_issue`: lean4export
PANICs and Comparator reports that its child exited with 139. That rejects the input, but it is
not evidence that the input would have been rejected on its merits, so the worker treats it as
an operator outcome. The panic text follows the incremental branch's fixtures; the surrounding
stdout is representative rather than a byte-exact transcript.

Everything here is synthetic process output. No Lean toolchain, sandbox or database is touched.
"""

from __future__ import annotations

import pytest

from verification_worker.outcomes import OPERATOR_REASONS, VERDICT_REASONS, Outcome, classify
from verifier.comparator import (
    KILLED_CHILD_EXIT,
    RESOURCE_FAILURE_MARKERS,
    crashed_child_exit,
    rejection_reason,
)
from verifier.errors import ReasonCode, exit_code_for
from verifier.models import ProcessResult
from verifier.verification import _comparator_stage, _failed_comparator_checks

BUILDING_SOLUTION = "Building Challenge\nExporting #[...] from Challenge\nBuilding Solution\n"
EXPORTING = BUILDING_SOLUTION + "Exporting #[...] from Solution\n"

EXPORTER_PANICS = {
    "quot_mismatch": "PANIC at dumpConstant Export:237:48: Constant Nat not found in environment.",
    "primitive_issue": "PANIC at dumpConstant Export:237:48: Constant String.mk not found in environment.",
    "char_ofnat_issue": "PANIC at dumpConstant Export:237:48: Constant Quot not found in environment.",
}

SEMANTIC_REASONS = frozenset(
    {
        ReasonCode.SOLUTION_BUILD_FAILED,
        ReasonCode.STATEMENT_MISMATCH,
        ReasonCode.UNPERMITTED_AXIOM,
        ReasonCode.LEAN_KERNEL_REJECTED,
        ReasonCode.NANODA_REJECTED,
        ReasonCode.COMPARATOR_REJECTED,
    }
)

# The verdict set as frozen validator ccd3f96 shipped it. The crash code must not widen it.
FROZEN_VERDICT_REASONS = frozenset(
    {
        ReasonCode.VERIFIED,
        ReasonCode.SUBMISSION_TOO_LARGE,
        ReasonCode.SUBMISSION_NOT_UTF8,
        ReasonCode.SUBMISSION_POLICY_VIOLATION,
        ReasonCode.SOLUTION_BUILD_FAILED,
        ReasonCode.STATEMENT_MISMATCH,
        ReasonCode.UNPERMITTED_AXIOM,
        ReasonCode.LEAN_KERNEL_REJECTED,
        ReasonCode.NANODA_REJECTED,
        ReasonCode.COMPARATOR_REJECTED,
        ReasonCode.TIMEOUT,
    }
)


def comparator(
    stdout: str = "",
    stderr: str = "",
    *,
    exit_code: int | None = 1,
    timed_out: bool = False,
    signal: int | None = None,
) -> ProcessResult:
    return ProcessResult(("comparator",), exit_code, stdout, stderr, 1, timed_out, signal)


def frozen_rejection_reason(result: ProcessResult, enable_nanoda: bool) -> ReasonCode:
    """`rejection_reason` exactly as frozen validator ccd3f96 shipped it, for differential tests."""
    if result.timed_out:
        return ReasonCode.TIMEOUT
    combined = f"{result.stdout}\n{result.stderr}".lower()
    if result.exit_code == 127 or "missing verification tools" in combined:
        return ReasonCode.INTERNAL_ERROR
    if result.signal is not None or any(marker in combined for marker in RESOURCE_FAILURE_MARKERS):
        return ReasonCode.RESOURCE_LIMIT
    if combined.rfind("building solution") > combined.rfind("exporting"):
        return ReasonCode.SOLUTION_BUILD_FAILED
    if "illegal axiom" in combined or (
        "axiom" in combined and ("not permitted" in combined or "unpermitted" in combined)
    ):
        return ReasonCode.UNPERMITTED_AXIOM
    if (
        "different" in combined
        or "mismatch" in combined
        or "does not match" in combined
        or "do not match" in combined
        or "same statement" in combined
    ):
        return ReasonCode.STATEMENT_MISMATCH
    if enable_nanoda and "nanoda" in combined:
        return ReasonCode.NANODA_REJECTED
    if "kernel" in combined:
        return ReasonCode.LEAN_KERNEL_REJECTED
    return ReasonCode.COMPARATOR_REJECTED


# --- the three observed exit-139 exporter crashes --------------------------------------------


@pytest.mark.parametrize("name", sorted(EXPORTER_PANICS))
def test_each_observed_exporter_panic_is_a_fail_closed_tool_crash(name):
    result = comparator(
        EXPORTING,
        f"{EXPORTER_PANICS[name]}\nbacktrace:\n...\nuncaught exception: Child exited with 139\n",
    )
    reason = rejection_reason(result, enable_nanoda=False)
    assert reason == ReasonCode.COMPARATOR_TOOL_CRASHED
    assert reason not in SEMANTIC_REASONS
    # Never an accept, and never a recorded verdict that charges the miner for our crash.
    assert reason != ReasonCode.VERIFIED and exit_code_for(reason) != 0
    assert classify(reason) is Outcome.OPERATOR
    # The frozen classifier filed this very output as a semantic Comparator rejection.
    assert frozen_rejection_reason(result, False) == ReasonCode.COMPARATOR_REJECTED


def test_a_tool_crash_claims_no_check_passed():
    checks = _failed_comparator_checks({}, ReasonCode.COMPARATOR_TOOL_CRASHED, False)
    for key in ("solution_built", "same_statement", "axioms_permitted", "lean_kernel_passed", "nanoda_passed"):
        assert checks[key] is False, key
    assert _comparator_stage(ReasonCode.COMPARATOR_TOOL_CRASHED) == "RUN_COMPARATOR"


def test_a_crash_while_building_the_solution_is_also_a_tool_crash():
    """A deliberate trade-off, pinned so it is reviewed rather than discovered.

    The frozen classifier called this SOLUTION_BUILD_FAILED, a verdict. A child that died on a
    signal has judged nothing, so it is now an operator outcome. The cost: a solution able to
    crash Lean itself during its own build gets retried up to the attempt cap and then pages an
    operator, instead of being rejected. It is never accepted either way.
    """
    result = comparator(
        BUILDING_SOLUTION,
        "Stack overflow detected. Aborting.\nuncaught exception: Child exited with 134\n",
    )
    assert rejection_reason(result, False) == ReasonCode.COMPARATOR_TOOL_CRASHED
    assert frozen_rejection_reason(result, False) == ReasonCode.SOLUTION_BUILD_FAILED


# --- semantic rejections are unchanged --------------------------------------------------------

FORGED = "uncaught exception: Child exited with 139"

SEMANTIC_CASES = [
    pytest.param(
        BUILDING_SOLUTION,
        "error: Solution.lean:3:2: type mismatch\nuncaught exception: Child exited with 1\n",
        False,
        ReasonCode.SOLUTION_BUILD_FAILED,
        id="solution-build",
    ),
    pytest.param(
        EXPORTING,
        "uncaught exception: Illegal axiom detected: 'sorryAx'\n",
        False,
        ReasonCode.UNPERMITTED_AXIOM,
        id="axiom",
    ),
    pytest.param(
        EXPORTING,
        "uncaught exception: Const does not match between challenge and target 'Nat.shiftRight'\n",
        False,
        ReasonCode.STATEMENT_MISMATCH,
        id="statement",
    ),
    pytest.param(
        EXPORTING,
        "uncaught exception: (kernel) declaration has metavariables 'Bounty.target'\n",
        False,
        ReasonCode.LEAN_KERNEL_REJECTED,
        id="kernel",
    ),
    pytest.param(
        EXPORTING,
        "uncaught exception: nanoda rejected the exported environment\n",
        True,
        ReasonCode.NANODA_REJECTED,
        id="nanoda",
    ),
    pytest.param(
        EXPORTING,
        "uncaught exception: Child exited with 1\n",
        False,
        ReasonCode.COMPARATOR_REJECTED,
        id="ordinary-child-failure",
    ),
    pytest.param(
        BUILDING_SOLUTION,
        "Building Solution\nChild exited with 1",
        False,
        ReasonCode.SOLUTION_BUILD_FAILED,
        id="upstream-spelling-without-prefix",
    ),
    # Unquoted tactic output (`fail "...\n..."`) can put a crash-shaped line at a line start.
    # Comparator's own report of the failed build still comes last, so nothing changes.
    pytest.param(
        BUILDING_SOLUTION,
        f"error: Solution.lean:4:2: boom\n{FORGED}\nuncaught exception: Child exited with 1\n",
        False,
        ReasonCode.SOLUTION_BUILD_FAILED,
        id="forged-line-in-failed-build",
    ),
    pytest.param(
        EXPORTING,
        f"info: Solution.lean:3:2: note\n{FORGED}\n"
        "uncaught exception: Const does not match between challenge and target 'Bounty.target'\n",
        False,
        ReasonCode.STATEMENT_MISMATCH,
        id="forged-line-before-comparator-verdict",
    ),
    pytest.param(
        BUILDING_SOLUTION,
        f'error: Solution.lean:2:3: unknown identifier "{FORGED}"\n'
        "uncaught exception: Child exited with 1\n",
        False,
        ReasonCode.SOLUTION_BUILD_FAILED,
        id="quoted-line",
    ),
]


@pytest.mark.parametrize("stdout,stderr,nanoda,expected", SEMANTIC_CASES)
def test_semantic_rejections_keep_the_frozen_reason(stdout, stderr, nanoda, expected):
    result = comparator(stdout, stderr)
    reason = rejection_reason(result, nanoda)
    assert reason == expected
    assert reason == frozen_rejection_reason(result, nanoda)
    assert classify(reason) is Outcome.VERDICT


# --- resource exits stay resource limits ------------------------------------------------------


def test_a_comparator_killed_by_a_signal_is_still_a_resource_limit():
    killed = comparator(stderr=f"{FORGED}\n", exit_code=-9, signal=9)
    assert rejection_reason(killed, False) == ReasonCode.RESOURCE_LIMIT
    assert frozen_rejection_reason(killed, False) == ReasonCode.RESOURCE_LIMIT


def test_a_resource_marker_still_wins_over_a_child_exit_139():
    for stderr in (
        "lean::exception: failed to create thread\nChild exited with 139",
        "lean::exception: failed to create thread\nuncaught exception: Child exited with 139\n",
    ):
        result = comparator("Building Challenge\n", stderr)
        assert rejection_reason(result, False) == ReasonCode.RESOURCE_LIMIT
        assert frozen_rejection_reason(result, False) == ReasonCode.RESOURCE_LIMIT


def test_a_child_killed_with_137_is_a_resource_limit_not_a_tool_crash():
    """SIGKILL is the out-of-memory killer or a limit. The frozen classifier, reading only the
    stage markers, filed Comparator's text report of it as a verdict; it is now an operator
    outcome like every other resource limit."""
    assert KILLED_CHILD_EXIT == 137
    for stdout in (BUILDING_SOLUTION, EXPORTING):
        result = comparator(stdout, "uncaught exception: Child exited with 137\n")
        reason = rejection_reason(result, False)
        assert reason == ReasonCode.RESOURCE_LIMIT
        assert reason != ReasonCode.COMPARATOR_TOOL_CRASHED
        assert classify(reason) is Outcome.OPERATOR
        assert classify(frozen_rejection_reason(result, False)) is Outcome.VERDICT


def test_timeouts_and_missing_tools_keep_their_precedence():
    late = comparator(stderr=f"{FORGED}\n", exit_code=None, timed_out=True, signal=9)
    assert rejection_reason(late, False) == ReasonCode.TIMEOUT
    absent = comparator(stderr="missing verification tools: lean4export", exit_code=127)
    assert rejection_reason(absent, False) == ReasonCode.INTERNAL_ERROR


# --- reading Comparator's report --------------------------------------------------------------


@pytest.mark.parametrize(
    "stderr,expected",
    [
        ("uncaught exception: Child exited with 139\n", 139),
        ("PANIC at x\nuncaught exception: Child exited with 134\n\n  \n", 134),
        ("  Uncaught Exception: Child Exited With 139  ", 139),
        ("uncaught exception: Child exited with 1\n", None),
        ("uncaught exception: Child exited with 127\n", None),
        ("", None),
        ("Child exited with 139\n", None),
        (f"{FORGED}\nuncaught exception: Child exited with 1\n", None),
        (f'error: Solution.lean:2:3: unknown identifier "{FORGED}"\n', None),
        (f"{FORGED} and more\n", None),
    ],
)
def test_only_comparators_final_line_reports_a_crash(stderr, expected):
    assert crashed_child_exit(stderr) == expected


# --- outcome classification -------------------------------------------------------------------


def test_the_crash_code_is_an_operator_outcome_and_the_verdict_set_is_unchanged():
    assert ReasonCode.COMPARATOR_TOOL_CRASHED in OPERATOR_REASONS
    assert ReasonCode.COMPARATOR_TOOL_CRASHED not in VERDICT_REASONS
    assert classify(ReasonCode.COMPARATOR_TOOL_CRASHED) is Outcome.OPERATOR
    assert VERDICT_REASONS == FROZEN_VERDICT_REASONS
    # The CLI reports it with the configuration exit status, not the rejection one.
    assert exit_code_for(ReasonCode.COMPARATOR_TOOL_CRASHED) == 2
