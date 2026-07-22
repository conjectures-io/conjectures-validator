from __future__ import annotations

import json
from pathlib import Path

from verifier.classification import supported_modes
from verifier.errors import ReasonCode, VerifierError
from verifier.hashing import compare_named_hashes, is_sha256
from verifier.models import TaskManifest
from verifier.task_generator import PERMITTED_AXIOMS, task_id


REQUIRED_TRUSTED_FILES = frozenset(
    {
        "Challenge.lean",
        "SolutionHeader.lean.txt",
        "SolutionFooter.lean.txt",
        "comparator-config.json",
        "source-metadata.json",
    }
)


def _validate_manifest(manifest: TaskManifest) -> None:
    if manifest.schema_version != 1:
        raise VerifierError(ReasonCode.INVALID_MANIFEST, "unsupported manifest schema")
    if len(manifest.repository_commit) != 40 or any(
        char not in "0123456789abcdef" for char in manifest.repository_commit
    ):
        raise VerifierError(ReasonCode.INVALID_MANIFEST, "repository commit must be a full lowercase Git hash")
    if manifest.timeout_seconds <= 0 or manifest.max_submission_bytes <= 0 or manifest.adapter_version <= 0:
        raise VerifierError(ReasonCode.INVALID_MANIFEST, "manifest limits and adapter version must be positive")
    source_path = Path(manifest.source_path)
    if (
        not manifest.source_theorem
        or not manifest.source_module
        or any(
            char.isspace() or ord(char) < 32
            for char in manifest.source_theorem + manifest.source_module
        )
        or source_path.is_absolute()
        or ".." in source_path.parts
        or source_path.suffix != ".lean"
    ):
        raise VerifierError(ReasonCode.INVALID_MANIFEST, "manifest source identity or path is invalid")
    expected_id = task_id(
        manifest.repository_commit,
        manifest.source_theorem,
        manifest.task_mode,
        manifest.adapter_version,
    )
    if (
        manifest.task_id != expected_id
        or not manifest.task_mode
        or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in manifest.task_mode)
    ):
        raise VerifierError(ReasonCode.INVALID_MANIFEST, "task ID is not the deterministic ID for this manifest")
    if (
        manifest.challenge_module != "Challenge"
        or manifest.solution_module != "Solution"
        or manifest.target_theorem != "Bounty.target"
        or manifest.theorem_names != ("Bounty.target",)
    ):
        raise VerifierError(ReasonCode.INVALID_MANIFEST, "manifest module or target names are inconsistent")
    if manifest.permitted_axioms != PERMITTED_AXIOMS:
        raise VerifierError(ReasonCode.INVALID_MANIFEST, "manifest permitted axioms differ from verifier policy")
    if manifest.source_theorem not in manifest.forbidden_dependencies:
        raise VerifierError(ReasonCode.INVALID_MANIFEST, "source theorem must be a forbidden proof dependency")
    modes = supported_modes(manifest.classification)
    if modes and manifest.task_mode not in modes:
        raise VerifierError(ReasonCode.INVALID_MANIFEST, "task mode is inconsistent with its classification")
    for label, digest in (
        ("source type", manifest.source_type_hash),
        ("generated target type", manifest.generated_target_type_hash),
    ):
        if not is_sha256(digest):
            raise VerifierError(ReasonCode.INVALID_MANIFEST, f"{label} hash is not SHA-256")
    names = frozenset(manifest.trusted_file_hashes)
    if names != REQUIRED_TRUSTED_FILES:
        raise VerifierError(ReasonCode.INVALID_MANIFEST, "manifest trusted file set is incomplete or unexpected")
    if any(Path(name).name != name or Path(name).is_absolute() for name in names):
        raise VerifierError(ReasonCode.INVALID_MANIFEST, "trusted file names must be local basenames")
    if any(not is_sha256(digest) for digest in manifest.trusted_file_hashes.values()):
        raise VerifierError(ReasonCode.INVALID_MANIFEST, "trusted file hash is not SHA-256")


def load_task(task_dir: Path) -> TaskManifest:
    if not task_dir.is_dir() or task_dir.is_symlink():
        raise VerifierError(ReasonCode.INVALID_MANIFEST, f"task directory not found or unsafe: {task_dir}")
    path = task_dir / "manifest.json"
    if not path.is_file() or path.is_symlink():
        raise VerifierError(ReasonCode.INVALID_MANIFEST, "manifest must be a regular non-symlink file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerifierError(ReasonCode.INVALID_MANIFEST, f"cannot load manifest: {exc}") from exc
    manifest = TaskManifest.from_dict(value)
    _validate_manifest(manifest)
    return manifest


def verify_trusted_hashes(task_dir: Path, manifest: TaskManifest) -> None:
    hash_file = task_dir / "trusted-hashes.json"
    if not hash_file.is_file() or hash_file.is_symlink():
        raise VerifierError(ReasonCode.TRUSTED_FILE_MODIFIED, "trusted-hashes.json is missing or unsafe")
    try:
        recorded = json.loads(hash_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerifierError(ReasonCode.TRUSTED_FILE_MODIFIED, f"cannot load trusted hashes: {exc}") from exc
    if recorded != dict(manifest.trusted_file_hashes):
        raise VerifierError(ReasonCode.TRUSTED_FILE_MODIFIED, "trusted-hashes.json disagrees with manifest")
    mismatches = compare_named_hashes(task_dir, manifest.trusted_file_hashes)
    if mismatches:
        raise VerifierError(ReasonCode.TRUSTED_FILE_MODIFIED, f"trusted files modified: {', '.join(mismatches)}")
    try:
        comparator = json.loads((task_dir / "comparator-config.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerifierError(ReasonCode.TRUSTED_FILE_MODIFIED, f"cannot load Comparator config: {exc}") from exc
    expected = {
        "challenge_module": manifest.challenge_module,
        "solution_module": manifest.solution_module,
        "theorem_names": list(manifest.theorem_names),
        "definition_names": list(manifest.definition_names),
        "permitted_axioms": list(manifest.permitted_axioms),
        "enable_nanoda": manifest.enable_nanoda,
    }
    if comparator != expected:
        raise VerifierError(ReasonCode.INVALID_MANIFEST, "Comparator config disagrees with manifest")
