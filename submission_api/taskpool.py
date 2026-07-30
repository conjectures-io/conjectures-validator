"""The set of tasks a miner may submit against.

Loaded once at startup and immutable thereafter. Every entry is checked against the audited
deny-by-default allowlist with `GoldTaskRegistry.assert_bundle`, so a task directory whose
bytes drift from the published commitment stops the process from starting rather than silently
admitting submissions against an unaudited task.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from verifier.gold_registry import AllowedTask, GoldTaskRegistry, TaskNotAllowed
from verifier.models import TaskManifest
from verifier.task_loader import load_task_bundle


@dataclass(frozen=True)
class TaskEntry:
    task_id: str
    task_bundle_sha256: str
    target_type_sha256s: tuple[str, ...]
    task_dir: Path
    manifest: TaskManifest


@dataclass(frozen=True)
class TaskCatalog:
    repository_commit: str
    entries: Mapping[str, TaskEntry]

    @classmethod
    def load(cls, *, allowlist_path: Path, pool_root: Path) -> "TaskCatalog":
        registry = GoldTaskRegistry.load(allowlist_path)
        entries: dict[str, TaskEntry] = {}
        for task_id, allowed in sorted(registry.tasks.items()):
            task_dir = pool_root / task_id
            bundle = load_task_bundle(task_dir)
            # Fail closed on task id, repository commit, whole-bundle digest, or target type
            # digest drift.
            registry.assert_bundle(bundle)
            entries[task_id] = TaskEntry(
                task_id=allowed.task_id,
                task_bundle_sha256=allowed.task_bundle_sha256,
                target_type_sha256s=allowed.target_type_sha256s,
                task_dir=task_dir,
                manifest=bundle.manifest,
            )
        if not entries:
            raise TaskNotAllowed("gold task pool is empty")
        return cls(repository_commit=registry.repository_commit, entries=entries)

    def get(self, task_id: str) -> TaskEntry:
        entry = self.entries.get(task_id)
        if entry is None:
            raise TaskNotAllowed("task is not on the audited gold allowlist")
        return entry

    def resolve(self, task_id: str, task_bundle_sha256: str) -> TaskEntry:
        """Look up a task and require the caller's digest to match the published one."""
        entry = self.get(task_id)
        if entry.task_bundle_sha256 != task_bundle_sha256:
            raise TaskNotAllowed("task bundle digest does not match the published commitment")
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


__all__ = ["AllowedTask", "TaskCatalog", "TaskEntry", "TaskNotAllowed", "catalog_from_entries"]
