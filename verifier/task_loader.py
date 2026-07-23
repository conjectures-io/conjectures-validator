from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from verifier.classification import supported_modes
from verifier.errors import ReasonCode, VerifierError
from verifier.hashing import is_sha256, sha256_bytes, sha256_named_bytes
from verifier.models import CatalogDeclaration, Classification, TaskManifest
from verifier.task_generator import (
    MAX_SUBMISSION_BYTES,
    MAX_TIMEOUT_SECONDS,
    PERMITTED_AXIOMS,
    LEAN_MODULE_NAME,
    task_id,
    trusted_task_payloads,
)
from verifier.task_policy import (
    GOLD_TASK_MODE,
    expected_answer_policy,
    production_policy_violations,
)


REQUIRED_TRUSTED_FILES = frozenset(
    {
        "Challenge.lean",
        "SolutionHeader.lean.txt",
        "SolutionFooter.lean.txt",
        "comparator-config.json",
        "source-metadata.json",
    }
)
TASK_FILE_NAMES = REQUIRED_TRUSTED_FILES | {"manifest.json", "trusted-hashes.json"}
MAX_TASK_FILE_BYTES = 8 * 1024 * 1024
MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "task_id",
        "repository_commit",
        "source_theorem",
        "source_module",
        "source_path",
        "source_type_hash",
        "generated_target_type_hash",
        "classification",
        "task_mode",
        "challenge_module",
        "solution_module",
        "target_theorem",
        "theorem_names",
        "definition_names",
        "forbidden_dependencies",
        "permitted_axioms",
        "enable_nanoda",
        "timeout_seconds",
        "max_submission_bytes",
        "adapter_version",
        "trusted_file_hashes",
        "production_eligible",
        "known_proof_collisions",
        "answer_policy",
    }
)


@dataclass(frozen=True)
class TaskBundle:
    manifest: TaskManifest
    source: CatalogDeclaration
    files: Mapping[str, bytes]
    sha256: str


def _read_regular_file(path: Path, max_bytes: int = MAX_TASK_FILE_BYTES) -> bytes:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise VerifierError(ReasonCode.INVALID_MANIFEST, f"cannot safely open task file {path.name}: {exc}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise VerifierError(ReasonCode.INVALID_MANIFEST, f"task file is not regular: {path.name}")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            content = handle.read(max_bytes + 1)
    finally:
        os.close(descriptor)
    if len(content) > max_bytes:
        raise VerifierError(ReasonCode.INVALID_MANIFEST, f"task file is too large: {path.name}")
    return content


def _json_object(content: bytes, name: str) -> Mapping[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = item
        return result

    try:
        value = json.loads(
            content.decode("utf-8", errors="strict"),
            object_pairs_hook=unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"invalid JSON constant: {value}")),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise VerifierError(ReasonCode.INVALID_MANIFEST, f"cannot parse {name}: {exc}") from exc
    if not isinstance(value, dict):
        raise VerifierError(ReasonCode.INVALID_MANIFEST, f"{name} must contain a JSON object")
    return value


def _validate_manifest_json(value: Mapping[str, Any]) -> None:
    if frozenset(value) != MANIFEST_FIELDS:
        unexpected = sorted(frozenset(value) - MANIFEST_FIELDS)
        missing = sorted(MANIFEST_FIELDS - frozenset(value))
        raise VerifierError(
            ReasonCode.INVALID_MANIFEST,
            f"manifest field set is not exact; missing={missing}, unexpected={unexpected}",
        )
    integer_fields = ("schema_version", "timeout_seconds", "max_submission_bytes", "adapter_version")
    boolean_fields = ("enable_nanoda", "production_eligible")
    string_fields = (
        "task_id",
        "repository_commit",
        "source_theorem",
        "source_module",
        "source_path",
        "source_type_hash",
        "generated_target_type_hash",
        "classification",
        "task_mode",
        "challenge_module",
        "solution_module",
        "target_theorem",
    )
    for field in integer_fields:
        if type(value.get(field)) is not int:
            raise VerifierError(ReasonCode.INVALID_MANIFEST, f"manifest field {field} must be an integer")
    for field in boolean_fields:
        if type(value.get(field)) is not bool:
            raise VerifierError(ReasonCode.INVALID_MANIFEST, f"manifest field {field} must be a boolean")
    for field in string_fields:
        if not isinstance(value.get(field), str):
            raise VerifierError(ReasonCode.INVALID_MANIFEST, f"manifest field {field} must be a string")
    if not isinstance(value.get("trusted_file_hashes"), dict) or not isinstance(value.get("answer_policy", {}), dict):
        raise VerifierError(ReasonCode.INVALID_MANIFEST, "manifest mappings have invalid types")
    for field in (
        "theorem_names",
        "definition_names",
        "forbidden_dependencies",
        "permitted_axioms",
        "known_proof_collisions",
    ):
        items = value.get(field, [])
        if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
            raise VerifierError(ReasonCode.INVALID_MANIFEST, f"manifest field {field} must be a string array")


def _validate_source_json(value: Mapping[str, Any]) -> None:
    for field in (
        "contains_answer_annotation",
        "contains_sorry_in_type",
        "contains_sorry_in_value",
        "depends_on_sorry",
        "has_parameters",
        "is_prop",
    ):
        if type(value.get(field)) is not bool:
            raise VerifierError(ReasonCode.INVALID_MANIFEST, f"source metadata field {field} must be a boolean")
    axioms = value.get("transitive_axioms")
    if not isinstance(axioms, list) or not all(isinstance(item, str) for item in axioms):
        raise VerifierError(ReasonCode.INVALID_MANIFEST, "source transitive axioms must be a string array")


def _validate_manifest(manifest: TaskManifest) -> None:
    if manifest.schema_version != 1:
        raise VerifierError(ReasonCode.INVALID_MANIFEST, "unsupported manifest schema")
    if len(manifest.repository_commit) != 40 or any(
        char not in "0123456789abcdef" for char in manifest.repository_commit
    ):
        raise VerifierError(ReasonCode.INVALID_MANIFEST, "repository commit must be a full lowercase Git hash")
    if not 0 < manifest.timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise VerifierError(ReasonCode.INVALID_MANIFEST, "manifest timeout exceeds verifier policy")
    if not 0 < manifest.max_submission_bytes <= MAX_SUBMISSION_BYTES:
        raise VerifierError(ReasonCode.INVALID_MANIFEST, "manifest submission size exceeds verifier policy")
    if manifest.adapter_version <= 0:
        raise VerifierError(ReasonCode.INVALID_MANIFEST, "adapter version must be positive")
    source_path = Path(manifest.source_path)
    if (
        not manifest.source_theorem
        or not manifest.source_module
        or any(char.isspace() or ord(char) < 32 for char in manifest.source_theorem)
        or LEAN_MODULE_NAME.fullmatch(manifest.source_module) is None
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
    if manifest.forbidden_dependencies != (manifest.source_theorem,):
        raise VerifierError(ReasonCode.INVALID_MANIFEST, "forbidden proof dependencies must contain exactly the source theorem")
    modes = supported_modes(manifest.classification)
    exact_formalized_mode = (
        manifest.classification == Classification.DIRECT_PROP
        and manifest.task_mode == GOLD_TASK_MODE
    )
    if modes and manifest.task_mode not in modes and not exact_formalized_mode:
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
    if manifest.production_eligible and manifest.known_proof_collisions:
        raise VerifierError(ReasonCode.INVALID_MANIFEST, "production task records known proof collisions")
    if (
        manifest.production_eligible
        and (
            manifest.task_mode != GOLD_TASK_MODE
            or manifest.generated_target_type_hash != manifest.source_type_hash
        )
    ):
        raise VerifierError(
            ReasonCode.INVALID_MANIFEST,
            "production task must preserve the exact source theorem type",
        )


def _validate_source_metadata(manifest: TaskManifest, source: CatalogDeclaration) -> None:
    expected = (
        (source.theorem, manifest.source_theorem, "theorem"),
        (source.module, manifest.source_module, "module"),
        (source.source_path, manifest.source_path, "source path"),
        (source.type_hash, manifest.source_type_hash, "source type hash"),
        (source.classification, manifest.classification, "classification"),
    )
    mismatch = next((label for actual, recorded, label in expected if actual != recorded), None)
    if mismatch is not None:
        raise VerifierError(ReasonCode.INVALID_MANIFEST, f"source metadata {mismatch} disagrees with manifest")
    expected_policy = expected_answer_policy(source)
    if dict(manifest.answer_policy) != expected_policy:
        raise VerifierError(ReasonCode.INVALID_MANIFEST, "answer policy disagrees with source classification")
    expected_definitions = ("Bounty.submittedAnswer",) if expected_policy else ()
    if manifest.definition_names != expected_definitions:
        raise VerifierError(ReasonCode.INVALID_MANIFEST, "definition targets disagree with source classification")
    violations = production_policy_violations(
        source,
        manifest.known_proof_collisions,
        manifest.task_mode,
    )
    if manifest.production_eligible != (not violations):
        raise VerifierError(ReasonCode.INVALID_MANIFEST, "production eligibility disagrees with source metadata")


def load_task_bundle(task_dir: Path) -> TaskBundle:
    if not task_dir.is_dir() or task_dir.is_symlink():
        raise VerifierError(ReasonCode.INVALID_MANIFEST, f"task directory not found or unsafe: {task_dir}")
    try:
        with os.scandir(task_dir) as entries:
            entry_names = frozenset(entry.name for entry in entries)
    except OSError as exc:
        raise VerifierError(ReasonCode.INVALID_MANIFEST, f"cannot list task directory: {exc}") from exc
    if entry_names != TASK_FILE_NAMES:
        extra = sorted(entry_names - TASK_FILE_NAMES)
        missing = sorted(TASK_FILE_NAMES - entry_names)
        raise VerifierError(
            ReasonCode.INVALID_MANIFEST,
            f"task file set is not exact; missing={missing}, unexpected={extra}",
        )
    files = {name: _read_regular_file(task_dir / name) for name in sorted(TASK_FILE_NAMES)}
    manifest_json = _json_object(files["manifest.json"], "manifest.json")
    _validate_manifest_json(manifest_json)
    manifest = TaskManifest.from_dict(manifest_json)
    _validate_manifest(manifest)
    recorded = _json_object(files["trusted-hashes.json"], "trusted-hashes.json")
    if recorded != dict(manifest.trusted_file_hashes):
        raise VerifierError(ReasonCode.TRUSTED_FILE_MODIFIED, "trusted-hashes.json disagrees with manifest")
    mismatches = tuple(
        name
        for name, digest in sorted(manifest.trusted_file_hashes.items())
        if sha256_bytes(files[name]) != digest
    )
    if mismatches:
        raise VerifierError(ReasonCode.TRUSTED_FILE_MODIFIED, f"trusted files modified: {', '.join(mismatches)}")
    comparator = _json_object(files["comparator-config.json"], "comparator-config.json")
    expected_config = {
        "challenge_module": manifest.challenge_module,
        "solution_module": manifest.solution_module,
        "theorem_names": list(manifest.theorem_names),
        "definition_names": list(manifest.definition_names),
        "permitted_axioms": list(manifest.permitted_axioms),
        "enable_nanoda": manifest.enable_nanoda,
    }
    if comparator != expected_config:
        raise VerifierError(ReasonCode.INVALID_MANIFEST, "Comparator config disagrees with manifest")
    source_json = _json_object(files["source-metadata.json"], "source-metadata.json")
    _validate_source_json(source_json)
    source = CatalogDeclaration.from_dict(source_json)
    _validate_source_metadata(manifest, source)
    expected_payloads = trusted_task_payloads(
        source,
        manifest.task_mode,
        manifest.enable_nanoda,
        manifest.repository_commit,
        manifest.adapter_version,
    )
    regenerated_mismatches = tuple(
        name for name, expected in sorted(expected_payloads.items()) if files[name] != expected
    )
    if regenerated_mismatches:
        raise VerifierError(
            ReasonCode.TRUSTED_FILE_MODIFIED,
            "trusted task payload was not produced by the pinned generator: "
            + ", ".join(regenerated_mismatches),
        )
    frozen_files = MappingProxyType(files)
    return TaskBundle(manifest, source, frozen_files, sha256_named_bytes(frozen_files))


def load_task(task_dir: Path) -> TaskManifest:
    return load_task_bundle(task_dir).manifest


def verify_trusted_hashes(task_dir: Path, manifest: TaskManifest) -> None:
    loaded = load_task_bundle(task_dir).manifest
    if loaded != manifest:
        raise VerifierError(ReasonCode.INVALID_MANIFEST, "task manifest changed while loading trusted files")
