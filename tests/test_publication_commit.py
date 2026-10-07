"""Committing publications to a tasks checkout: concurrent writers and interrupted commits.

Real files, real processes. Two full publishers race through `checkout_writer`; a commit is
killed between each of its steps; readers are checked against every intermediate state.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import time
from pathlib import Path

import pytest

from submission_api.taskpool import TaskCatalog
from version_fixtures import COMMIT_A, COMMIT_B, declaration, release, standard
from verification_worker.tasks import load_task_resolver
from verifier.hashing import sha256_bytes
from verifier.publication import (
    JOURNAL_NAME,
    PublicationError,
    assert_no_pending_publication,
    checkout_writer,
    commit_publication,
)
from verifier.task_store import TaskVersionStore
from verifier.version_registry import (
    REGISTRY_NAME,
    RegistryError,
    VersionRegistry,
    assert_append_only,
    assert_matches_allowlist,
)

PAIR = "FormalConjectures.Problems.Pair"
SOLO = "FormalConjectures.Problems.Solo"


def decls():
    return [declaration("Pair.first", PAIR), declaration("Pair.second", PAIR), declaration("Solo.third", SOLO)]


def allowlist_for(registry: VersionRegistry) -> bytes:
    """A minimal allowlist naming exactly the current instance's active versions."""
    current = registry.current.instance
    rows = [
        {"task_id": record.task_id, "task_bundle_sha256": record.task_bundle_sha256,
         "problem_id": record.admission_at(current).problem_id}
        for record in registry.versions.values()
        if record.state == "active" and record.admission_at(current) is not None
    ]
    return json.dumps({"repository_commit": current.repository_commit, "allowed_task_bundles": rows}, sort_keys=True).encode()


def publish_into(checkout: Path, env_root: Path, commit: str, hold_seconds: float = 0.0, after_step=None) -> str:
    """One complete publication: read history inside the lock, build, commit."""
    import uuid

    env = standard(env_root / uuid.uuid4().hex)
    (checkout / "versions").mkdir(exist_ok=True)
    with checkout_writer(checkout):
        path = checkout / REGISTRY_NAME
        previous_bytes = path.read_bytes() if path.exists() else None
        previous = VersionRegistry.from_bytes(previous_bytes) if previous_bytes else VersionRegistry.empty()
        result, _validator, _index = release(
            env, decls(), store=TaskVersionStore(checkout / "versions"), previous=previous, commit=commit
        )
        allowlist = allowlist_for(result.registry)
        registry = _with_allowlist_digest(result.registry, allowlist)
        time.sleep(hold_seconds)
        commit_publication(
            checkout,
            previous_registry_sha256=sha256_bytes(previous_bytes) if previous_bytes else None,
            registry=registry.to_bytes(),
            allowlist=allowlist,
            after_step=after_step or (lambda step: None),
        )
    return commit


def _with_allowlist_digest(registry: VersionRegistry, allowlist: bytes) -> VersionRegistry:
    """The publication's registry names the exact allowlist it opens."""
    from dataclasses import replace

    publications = (*registry.publications[:-1], replace(registry.publications[-1], allowlist_sha256=sha256_bytes(allowlist)))
    return VersionRegistry.from_bytes(replace(registry, publications=publications).to_bytes())


def _publisher(checkout: str, env_root: str, commit: str, hold: float, queue) -> None:
    try:
        queue.put(("ok", publish_into(Path(checkout), Path(env_root), commit, hold)))
    except Exception as exc:  # noqa: BLE001 - reported to the parent
        queue.put(("error", repr(exc)))


def test_concurrent_full_publishers_serialize_and_keep_both_histories(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    processes = [
        context.Process(target=_publisher, args=(str(checkout), str(tmp_path / f"env-{commit[0]}"), commit, 1.5, queue))
        for commit in (COMMIT_A, COMMIT_B)
    ]
    started = time.monotonic()
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=120)
        assert process.exitcode == 0
    assert time.monotonic() - started < 120
    outcomes = [queue.get(timeout=10) for _ in processes]
    assert {status for status, _ in outcomes} == {"ok"}, outcomes
    final = VersionRegistry.load(checkout / REGISTRY_NAME)
    # Both publications survive: neither publisher read history the other was about to replace.
    assert {item.instance.repository_commit for item in final.publications} == {COMMIT_A, COMMIT_B}
    assert len(final.publications) == 2
    assert_matches_allowlist(final, checkout / "allowlist.json")
    assert not os.path.lexists(checkout / JOURNAL_NAME)


def test_a_writer_that_read_stale_history_cannot_commit_over_newer_history(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    publish_into(checkout, tmp_path / "env-a", COMMIT_A)
    stale_bytes = (checkout / REGISTRY_NAME).read_bytes()
    publish_into(checkout, tmp_path / "env-b", COMMIT_B)
    newer = (checkout / REGISTRY_NAME).read_bytes()
    with checkout_writer(checkout):
        with pytest.raises(PublicationError, match="registry changed"):
            commit_publication(
                checkout, previous_registry_sha256=sha256_bytes(stale_bytes),
                registry=stale_bytes, allowlist=b"{}",
            )
    assert (checkout / REGISTRY_NAME).read_bytes() == newer


def _interrupted_commit(checkout: str, env_root: str, stop_after: str) -> None:
    """Publish, but die right after `stop_after`: no cleanup and no exception handling run."""

    def after_step(step: str) -> None:
        if step == stop_after:
            os._exit(17)

    publish_into(Path(checkout), Path(env_root), COMMIT_B, after_step=after_step)


@pytest.mark.parametrize("stop_after", ["journal", "allowlist", "registry"])
def test_a_commit_killed_between_steps_is_never_read_and_is_rolled_forward(tmp_path, stop_after):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    publish_into(checkout, tmp_path / "env-a", COMMIT_A)
    before_registry = (checkout / REGISTRY_NAME).read_bytes()
    context = multiprocessing.get_context("spawn")
    process = context.Process(target=_interrupted_commit, args=(str(checkout), str(tmp_path / "env-b"), stop_after))
    process.start()
    process.join(timeout=120)
    assert process.exitcode == 17
    assert os.path.lexists(checkout / JOURNAL_NAME)
    # Every reader refuses the interrupted state, whatever subset of files changed: the check
    # itself, the API task catalog, and the worker's resolver for either instance.
    with pytest.raises(PublicationError, match="unfinished publication"):
        assert_no_pending_publication(checkout)
    with pytest.raises(PublicationError, match="unfinished publication"):
        TaskCatalog.load(allowlist_path=checkout / "allowlist.json", pool_root=checkout / "tasks")
    for instance in VersionRegistry.from_bytes(before_registry).instances:
        with pytest.raises(PublicationError, match="unfinished publication"):
            load_task_resolver(tasks_root=checkout, allowlist_path=checkout / "allowlist.json",
                               pool_root=checkout / "tasks", environment=instance)
    if stop_after == "allowlist":
        assert (checkout / REGISTRY_NAME).read_bytes() == before_registry
        with pytest.raises(RegistryError):
            assert_matches_allowlist(VersionRegistry.load(checkout / REGISTRY_NAME), checkout / "allowlist.json")
    # The next writer completes it from the journal's own staged, digest-checked files.
    with checkout_writer(checkout) as recovered:
        assert recovered is not None
    final = VersionRegistry.load(checkout / REGISTRY_NAME)
    assert sha256_bytes(final.to_bytes()) == recovered
    assert {item.instance.repository_commit for item in final.publications} == {COMMIT_A, COMMIT_B}
    assert_append_only(VersionRegistry.from_bytes(before_registry), final)
    assert_matches_allowlist(final, checkout / "allowlist.json")
    assert_no_pending_publication(checkout)
    assert not [path for path in checkout.iterdir() if path.name.startswith(".publication-")]
    # After recovery the worker serves exactly the rolled-forward publication.
    resolver = load_task_resolver(tasks_root=checkout, allowlist_path=checkout / "allowlist.json",
                                  pool_root=checkout / "tasks", environment=final.current.instance)
    assert {key.task_id for key in resolver.served_keys()} == {
        record.task_id for record in final.versions.values()
        if record.state == "active" and record.admission_at(final.current.instance) is not None
    }


def test_a_tampered_staged_file_stops_recovery(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    publish_into(checkout, tmp_path / "env-a", COMMIT_A)
    context = multiprocessing.get_context("spawn")
    process = context.Process(target=_interrupted_commit, args=(str(checkout), str(tmp_path / "env-b"), "journal"))
    process.start()
    process.join(timeout=120)
    staged = json.loads((checkout / JOURNAL_NAME).read_text())["steps"][-1]["staged"]
    (checkout / staged).write_bytes(b"{}")
    with pytest.raises(PublicationError, match="matches neither"):
        with checkout_writer(checkout):
            pass
    with pytest.raises(PublicationError):
        assert_no_pending_publication(checkout)
