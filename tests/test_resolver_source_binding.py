"""A registry record must describe its own bundle: independent review finding P2 (reproduced).

The first test is the reviewer's exact reproduction (reproduce-registry-theorem-mismatch.py in
the review packet, SHA256 a9b579ef...): every registry field naming the theorem is rewritten
consistently, so the strict registry loader accepts it, while the bundle still says Pair.first.
Before the fix the historical resolver advertised claim keys for Other.unrelated. No database.
"""

from __future__ import annotations

import json

import pytest

from version_fixtures import COMMIT_B, declaration, release, standard
from verification_worker.tasks import TaskNotAllowed, VersionedTaskResolver
from verifier.task_generator import problem_id
from verifier.task_pool import reward_target_identity
from verifier.task_store import TaskVersionStore
from verifier.version_registry import RegistryError, VersionRegistry, assert_record_matches_bundle

PAIR = "FormalConjectures.Problems.Pair"


@pytest.fixture
def published(tmp_path):
    env = standard(tmp_path / "env")
    tasks = tmp_path / "tasks"
    (tasks / "versions").mkdir(parents=True)
    result, _, _ = release(
        env, [declaration("Pair.first", PAIR)], store=TaskVersionStore(tasks / "versions"),
        previous=VersionRegistry.empty(),
    )
    return tasks, result.registry


def write(tasks, raw) -> VersionRegistry:
    altered = VersionRegistry.from_bytes(json.dumps(raw).encode())
    (tasks / "task-versions.json").write_bytes(altered.to_bytes())
    return altered


def test_reviewer_reproduction_registry_theorem_rewritten_consistently_is_refused(published):
    tasks, registry = published
    raw = json.loads(registry.to_bytes())
    for row in raw["versions"]:
        row["theorems"] = ["Other.unrelated"]
        row["reward_target_id"] = reward_target_identity("Other.unrelated")
        for admission in row["admissions"]:
            admission["problem_id"] = problem_id(admission["repository_commit"], ("Other.unrelated",))
    altered = write(tasks, raw)  # the strict loader alone accepts this
    with pytest.raises(TaskNotAllowed, match="does not describe the bundle"):
        VersionedTaskResolver.load(tasks_root=tasks, environment=altered.current.instance)


def test_the_unaltered_registry_still_loads(published):
    tasks, registry = published
    (tasks / "task-versions.json").write_bytes(registry.to_bytes())
    resolver = VersionedTaskResolver.load(tasks_root=tasks, environment=registry.current.instance)
    keys = resolver.served_keys()
    assert {key.problem_id for key in keys} == {problem_id(registry.current.instance.repository_commit, ("Pair.first",))}


@pytest.mark.parametrize(
    "mutate,message",
    [
        # Swap the two versions' modes: each record now names the other's mode.
        (lambda raw: [row.update(mode="counterexample" if row["mode"] == "formalized" else "formalized") for row in raw["versions"]], "does not describe"),
        # Admit the version for a problem ID of another commit while keeping the instance.
        (lambda raw: [a.update(problem_id=problem_id(COMMIT_B, ("Pair.first",))) for row in raw["versions"] for a in row["admissions"]], "admission is invalid|problem ID"),
    ],
)
def test_other_metadata_that_disagrees_with_the_bundle_is_refused(published, mutate, message):
    tasks, registry = published
    raw = json.loads(registry.to_bytes())
    mutate(raw)
    try:
        altered = write(tasks, raw)
    except RegistryError as exc:
        assert any(part in str(exc) for part in message.split("|"))
        return
    with pytest.raises(TaskNotAllowed, match=message):
        VersionedTaskResolver.load(tasks_root=tasks, environment=altered.current.instance)


def test_record_binding_is_checked_field_by_field(published):
    tasks, registry = published
    from dataclasses import replace

    from verifier.task_loader import load_task_bundle

    record = next(iter(registry.versions.values()))
    bundle = load_task_bundle(tasks / record.location)
    assert_record_matches_bundle(record, bundle)
    for broken in (
        replace(record, theorems=("Other.unrelated",)),
        replace(record, reward_target_id=reward_target_identity("Other.unrelated")),
        replace(record, dependency_identity_sha256="sha256:" + "0" * 64),
        replace(record, environment_identity_sha256="sha256:" + "0" * 64),
        replace(record, task_bundle_sha256="sha256:" + "0" * 64),
    ):
        with pytest.raises(RegistryError):
            assert_record_matches_bundle(broken, bundle)
