import json
import os
from pathlib import Path

import pytest

from verifier.catalog import load_catalog
from verifier.classification import catalog_statistics
from verifier.errors import ReasonCode
from verifier.models import Classification
from verifier.repository import tasks_repository_root
from verifier.task_generator import generate_task
from verifier.task_loader import load_task_bundle
from verifier.verification import verify
from verifier.workspace import target_validator


ROOT = Path(__file__).resolve().parent.parent
TASKS_ROOT = tasks_repository_root(ROOT)


def repository_area(module: str) -> str:
    components = module.split(".")
    return components[1] if len(components) > 1 else components[0]


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("FC_RUN_INTEGRATION") != "1", reason="set FC_RUN_INTEGRATION=1")
def test_real_catalog_is_substantial_and_diverse():
    catalog = load_catalog(ROOT / "data/catalog.json")
    assert len(catalog.declarations) > 1000
    assert len({item.category for item in catalog.declarations}) == 5
    assert any(item.contains_answer_annotation for item in catalog.declarations)
    assert len({repository_area(item.module) for item in catalog.declarations}) >= 10
    assert any(item.theorem == "GracefulLabeling.graceful_tree_conjecture" for item in catalog.declarations)
    stats = catalog_statistics(catalog.declarations)
    assert stats["total_declarations"] == len(catalog.declarations)


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("FC_RUN_INTEGRATION") != "1", reason="set FC_RUN_INTEGRATION=1")
def test_generate_ten_audited_production_challenges(tmp_path):
    catalog = load_catalog(ROOT / "data/catalog.json")
    target_policy = json.loads(
        (TASKS_ROOT / "tiers/tier-1/task-targets.json").read_text(encoding="utf-8")
    )
    selected_names = [item["theorem"] for item in target_policy["targets"][:10]]
    declarations = {item.theorem: item for item in catalog.declarations}
    selected = tuple(declarations[name] for name in selected_names)
    assert len(selected) == 10
    assert all(
        item.classification == Classification.DIRECT_PROP
        and item.category == "research open"
        for item in selected
    )
    validator = target_validator(ROOT)
    tasks = tuple(
        generate_task(
            catalog=catalog,
            declaration=item,
            mode="formalized",
            output=tmp_path / f"task-{index}",
            validate_target=validator,
        )
        for index, item in enumerate(selected)
    )
    assert len(tasks) == 10


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("FC_RUN_INTEGRATION") != "1", reason="set FC_RUN_INTEGRATION=1")
@pytest.mark.parametrize("mode", ("formalized", "counterexample"))
def test_generate_direct_prop_with_answer_placeholder_from_live_source(tmp_path, mode):
    catalog = load_catalog(ROOT / "data/catalog.json")
    declaration = next(
        item
        for item in catalog.declarations
        if item.theorem == "Erdos375.erdos_375"
    )
    assert declaration.type_pretty == "True ↔ Erdos375.Erdos375Prop"

    manifest = generate_task(
        catalog=catalog,
        declaration=declaration,
        mode=mode,
        output=tmp_path / f"erdos-375-{mode}",
        validate_target=target_validator(ROOT),
    )

    assert manifest.source_type_hash == declaration.type_hash
    assert (manifest.generated_target_type_hash == declaration.type_hash) == (
        mode == "formalized"
    )


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("FC_RUN_INTEGRATION") != "1", reason="set FC_RUN_INTEGRATION=1")
def test_comparator_accepts_valid_fixture_and_rejects_direct_failures():
    accepted = verify(
        task_dir=TASKS_ROOT / "fixtures/formalized/task-formalized",
        submission_path=ROOT / "examples/valid-submission/Main.lean",
        project_root=ROOT,
        allow_insecure_development=True,
        allow_test_task=True,
    )
    sorry = verify(
        task_dir=TASKS_ROOT / "fixtures/formalized/task-formalized",
        submission_path=ROOT / "examples/sorry-submission/Main.lean",
        project_root=ROOT,
        allow_insecure_development=True,
        allow_test_task=True,
    )
    mismatch = verify(
        task_dir=TASKS_ROOT / "fixtures/formalized/task-formalized",
        submission_path=ROOT / "examples/invalid-statement/Main.lean",
        project_root=ROOT,
        allow_insecure_development=True,
        allow_test_task=True,
    )
    unpermitted = verify(
        task_dir=TASKS_ROOT / "fixtures/formalized/task-formalized",
        submission_path=ROOT / "examples/unpermitted-dependency/Main.lean",
        project_root=ROOT,
        allow_insecure_development=True,
        allow_test_task=True,
    )
    assert accepted.accepted and accepted.reason_code == ReasonCode.VERIFIED
    assert not sorry.accepted and sorry.reason_code == ReasonCode.SUBMISSION_POLICY_VIOLATION
    assert not mismatch.accepted and mismatch.reason_code == ReasonCode.STATEMENT_MISMATCH
    assert not unpermitted.accepted and unpermitted.reason_code == ReasonCode.UNPERMITTED_AXIOM


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("FC_RUN_INTEGRATION") != "1", reason="set FC_RUN_INTEGRATION=1")
def test_numeric_answer_fixture():
    numeric = verify(
        task_dir=TASKS_ROOT / "fixtures/numeric-answer/task-answer",
        submission_path=ROOT / "examples/numeric-answer/Valid.lean",
        project_root=ROOT,
        allow_insecure_development=True,
        allow_test_task=True,
    )
    wrong = verify(
        task_dir=TASKS_ROOT / "fixtures/numeric-answer/task-answer",
        submission_path=ROOT / "examples/numeric-answer/Wrong.lean",
        project_root=ROOT,
        allow_insecure_development=True,
        allow_test_task=True,
    )
    nonliteral = verify(
        task_dir=TASKS_ROOT / "fixtures/numeric-answer/task-answer",
        submission_path=ROOT / "examples/numeric-answer/NonLiteral.lean",
        project_root=ROOT,
        allow_insecure_development=True,
        allow_test_task=True,
    )
    assert numeric.accepted and numeric.reason_code == ReasonCode.VERIFIED
    assert not wrong.accepted
    assert not nonliteral.accepted and nonliteral.reason_code == ReasonCode.SUBMISSION_POLICY_VIOLATION


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("FC_RUN_INTEGRATION") != "1", reason="set FC_RUN_INTEGRATION=1")
def test_counterexample_fixture_accepts_refutation_and_rejects_wrong_or_admitted_proofs():
    task = TASKS_ROOT / "fixtures/counterexample/task-counterexample"
    digest = load_task_bundle(task).sha256
    options = {
        "task_dir": task,
        "project_root": ROOT,
        "expected_task_sha256": digest,
        "allow_insecure_development": True,
    }
    accepted = verify(
        **options,
        submission_path=ROOT / "examples/counterexample/Valid.lean",
    )
    wrong = verify(
        **options,
        submission_path=ROOT / "examples/counterexample/WrongStatement.lean",
    )
    admitted = verify(
        **options,
        submission_path=ROOT / "examples/counterexample/SourceDependency.lean",
    )
    assert accepted.accepted and accepted.reason_code == ReasonCode.VERIFIED
    assert not wrong.accepted and wrong.reason_code == ReasonCode.STATEMENT_MISMATCH
    assert not admitted.accepted
    assert admitted.reason_code == ReasonCode.SUBMISSION_POLICY_VIOLATION
