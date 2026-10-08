"""Incremental publication: exact reuse of unchanged versions, rebuilds of exactly the changed ones.

Every release here goes through `publish_release` with a validator that counts Lean builds, so
the reused/built numbers are the real decisions. Identical source commits are used deliberately
where a release changes only the toolchain, the policy or admission: those must publish without
inventing a source commit.
"""

from __future__ import annotations

import pytest

from version_fixtures import COMMIT_A, COMMIT_B, declaration, definition, node_type, release, standard
from verifier.errors import ReasonCode, VerifierError
from verifier.task_store import TaskVersionStore
from verifier.version_registry import Instance, VersionRegistry, assert_append_only, served_keys

PAIR = "FormalConjectures.Problems.Pair"
SOLO = "FormalConjectures.Problems.Solo"


def decls():
    return [declaration("Pair.first", PAIR), declaration("Pair.second", PAIR), declaration("Solo.third", SOLO)]


@pytest.fixture
def setting(tmp_path):
    env = standard(tmp_path / "env")
    (tmp_path / "store").mkdir()
    return env, TaskVersionStore(tmp_path / "store")


def ids_by_key(result):
    return {(item.theorem, item.mode): item.task_id for item in result.decisions}


def test_cold_publication_builds_everything_once(setting):
    env, store = setting
    result, validator, _ = release(env, decls(), store=store, previous=VersionRegistry.empty())
    assert result.counts["built"] == 6 and result.counts["reused"] == 0
    assert len(validator.builds) == 6
    assert len(result.registry.instances) == 1 and len(result.registry.publications) == 1


def test_republishing_the_same_instance_rebuilds_nothing_and_rotates_nothing(setting):
    env, store = setting
    first, _, _ = release(env, decls(), store=store, previous=VersionRegistry.empty())
    second, validator, _ = release(env, decls(), store=store, previous=first.registry)
    assert second.counts["reused"] == 6 and second.counts["built"] == 0 and validator.builds == []
    assert ids_by_key(second) == ids_by_key(first)
    # An admission-only publication: same instance, one more publication, no new commit.
    assert second.registry.instances == first.registry.instances
    assert len(second.registry.publications) == 2
    assert_append_only(first.registry, second.registry)


def test_a_one_problem_change_in_a_shared_module_rebuilds_only_that_problem(setting):
    env, store = setting
    first, _, _ = release(env, decls(), store=store, previous=VersionRegistry.empty())
    env.write_module(PAIR, "-- Pair.second's statement edited\n")
    env.statements["Pair.second"][1]["Pair.second"] = node_type("Pair.secondEdited")
    second, validator, _ = release(env, decls(), store=store, previous=first.registry, commit=COMMIT_B)
    assert sorted(validator.builds) == [("Pair.second", "counterexample"), ("Pair.second", "formalized")]
    assert second.counts == {
        "planned_versions": 6, "reused": 4, "built": 2, "superseded": 2, "retired": 0, "held": 0,
        "retained_admissions": 0,
    }
    before, after = ids_by_key(first), ids_by_key(second)
    for key in before:
        assert (before[key] == after[key]) == (key[0] != "Pair.second")
    for task_id, state in second.state_changes.items():
        assert state == "superseded" and second.registry.versions[task_id].admissions == first.registry.versions[task_id].admissions


def test_a_shared_named_definition_change_rebuilds_its_users_only(setting):
    env, store = setting
    first, _, _ = release(env, decls(), store=store, previous=VersionRegistry.empty())
    for theorem in ("Pair.first", "Pair.second"):
        env.statements[theorem][1]["Shared.helper"] = definition("helper.v2")
    second, validator, _ = release(env, decls(), store=store, previous=first.registry, commit=COMMIT_B)
    assert {item[0] for item in validator.builds} == {"Pair.first", "Pair.second"}
    assert second.counts["built"] == 4 and second.counts["reused"] == 2


def test_a_toolchain_or_policy_only_release_at_the_same_commit_rebuilds_everything(setting):
    env, store = setting
    first, _, first_index = release(env, decls(), store=store, previous=VersionRegistry.empty())
    for overrides in ({"lean_toolchain": "leanprover/lean4:v4.36.0"}, {"verification_policy": {"version": "next"}}):
        newer = env.environment(**overrides)
        second, validator, second_index = release(
            env, decls(), store=store, previous=first.registry, commit=COMMIT_A, environment=newer
        )
        # No identity-only upgrade: every version is new and is built and validated again.
        assert second.counts["built"] == 6 and second.counts["reused"] == 0 and len(validator.builds) == 6
        assert set(ids_by_key(second).values()).isdisjoint(ids_by_key(first).values())
        old = Instance(COMMIT_A, first_index.environment.sha256)
        new = Instance(COMMIT_A, second_index.environment.sha256)
        assert old != new and set(second.registry.instances) == {old, new}
        # Same source commit, hence the same problem IDs, yet the two instances serve disjoint keys.
        old_keys = {(key.task_id, key.problem_id) for key in served_keys(second.registry, old)}
        new_keys = {(key.task_id, key.problem_id) for key in served_keys(second.registry, new)}
        assert len(old_keys) == len(new_keys) == 6 and old_keys.isdisjoint(new_keys)
        assert {problem for _task, problem in old_keys} == {problem for _task, problem in new_keys}


def test_retirement_and_reinstatement_are_admission_only(setting):
    env, store = setting
    first, _, _ = release(env, decls(), store=store, previous=VersionRegistry.empty())
    retired, validator, _ = release(
        env, decls()[:2], store=store, previous=first.registry, exit_states={"Solo.third": "retired"}
    )
    assert validator.builds == [] and retired.counts["retired"] == 2
    solo = {task_id for (theorem, _mode), task_id in ids_by_key(first).items() if theorem == "Solo.third"}
    instance = first.registry.current.instance
    # Closed to intake, still verifiable by the instance that accepted its paid work.
    assert {key.task_id for key in served_keys(retired.registry, instance)} >= solo
    assert all(retired.registry.versions[task_id].state == "retired" for task_id in solo)
    back, validator, _ = release(env, decls(), store=store, previous=retired.registry)
    assert validator.builds == [] and back.counts["reused"] == 6
    assert all(back.registry.versions[task_id].state == "active" for task_id in solo)
    assert ids_by_key(back) == ids_by_key(first)


def test_a_held_but_unchanged_version_is_admitted_in_the_next_snapshot(setting):
    env, store = setting
    first, _, _ = release(env, decls(), store=store, previous=VersionRegistry.empty())
    held, _, held_index = release(
        env, decls()[:2], store=store, previous=first.registry, commit=COMMIT_B,
        exit_states={"Solo.third": "held"}, indexed=decls(),
    )
    assert held.counts["held"] == 2 and held.counts["retained_admissions"] == 2
    new = Instance(COMMIT_B, held_index.environment.sha256)
    keys = served_keys(held.registry, new)
    solo = [key for key in keys if "solo" in key.task_id]
    # The new snapshot may verify paid work it accepts for the version; it accepted none while
    # the target is held, and the old snapshot's submissions stay with the old snapshot.
    assert len(solo) == 2 and all(key.problem_id.startswith("fc-bbbbbbbb-") for key in solo)


def test_a_changed_and_held_version_keeps_only_its_original_instance(setting):
    env, store = setting
    first, _, _ = release(env, decls(), store=store, previous=VersionRegistry.empty())
    env.statements["Solo.third"][1]["Solo.third"] = node_type("Solo.changed")
    held, _, held_index = release(
        env, decls()[:2], store=store, previous=first.registry, commit=COMMIT_B,
        exit_states={"Solo.third": "held"}, indexed=decls(),
    )
    assert held.counts["retained_admissions"] == 0
    new = Instance(COMMIT_B, held_index.environment.sha256)
    assert not [key for key in served_keys(held.registry, new) if "solo" in key.task_id]


def test_leaving_the_selection_requires_a_recorded_exit(setting):
    env, store = setting
    first, _, _ = release(env, decls(), store=store, previous=VersionRegistry.empty())
    with pytest.raises(VerifierError, match="without a recorded retirement or hold"):
        release(env, decls()[:2], store=store, previous=first.registry)


def test_a_published_version_missing_from_the_store_is_never_silently_rebuilt(setting, tmp_path):
    env, store = setting
    first, _, _ = release(env, decls(), store=store, previous=VersionRegistry.empty())
    empty = tmp_path / "empty-store"
    empty.mkdir()
    with pytest.raises(VerifierError) as raised:
        release(env, decls(), store=TaskVersionStore(empty), previous=first.registry)
    assert raised.value.reason == ReasonCode.TRUSTED_FILE_MODIFIED


def test_a_poisoned_store_entry_for_a_published_version_stops_publication(setting):
    env, store = setting
    first, _, _ = release(env, decls(), store=store, previous=VersionRegistry.empty())
    task_id = next(iter(first.registry.versions))
    path = store.path_for(task_id)
    path.chmod(0o755)
    (path / "source-metadata.json").chmod(0o644)
    (path / "source-metadata.json").write_text("{}\n")
    with pytest.raises(VerifierError):
        release(env, decls(), store=store, previous=first.registry)


def test_an_unpublished_cache_entry_is_rebuilt_and_must_match(setting):
    env, store = setting
    # Built into the store but never published (no registry): reuse would trust a cache, so the
    # version is rebuilt and converges with the identical entry.
    release(env, decls(), store=store, previous=VersionRegistry.empty())
    again, validator, _ = release(env, decls(), store=store, previous=VersionRegistry.empty())
    assert again.counts["built"] == 6 and len(validator.builds) == 6


# --- parallel builds run in worker processes, bounded --------------------------------------


def parallel_release(env, store, validator, *, jobs, timeout):
    from verifier.incremental import SelectedTarget, publish_release
    from verifier.models import Catalog
    from version_fixtures import MATHLIB_COMMIT, fake_allowlist, index

    built = index(env, decls())
    catalog = Catalog(1, COMMIT_A, env.toolchain, MATHLIB_COMMIT, "test", 0, tuple(decls()))
    return publish_release(
        catalog=catalog,
        targets=[SelectedTarget(item, "tier-1", ("formalized", "counterexample")) for item in decls()],
        index=built, store=store, previous=VersionRegistry.empty(),
        instance=Instance(COMMIT_A, built.environment.sha256), allowlist_for=fake_allowlist,
        validate_target=validator, jobs=jobs, job_timeout_seconds=timeout,
    )


def test_parallel_builds_run_in_separate_processes_and_publish_atomically(setting, tmp_path):
    import os
    import time

    from version_fixtures import MarkerValidator

    env, store = setting
    markers = tmp_path / "markers"
    markers.mkdir()
    started = time.monotonic()
    result, _ = parallel_release(env, store, MarkerValidator(markers), jobs=3, timeout=120)
    assert time.monotonic() - started < 120
    builds = MarkerValidator(markers).builds()
    assert len(builds) == 6 and result.counts["built"] == 6
    # Built outside this process, never in a thread of it.
    assert str(os.getpid()) not in {name.rsplit(".", 1)[1] for name in builds}
    sequential, _, _ = release(env, decls(), store=TaskVersionStore(_fresh(tmp_path)), previous=VersionRegistry.empty())
    assert ids_by_key(result) == ids_by_key(sequential)
    assert {bundle.sha256 for bundle in result.bundles} == {bundle.sha256 for bundle in sequential.bundles}


def _fresh(tmp_path):
    path = tmp_path / "fresh-store"
    path.mkdir()
    return path


def test_a_stuck_build_is_terminated_by_the_outer_bound_and_publication_fails_closed(setting, tmp_path):
    import time

    from version_fixtures import MarkerValidator

    env, store = setting
    markers = tmp_path / "markers"
    markers.mkdir()
    started = time.monotonic()
    with pytest.raises(VerifierError, match="exceeded 2s; workers terminated"):
        parallel_release(env, store, MarkerValidator(markers, delay_seconds=600), jobs=2, timeout=2)
    assert time.monotonic() - started < 60
    assert [entry for entry in store.root.iterdir() if not entry.name.startswith(".")] == []
