from __future__ import annotations

import json
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from verifier.errors import ReasonCode, VerifierError
from verifier.environment import tool_path, trusted_environment
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
    root = json.dumps(str(project_root.resolve()), ensure_ascii=False)
    return (
        'name = "fc_verification_workspace"\n'
        'version = "0.1.0"\n\n'
        '[[require]]\n'
        'name = "formal_conjectures_verifier"\n'
        f"path = {root}\n\n"
        '[[lean_lib]]\nname = "Challenge"\n\n'
        '[lean_lib.leanOptions]\nweak.google.answer = "postpone"\n\n'
        '[[lean_lib]]\nname = "Solution"\n'
        '[lean_lib.leanOptions]\nweak.google.answer = "postpone"\n'
    )


def create_workspace(
    *,
    task_files: Mapping[str, bytes],
    project_root: Path,
    workspace_parent: Path | None = None,
    retain: bool = False,
) -> WorkspacePaths:
    parent = workspace_parent or project_root / ".work"
    parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="verify-", dir=parent))
    try:
        for name in ("Challenge.lean", "comparator-config.json"):
            content = task_files.get(name)
            if content is None:
                raise VerifierError(ReasonCode.WORKSPACE_ERROR, f"trusted task file is missing: {name}")
            destination = root / name
            destination.write_bytes(content)
            destination.chmod(0o600)
        (root / "lakefile.toml").write_text(_lakefile(project_root), encoding="utf-8")
        (root / ".home" / ".tmp").mkdir(parents=True, mode=0o700)
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


def package_solution(
    paths: WorkspacePaths, task_files: Mapping[str, bytes], submission: Submission
) -> None:
    try:
        header = task_files["SolutionHeader.lean.txt"].decode("utf-8", errors="strict")
        footer = task_files["SolutionFooter.lean.txt"].decode("utf-8", errors="strict")
        paths.solution.write_text(header + submission.text + footer, encoding="utf-8")
        paths.solution.chmod(0o600)
    except (KeyError, OSError, UnicodeDecodeError) as exc:
        raise VerifierError(ReasonCode.WORKSPACE_ERROR, f"cannot package solution: {exc}") from exc


def cleanup_workspace(paths: WorkspacePaths) -> None:
    if not paths.retained:
        shutil.rmtree(paths.root, ignore_errors=True)


def _remaining_seconds(deadline: float) -> int:
    return max(1, int(deadline - time.monotonic() + 0.999))


def build_challenge(
    paths: WorkspacePaths,
    lake: Path,
    env: Mapping[str, str],
    timeout_seconds: int,
) -> ProcessResult:
    deadline = time.monotonic() + timeout_seconds
    update = run_process((str(lake), "update"), cwd=paths.root, timeout_seconds=_remaining_seconds(deadline), env=env)
    if update.exit_code != 0 or update.timed_out:
        return update
    return run_process(
        (str(lake), "build", "Challenge"),
        cwd=paths.root,
        timeout_seconds=_remaining_seconds(deadline),
        env=env,
    )


def inspect_generated_target(
    *,
    paths: WorkspacePaths,
    lake: Path,
    project_root: Path,
    source_module: str,
    source_theorem: str,
    classification: str,
    mode: str,
    env: Mapping[str, str],
    timeout_seconds: int,
) -> dict[str, object]:
    inspector = project_root / ".lake" / "build" / "bin" / "task_inspector"
    args = (
        str(lake),
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
        axioms = payload["source_transitive_axioms"]
        if not isinstance(axioms, list) or not all(isinstance(item, str) for item in axioms):
            raise TypeError("source_transitive_axioms must be a string array")
        return {
            "source_hash": sha256_text(str(payload["source_type_canonical"])),
            "target_hash": sha256_text(str(payload["target_type_canonical"])),
            "matches": bool(payload["matches_intended_target"]),
            "target_contains_sorry": bool(payload["target_contains_sorry"]),
            "source_axioms": tuple(sorted(axioms)),
            "source_depends_on_sorry": bool(payload["source_depends_on_sorry"]),
            "source_category": payload.get("source_category"),
            "source_has_formal_proof": bool(payload["source_has_formal_proof"]),
            "source_declaration_kind": str(payload["source_declaration_kind"]),
        }
    except (StopIteration, ValueError, KeyError, TypeError) as exc:
        raise VerifierError(ReasonCode.CHALLENGE_BUILD_FAILED, f"invalid task inspector output: {exc}") from exc


def target_validator(
    project_root: Path,
) -> Callable[[Path, CatalogDeclaration, object, str], str]:
    lake = tool_path(project_root, "lake")

    def validate(task_dir: Path, declaration: CatalogDeclaration, _generated: object, mode: str) -> str:
        task_files = {
            name: (task_dir / name).read_bytes()
            for name in ("Challenge.lean", "comparator-config.json")
        }
        paths = create_workspace(task_files=task_files, project_root=project_root)
        try:
            effective_env = trusted_environment(project_root, paths.root / ".home")
            build = build_challenge(paths, lake, effective_env, 1800)
            if build.timed_out:
                raise VerifierError(ReasonCode.TIMEOUT, "generated challenge build timed out")
            if build.exit_code != 0:
                raise VerifierError(
                    ReasonCode.CHALLENGE_BUILD_FAILED, build.stderr[-4000:] or build.stdout[-4000:]
                )
            inspection = inspect_generated_target(
                paths=paths,
                lake=lake,
                project_root=project_root,
                source_module=declaration.module,
                source_theorem=declaration.theorem,
                classification=declaration.classification.value,
                mode=mode,
                env=effective_env,
                timeout_seconds=600,
            )
            if inspection["source_hash"] != declaration.type_hash:
                raise VerifierError(ReasonCode.SOURCE_TYPE_CHANGED, "source type differs during task generation")
            if not inspection["matches"]:
                raise VerifierError(ReasonCode.STATEMENT_MISMATCH, "generated challenge is not the intended target")
            if declaration.category == "research open" and (
                inspection["source_category"] != "research open"
                or inspection["source_declaration_kind"] != "theorem"
                or not inspection["source_depends_on_sorry"]
                or inspection["source_has_formal_proof"]
                or inspection["target_contains_sorry"]
                or inspection["source_axioms"] != tuple(sorted(declaration.transitive_axioms))
            ):
                raise VerifierError(
                    ReasonCode.INELIGIBLE_TASK,
                    "source production policy differs from the compiled Lean environment",
                )
            return str(inspection["target_hash"])
        finally:
            cleanup_workspace(paths)

    return validate
