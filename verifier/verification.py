from __future__ import annotations

import time
from collections.abc import Mapping
from pathlib import Path

from verifier.comparator import (
    production_sandbox_available,
    rejection_reason,
    resolve_tools,
    run_comparator,
)
from verifier.environment import tool_path, trusted_environment
from verifier.errors import ReasonCode, VerifierError
from verifier.hashing import is_sha256
from verifier.models import VerificationReport
from verifier.reports import build_report, updated_checks
from verifier.repository import (
    assert_dependency_pins,
    formal_conjectures_pin,
    repository_commit,
)
from verifier.static_checks import check_submission
from verifier.submission import load_submission
from verifier.task_loader import load_task_bundle
from verifier.task_policy import (
    COUNTEREXAMPLE_TASK_MODE,
    EXACT_TASK_MODE,
    is_production_task_mode,
)
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
    expected_task_sha256: str | None = None,
    allow_uncommitted_task: bool = False,
    allow_insecure_development: bool = False,
    allow_test_task: bool = False,
) -> VerificationReport:
    started = time.monotonic_ns()
    bundle = load_task_bundle(task_dir)
    manifest = bundle.manifest
    deadline = started + manifest.timeout_seconds * 1_000_000_000
    checks = updated_checks(
        {},
        manifest_valid=True,
        production_task=manifest.production_eligible,
        nanoda_enabled=manifest.enable_nanoda,
    )
    submission_hash = ""
    paths = None

    def seconds_remaining(cap: int | None = None) -> int:
        remaining = max(0, (deadline - time.monotonic_ns() + 999_999_999) // 1_000_000_000)
        return int(min(remaining, cap) if cap is not None else remaining)

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
            task_bundle_sha256=bundle.sha256,
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
        if expected_task_sha256 is not None:
            if not is_sha256(expected_task_sha256) or expected_task_sha256 != bundle.sha256:
                return rejected(
                    ReasonCode.TASK_COMMITMENT_MISMATCH,
                    "LOAD_TASK",
                    stderr="task bundle does not match the externally committed SHA-256",
                )
            checks = updated_checks(checks, task_commitment_valid=True)
        elif manifest.production_eligible and not allow_uncommitted_task:
            return rejected(
                ReasonCode.TASK_COMMITMENT_MISMATCH,
                "LOAD_TASK",
                stderr="production verification requires an externally committed task bundle SHA-256",
            )
        if not manifest.production_eligible and not allow_test_task:
            return rejected(
                ReasonCode.INELIGIBLE_TASK,
                "LOAD_TASK",
                stderr="non-production task requires the explicit testing override",
            )
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
        checks = updated_checks(checks, trusted_hashes_valid=True)

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

        tools = resolve_tools(
            project_root, insecure_development=allow_insecure_development
        )
        production_sandbox = production_sandbox_available(tools, project_root)
        checks = updated_checks(checks, production_sandbox=production_sandbox)
        if not production_sandbox and not allow_insecure_development:
            return rejected(
                ReasonCode.INSECURE_SANDBOX,
                "CREATE_WORKSPACE",
                stderr=(
                    "production verification requires Linux and the live fail-closed "
                    "Landrun/seccomp isolation probe"
                ),
                sandbox_mode=tools.sandbox_mode,
            )

        try:
            paths = create_workspace(
                task_files=bundle.files,
                project_root=project_root,
                retain=retain_workspace,
            )
        except (OSError, VerifierError) as exc:
            return rejected(ReasonCode.WORKSPACE_ERROR, "CREATE_WORKSPACE", stderr=str(exc))
        effective_env = trusted_environment(project_root, paths.root / ".home")
        lake = tool_path(project_root, "lake")

        remaining = seconds_remaining()
        if remaining <= 0:
            return rejected(ReasonCode.TIMEOUT, "BUILD_CHALLENGE", sandbox_mode=tools.sandbox_mode)
        challenge = build_challenge(paths, lake, effective_env, remaining)
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

        inspections = []
        try:
            for source, target_theorem in zip(
                bundle.sources,
                manifest.theorem_names,
                strict=True,
            ):
                remaining = seconds_remaining(600)
                if remaining <= 0:
                    return rejected(
                        ReasonCode.TIMEOUT,
                        "BUILD_CHALLENGE",
                        sandbox_mode=tools.sandbox_mode,
                    )
                inspections.append(
                    inspect_generated_target(
                        paths=paths,
                        lake=lake,
                        project_root=project_root,
                        source_module=source.module,
                        source_theorem=source.theorem,
                        classification=source.classification.value,
                        mode=manifest.task_mode,
                        env=effective_env,
                        timeout_seconds=remaining,
                        target_theorem=target_theorem,
                    )
                )
        except VerifierError as exc:
            return rejected(exc.reason, "BUILD_CHALLENGE", stderr=str(exc))
        if any(
            inspection["source_hash"] != source.type_hash
            for source, inspection in zip(bundle.sources, inspections, strict=True)
        ):
            return rejected(ReasonCode.SOURCE_TYPE_CHANGED, "BUILD_CHALLENGE")
        checks = updated_checks(checks, source_type_hash_valid=True)
        if (
            inspections[0]["source_hash"] != manifest.source_type_hash
            or inspections[0]["target_hash"] != manifest.generated_target_type_hash
            or any(
                not inspection["matches"]
                for inspection in inspections
            )
        ):
            return rejected(ReasonCode.STATEMENT_MISMATCH, "BUILD_CHALLENGE")
        if manifest.production_eligible and any(
            inspection["source_category"] != "research open"
            or inspection["source_declaration_kind"] != "theorem"
            or not inspection["source_depends_on_sorry"]
            or inspection["source_has_formal_proof"]
            or inspection["target_contains_sorry"]
            or not is_production_task_mode(manifest.task_mode)
            or (
                manifest.task_mode == EXACT_TASK_MODE
                and inspection["target_hash"] != inspection["source_hash"]
            )
            or (
                manifest.task_mode == COUNTEREXAMPLE_TASK_MODE
                and inspection["target_hash"] == inspection["source_hash"]
            )
            or inspection["source_axioms"] != tuple(sorted(source.transitive_axioms))
            for source, inspection in zip(
                bundle.sources,
                inspections,
                strict=True,
            )
        ):
            return rejected(
                ReasonCode.INELIGIBLE_TASK,
                "BUILD_CHALLENGE",
                stderr="source production policy differs from the compiled Lean environment",
            )

        try:
            package_solution(paths, bundle.files, submission)
        except VerifierError as exc:
            return rejected(exc.reason, "CREATE_WORKSPACE", stderr=str(exc))

        remaining = seconds_remaining()
        if remaining <= 0:
            return rejected(ReasonCode.TIMEOUT, "RUN_COMPARATOR", sandbox_mode=tools.sandbox_mode)
        comparator, tools = run_comparator(
            paths=paths,
            manifest=manifest,
            project_root=project_root,
            lake=lake,
            env=effective_env,
            timeout_seconds=remaining,
            # Must match the tools resolved above, or the comparator would run under the real
            # Landrun after the gate was passed on the strength of the shim.
            insecure_development=allow_insecure_development,
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
            task_bundle_sha256=bundle.sha256,
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
