"""The set of tasks a miner may submit against.

Loaded once at startup and immutable thereafter. Every entry is checked against the audited
deny-by-default allowlist with `TaskPoolRegistry.assert_bundle`, so a task directory whose
bytes drift from the published commitment stops the process from starting rather than silently
admitting submissions against an unaudited task.

The pool is tiered, so a task's bytes live under its tier rather than directly under the pool
root. The tier is carried on each entry because it is part of the path; it is deliberately not
in the miner-facing response, since what a tier means for pricing or eligibility is not decided
yet. It *is* published on the public catalog, as a facet — a reader of the website can see how
the pool is grouped without that grouping meaning anything about price.

Each entry also keeps the source declaration and the exact `Challenge.lean` bytes it was
admitted with. Both are already in the bundle `load_task_bundle` hash-verified against the
allowlist, so serving them on `/v1/catalog/conjectures/{slug}` costs no extra read and — more
importantly — publishes the same bytes the verifier compiles. Reading `Challenge.lean` off disk
per request would let the published statement drift from the audited one between startup and
the request.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from verifier.models import CatalogDeclaration, TaskManifest
from verifier.task_loader import load_task_bundle
from verifier.task_registry import AllowedTask, TaskNotAllowed, TaskPoolRegistry

CHALLENGE_NAME = "Challenge.lean"


@dataclass(frozen=True)
class TaskEntry:
    task_id: str
    tier: str
    task_bundle_sha256: str
    target_type_sha256s: tuple[str, ...]
    task_dir: Path
    manifest: TaskManifest
    # The audited source declaration: statement, docstring, category, AMS subjects, references.
    source: CatalogDeclaration
    # `Challenge.lean` exactly as hashed into the task bundle digest above.
    challenge_lean: str


@dataclass(frozen=True)
class TaskCatalog:
    repository_commit: str
    entries: Mapping[str, TaskEntry]

    @classmethod
    def load(cls, *, allowlist_path: Path, pool_root: Path) -> TaskCatalog:
        registry = TaskPoolRegistry.load(allowlist_path)
        entries: dict[str, TaskEntry] = {}
        for tier in sorted({allowed.tier for allowed in registry.tasks.values()}):
            tier_root = pool_root / tier
            if not tier_root.is_dir():
                # The pool is no longer committed here; it is a pinned checkout of the task
                # repository. Absent bytes usually mean that checkout was never materialized.
                raise TaskNotAllowed(
                    f"task pool tier {tier} is missing at {tier_root}; the task bundles are a "
                    "pinned checkout materialized by scripts/pin_dependencies.sh"
                )
            # A task is identified by the task ID in its manifest, never by the name of the
            # directory holding it: the task repository names directories for humans and may
            # rename them without reissuing a task. Tasks do live under their tier, so the
            # tier is a path component rather than something to search the pool for.
            for task_dir in sorted(path for path in tier_root.iterdir() if path.is_dir()):
                bundle = load_task_bundle(task_dir)
                # Fail closed on task id, repository commit, whole-bundle digest, or target
                # type digest drift. An unaudited bundle stops startup here.
                allowed = registry.assert_bundle(bundle)
                if allowed.tier != tier:
                    raise TaskNotAllowed(
                        f"task {allowed.task_id} is published for {allowed.tier} "
                        f"but is stored under {tier}"
                    )
                if allowed.task_id in entries:
                    raise TaskNotAllowed(
                        f"task {allowed.task_id} appears in more than one pool directory"
                    )
                entries[allowed.task_id] = TaskEntry(
                    task_id=allowed.task_id,
                    tier=allowed.tier,
                    task_bundle_sha256=allowed.task_bundle_sha256,
                    target_type_sha256s=allowed.target_type_sha256s,
                    task_dir=task_dir,
                    manifest=bundle.manifest,
                    source=bundle.source,
                    # A trusted file, so its bytes are already hash-verified. Lean source is
                    # UTF-8 by construction; a decode failure here would mean the digest check
                    # above passed on bytes no verifier could compile.
                    challenge_lean=bundle.files[CHALLENGE_NAME].decode("utf-8"),
                )
        missing = sorted(set(registry.tasks) - set(entries))
        if missing:
            # An allowlisted task with no bytes on disk would otherwise be a 404 at submission
            # time for a miner who paid, so it is a startup failure instead.
            raise TaskNotAllowed(f"allowlisted tasks are missing from the pool: {missing}")
        if not entries:  # pragma: no cover - the registry already refuses an empty pool
            raise TaskNotAllowed("task pool is empty")
        return cls(repository_commit=registry.repository_commit, entries=entries)

    def get(self, task_id: str) -> TaskEntry:
        entry = self.entries.get(task_id)
        if entry is None:
            raise TaskNotAllowed("task is not on the audited task allowlist")
        return entry

    def resolve(self, task_id: str, task_bundle_sha256: str) -> TaskEntry:
        """Look up a task and require the caller's digest to match the published one."""
        entry = self.get(task_id)
        if entry.task_bundle_sha256 != task_bundle_sha256:
            raise TaskNotAllowed(
                "task bundle digest does not match the published commitment"
            )
        return entry

    def summaries(self) -> tuple[TaskEntry, ...]:
        return tuple(sorted(self.entries.values(), key=lambda item: item.task_id))


def catalog_from_entries(
    *, repository_commit: str, entries: tuple[TaskEntry, ...]
) -> TaskCatalog:
    """Build a catalog directly. Used by tests, which need no full audited pool."""
    return TaskCatalog(
        repository_commit=repository_commit,
        entries={entry.task_id: entry for entry in entries},
    )


__all__ = [
    "CHALLENGE_NAME",
    "AllowedTask",
    "TaskCatalog",
    "TaskEntry",
    "TaskNotAllowed",
    "catalog_from_entries",
]
