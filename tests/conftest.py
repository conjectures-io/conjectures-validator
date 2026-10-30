from __future__ import annotations

import json
import os
from pathlib import Path

# Database tests are destructive, so the suite never discovers a server: there is no default DSN
# and no probe of the shared pytest stack. `tests/database_guard.py` decides, fail-closed, from an
# explicit DSN plus the declared identity of the fixture it must reach. `postgres_dsn` and
# `competition_dsn` keep their names and signatures here, so every caller is unchanged.
from database_guard import (
    IDENTITY_ENV,
    DatabaseGuardError,
    competition_dsn,
    isolate_worker,
    postgres_dsn,
)
from verifier.hashing import hash_named_files, pretty_json
from verifier.models import Catalog, CatalogDeclaration, Classification, TaskManifest
from verifier.task_generator import group_task_id, task_id

DATABASE_SKIP_REASON = (
    "no database: set FC_POSTGRES_DSN and FC_TEST_DATABASE_SYSTEM_IDENTIFIER to a private "
    "fixture (see tests/database_guard.py)"
)

# The competition schema lives in a second database in the same private fixture cluster, so the
# one declared identity covers both DSNs.
COMPETITION_SKIP_REASON = (
    "no competition database: set FC_COMPETITION_POSTGRES_DSN and "
    "FC_TEST_DATABASE_SYSTEM_IDENTIFIER to a private fixture (see tests/database_guard.py)"
)

__all__ = [
    "COMPETITION_SKIP_REASON",
    "DATABASE_SKIP_REASON",
    "IDENTITY_ENV",
    "DatabaseGuardError",
    "catalog",
    "competition_dsn",
    "declaration",
    "manifest",
    "postgres_dsn",
]


def pytest_configure(config) -> None:
    # Under `pytest -n`, each worker is its own process with its own databases; see
    # `database_guard.isolate_worker`. Runs before collection, so before any DSN is read.
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if worker:
        isolate_worker(worker)


def declaration(
    *,
    theorem: str = "VerifierFixtures.direct",
    classification: Classification = Classification.DIRECT_PROP,
    category: str = "research open",
) -> CatalogDeclaration:
    if classification == Classification.DIRECT_PROP:
        modes = ("formalized", "counterexample")
    elif classification in {
        Classification.BOOL_ANSWER,
        Classification.NAT_ANSWER,
        Classification.INT_ANSWER,
        Classification.FINITE_ANSWER,
    }:
        modes = ("answer",)
    else:
        modes = ()
    return CatalogDeclaration(
        theorem=theorem,
        module="TestFixtures",
        source_path="lean/TestFixtures.lean",
        category=category,
        ams_subjects=(5,),
        formal_proof_kind=None,
        formal_proof_link=None,
        declaration_kind="theorem",
        type_pretty="True",
        type_hash="sha256:" + "1" * 64,
        contains_answer_annotation=classification != Classification.DIRECT_PROP,
        answer_occurrences=(),
        contains_sorry_in_type=False,
        contains_sorry_in_value=True,
        depends_on_sorry=True,
        transitive_axioms=("sorryAx",),
        has_parameters=False,
        is_prop=True,
        docstring="fixture",
        supported_modes=modes,
        classification=classification,
    )


def catalog(*items: CatalogDeclaration) -> Catalog:
    return Catalog(
        schema_version=1,
        repository_commit="e923379e609b9d5987011a1d1f06ec22ea25cd20",
        lean_toolchain="leanprover/lean4:v4.27.0",
        mathlib_commit="a3a10db0e9d66acbebf76c5e6a135066525ac900",
        generated_by="test",
        extraction_duration_ms=1,
        declarations=tuple(items),
    )


def manifest(*, answer_policy=None, forbidden=()) -> TaskManifest:
    return TaskManifest(
        schema_version=1,
        task_id="fixture",
        repository_commit="e923379e609b9d5987011a1d1f06ec22ea25cd20",
        source_theorem="VerifierFixtures.direct",
        source_module="TestFixtures",
        source_path="lean/TestFixtures.lean",
        source_type_hash="sha256:" + "1" * 64,
        generated_target_type_hash="sha256:" + "2" * 64,
        classification=Classification.DIRECT_PROP,
        task_mode="formalized",
        challenge_module="Challenge",
        solution_module="Solution",
        target_theorem="Bounty.target",
        theorem_names=("Bounty.target",),
        definition_names=(),
        forbidden_dependencies=tuple(forbidden),
        permitted_axioms=("propext", "Quot.sound", "Classical.choice"),
        enable_nanoda=False,
        timeout_seconds=30,
        max_submission_bytes=10000,
        adapter_version=1,
        trusted_file_hashes={},
        production_eligible=True,
        answer_policy=answer_policy or {},
    )


def recommit_trusted_files(task: Path) -> None:
    """Re-hash every trusted payload, as a forger with write access to a bundle would."""
    manifest_path = task / "manifest.json"
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    value["trusted_file_hashes"] = hash_named_files(task, tuple(value["trusted_file_hashes"]))
    manifest_path.write_text(pretty_json(value), encoding="utf-8")
    (task / "trusted-hashes.json").write_text(
        pretty_json(value["trusted_file_hashes"]), encoding="utf-8"
    )


def relabel_adapter_version(task: Path, version: int) -> None:
    """Consistently move a bundle's identity and metadata to another adapter version.

    The Lean payloads are deliberately left alone, so only header reconstruction can tell.
    """
    manifest_path = task / "manifest.json"
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    value["adapter_version"] = version
    metadata_names = ["source-metadata.json"]
    if (task / "group-metadata.json").exists():
        metadata_names.append("group-metadata.json")
        value["task_id"] = group_task_id(
            value["repository_commit"],
            tuple(value["forbidden_dependencies"]),
            value["task_mode"],
            version,
            max_submission_bytes=value["max_submission_bytes"],
        )
    else:
        value["task_id"] = task_id(
            value["repository_commit"],
            value["source_theorem"],
            value["task_mode"],
            version,
            max_submission_bytes=value["max_submission_bytes"],
        )
    manifest_path.write_text(pretty_json(value), encoding="utf-8")
    for name in metadata_names:
        path = task / name
        metadata = json.loads(path.read_text(encoding="utf-8"))
        for source in metadata.get("sources", [metadata]):
            source["adapter_version"] = version
        path.write_text(pretty_json(metadata), encoding="utf-8")
    recommit_trusted_files(task)
