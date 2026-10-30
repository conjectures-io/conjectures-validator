"""`verify()` on a v2 task: the environment is checked before the proof is read, the compiled
statement before the comparator runs, and every mismatch is an operator outcome, never a verdict.

The Lean toolchain is replaced by stubs at the module boundary; the in-container harness runs the
same checks for real (W/scripts/container-controls.py).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from version_fixtures import COMMIT_A, MATHLIB_COMMIT, CountingValidator, declaration, index, standard
from verification_worker.outcomes import Outcome, classify
from verifier import verification
from verifier.errors import ReasonCode
from verifier.models import Catalog, ProcessResult
from verifier.task_generator import build_task_version, plan_task_version
from verifier.task_loader import load_task_bundle

PAIR = "FormalConjectures.Problems.Pair"


@pytest.fixture
def v2_task(tmp_path):
    env = standard(tmp_path / "env")
    decl = declaration("Pair.first", PAIR)
    built = index(env, [decl])
    catalog = Catalog(1, COMMIT_A, env.toolchain, MATHLIB_COMMIT, "test", 0, (decl,))
    plan = plan_task_version(catalog=catalog, declaration=decl, mode="formalized",
                             environment=built.environment, dependency=built.dependencies[decl.theorem])
    task = tmp_path / "task"
    build_task_version(plan, catalog=catalog, output=task, validate_target=CountingValidator())
    proof = tmp_path / "Main.lean"
    proof.write_text("theorem target : True := trivial\n")
    return env, built, task, proof


def stub_environment(monkeypatch, *, environment, read_proof_ok=True):
    monkeypatch.setattr(verification, "assert_dependency_pins", lambda root: {})
    monkeypatch.setattr(verification, "formal_conjectures_pin", lambda root: COMMIT_A)
    monkeypatch.setattr(verification, "repository_commit", lambda path: COMMIT_A)
    monkeypatch.setattr(verification, "derive_environment_identity", lambda root: environment)
    if not read_proof_ok:
        def refuse(*args, **kwargs):
            raise AssertionError("the proof must not be read before the environment is accepted")

        monkeypatch.setattr(verification, "load_submission", refuse)


def test_a_v2_task_from_another_environment_is_refused_before_the_proof_is_read(monkeypatch, v2_task):
    env, built, task, proof = v2_task
    other = env.environment(lean_toolchain="leanprover/lean4:v4.36.0")
    stub_environment(monkeypatch, environment=other, read_proof_ok=False)
    bundle = load_task_bundle(task)
    report = verification.verify(task_dir=task, submission_path=proof, project_root=env.project,
                                 expected_task_sha256=bundle.sha256, expected_build_provenance_sha256=built.provenance["Pair.first"].sha256)
    assert report.reason_code == ReasonCode.ENVIRONMENT_MISMATCH and report.stage == "LOAD_TASK"
    assert not report.accepted and report.submission_sha256 == ""
    assert classify(report.reason_code) is Outcome.OPERATOR


def test_production_verification_of_a_v2_task_requires_the_snapshot_provenance(monkeypatch, v2_task):
    env, built, task, proof = v2_task
    stub_environment(monkeypatch, environment=built.environment, read_proof_ok=False)
    bundle = load_task_bundle(task)
    assert bundle.manifest.production_eligible
    report = verification.verify(task_dir=task, submission_path=proof, project_root=env.project,
                                 expected_task_sha256=bundle.sha256)
    assert report.reason_code == ReasonCode.ENVIRONMENT_MISMATCH
    assert "build provenance" in report.stderr_tail


def test_a_v2_report_names_the_environment_commit_that_ran_it(monkeypatch, v2_task):
    env, built, task, proof = v2_task
    other = env.environment(generator_version="older")
    stub_environment(monkeypatch, environment=other)
    report = verification.verify(task_dir=task, submission_path=proof, project_root=env.project,
                                 expected_task_sha256=load_task_bundle(task).sha256,
                                 expected_build_provenance_sha256=built.provenance["Pair.first"].sha256)
    assert report.repository_commit == COMMIT_A


def test_a_compiled_statement_with_another_dependency_identity_is_refused_before_comparator(monkeypatch, v2_task):
    env, built, task, proof = v2_task
    stub_environment(monkeypatch, environment=built.environment)
    bundle = load_task_bundle(task)

    class Tools:
        sandbox_mode = "landrun+seccomp"

    monkeypatch.setattr(verification, "resolve_tools", lambda root, insecure_development=False: Tools())
    monkeypatch.setattr(verification, "production_sandbox_available", lambda tools, root: True)
    monkeypatch.setattr(verification, "tool_path", lambda root, name: Path("/bin/true"))
    monkeypatch.setattr(verification, "trusted_environment", lambda root, home: {})

    class Paths:
        root = task
        retained = False

    monkeypatch.setattr(verification, "create_workspace", lambda **kwargs: Paths())
    monkeypatch.setattr(verification, "cleanup_workspace", lambda paths: None)
    monkeypatch.setattr(verification, "build_challenge", lambda *args: ProcessResult((), 0, "", "", 1))
    monkeypatch.setattr(verification.SourceTrees, "for_project", classmethod(lambda cls, root: env.trees))
    # The source definition's body changed in this environment after the task was published.
    env.statements["Pair.first"][1]["Pair.onlyFirst"]["nodes"][1] = ["C", ["only", "v9"], []]

    def inspect(**kwargs):
        return {
            "closure": env.report([(PAIR, "Pair.first")], canonical=False),
            "source_hash": bundle.source.type_hash,
            "target_hash": bundle.manifest.generated_target_type_hash,
            "matches": True,
            "target_contains_sorry": False,
            "source_axioms": tuple(sorted(bundle.source.transitive_axioms)),
            "source_depends_on_sorry": True,
            "source_category": "research open",
            "source_has_formal_proof": False,
            "source_declaration_kind": "theorem",
        }

    monkeypatch.setattr(verification, "inspect_generated_target", inspect)

    def no_comparator(**kwargs):
        raise AssertionError("the comparator must not run on a statement that is not the task's")

    monkeypatch.setattr(verification, "run_comparator", no_comparator)
    report = verification.verify(task_dir=task, submission_path=proof, project_root=env.project,
                                 expected_task_sha256=bundle.sha256,
                                 expected_build_provenance_sha256=built.provenance["Pair.first"].sha256)
    assert report.reason_code == ReasonCode.DEPENDENCY_IDENTITY_MISMATCH and report.stage == "BUILD_CHALLENGE"
    assert classify(report.reason_code) is Outcome.OPERATOR


def test_a_modern_image_that_failed_its_identity_stops_startup_but_an_old_image_is_legacy():
    from verification_worker.runner import RunnerFailure, instance_from_doctor
    from verifier.version_registry import Instance

    commit = "4b69a7dca3e731dd1f28cb523b9ebd2ea32527f4"
    modern = {"environment_identity_sha256": "sha256:" + "a" * 64, "error": None, "repository_commit": commit}
    assert instance_from_doctor({"task_versions": modern}) == Instance(commit, "sha256:" + "a" * 64)
    for broken in (
        {**modern, "environment_identity_sha256": None, "error": "pins drifted"},
        {**modern, "error": "late failure"},
        {**modern, "environment_identity_sha256": None, "error": None},
        {"repository_commit": commit},
        None,
    ):
        with pytest.raises(RunnerFailure, match="environment identity"):
            instance_from_doctor({"task_versions": broken, "formal_conjectures": {"actual_commit": commit}})
    # An image that predates the field entirely is a legacy image of its own commit.
    assert instance_from_doctor({"formal_conjectures": {"actual_commit": commit}}) == Instance(commit, None)
