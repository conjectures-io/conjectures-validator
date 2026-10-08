"""The version registry is strict, append-only, and gives every paid submission one fixed environment."""

from __future__ import annotations

import json

import pytest

from version_fixtures import COMMIT_A, COMMIT_B, declaration, release, standard
from verifier.hashing import pretty_json
from verifier.task_generator import problem_id
from verifier.task_store import TaskVersionStore
from verifier.version_registry import (
    Instance,
    RegistryError,
    VersionRegistry,
    assert_append_only,
    served_keys,
)

PAIR = "FormalConjectures.Problems.Pair"
SOLO = "FormalConjectures.Problems.Solo"


@pytest.fixture
def published(tmp_path):
    env = standard(tmp_path / "env")
    (tmp_path / "store").mkdir()
    store = TaskVersionStore(tmp_path / "store")
    decls = [declaration("Pair.first", PAIR), declaration("Pair.second", PAIR), declaration("Solo.third", SOLO)]
    first, _, first_index = release(env, decls, store=store, previous=VersionRegistry.empty())
    return env, store, decls, first, first_index


def mutated(registry: VersionRegistry, change) -> bytes:
    value = json.loads(registry.to_bytes())
    change(value)
    return pretty_json(value).encode()


def test_the_registry_round_trips_exactly(published):
    *_, first, _ = published
    assert VersionRegistry.from_bytes(first.registry.to_bytes()).to_bytes() == first.registry.to_bytes()


@pytest.mark.parametrize(
    "change,message",
    [
        (lambda v: v.update(default="ALLOW"), "default"),
        (lambda v: v["versions"].reverse(), "sorted"),
        (lambda v: v["versions"][0].update(provenance="something-else"), "unknown provenance"),
        (lambda v: v["versions"][0]["admissions"][0].update(problem_id="fc-forged-problem"), "admission is invalid"),
        (lambda v: v["versions"][0]["admissions"][0].update(environment_identity_sha256="sha256:" + "0" * 64), "instance is invalid"),
        (lambda v: v["versions"][0].update(location="versions/../../etc"), "invalid"),
        (lambda v: v["publications"][0].update(sequence=2), "sequence"),
        (lambda v: v["versions"][1].update(task_bundle_sha256=v["versions"][0]["task_bundle_sha256"]), "twice"),
        (lambda v: v["versions"][0]["admissions"].append(dict(v["versions"][0]["admissions"][0])), "twice in one instance"),
    ],
)
def test_structural_tampering_is_refused(published, change, message):
    *_, first, _ = published
    with pytest.raises(RegistryError, match=message):
        VersionRegistry.from_bytes(mutated(first.registry, change))


def test_two_active_versions_of_one_target_and_mode_are_refused(published):
    *_, first, _ = published

    def duplicate(value):
        copy = json.loads(json.dumps(value["versions"][0]))
        copy["task_id"] = f"fc-v2-pair-first-{'0' * 24}-{copy['mode']}"
        copy["location"] = "versions/" + copy["task_id"]
        copy["task_bundle_sha256"] = "sha256:" + "7" * 64
        value["versions"].append(copy)
        value["versions"].sort(key=lambda row: row["task_id"])

    with pytest.raises(RegistryError, match="two versions"):
        VersionRegistry.from_bytes(mutated(first.registry, duplicate))


def test_a_legacy_version_is_bound_to_exactly_one_instance(published):
    *_, first, _ = published
    legacy_id = f"fc-{COMMIT_A[:8]}-pair-first-0123456789-formalized-v1"
    row = {
        "admissions": [
            {"build_provenance_sha256": None, "environment_identity_sha256": None, "first_publication": 1,
             "problem_id": problem_id(COMMIT_A, ("Pair.first",)), "repository_commit": COMMIT_A},
        ],
        "dependency_identity_sha256": None,
        "environment_identity_sha256": None,
        "location": "pool/tier-1/pair-first-formalized",
        "mode": "formalized",
        "provenance": "legacy-source-commit",
        "reward_target_id": "fc-target:Pair.first",
        "state": "superseded",
        "task_bundle_sha256": "sha256:" + "5" * 64,
        "task_id": legacy_id,
        "theorems": ["Pair.first"],
        "tier": "tier-1",
    }

    def add(value, admissions):
        value["instances"].insert(0, {"environment_identity_sha256": None, "repository_commit": COMMIT_A})
        value["publications"].insert(0, {"allowlist_sha256": "sha256:" + "4" * 64, "environment_identity_sha256": None, "repository_commit": COMMIT_A, "sequence": 1})
        for index, item in enumerate(value["publications"], start=1):
            item["sequence"] = index
        for version in value["versions"]:
            for admission in version["admissions"]:
                admission["first_publication"] += 1
        value["versions"].append({**row, "admissions": admissions})
        value["versions"].sort(key=lambda item: item["task_id"])

    VersionRegistry.from_bytes(mutated(first.registry, lambda v: add(v, row["admissions"])))
    second = dict(row["admissions"][0], first_publication=2, environment_identity_sha256=first.registry.current.instance.environment_identity_sha256)
    with pytest.raises(RegistryError, match="exactly one instance"):
        VersionRegistry.from_bytes(mutated(first.registry, lambda v: add(v, [row["admissions"][0], second])))
    wrong_commit = [dict(row["admissions"][0], repository_commit=COMMIT_B, problem_id=problem_id(COMMIT_B, ("Pair.first",)))]
    with pytest.raises(RegistryError):
        VersionRegistry.from_bytes(mutated(first.registry, lambda v: add(v, wrong_commit)))


def test_later_registries_cannot_rewrite_history(published):
    env, store, decls, first, _ = published
    second, _, _ = release(env, decls, store=store, previous=first.registry, commit=COMMIT_B)
    assert_append_only(first.registry, second.registry)
    for change, message in (
        (lambda v: v["publications"][0].update(allowlist_sha256="sha256:" + "1" * 64), "publications"),
        (lambda v: v["versions"][0]["admissions"].pop(0), "admissions|instance"),
        (lambda v: v["versions"][0]["admissions"][0].update(build_provenance_sha256="sha256:" + "2" * 64), "admissions"),
        (lambda v: v["versions"][0].update(task_bundle_sha256="sha256:" + "3" * 64), "commitment"),
    ):
        try:
            forged = VersionRegistry.from_bytes(mutated(second.registry, change))
        except RegistryError:
            continue  # refused structurally already
        with pytest.raises(RegistryError, match=message):
            assert_append_only(first.registry, forged)
    dropped = mutated(second.registry, lambda v: v["versions"].pop(0))
    try:
        forged = VersionRegistry.from_bytes(dropped)
    except RegistryError:
        return
    with pytest.raises(RegistryError, match="dropped"):
        assert_append_only(first.registry, forged)


def test_an_import_only_change_keeps_the_id_but_never_moves_old_paid_work(published):
    """Adversarial: same task IDs in two snapshots must not let the new one claim the old work."""
    env, store, decls, first, first_index = published
    # A text-only change in a module everything imports: no statement's dependency identity
    # changes, so every task ID is reused, but the proof environment's bytes differ.
    env.write_module("FormalConjecturesUtil", "-- reformatted shared utilities\n")
    second, validator, second_index = release(env, decls, store=store, previous=first.registry, commit=COMMIT_B)
    assert validator.builds == [] and second.counts["reused"] == 6
    old = Instance(COMMIT_A, first_index.environment.sha256)
    new = Instance(COMMIT_B, second_index.environment.sha256)
    old_keys = served_keys(second.registry, old)
    new_keys = served_keys(second.registry, new)
    assert {key.task_id for key in old_keys} == {key.task_id for key in new_keys}
    # Paid work accepted by the old snapshot carries the old problem ID; only the old instance
    # serves it, with the old snapshot's own build provenance.
    old_problem = problem_id(COMMIT_A, ("Pair.first",))
    assert all(not key.problem_id.startswith("fc-bbbbbbbb-") for key in old_keys)
    assert all(key.problem_id != old_problem for key in new_keys)
    old_pair = next(key for key in old_keys if key.problem_id == old_problem and key.task_id.endswith("formalized"))
    assert old_pair.expected_build_provenance_sha256 == first_index.provenance["Pair.first"].sha256
    new_pair = next(key for key in new_keys if key.task_id == old_pair.task_id)
    assert new_pair.expected_build_provenance_sha256 == second_index.provenance["Pair.first"].sha256
    assert new_pair.expected_build_provenance_sha256 != old_pair.expected_build_provenance_sha256
    # Rewriting the old admission to the new provenance, so the new environment would match it,
    # is a history rewrite and refused.
    def forge(value):
        for version in value["versions"]:
            for admission in version["admissions"]:
                if admission["repository_commit"] == COMMIT_A:
                    admission["build_provenance_sha256"] = second_index.provenance[version["theorems"][0]].sha256
    with pytest.raises(RegistryError, match="admissions"):
        assert_append_only(first.registry, VersionRegistry.from_bytes(mutated(second.registry, forge)))


def test_an_unpublished_instance_serves_nothing(published):
    *_, first, first_index = published
    assert served_keys(first.registry, Instance(COMMIT_B, first_index.environment.sha256)) == ()
    assert served_keys(first.registry, Instance(COMMIT_A, None)) == ()
    assert served_keys(first.registry, Instance(COMMIT_A, "sha256:" + "6" * 64)) == ()
