from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from verifier.errors import ReasonCode, VerifierError
from verifier.hashing import sha256_text
from verifier.models import CatalogDeclaration, ProcessResult
from verifier.process import run_process
from verifier.submission import Submission


@dataclass(frozen=True)
class WorkspacePaths:
    root: Path
    challenge: Path
    solution: Path
    config: Path
    retained: bool


def _lakefile(project_root: Path) -> str:
    root = str(project_root.resolve()).replace('"', '\\"')
    return (
        'name = "fc_verification_workspace"\n'
        'version = "0.1.0"\n\n'
        '[[require]]\n'
        'name = "formal_conjectures_verifier"\n'
        f'path = "{root}"\n\n'
        '[[lean_lib]]\nname = "Challenge"\n\n'
        '[lean_lib.leanOptions]\nweak.google.answer = "postpone"\n\n'
        '[[lean_lib]]\nname = "Solution"\n'
        '[lean_lib.leanOptions]\nweak.google.answer = "postpone"\n'
    )


def create_workspace(
    *,
    task_dir: Path,
    project_root: Path,
    workspace_parent: Path | None = None,
    retain: bool = False,
) -> WorkspacePaths:
    parent = workspace_parent or project_root / ".work"
    parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="verify-", dir=parent))
    try:
        for name in ("Challenge.lean", "comparator-config.json"):
            source = task_dir / name
            if not source.is_file() or source.is_symlink():
                raise VerifierError(ReasonCode.WORKSPACE_ERROR, f"trusted task file is unsafe: {source}")
            shutil.copyfile(source, root / name)
        (root / "lakefile.toml").write_text(_lakefile(project_root), encoding="utf-8")
        return WorkspacePaths(
            root=root,
            challenge=root / "Challenge.lean",
            solution=root / "Solution.lean",
            config=root / "comparator-config.json",
            retained=retain,
        )
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise


def package_solution(paths: WorkspacePaths, task_dir: Path, submission: Submission) -> None:
    try:
        header = (task_dir / "SolutionHeader.lean.txt").read_text(encoding="utf-8")
        footer = (task_dir / "SolutionFooter.lean.txt").read_text(encoding="utf-8")
        paths.solution.write_text(header + submission.text + footer, encoding="utf-8")
    except OSError as exc:
        raise VerifierError(ReasonCode.WORKSPACE_ERROR, f"cannot package solution: {exc}") from exc


def cleanup_workspace(paths: WorkspacePaths) -> None:
    if not paths.retained:
        shutil.rmtree(paths.root, ignore_errors=True)


def build_challenge(paths: WorkspacePaths, env: Mapping[str, str], timeout_seconds: int) -> ProcessResult:
    update = run_process(("lake", "update"), cwd=paths.root, timeout_seconds=timeout_seconds, env=env)
    if update.exit_code != 0 or update.timed_out:
        return update
    return run_process(("lake", "build", "Challenge"), cwd=paths.root, timeout_seconds=timeout_seconds, env=env)


def inspect_generated_target(
    *,
    paths: WorkspacePaths,
    project_root: Path,
    source_module: str,
    source_theorem: str,
    classification: str,
    mode: str,
    env: Mapping[str, str],
    timeout_seconds: int,
) -> tuple[str, str, bool]:
    inspector = project_root / ".lake" / "build" / "bin" / "task_inspector"
    args = (
        "lake",
        "env",
        str(inspector),
        "Challenge",
        "Bounty.target",
        source_module,
        source_theorem,
        classification,
        mode,
    )
    result = run_process(args, cwd=paths.root, timeout_seconds=timeout_seconds, env=env)
    if result.timed_out:
        raise VerifierError(ReasonCode.TIMEOUT, "task inspection timed out")
    if result.exit_code != 0:
        raise VerifierError(ReasonCode.CHALLENGE_BUILD_FAILED, result.stderr[-4000:] or result.stdout[-4000:])
    try:
        candidate = next(
            line.strip()
            for line in reversed(result.stdout.splitlines())
            if line.lstrip().startswith("{")
        )
        payload = json.loads(candidate)
        return (
            sha256_text(str(payload["source_type_canonical"])),
            sha256_text(str(payload["target_type_canonical"])),
            bool(payload["matches_intended_target"]),
        )
    except (StopIteration, ValueError, KeyError, TypeError) as exc:
        raise VerifierError(ReasonCode.CHALLENGE_BUILD_FAILED, f"invalid task inspector output: {exc}") from exc


def target_validator(
    project_root: Path, env: Mapping[str, str] | None = None
) -> Callable[[Path, CatalogDeclaration, object, str], str]:
    effective_env = dict(os.environ if env is None else env)

    def validate(task_dir: Path, declaration: CatalogDeclaration, _generated: object, mode: str) -> str:
        paths = create_workspace(task_dir=task_dir, project_root=project_root)
        try:
            build = build_challenge(paths, effective_env, 1800)
            if build.timed_out:
                raise VerifierError(ReasonCode.TIMEOUT, "generated challenge build timed out")
            if build.exit_code != 0:
                raise VerifierError(
                    ReasonCode.CHALLENGE_BUILD_FAILED, build.stderr[-4000:] or build.stdout[-4000:]
                )
            source_hash, target_hash, matches = inspect_generated_target(
                paths=paths,
                project_root=project_root,
                source_module=declaration.module,
                source_theorem=declaration.theorem,
                classification=declaration.classification.value,
                mode=mode,
                env=effective_env,
                timeout_seconds=600,
            )
            if source_hash != declaration.type_hash:
                raise VerifierError(ReasonCode.SOURCE_TYPE_CHANGED, "source type differs during task generation")
            if not matches:
                raise VerifierError(ReasonCode.STATEMENT_MISMATCH, "generated challenge is not the intended target")
            return target_hash
        finally:
            cleanup_workspace(paths)

    return validate
