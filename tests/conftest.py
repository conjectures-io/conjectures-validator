from __future__ import annotations

from verifier.models import Catalog, CatalogDeclaration, Classification, TaskManifest

# Database tests are destructive, so the suite never discovers a server: there is no default
# DSN and no probe of the shared pytest stack. `tests/database_guard.py` decides, fail-closed,
# from an explicit DSN plus the declared identity of the fixture it must reach.
from database_guard import (  # noqa: E402 - documented above
    IDENTITY_ENV,
    DatabaseGuardError,
    competition_dsn,
    postgres_dsn,
)

DATABASE_SKIP_REASON = (
    "no database: set FC_POSTGRES_DSN and FC_TEST_DATABASE_SYSTEM_IDENTIFIER to a private "
    "fixture (see tests/database_guard.py)"
)

COMPETITION_SKIP_REASON = (
    "no competition database: set FC_COMPETITION_POSTGRES_DSN and "
    "FC_TEST_DATABASE_SYSTEM_IDENTIFIER to a private fixture (see tests/database_guard.py)"
)

__all__ = [
    "COMPETITION_SKIP_REASON",
    "DATABASE_SKIP_REASON",
    "IDENTITY_ENV",
    "DatabaseGuardError",
    "competition_dsn",
    "postgres_dsn",
]


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
