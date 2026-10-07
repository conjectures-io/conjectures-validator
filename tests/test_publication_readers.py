"""Readers of a tasks checkout see exactly one committed publication, or refuse.

Independent review follow-up (packet abff941f..., reader-race-reproduction.py and
reader-race-new-environment.py): a reader that checked for a journal only on entry could be
overtaken by a publication starting right after that check, and the API task catalog parsed the
allowlist from one read and checked a second read against the registry.

Here a REAL publisher runs at every point a reader can be overtaken - right after its first
journal check, between its read of the registry and its read of the allowlist, and while it
loads bundles - and is stopped right after each commit step (`journal`, `allowlist`,
`registry`) or allowed to finish. Every reader - the API task catalog, the worker's registry
resolver for the old and the new instance, and `load_task_resolver` - must refuse the unfinished
state, and must return, for a finished one, exactly what an undisturbed read of the final
checkout returns. Readers take no lock and write nothing. No database, no Lean.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from submission_api.taskpool import TaskCatalog
from test_publication_commit import _with_allowlist_digest
from version_fixtures import COMMIT_A, COMMIT_B, declaration, release, standard
from verification_worker import tasks as worker_tasks
from verification_worker.tasks import TaskNotAllowed, VersionedTaskResolver, load_task_resolver
from verifier import publication, task_loader, task_store
from verifier.hashing import sha256_bytes
from verifier.publication import JOURNAL_NAME, LOCK_NAME, PublicationError, checkout_writer, commit_publication
from verifier.repository import tasks_repository_root
from verifier.task_generator import problem_id
from verifier.task_pool import reward_target_identity
from verifier.task_registry import TASK_POOL_SCHEMA_VERSION, TaskPoolRegistry
from verifier.task_store import TaskVersionStore
from verifier.version_registry import REGISTRY_NAME, Instance, RegistryError, VersionRegistry

SOURCES = {
    "Pair.first": ("FormalConjectures.Problems.Pair", "FormalConjectures/ErdosProblems/1.lean"),
    "Pair.second": ("FormalConjectures.Problems.Pair", "FormalConjectures/ErdosProblems/2.lean"),
    "Solo.third": ("FormalConjectures.Problems.Solo", "FormalConjectures/ErdosProblems/3.lean"),
}


def decls():
    # Source paths in an allowlisted family, so the API's full allowlist validation applies.
    return [replace(declaration(theorem, module), source_path=path) for theorem, (module, path) in SOURCES.items()]


def full_allowlist(commit: str, bundles) -> bytes:
    """A complete allowlist, as the API parses it, for one publication's bundles."""
    policy = json.loads((tasks_repository_root(Path(__file__).resolve().parents[1]) / "allowlist.json").read_text())[
        "tier_policies"
    ]["tier-1"]
    declarations = decls()
    index = {item.theorem: number for number, item in enumerate(declarations)}
    sources = [
        {"index": number, "source_path": item.source_path, "source_type_sha256": item.type_hash,
         "theorem": item.theorem, "tier": "tier-1"}
        for number, item in enumerate(declarations)
    ]
    rows = []
    for bundle in bundles:
        theorem = bundle.sources[0].theorem
        rows.append({
            "completion_policy": "all_of", "mode": bundle.manifest.task_mode,
            "problem_id": problem_id(commit, (theorem,)), "reward_target_id": reward_target_identity(theorem),
            "source_indices": [index[theorem]], "source_path": SOURCES[theorem][1],
            "target_type_sha256s": [bundle.manifest.generated_target_type_hash],
            "task_bundle_sha256": bundle.sha256, "task_id": bundle.manifest.task_id, "theorems": [theorem],
            "tier": "tier-1",
        })
    policy = {**policy, "pool_size": len(rows), "source_theorem_count": len(sources),
              "reward_target_count": len(sources), "minimum_erdos_tasks": 0}
    return json.dumps({
        "allowed_source_theorems": sources, "allowed_task_bundles": rows, "audit_date_utc": "2026-10-07",
        "default": "DENY", "repository_commit": commit, "schema_version": TASK_POOL_SCHEMA_VERSION,
        "tier_order": ["tier-1"], "tier_policies": {"tier-1": policy},
    }, indent=2, sort_keys=True).encode()


class Interrupted(Exception):
    """The publisher stops dead after a commit step: no cleanup runs, the journal stays."""


def publish(checkout: Path, env_root: Path, commit: str, stop_after: str | None = None) -> None:
    """One complete publication, through the writer lock and the journaled commit."""
    env = standard(env_root / uuid.uuid4().hex)
    (checkout / "versions").mkdir(exist_ok=True)

    def after_step(step: str) -> None:
        if step == stop_after:
            raise Interrupted(step)

    with checkout_writer(checkout):
        path = checkout / REGISTRY_NAME
        previous_bytes = path.read_bytes() if path.exists() else None
        previous = VersionRegistry.from_bytes(previous_bytes) if previous_bytes else VersionRegistry.empty()
        result, _validator, _index = release(
            env, decls(), store=TaskVersionStore(checkout / "versions"), previous=previous, commit=commit
        )
        allowlist = full_allowlist(commit, result.bundles)
        registry = _with_allowlist_digest(result.registry, allowlist)
        commit_publication(
            checkout, previous_registry_sha256=sha256_bytes(previous_bytes) if previous_bytes else None,
            registry=registry.to_bytes(), allowlist=allowlist, after_step=after_step,
        )


@pytest.fixture
def checkout(tmp_path) -> Path:
    root = tmp_path / "checkout"
    root.mkdir()
    publish(root, tmp_path / "env", COMMIT_A)
    return root


def instances(checkout: Path) -> tuple[Instance, Instance]:
    """The published instance, and the one the racing publication will add (same environment)."""
    old = VersionRegistry.load(checkout / REGISTRY_NAME).current.instance
    return old, Instance(COMMIT_B, old.environment_identity_sha256)


READERS = {
    "api-catalog": lambda root, old, new: frozenset(
        (entry.task_id, entry.task_bundle_sha256, entry.problem_id)
        for entry in TaskCatalog.load(allowlist_path=root / "allowlist.json", pool_root=root / "tasks").entries.values()
    ),
    "worker-old-instance": lambda root, old, new: frozenset(
        VersionedTaskResolver.load(tasks_root=root, environment=old, allowlist_path=root / "allowlist.json").served_keys()
    ),
    "worker-new-instance": lambda root, old, new: frozenset(
        VersionedTaskResolver.load(tasks_root=root, environment=new, allowlist_path=root / "allowlist.json").served_keys()
    ),
    "load-task-resolver-new-instance": lambda root, old, new: frozenset(
        load_task_resolver(tasks_root=root, allowlist_path=root / "allowlist.json", pool_root=root / "tasks",
                           environment=new).served_keys()
    ),
}


def overtake(monkeypatch, where: str, writer) -> list[bool]:
    """Run `writer` once, at `where` in the reader. Returns a flag list: [True] once it ran."""
    ran: list[bool] = []

    def once() -> None:
        if not ran:
            ran.append(True)
            writer()

    if where == "after-first-journal-check":
        original = publication.assert_no_pending_publication

        def check_then_write(root):
            original(root)
            once()

        monkeypatch.setattr(publication, "assert_no_pending_publication", check_then_write)
    elif where == "between-registry-and-allowlist-reads":
        original_read = publication._read_once

        def read_then_write(path):
            content = original_read(path)
            if path.name == REGISTRY_NAME:
                once()
            return content

        monkeypatch.setattr(publication, "_read_once", read_then_write)
    elif where == "while-loading-bundles":
        original_load = task_loader.load_task_bundle

        def load_then_write(*args, **kwargs):
            bundle = original_load(*args, **kwargs)
            once()
            return bundle

        monkeypatch.setattr(worker_tasks, "load_task_bundle", load_then_write)
        monkeypatch.setattr(task_store, "load_task_bundle", load_then_write)
    else:  # pragma: no cover - parametrization
        raise AssertionError(where)
    return ran


POINTS = ["after-first-journal-check", "between-registry-and-allowlist-reads", "while-loading-bundles"]
STEPS = ["journal", "allowlist", "registry", None]


@pytest.mark.parametrize("reader", sorted(READERS))
@pytest.mark.parametrize("where", POINTS)
@pytest.mark.parametrize("stop_after", STEPS, ids=lambda step: f"stop-after-{step}" if step else "finished")
def test_a_publication_overtaking_a_reader_is_refused_or_read_whole(checkout, tmp_path, monkeypatch, reader, where, stop_after):
    old, new = instances(checkout)
    read = READERS[reader]
    ran = overtake(monkeypatch, where, lambda: publish_quietly(checkout, tmp_path / "env-b", stop_after))
    outcome = attempt(lambda: read(checkout, old, new))
    monkeypatch.undo()
    if not ran:
        # Only a new-instance worker reading the old state never reaches its bundles: the
        # instance is not published there, which it reports before loading anything.
        assert reader.endswith("new-instance") and where == "while-loading-bundles"
        assert isinstance(outcome, TaskNotAllowed)
        return
    if stop_after is not None:
        assert os.path.lexists(checkout / JOURNAL_NAME)
        assert isinstance(outcome, PublicationError) and "unfinished publication" in str(outcome), outcome
        return
    # Finished: the reader returns exactly what an undisturbed read of the final checkout does.
    assert not os.path.lexists(checkout / JOURNAL_NAME)
    assert outcome == read(checkout, old, new)
    assert VersionRegistry.load(checkout / REGISTRY_NAME).current.instance == new


def publish_quietly(checkout: Path, env_root: Path, stop_after: str | None) -> None:
    try:
        publish(checkout, env_root, COMMIT_B, stop_after=stop_after)
    except Interrupted:
        pass


def attempt(read):
    try:
        return read()
    except (PublicationError, TaskNotAllowed, RegistryError) as exc:
        return exc


def test_the_api_parses_exactly_the_allowlist_bytes_it_checked(checkout, tmp_path, monkeypatch):
    parsed: list[bytes] = []
    checked: list[bytes] = []
    original_parse = TaskPoolRegistry.from_bytes.__func__
    import submission_api.taskpool as taskpool

    original_check = taskpool.assert_matches_allowlist_bytes
    monkeypatch.setattr(TaskPoolRegistry, "from_bytes", classmethod(lambda cls, content: (parsed.append(content), original_parse(cls, content))[1]))
    monkeypatch.setattr(taskpool, "assert_matches_allowlist_bytes", lambda registry, content: (checked.append(content), original_check(registry, content))[1])
    overtake(monkeypatch, "between-registry-and-allowlist-reads", lambda: publish_quietly(checkout, tmp_path / "env-b", None))
    catalog = TaskCatalog.load(allowlist_path=checkout / "allowlist.json", pool_root=checkout / "tasks")
    # The first attempt read the old registry and the new allowlist, and its check refused that
    # pair; the retry read both anew. In each attempt the bytes parsed are the bytes checked, and
    # the last are the ones on disk now.
    assert len(parsed) == 2 and checked == parsed
    assert parsed[-1] == (checkout / "allowlist.json").read_bytes()
    assert {entry.problem_id for entry in catalog.entries.values()} == {
        problem_id(COMMIT_B, (theorem,)) for theorem in SOURCES
    }


def test_a_settled_but_inconsistent_checkout_reports_its_own_error(checkout):
    allowlist = checkout / "allowlist.json"
    allowlist.write_bytes(allowlist.read_bytes() + b"\n")  # no journal, no publication: just wrong
    with pytest.raises(RegistryError, match="not the one the current publication opened"):
        TaskCatalog.load(allowlist_path=allowlist, pool_root=checkout / "tasks")
    old, _new = instances(checkout)
    with pytest.raises(RegistryError, match="not the one the current publication opened"):
        VersionedTaskResolver.load(tasks_root=checkout, environment=old, allowlist_path=allowlist)


def test_a_checkout_that_never_settles_is_refused(checkout, monkeypatch):
    original_read = publication._read_once
    allowlist = checkout / "allowlist.json"

    def read_and_change(path):
        content = original_read(path)
        if path == allowlist:
            allowlist.write_bytes(content + b" ")
        return content

    monkeypatch.setattr(publication, "_read_once", read_and_change)
    with pytest.raises(PublicationError, match="kept changing"):
        TaskCatalog.load(allowlist_path=allowlist, pool_root=checkout / "tasks")


def test_readers_need_no_lock_file_and_no_write_access(checkout):
    old, _new = instances(checkout)
    (checkout / LOCK_NAME).unlink()  # a fresh checkout of the tasks repository has none
    before = sorted(str(path.relative_to(checkout)) for path in checkout.rglob("*"))
    modes = {}
    for path in [checkout, *checkout.rglob("*")]:
        if not path.is_symlink():
            modes[path] = path.stat().st_mode
            path.chmod(0o555 if path.is_dir() else 0o444)
    try:
        for name in ("api-catalog", "worker-old-instance"):
            assert READERS[name](checkout, old, old)
    finally:
        for path, mode in sorted(modes.items(), key=lambda item: len(item[0].parts)):
            path.chmod(mode)
    assert sorted(str(path.relative_to(checkout)) for path in checkout.rglob("*")) == before
    assert not os.path.lexists(checkout / LOCK_NAME)
