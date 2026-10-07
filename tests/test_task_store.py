"""The version store is a cache that can only ever hand back exact, valid, published bytes."""

from __future__ import annotations

import os
import shutil
import threading

import pytest

from version_fixtures import COMMIT_A, MATHLIB_COMMIT, CountingValidator, declaration, index, standard
from verifier.errors import ReasonCode, VerifierError
from verifier.models import Catalog
from verifier.task_generator import build_task_version, plan_task_version
from verifier.task_store import TaskVersionStore

PAIR = "FormalConjectures.Problems.Pair"


def planned(tmp_path):
    env = standard(tmp_path / "env")
    decl = declaration("Pair.first", PAIR)
    built = index(env, [decl])
    catalog = Catalog(1, COMMIT_A, env.toolchain, MATHLIB_COMMIT, "test", 0, (decl,))
    plan = plan_task_version(
        catalog=catalog, declaration=decl, mode="formalized",
        environment=built.environment, dependency=built.dependencies[decl.theorem],
    )
    (tmp_path / "store").mkdir()
    return TaskVersionStore(tmp_path / "store"), plan, catalog


def stage(store, plan, catalog):
    staging = store.staging_directory(plan.task_id)
    build_task_version(plan, catalog=catalog, output=staging / "bundle", validate_target=CountingValidator())
    return staging / "bundle"


def test_publish_is_atomic_read_only_and_reloads_exactly(tmp_path):
    store, plan, catalog = planned(tmp_path)
    assert store.load(plan.task_id) is None
    bundle = store.publish(stage(store, plan, catalog), plan.task_id)
    assert store.load(plan.task_id, expected_sha256=bundle.sha256).sha256 == bundle.sha256
    path = store.path_for(plan.task_id)
    assert not os.access(path / "Challenge.lean", os.W_OK) or os.geteuid() == 0
    assert oct(path.stat().st_mode & 0o777) == "0o555"
    assert list((tmp_path / "store" / ".staging").iterdir()) == []


def test_publishing_identical_bytes_twice_converges_and_different_bytes_conflict(tmp_path):
    store, plan, catalog = planned(tmp_path)
    first = store.publish(stage(store, plan, catalog), plan.task_id)
    second = store.publish(stage(store, plan, catalog), plan.task_id)
    assert first.sha256 == second.sha256
    staged = stage(store, plan, catalog)
    # A different, still self-consistent bundle cannot exist for the same ID: the loader
    # recomputes the ID from the bytes. So corrupt the published copy instead and republish.
    path = store.path_for(plan.task_id)
    path.chmod(0o755)
    (path / "Challenge.lean").chmod(0o644)
    (path / "Challenge.lean").write_text("-- poisoned\n")
    with pytest.raises(VerifierError) as raised:
        store.publish(staged, plan.task_id)
    assert raised.value.reason == ReasonCode.TRUSTED_FILE_MODIFIED


def test_a_corrupt_or_poisoned_entry_is_never_used(tmp_path):
    store, plan, catalog = planned(tmp_path)
    bundle = store.publish(stage(store, plan, catalog), plan.task_id)
    path = store.path_for(plan.task_id)
    path.chmod(0o755)
    (path / "manifest.json").chmod(0o644)
    manifest = (path / "manifest.json").read_text().replace('"timeout_seconds": 3600', '"timeout_seconds": 10')
    (path / "manifest.json").write_text(manifest)
    with pytest.raises(VerifierError):
        store.load(plan.task_id, expected_sha256=bundle.sha256)


def test_a_valid_bundle_under_the_wrong_digest_is_refused(tmp_path):
    store, plan, catalog = planned(tmp_path)
    store.publish(stage(store, plan, catalog), plan.task_id)
    with pytest.raises(VerifierError, match="registry publishes"):
        store.load(plan.task_id, expected_sha256="sha256:" + "0" * 64)


def test_symlinked_entries_and_traversal_ids_are_refused(tmp_path):
    store, plan, catalog = planned(tmp_path)
    store.publish(stage(store, plan, catalog), plan.task_id)
    elsewhere = tmp_path / "elsewhere"
    shutil.copytree(store.path_for(plan.task_id), elsewhere)
    other_id = plan.task_id.replace("-formalized", "-counterexample")
    os.symlink(elsewhere, tmp_path / "store" / other_id)
    with pytest.raises(VerifierError, match="not a real directory"):
        store.load(other_id)
    for unsafe in ("../escape", "fc-v2-x/../../etc", "fc-4b69a7dc-legacy-formalized-v1", ""):
        with pytest.raises(VerifierError):
            store.path_for(unsafe)
    with pytest.raises(VerifierError):
        TaskVersionStore(tmp_path / "store" / other_id)


def test_only_bundles_staged_in_this_store_can_be_published(tmp_path):
    store, plan, catalog = planned(tmp_path)
    outside = tmp_path / "outside-bundle"
    build_task_version(plan, catalog=catalog, output=outside, validate_target=CountingValidator())
    with pytest.raises(VerifierError, match="staged in this store"):
        store.publish(outside, plan.task_id)


def test_concurrent_publishers_of_one_version_converge_on_one_entry(tmp_path):
    store, plan, catalog = planned(tmp_path)
    staged = [stage(store, plan, catalog) for _ in range(6)]
    results, errors = [], []

    def publish(path):
        try:
            results.append(store.publish(path, plan.task_id).sha256)
        except Exception as exc:  # noqa: BLE001 - collected for the assertion
            errors.append(exc)

    threads = [threading.Thread(target=publish, args=(path,)) for path in staged]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(set(results)) == 1
    assert [entry.name for entry in (tmp_path / "store").iterdir() if not entry.name.startswith(".")] == [plan.task_id]
