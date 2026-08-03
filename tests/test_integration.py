import os
from pathlib import Path

import pytest

from verifier.catalog import load_catalog
from verifier.classification import catalog_statistics
from verifier.errors import ReasonCode
from verifier.models import Classification
from verifier.task_generator import generate_task
from verifier.task_loader import load_task_bundle
from verifier.verification import verify
from verifier.workspace import target_validator


ROOT = Path(__file__).resolve().parent.parent


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
def test_generate_ten_real_challenges_from_distinct_areas(tmp_path):
    catalog = load_catalog(ROOT / "data/catalog.json")
    by_area = {}
    for item in catalog.declarations:
        area = repository_area(item.module)
        if item.classification == Classification.DIRECT_PROP and item.category == "research open":
            by_area.setdefault(area, item)
    selected = tuple(by_area[key] for key in sorted(by_area)[:10])
    assert len(selected) == 10
    validator = target_validator(ROOT)
    tasks = tuple(
        generate_task(
            catalog=catalog,
            declaration=item,
            mode="positive",
            output=tmp_path / f"task-{index}",
            allow_non_open=True,
            validate_target=validator,
        )
        for index, item in enumerate(selected)
    )
    assert len(tasks) == 10


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("FC_RUN_INTEGRATION") != "1", reason="set FC_RUN_INTEGRATION=1")
def test_comparator_accepts_valid_fixture_and_rejects_direct_failures():
    accepted = verify(
        task_dir=ROOT / "examples/simple-direct/task-positive",
        submission_path=ROOT / "examples/valid-submission/Main.lean",
        project_root=ROOT,
        allow_insecure_development=True,
        allow_test_task=True,
    )
    sorry = verify(
        task_dir=ROOT / "examples/simple-direct/task-positive",
        submission_path=ROOT / "examples/sorry-submission/Main.lean",
        project_root=ROOT,
        allow_insecure_development=True,
        allow_test_task=True,
    )
    mismatch = verify(
        task_dir=ROOT / "examples/simple-direct/task-positive",
        submission_path=ROOT / "examples/invalid-statement/Main.lean",
        project_root=ROOT,
        allow_insecure_development=True,
        allow_test_task=True,
    )
    unpermitted = verify(
        task_dir=ROOT / "examples/simple-direct/task-positive",
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
def test_prop_and_numeric_answer_fixtures():
    prop = verify(
        task_dir=ROOT / "examples/answer-prop/task-positive",
        submission_path=ROOT / "examples/answer-prop/Valid.lean",
        project_root=ROOT,
        allow_insecure_development=True,
        allow_test_task=True,
    )
    numeric = verify(
        task_dir=ROOT / "examples/numeric-answer/task-answer",
        submission_path=ROOT / "examples/numeric-answer/Valid.lean",
        project_root=ROOT,
        allow_insecure_development=True,
        allow_test_task=True,
    )
    wrong = verify(
        task_dir=ROOT / "examples/numeric-answer/task-answer",
        submission_path=ROOT / "examples/numeric-answer/Wrong.lean",
        project_root=ROOT,
        allow_insecure_development=True,
        allow_test_task=True,
    )
    nonliteral = verify(
        task_dir=ROOT / "examples/numeric-answer/task-answer",
        submission_path=ROOT / "examples/numeric-answer/NonLiteral.lean",
        project_root=ROOT,
        allow_insecure_development=True,
        allow_test_task=True,
    )
    assert prop.accepted and prop.reason_code == ReasonCode.VERIFIED
    assert numeric.accepted and numeric.reason_code == ReasonCode.VERIFIED
    assert not wrong.accepted
    assert not nonliteral.accepted and nonliteral.reason_code == ReasonCode.SUBMISSION_POLICY_VIOLATION


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("FC_RUN_INTEGRATION") != "1", reason="set FC_RUN_INTEGRATION=1")
def test_counterexample_fixture_accepts_refutation_and_rejects_wrong_or_admitted_proofs():
    task = ROOT / "examples/counterexample/task-counterexample"
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
