from __future__ import annotations

import os
import platform
import time
from pathlib import Path
from typing import Mapping

from verifier.comparator import rejection_reason, run_comparator
from verifier.errors import ReasonCode, VerifierError
from verifier.models import VerificationReport
from verifier.reports import build_report, updated_checks
from verifier.repository import assert_dependency_pins, formal_conjectures_pin, repository_commit
from verifier.static_checks import check_submission
from verifier.submission import load_submission
from verifier.task_loader import load_task, verify_trusted_hashes
from verifier.workspace import (
    build_challenge,
    cleanup_workspace,
    create_workspace,
    inspect_generated_target,
    package_solution,
)


def _comparator_stage(reason: ReasonCode) -> str:
    return {
        ReasonCode.SOLUTION_BUILD_FAILED: "BUILD_SOLUTION",
        ReasonCode.LEAN_KERNEL_REJECTED: "RUN_KERNEL",
        ReasonCode.NANODA_REJECTED: "RUN_NANODA",
    }.get(reason, "RUN_COMPARATOR")


def _failed_comparator_checks(
    checks: Mapping[str, bool], reason: ReasonCode, nanoda_enabled: bool
) -> dict[str, bool]:
    built = {
        ReasonCode.STATEMENT_MISMATCH,
        ReasonCode.UNPERMITTED_AXIOM,
        ReasonCode.LEAN_KERNEL_REJECTED,
        ReasonCode.NANODA_REJECTED,
    }
    statement_matched = {
        ReasonCode.UNPERMITTED_AXIOM,
        ReasonCode.LEAN_KERNEL_REJECTED,
        ReasonCode.NANODA_REJECTED,
    }
    axioms_permitted = {ReasonCode.LEAN_KERNEL_REJECTED, ReasonCode.NANODA_REJECTED}
    return updated_checks(
        checks,
        solution_built=reason in built,
        same_statement=reason in statement_matched,
        axioms_permitted=reason in axioms_permitted,
        nanoda_passed=nanoda_enabled and reason == ReasonCode.LEAN_KERNEL_REJECTED,
    )


def verify(
    *,
    task_dir: Path,
    submission_path: Path,
    project_root: Path,
    retain_workspace: bool = False,
    env: Mapping[str, str] | None = None,
) -> VerificationReport:
    started = time.monotonic_ns()
    effective_env = dict(os.environ if env is None else env)
    manifest = load_task(task_dir)
    checks = updated_checks({}, manifest_valid=True, nanoda_enabled=manifest.enable_nanoda)
    submission_hash = ""
    paths = None

    def sanitized(value: str) -> str:
        result = value
        if paths is not None:
            result = result.replace(str(paths.root), "<WORKSPACE>")
        return result.replace(str(project_root), "<PROJECT>")

    def rejected(
        reason: ReasonCode,
        stage: str,
        *,
        stdout: str = "",
        stderr: str = "",
        comparator_exit_code: int | None = None,
        sandbox_mode: str = "not-started",
    ) -> VerificationReport:
        return build_report(
            manifest=manifest,
            submission_sha256=submission_hash,
            accepted=False,
            stage=stage,
            reason=reason,
            checks=checks,
            duration_ms=(time.monotonic_ns() - started) // 1_000_000,
            comparator_exit_code=comparator_exit_code,
            stdout=sanitized(stdout),
            stderr=sanitized(stderr),
            workspace_retained=bool(paths and paths.retained),
            sandbox_mode=sandbox_mode,
        )

    try:
        try:
            assert_dependency_pins(project_root)
            expected_commit = formal_conjectures_pin(project_root)
            actual_commit = repository_commit(project_root / "vendor" / "formal-conjectures")
        except VerifierError as exc:
            return rejected(exc.reason, "LOAD_TASK", stderr=str(exc))
        if manifest.repository_commit != expected_commit or actual_commit != expected_commit:
            return rejected(
                ReasonCode.REPOSITORY_COMMIT_MISMATCH,
                "LOAD_TASK",
                stderr=(
                    f"task={manifest.repository_commit}, expected={expected_commit}, repository={actual_commit}"
                ),
            )
        try:
            verify_trusted_hashes(task_dir, manifest)
            checks = updated_checks(checks, trusted_hashes_valid=True)
        except VerifierError as exc:
            return rejected(exc.reason, "VERIFY_TRUSTED_HASHES", stderr=str(exc))

        try:
            submission = load_submission(submission_path, manifest.max_submission_bytes)
            submission_hash = submission.sha256
        except VerifierError as exc:
            return rejected(exc.reason, "LOAD_SUBMISSION", stderr=str(exc))

        static = check_submission(submission.text, manifest)
        if not static.valid:
            return rejected(
                ReasonCode.SUBMISSION_POLICY_VIOLATION,
                "STATIC_POLICY_CHECK",
                stderr="\n".join(static.violations),
            )
        checks = updated_checks(checks, submission_policy_valid=True)

        if platform.system() == "Linux" and hasattr(os, "geteuid") and os.geteuid() == 0:
            return rejected(
                ReasonCode.WORKSPACE_ERROR,
                "CREATE_WORKSPACE",
                stderr="verification must run as an unprivileged user",
            )

        try:
            paths = create_workspace(
                task_dir=task_dir,
                project_root=project_root,
                retain=retain_workspace,
            )
        except (OSError, VerifierError) as exc:
            return rejected(ReasonCode.WORKSPACE_ERROR, "CREATE_WORKSPACE", stderr=str(exc))

        challenge = build_challenge(paths, effective_env, manifest.timeout_seconds)
        if challenge.timed_out:
            return rejected(ReasonCode.TIMEOUT, "BUILD_CHALLENGE", stdout=challenge.stdout, stderr=challenge.stderr)
        if challenge.exit_code != 0:
            return rejected(
                ReasonCode.CHALLENGE_BUILD_FAILED,
                "BUILD_CHALLENGE",
                stdout=challenge.stdout,
                stderr=challenge.stderr,
            )
        checks = updated_checks(checks, challenge_built=True)

        try:
            source_hash, target_hash, matches = inspect_generated_target(
                paths=paths,
                project_root=project_root,
                source_module=manifest.source_module,
                source_theorem=manifest.source_theorem,
                classification=manifest.classification.value,
                mode=manifest.task_mode,
                env=effective_env,
                timeout_seconds=min(manifest.timeout_seconds, 600),
            )
        except VerifierError as exc:
            return rejected(exc.reason, "BUILD_CHALLENGE", stderr=str(exc))
        if source_hash != manifest.source_type_hash:
            return rejected(ReasonCode.SOURCE_TYPE_CHANGED, "BUILD_CHALLENGE")
        checks = updated_checks(checks, source_type_hash_valid=True)
        if target_hash != manifest.generated_target_type_hash or not matches:
            return rejected(ReasonCode.STATEMENT_MISMATCH, "BUILD_CHALLENGE")

        try:
            package_solution(paths, task_dir, submission)
        except VerifierError as exc:
            return rejected(exc.reason, "CREATE_WORKSPACE", stderr=str(exc))

        comparator, tools = run_comparator(
            paths=paths,
            manifest=manifest,
            project_root=project_root,
            env=effective_env,
        )
        if comparator.exit_code != 0 or comparator.timed_out:
            reason = rejection_reason(comparator, manifest.enable_nanoda)
            checks = _failed_comparator_checks(checks, reason, manifest.enable_nanoda)
            return rejected(
                reason,
                _comparator_stage(reason),
                stdout=comparator.stdout,
                stderr=comparator.stderr,
                comparator_exit_code=comparator.exit_code,
                sandbox_mode=tools.sandbox_mode,
            )
        checks = updated_checks(
            checks,
            solution_built=True,
            same_statement=True,
            axioms_permitted=True,
            lean_kernel_passed=True,
            nanoda_passed=manifest.enable_nanoda,
        )
        return build_report(
            manifest=manifest,
            submission_sha256=submission_hash,
            accepted=True,
            stage="COMPLETED",
            reason=ReasonCode.VERIFIED,
            checks=checks,
            duration_ms=(time.monotonic_ns() - started) // 1_000_000,
            comparator_exit_code=comparator.exit_code,
            stdout=sanitized(comparator.stdout),
            stderr=sanitized(comparator.stderr),
            workspace_retained=paths.retained,
            sandbox_mode=tools.sandbox_mode,
        )
    finally:
        if paths is not None:
            cleanup_workspace(paths)
