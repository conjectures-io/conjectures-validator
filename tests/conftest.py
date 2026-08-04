from __future__ import annotations

import os
from functools import cache

from verifier.models import Catalog, CatalogDeclaration, Classification, TaskManifest

# The stack in docker-compose.pytest-db.yml, credentials and all. Duplicated there rather than
# read from a file so neither side can drift into pointing somewhere else, and separate from the
# development database so a suite that drops and recreates the schema cannot reach real data.
#
# It lives here rather than in conftest_api because more than the API tests need a database, and
# this module has no imports that a half-finished refactor elsewhere can break.
PYTEST_DSN = (
    "postgresql+psycopg://conjectures-pytest:conjectures-pytest-pw"
    "@127.0.0.1:5440/conjectures-pytest"
)


def _reachable(dsn: str) -> bool:
    """Whether a server is actually answering on `dsn`.

    Probed rather than assumed: the alternative to skipping is every database test failing with
    a connection error, which reads like a broken suite rather than a stack that is not up.
    """
    try:
        import psycopg
    except ModuleNotFoundError:  # pragma: no cover - psycopg is a test dependency
        return False
    # psycopg wants a libpq DSN; the SQLAlchemy driver suffix is not part of one.
    libpq = dsn.replace("postgresql+psycopg://", "postgresql://", 1)
    try:
        with psycopg.connect(libpq, connect_timeout=2):
            return True
    except (psycopg.Error, OSError):
        return False


@cache
def postgres_dsn() -> str | None:
    """The database tests' DSN, or None to skip them.

    Cached because this opens a connection and is called once per harness. `FC_POSTGRES_DSN`
    wins when set, so pointing the suite at another server stays possible; otherwise the fixed
    pytest stack is used if it is up, which is what makes the tests need no configuration.
    """
    explicit = os.environ.get("FC_POSTGRES_DSN", "").strip()
    if explicit:
        return explicit
    return PYTEST_DSN if _reachable(PYTEST_DSN) else None


DATABASE_SKIP_REASON = (
    "no database: run `docker compose -f docker-compose.pytest-db.yml up -d`"
)


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
