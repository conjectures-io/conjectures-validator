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


def _publish_in_process(root, staged, task_id, queue):
    from verifier.task_store import TaskVersionStore

    try:
        queue.put(("ok", TaskVersionStore(root).publish(staged, task_id).sha256))
    except Exception as exc:  # noqa: BLE001 - reported to the parent
        queue.put(("error", repr(exc)))


def test_concurrent_publisher_processes_converge_on_one_entry(tmp_path):
    import multiprocessing

    store, plan, catalog = planned(tmp_path)
    staged = [stage(store, plan, catalog) for _ in range(4)]
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    processes = [
        context.Process(target=_publish_in_process, args=(store.root, path, plan.task_id, queue)) for path in staged
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=120)
        assert process.exitcode == 0
    results = [queue.get(timeout=10) for _ in processes]
    assert {status for status, _ in results} == {"ok"}
    assert len({digest for _, digest in results}) == 1
    assert [entry.name for entry in store.root.iterdir() if not entry.name.startswith(".")] == [plan.task_id]


# --- durability and recovery (independent review finding P2) --------------------------------


def _crashing_publish(root, staged, task_id, step):
    from verifier.task_store import TaskVersionStore

    def after_step(name):
        if name == step:
            os._exit(23)

    TaskVersionStore(root).publish(staged, task_id, after_step=after_step)


def _crash(store, plan, catalog, step):
    import multiprocessing

    staged = stage(store, plan, catalog)
    process = multiprocessing.get_context("spawn").Process(
        target=_crashing_publish, args=(store.root, staged, plan.task_id, step)
    )
    process.start()
    process.join(timeout=120)
    assert process.exitcode == 23


def test_a_crash_after_rename_before_sealing_is_completed_by_the_next_publisher(tmp_path):
    from verifier.task_store import is_sealed

    store, plan, catalog = planned(tmp_path)
    _crash(store, plan, catalog, "renamed")
    entry = store.path_for(plan.task_id)
    assert entry.is_dir() and not is_sealed(entry)
    # Complete and valid, so readers may already use it; the next publisher seals it.
    retried = store.publish(stage(store, plan, catalog), plan.task_id)
    assert is_sealed(entry)
    assert store.load(plan.task_id, expected_sha256=retried.sha256) is not None


def test_concurrent_retries_after_an_interrupted_publication_converge(tmp_path):
    import multiprocessing

    from verifier.task_store import is_sealed

    store, plan, catalog = planned(tmp_path)
    _crash(store, plan, catalog, "renamed")
    staged = [stage(store, plan, catalog) for _ in range(3)]
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    processes = [context.Process(target=_publish_in_process, args=(store.root, path, plan.task_id, queue)) for path in staged]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=120)
        assert process.exitcode == 0
    results = [queue.get(timeout=10) for _ in processes]
    assert {status for status, _ in results} == {"ok"} and len({digest for _, digest in results}) == 1
    assert is_sealed(store.path_for(plan.task_id))


def test_an_interrupted_entry_whose_bytes_were_lost_is_never_sealed_or_accepted(tmp_path):
    from verifier.task_store import is_sealed

    store, plan, catalog = planned(tmp_path)
    _crash(store, plan, catalog, "renamed")
    entry = store.path_for(plan.task_id)
    # What a lost write looks like after a power failure: a truncated trusted file.
    (entry / "Challenge.lean").write_bytes(b"")
    with pytest.raises(VerifierError):
        store.load(plan.task_id)
    with pytest.raises(VerifierError):
        store.publish(stage(store, plan, catalog), plan.task_id)
    assert not is_sealed(entry)


def test_a_crash_before_the_rename_publishes_nothing(tmp_path):
    store, plan, catalog = planned(tmp_path)
    _crash(store, plan, catalog, "staged-synced")
    assert store.load(plan.task_id) is None
    published = store.publish(stage(store, plan, catalog), plan.task_id)
    assert store.load(plan.task_id, expected_sha256=published.sha256) is not None
