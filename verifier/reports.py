from __future__ import annotations

from typing import Mapping

from verifier.errors import ReasonCode
from verifier.models import DEFAULT_CHECKS, TaskManifest, VerificationReport
from verifier.task_generator import problem_id


def tail(value: str, limit: int = 4000) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return normalized[-limit:]


def updated_checks(checks: Mapping[str, bool], **updates: bool) -> dict[str, bool]:
    return {**DEFAULT_CHECKS, **dict(checks), **updates}


def build_report(
    *,
    manifest: TaskManifest,
    task_bundle_sha256: str,
    submission_sha256: str,
    accepted: bool,
    stage: str,
    reason: ReasonCode,
    checks: Mapping[str, bool],
    duration_ms: int,
    comparator_exit_code: int | None = None,
    stdout: str = "",
    stderr: str = "",
    workspace_retained: bool = False,
    sandbox_mode: str = "not-started",
) -> VerificationReport:
    return VerificationReport(
        schema_version=2,
        problem_id=problem_id(
            manifest.repository_commit,
            manifest.forbidden_dependencies or (manifest.source_theorem,),
        ),
        task_id=manifest.task_id,
        repository_commit=manifest.repository_commit,
        source_theorem=manifest.source_theorem,
        task_mode=manifest.task_mode,
        task_bundle_sha256=task_bundle_sha256,
        submission_sha256=submission_sha256,
        accepted=accepted,
        stage=stage,
        reason_code=reason,
        checks=updated_checks(checks),
        theorem_names=manifest.theorem_names,
        permitted_axioms=manifest.permitted_axioms,
        duration_ms=duration_ms,
        comparator_exit_code=comparator_exit_code,
        stdout_tail=tail(stdout),
        stderr_tail=tail(stderr),
        workspace_retained=workspace_retained,
        sandbox_mode=sandbox_mode,
    )
