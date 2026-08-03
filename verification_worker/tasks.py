"""Which task a claimed submission is about, and where its bytes are.

Built straight on `verifier.task_registry`, not on `submission_api.taskpool`: the worker holds
database credentials and has no business importing the network-facing package, and the two need
different things from the pool anyway — the API needs what to advertise, the worker needs one
directory and one declared timeout.

Loaded once at startup and immutable after. Every entry is checked with
`TaskPoolRegistry.assert_bundle`, so a task directory whose bytes have drifted from the audited
allowlist stops the worker from starting rather than being verified against quietly.

`resolve` takes the digest recorded on the submission and requires it to match the published
one. That is the fail-closed step: a task whose bundle has been regenerated since a miner paid
must not be verified against the new bytes, because the miner proved something about the old
ones.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from verifier.task_loader import load_task_bundle
from verifier.task_registry import TaskNotAllowed, TaskPoolRegistry


@dataclass(frozen=True)
class ResolvedTask:
    task_id: str
    tier: str
    task_dir: Path
    task_bundle_sha256: str  # sha256:<hex>, as published in the allowlist
    timeout_seconds: int  # the manifest's own deadline, which the verifier enforces


class TaskResolver(Protocol):
    def resolve(self, *, task_id: str, task_bundle_sha256: str) -> ResolvedTask:
        """The task, or raise TaskNotAllowed."""
        ...


@dataclass(frozen=True)
class PoolTaskResolver:
    repository_commit: str
    tasks: Mapping[str, ResolvedTask]

    @classmethod
    def load(cls, *, allowlist_path: Path, pool_root: Path) -> PoolTaskResolver:
        registry = TaskPoolRegistry.load(allowlist_path)
        resolved: dict[str, ResolvedTask] = {}
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
            # tier is a path component rather than something to search for.
            for task_dir in sorted(path for path in tier_root.iterdir() if path.is_dir()):
                bundle = load_task_bundle(task_dir)
                # Fail closed on task id, repository commit, whole-bundle digest, or target
                # type digest drift.
                allowed = registry.assert_bundle(bundle)
                if allowed.tier != tier:
                    raise TaskNotAllowed(
                        f"task {allowed.task_id} is published for {allowed.tier} "
                        f"but is stored under {tier}"
                    )
                if allowed.task_id in resolved:
                    raise TaskNotAllowed(
                        f"task {allowed.task_id} appears in more than one pool directory"
                    )
                resolved[allowed.task_id] = ResolvedTask(
                    task_id=allowed.task_id,
                    tier=allowed.tier,
                    task_dir=task_dir,
                    task_bundle_sha256=allowed.task_bundle_sha256,
                    timeout_seconds=bundle.manifest.timeout_seconds,
                )
        missing = sorted(set(registry.tasks) - set(resolved))
        if missing:
            # A claimed submission whose task has no bytes on disk would be released and
            # retried forever, so the worker refuses to start instead.
            raise TaskNotAllowed(f"allowlisted tasks are missing from the pool: {missing}")
        if not resolved:  # pragma: no cover - the registry already refuses an empty pool
            raise TaskNotAllowed("task pool is empty")
        return cls(repository_commit=registry.repository_commit, tasks=resolved)

    def resolve(self, *, task_id: str, task_bundle_sha256: str) -> ResolvedTask:
        task = self.tasks.get(task_id)
        if task is None:
            raise TaskNotAllowed("task is not on the audited task allowlist")
        if task.task_bundle_sha256 != task_bundle_sha256:
            raise TaskNotAllowed(
                "task bundle digest does not match the published commitment"
            )
        return task


def resolver_from_tasks(
    *, repository_commit: str, tasks: tuple[ResolvedTask, ...]
) -> PoolTaskResolver:
    """Build a resolver directly. Used by tests, which need no audited pool on disk."""
    return PoolTaskResolver(
        repository_commit=repository_commit,
        tasks={task.task_id: task for task in tasks},
    )


__all__ = [
    "PoolTaskResolver",
    "ResolvedTask",
    "TaskNotAllowed",
    "TaskResolver",
    "resolver_from_tasks",
]
