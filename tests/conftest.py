from __future__ import annotations

from verifier.models import Catalog, CatalogDeclaration, Classification, TaskManifest


def declaration(
    *,
    theorem: str = "VerifierFixtures.direct",
    classification: Classification = Classification.DIRECT_PROP,
    category: str = "research open",
) -> CatalogDeclaration:
    modes = ("positive", "negative") if classification == Classification.DIRECT_PROP else ("answer",)
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
        task_mode="positive",
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
