from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from verifier.errors import ReasonCode, VerifierError
from verifier.hashing import pretty_json, sha256_bytes
from verifier.models import Catalog, CatalogDeclaration, TaskManifest
from verifier.task_loader import TaskBundle
from verifier.task_policy import GOLD_TASK_MODE, production_eligibility


GOLD_POOL_SCHEMA_VERSION = 2
DEFAULT_GOLD_POOL_SIZE = 64
GOLD_POOL_SELECTION = "balanced-repository-area-v1"


@dataclass(frozen=True)
class RetiredSources:
    repository_commit: str
    theorems: frozenset[str]
    sha256: str


def repository_area(declaration: CatalogDeclaration) -> str:
    components = declaration.module.split(".")
    return components[1] if len(components) > 1 else components[0]


def load_retired_sources(path: Path) -> RetiredSources:
    try:
        content = path.read_bytes()
        value = json.loads(content.decode("utf-8", errors="strict"))
        if not isinstance(value, dict) or set(value) != {
            "repository_commit",
            "schema_version",
            "source_theorems",
        }:
            raise ValueError("retired source field set is not exact")
        repository_commit = value["repository_commit"]
        theorems = value["source_theorems"]
        if (
            type(value["schema_version"]) is not int
            or value["schema_version"] != 1
            or not isinstance(repository_commit, str)
            or len(repository_commit) != 40
            or any(character not in "0123456789abcdef" for character in repository_commit)
            or not isinstance(theorems, list)
            or not theorems
            or not all(isinstance(theorem, str) and theorem for theorem in theorems)
            or theorems != sorted(theorems)
            or len(theorems) != len(set(theorems))
        ):
            raise ValueError("retired source data is invalid")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise VerifierError(
            ReasonCode.INVALID_ARGUMENT,
            f"cannot load retired gold sources: {exc}",
        ) from exc
    return RetiredSources(
        repository_commit=repository_commit,
        theorems=frozenset(theorems),
        sha256=sha256_bytes(content),
    )


def select_gold_declarations(
    *,
    catalog: Catalog,
    retired: RetiredSources,
    pool_size: int = DEFAULT_GOLD_POOL_SIZE,
) -> tuple[CatalogDeclaration, ...]:
    if retired.repository_commit != catalog.repository_commit:
        raise VerifierError(
            ReasonCode.REPOSITORY_COMMIT_MISMATCH,
            "retired gold sources and catalog use different repository commits",
        )
    if pool_size <= 0:
        raise VerifierError(ReasonCode.INVALID_ARGUMENT, "gold pool size must be positive")

    retired_declarations = tuple(
        declaration
        for declaration in catalog.declarations
        if declaration.theorem in retired.theorems
    )
    if len(retired_declarations) != len(retired.theorems):
        raise VerifierError(
            ReasonCode.THEOREM_NOT_FOUND,
            "one or more retired gold sources are missing from the pinned catalog",
        )
    retired_paths = {declaration.source_path for declaration in retired_declarations}
    retired_types = {declaration.type_hash for declaration in retired_declarations}
    candidates: list[CatalogDeclaration] = []
    used_paths: set[str] = set()
    used_types: set[str] = set()
    for declaration in catalog.declarations:
        eligible, _violations, _collisions = production_eligibility(
            catalog,
            declaration,
            GOLD_TASK_MODE,
        )
        if (
            not eligible
            or declaration.theorem in retired.theorems
            or declaration.source_path in retired_paths
            or declaration.type_hash in retired_types
            or declaration.source_path in used_paths
            or declaration.type_hash in used_types
        ):
            continue
        used_paths.add(declaration.source_path)
        used_types.add(declaration.type_hash)
        candidates.append(declaration)

    by_area: dict[str, list[CatalogDeclaration]] = defaultdict(list)
    for declaration in candidates:
        by_area[repository_area(declaration)].append(declaration)
    for declarations in by_area.values():
        declarations.sort(key=lambda item: item.theorem)

    selected: list[CatalogDeclaration] = []
    while len(selected) < pool_size:
        progressed = False
        for area in sorted(by_area):
            if by_area[area] and len(selected) < pool_size:
                selected.append(by_area[area].pop(0))
                progressed = True
        if not progressed:
            break
    if len(selected) != pool_size:
        raise VerifierError(
            ReasonCode.INVALID_ARGUMENT,
            f"requested {pool_size} gold tasks but only {len(selected)} are eligible",
        )
    return tuple(selected)


def build_gold_allowlist(
    *,
    catalog: Catalog,
    retired: RetiredSources,
    selected: Iterable[CatalogDeclaration],
    bundles: Iterable[TaskBundle],
    audit_date_utc: str,
) -> bytes:
    declarations = tuple(selected)
    task_bundles = tuple(bundles)
    if len(declarations) != len(task_bundles) or not declarations:
        raise VerifierError(
            ReasonCode.INVALID_ARGUMENT,
            "gold declarations and generated bundles must be non-empty and one-to-one",
        )
    catalog_indices = {
        declaration.theorem: index
        for index, declaration in enumerate(catalog.declarations)
    }
    sources = []
    tasks = []
    for declaration, bundle in zip(declarations, task_bundles, strict=True):
        manifest: TaskManifest = bundle.manifest
        if (
            manifest.source_theorem != declaration.theorem
            or manifest.task_mode != GOLD_TASK_MODE
            or not manifest.production_eligible
            or manifest.source_type_hash != declaration.type_hash
            or manifest.generated_target_type_hash != declaration.type_hash
        ):
            raise VerifierError(
                ReasonCode.INVALID_MANIFEST,
                f"generated gold task is not the exact source formalization: {declaration.theorem}",
            )
        source_index = catalog_indices[declaration.theorem]
        sources.append(
            {
                "index": source_index,
                "source_path": declaration.source_path,
                "source_type_sha256": declaration.type_hash,
                "theorem": declaration.theorem,
            }
        )
        tasks.append(
            {
                "mode": GOLD_TASK_MODE,
                "source_index": source_index,
                "source_path": declaration.source_path,
                "target_type_sha256": manifest.generated_target_type_hash,
                "task_bundle_sha256": bundle.sha256,
                "task_id": manifest.task_id,
                "theorem": declaration.theorem,
            }
        )
    value = {
        "allowed_source_theorems": sources,
        "allowed_task_bundles": tasks,
        "audit_date_utc": audit_date_utc,
        "default": "DENY",
        "pool_policy": {
            "classification": "DIRECT_PROP",
            "compiled_target_validation": True,
            "exact_source_type": True,
            "mode": GOLD_TASK_MODE,
            "one_task_per_source_path": True,
            "pool_size": len(declarations),
            "retired_source_theorems_sha256": retired.sha256,
            "selection": GOLD_POOL_SELECTION,
            "source_category": "research open",
            "synthetic_negation": False,
        },
        "repository_commit": catalog.repository_commit,
        "schema_version": GOLD_POOL_SCHEMA_VERSION,
    }
    return pretty_json(value).encode("utf-8")
