from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from verifier.errors import ReasonCode, VerifierError


PINNED_REPOSITORIES = (
    ("formal_conjectures", "formal-conjectures"),
    ("comparator", "comparator"),
    ("lean4export", "lean4export"),
    ("landrun", "landrun"),
    ("nanoda", "nanoda"),
)


def git_output(repo_dir: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_dir,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            shell=False,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise VerifierError(ReasonCode.REPOSITORY_NOT_FOUND, f"cannot inspect repository {repo_dir}: {exc}") from exc
    return result.stdout.strip()


def repository_commit(repo_dir: Path) -> str:
    if not repo_dir.is_dir() or not (repo_dir / ".git").exists():
        raise VerifierError(ReasonCode.REPOSITORY_NOT_FOUND, f"repository not found: {repo_dir}")
    return git_output(repo_dir, "rev-parse", "HEAD")


def assert_repository_commit(repo_dir: Path, expected: str) -> str:
    actual = repository_commit(repo_dir)
    clean = not bool(
        git_output(repo_dir, "--no-optional-locks", "status", "--porcelain", "--untracked-files=no")
    )
    if actual != expected or not clean:
        raise VerifierError(
            ReasonCode.REPOSITORY_COMMIT_MISMATCH,
            f"Formal Conjectures checkout mismatch: expected clean {expected}, found {actual}, clean={clean}",
        )
    return actual


def load_pins(project_root: Path) -> dict[str, Any]:
    path = project_root / "pins.lock.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerifierError(ReasonCode.INVALID_ARGUMENT, f"cannot load dependency pins: {exc}") from exc
    if not isinstance(value, dict):
        raise VerifierError(ReasonCode.INVALID_ARGUMENT, "pins.lock.json must contain an object")
    return value


def formal_conjectures_pin(project_root: Path) -> str:
    try:
        return str(load_pins(project_root)["formal_conjectures"]["commit"])
    except (KeyError, TypeError) as exc:
        raise VerifierError(ReasonCode.INVALID_ARGUMENT, "missing formal_conjectures commit pin") from exc


def dependency_pin_status(project_root: Path) -> dict[str, dict[str, Any]]:
    pins = load_pins(project_root)
    statuses: dict[str, dict[str, Any]] = {}
    for key, directory in PINNED_REPOSITORIES:
        expected = str(pins.get(key, {}).get("commit", ""))
        repository = project_root / "vendor" / directory
        try:
            actual = repository_commit(repository)
            clean = not bool(
                git_output(
                    repository,
                    "--no-optional-locks",
                    "status",
                    "--porcelain",
                    "--untracked-files=no",
                )
            )
        except VerifierError:
            actual = None
            clean = False
        statuses[key] = {
            "expected": expected,
            "actual": actual,
            "clean": clean,
            "pinned": bool(expected) and actual == expected and clean,
        }
    formal_repository = project_root / "vendor" / "formal-conjectures"
    try:
        actual_mathlib = mathlib_pin(formal_repository)
        actual_toolchain = lean_toolchain(formal_repository)
    except VerifierError:
        actual_mathlib = None
        actual_toolchain = None
    expected_mathlib = str(pins.get("mathlib", {}).get("commit", ""))
    expected_toolchain = str(pins.get("lean", {}).get("toolchain", ""))
    statuses["mathlib"] = {
        "expected": expected_mathlib,
        "actual": actual_mathlib,
        "pinned": bool(expected_mathlib) and actual_mathlib == expected_mathlib,
    }
    statuses["lean"] = {
        "expected": expected_toolchain,
        "actual": actual_toolchain,
        "pinned": bool(expected_toolchain) and actual_toolchain == expected_toolchain,
    }
    return statuses


def assert_dependency_pins(project_root: Path) -> dict[str, dict[str, Any]]:
    statuses = dependency_pin_status(project_root)
    drifted = tuple(name for name, status in statuses.items() if not status["pinned"])
    if drifted:
        raise VerifierError(
            ReasonCode.REPOSITORY_COMMIT_MISMATCH,
            f"pinned dependency check failed: {', '.join(drifted)}",
        )
    return statuses


def mathlib_pin(repo_dir: Path) -> str:
    try:
        manifest = json.loads((repo_dir / "lake-manifest.json").read_text(encoding="utf-8"))
        package = next(item for item in manifest["packages"] if item["name"] == "mathlib")
        return str(package["rev"])
    except (OSError, json.JSONDecodeError, KeyError, StopIteration, TypeError) as exc:
        raise VerifierError(ReasonCode.INVALID_ARGUMENT, f"cannot read pinned Mathlib revision: {exc}") from exc


def lean_toolchain(repo_dir: Path) -> str:
    try:
        return (repo_dir / "lean-toolchain").read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise VerifierError(ReasonCode.INVALID_ARGUMENT, f"cannot read Lean toolchain: {exc}") from exc
