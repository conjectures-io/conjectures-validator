from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from verifier.classification import catalog_statistics, supported_modes
from verifier.errors import ReasonCode, VerifierError
from verifier.hashing import is_sha256, pretty_json, sha256_text
from verifier.models import CATEGORY_ORDER, Catalog, CatalogDeclaration
from verifier.process import run_process
from verifier.repository import assert_repository_commit, lean_toolchain, mathlib_pin


def _extract_json(stdout: str) -> Mapping[str, Any]:
    candidates = tuple(line.strip() for line in stdout.splitlines() if line.lstrip().startswith("{"))
    value = None
    for candidate in reversed(candidates):
        try:
            value = json.loads(candidate)
            break
        except json.JSONDecodeError:
            continue
    if value is None:
        raise VerifierError(ReasonCode.INTERNAL_ERROR, "Lean catalog extractor did not emit valid JSON")
    if not isinstance(value, dict):
        raise VerifierError(ReasonCode.INTERNAL_ERROR, "Lean catalog extractor did not emit a JSON object")
    return value


def _normalize_declaration(value: Mapping[str, Any]) -> CatalogDeclaration:
    canonical = str(value["type_canonical"])
    merged = {key: item for key, item in value.items() if key != "type_canonical"}
    merged = {**merged, "type_hash": sha256_text(canonical)}
    return CatalogDeclaration.from_dict(merged)


def build_catalog(
    *,
    repo_dir: Path,
    output: Path,
    project_root: Path,
    expected_commit: str,
    command_runner: Callable[..., Any] = run_process,
) -> Catalog:
    assert_repository_commit(repo_dir, expected_commit)
    extractor = project_root / "lean" / "CatalogExtractor.lean"
    if not extractor.is_file():
        raise VerifierError(ReasonCode.INTERNAL_ERROR, f"catalog extractor not found: {extractor}")
    env = dict(os.environ)
    started = time.monotonic_ns()
    source_directory = repo_dir / "FormalConjectures"
    command = ("lake", "exe", "catalog_extractor", str(source_directory))
    result = command_runner(
        command,
        cwd=project_root,
        timeout_seconds=7200,
        env=env,
        max_output_bytes=None,
    )
    if result.timed_out:
        raise VerifierError(ReasonCode.TIMEOUT, "catalog extraction timed out")
    if result.exit_code != 0:
        detail = result.stderr[-4000:] or result.stdout[-4000:]
        raise VerifierError(ReasonCode.INTERNAL_ERROR, f"catalog extraction failed: {detail}")
    raw = _extract_json(result.stdout)
    raw_declarations = raw.get("declarations")
    if not isinstance(raw_declarations, list):
        raise VerifierError(ReasonCode.INTERNAL_ERROR, "catalog extractor output has no declarations array")
    declarations = tuple(
        sorted((_normalize_declaration(dict(item)) for item in raw_declarations), key=lambda item: item.theorem)
    )
    catalog = Catalog(
        schema_version=1,
        repository_commit=expected_commit,
        lean_toolchain=lean_toolchain(repo_dir),
        mathlib_commit=mathlib_pin(repo_dir),
        generated_by="lean/CatalogExtractor.lean",
        extraction_duration_ms=(time.monotonic_ns() - started) // 1_000_000,
        declarations=declarations,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(pretty_json(catalog.to_dict()), encoding="utf-8")
    summary = output.with_name("catalog-summary.json")
    summary.write_text(pretty_json(catalog_statistics(catalog.declarations)), encoding="utf-8")
    return catalog


def load_catalog(path: Path) -> Catalog:
    if not path.is_file():
        raise VerifierError(ReasonCode.CATALOG_NOT_FOUND, f"catalog not found: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerifierError(ReasonCode.INVALID_ARGUMENT, f"cannot load catalog: {exc}") from exc
    catalog = Catalog.from_dict(value)
    if catalog.schema_version != 1:
        raise VerifierError(ReasonCode.INVALID_ARGUMENT, "unsupported catalog schema")
    if len(catalog.repository_commit) != 40 or any(
        char not in "0123456789abcdef" for char in catalog.repository_commit
    ):
        raise VerifierError(ReasonCode.INVALID_ARGUMENT, "catalog repository commit is invalid")
    names = tuple(item.theorem for item in catalog.declarations)
    if len(names) != len(set(names)):
        raise VerifierError(ReasonCode.INVALID_ARGUMENT, "catalog contains duplicate declarations")
    invalid = next(
        (
            item
            for item in catalog.declarations
            if item.category not in CATEGORY_ORDER
            or not item.theorem
            or not item.module
            or any(char.isspace() or ord(char) < 32 for char in item.theorem + item.module)
            or not is_sha256(item.type_hash)
            or item.supported_modes != supported_modes(item.classification)
            or Path(item.source_path).is_absolute()
            or ".." in Path(item.source_path).parts
            or Path(item.source_path).suffix != ".lean"
        ),
        None,
    )
    if invalid is not None:
        raise VerifierError(ReasonCode.INVALID_ARGUMENT, f"invalid catalog declaration: {invalid.theorem}")
    return catalog


def find_declaration(catalog: Catalog, theorem: str) -> CatalogDeclaration:
    match = next((item for item in catalog.declarations if item.theorem == theorem), None)
    if match is None:
        raise VerifierError(ReasonCode.THEOREM_NOT_FOUND, f"theorem not found in catalog: {theorem}")
    return match
