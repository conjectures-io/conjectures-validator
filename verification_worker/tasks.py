"""Which task a claimed submission is about, where its bytes are, and which ones this worker may claim.

Built straight on `verifier.task_registry` and `verifier.version_registry`, not on
`submission_api.taskpool`: the worker holds database credentials and has no business importing the
network-facing package, and the two need different things from the pool anyway — the API needs
what to advertise, the worker needs one directory, one declared timeout, and the exact set of
submissions its verification environment is the original environment for.

Loaded once at startup and immutable after. Every entry is checked against the audited allowlist
or the version registry and its bundle is re-validated, so a task directory whose bytes have
drifted stops the worker from starting rather than being verified against quietly.

A submission is identified by three values fixed at intake: `task_id`, `task_bundle_sha256` and
`problem_id`, the last of which commits to the source snapshot that accepted it. `served_keys` is
exactly the set of those triples this worker's environment accepted itself, and the queue claim
is filtered by it (`conjectures_subnet.db.verification.claim_next`). Paid work from any other
environment — an older release, a different toolchain, the original source of a retired task — is
never claimed here, so it is never charged an attempt and never verified against bytes or an
environment the miner did not submit against. `resolve` requires the same exact triple: it never
rebinds a submission to another version of the same target.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from verifier.publication import CheckoutSnapshot, assert_no_pending_publication, read_coherently
from verifier.task_loader import load_task_bundle
from verifier.task_registry import TaskNotAllowed, TaskPoolRegistry
from verifier.task_versions import LEGACY_PROVENANCE, V2_PROVENANCE
from verifier.version_registry import (
    REGISTRY_NAME,
    Instance,
    RegistryError,
    VersionRegistry,
    assert_matches_allowlist_bytes,
    assert_record_matches_bundle,
    served_keys,
)


@dataclass(frozen=True)
class ClaimKey:
    """What a worker may claim: the exact values a submission row carries."""

    task_id: str
    task_bundle_sha256: str  # sha256:<hex>
    problem_id: str


@dataclass(frozen=True)
class ResolvedTask:
    task_id: str
    tier: str
    task_dir: Path
    task_bundle_sha256: str  # sha256:<hex>, as published in the allowlist
    timeout_seconds: int  # the manifest's own deadline, which the verifier enforces
    enable_nanoda: bool = False
    # The intake snapshot this resolution is for; None only for resolvers built in tests.
    problem_id: str | None = None
    provenance: str = LEGACY_PROVENANCE
    # v2 only: the build provenance the verifier must recompute in its container.
    expected_build_provenance_sha256: str | None = None


class TaskResolver(Protocol):
    def served_keys(self) -> tuple[ClaimKey, ...]:
        """Exactly the submissions this worker's environment may claim."""
        ...

    def resolve(self, *, task_id: str, task_bundle_sha256: str, problem_id: str) -> ResolvedTask:
        """The task, or raise TaskNotAllowed."""
        ...


def _key(task: ResolvedTask) -> ClaimKey:
    if task.problem_id is None:
        raise TaskNotAllowed(f"task {task.task_id} has no intake problem identity to route by")
    return ClaimKey(task.task_id, task.task_bundle_sha256, task.problem_id)


def _resolve_exact(
    tasks: Mapping[ClaimKey, ResolvedTask], *, task_id: str, task_bundle_sha256: str, problem_id: str
) -> ResolvedTask:
    task = tasks.get(ClaimKey(task_id, task_bundle_sha256, problem_id))
    if task is not None:
        return task
    if not any(key.task_id == task_id for key in tasks):
        raise TaskNotAllowed("task is not served by this verification environment")
    if not any(key.task_id == task_id and key.task_bundle_sha256 == task_bundle_sha256 for key in tasks):
        raise TaskNotAllowed("task bundle digest does not match the published commitment")
    raise TaskNotAllowed(
        "submission was accepted by a different source snapshot; only its original "
        "verification environment may verify it"
    )


def _resolve_single(
    tasks: Mapping[str, ResolvedTask], *, task_id: str, task_bundle_sha256: str, problem_id: str
) -> ResolvedTask:
    task = tasks.get(task_id)
    if task is None:
        raise TaskNotAllowed("task is not on the audited task allowlist")
    if task.task_bundle_sha256 != task_bundle_sha256:
        raise TaskNotAllowed("task bundle digest does not match the published commitment")
    if problem_id != task.problem_id:
        raise TaskNotAllowed(
            "submission was accepted by a different source snapshot; only its original "
            "verification environment may verify it"
        )
    return task


@dataclass(frozen=True)
class PoolTaskResolver:
    """The allowlist alone: for a tasks release without a version registry.

    Serves exactly the current allowlist's rows, so the only submissions it claims are the ones
    this very snapshot accepted.
    """

    repository_commit: str
    tasks: Mapping[str, ResolvedTask]

    @classmethod
    def load(cls, *, allowlist_path: Path, pool_root: Path) -> PoolTaskResolver:
        return cls.from_allowlist(TaskPoolRegistry.load(allowlist_path), pool_root=pool_root)

    @classmethod
    def from_allowlist(cls, registry: TaskPoolRegistry, *, pool_root: Path) -> PoolTaskResolver:
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
                    enable_nanoda=bundle.manifest.enable_nanoda,
                    problem_id=allowed.problem_id,
                )
        missing = sorted(set(registry.tasks) - set(resolved))
        if missing:
            # A claimed submission whose task has no bytes on disk would be released and
            # retried forever, so the worker refuses to start instead.
            raise TaskNotAllowed(f"allowlisted tasks are missing from the pool: {missing}")
        if not resolved:  # pragma: no cover - the registry already refuses an empty pool
            raise TaskNotAllowed("task pool is empty")
        return cls(repository_commit=registry.repository_commit, tasks=resolved)

    def served_keys(self) -> tuple[ClaimKey, ...]:
        return tuple(sorted({_key(task) for task in self.tasks.values()}, key=lambda key: (key.task_id, key.problem_id)))

    def resolve(self, *, task_id: str, task_bundle_sha256: str, problem_id: str) -> ResolvedTask:
        return _resolve_single(
            self.tasks, task_id=task_id, task_bundle_sha256=task_bundle_sha256, problem_id=problem_id
        )


def _bundle_directory(tasks_root: Path, location: str) -> Path:
    """`tasks_root/location`, refusing any symlink or escape on the way."""
    parts = Path(location).parts
    if not parts or Path(location).is_absolute() or any(part in {"", ".", ".."} for part in parts):
        raise TaskNotAllowed(f"unsafe task location: {location!r}")
    current = tasks_root
    for part in parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except OSError as exc:
            raise TaskNotAllowed(f"task bytes are missing at {location}: {exc}") from exc
        if not stat.S_ISDIR(mode):
            raise TaskNotAllowed(f"task location is not a real directory: {location}")
    if os.path.realpath(current) != os.path.join(os.path.realpath(tasks_root), *parts):
        raise TaskNotAllowed(f"task location escapes the tasks checkout: {location}")
    return current


@dataclass(frozen=True)
class VersionedTaskResolver:
    """Every version this environment is the original environment for, current or historical."""

    environment: Instance
    tasks: Mapping[ClaimKey, ResolvedTask]

    @classmethod
    def load(
        cls,
        *,
        tasks_root: Path,
        environment: Instance,
        allowlist_path: Path | None = None,
    ) -> VersionedTaskResolver:
        # Fails fast; `read_coherently` checks again before and after it reads.
        assert_no_pending_publication(tasks_root)
        return read_coherently(
            tasks_root,
            lambda snapshot: cls.from_snapshot(snapshot, tasks_root=tasks_root, environment=environment,
                                               check_allowlist=allowlist_path is not None),
            allowlist_path=allowlist_path,
        )

    @classmethod
    def from_snapshot(
        cls,
        snapshot: CheckoutSnapshot,
        *,
        tasks_root: Path,
        environment: Instance,
        check_allowlist: bool,
    ) -> VersionedTaskResolver:
        """The resolver for one committed state; registry and allowlist come only from `snapshot`."""
        if snapshot.registry is None:
            raise TaskNotAllowed(f"the tasks checkout has no version registry {REGISTRY_NAME}")
        registry = VersionRegistry.from_bytes(snapshot.registry)
        if environment not in registry.instances:
            raise TaskNotAllowed(
                f"verification instance {environment} is not published in the version registry; "
                "this environment is not the original environment for any paid work"
            )
        if check_allowlist and registry.current.instance == environment:
            if snapshot.allowlist is None:
                raise TaskNotAllowed("the current publication's allowlist is missing")
            assert_matches_allowlist_bytes(registry, snapshot.allowlist)
        resolved: dict[ClaimKey, ResolvedTask] = {}
        bundles: dict[str, object] = {}
        for key in served_keys(registry, environment):
            record = registry.versions[key.task_id]
            if key.task_id not in bundles:
                bundle = load_task_bundle(_bundle_directory(tasks_root, key.location))
                try:
                    # Every field that names the task, re-derived from the bundle's own bytes.
                    assert_record_matches_bundle(record, bundle)
                except RegistryError as exc:
                    raise TaskNotAllowed(f"bundle at {key.location} does not match its registry record: {exc}") from exc
                manifest = bundle.manifest
                if (
                    record.provenance == LEGACY_PROVENANCE
                    and manifest.repository_commit != environment.repository_commit
                ) or (
                    record.provenance == V2_PROVENANCE
                    and manifest.environment_identity_sha256 != environment.environment_identity_sha256
                ):
                    raise TaskNotAllowed(f"bundle at {key.location} belongs to another environment")
                bundles[key.task_id] = bundle
            bundle = bundles[key.task_id]
            resolved[ClaimKey(key.task_id, key.task_bundle_sha256, key.problem_id)] = ResolvedTask(
                task_id=key.task_id,
                tier=record.tier,
                task_dir=_bundle_directory(tasks_root, key.location),
                task_bundle_sha256=key.task_bundle_sha256,
                timeout_seconds=bundle.manifest.timeout_seconds,  # type: ignore[attr-defined]
                enable_nanoda=bundle.manifest.enable_nanoda,  # type: ignore[attr-defined]
                problem_id=key.problem_id,
                provenance=record.provenance,
                expected_build_provenance_sha256=key.expected_build_provenance_sha256,
            )
        return cls(environment=environment, tasks=resolved)

    def served_keys(self) -> tuple[ClaimKey, ...]:
        return tuple(sorted(self.tasks, key=lambda key: (key.task_id, key.problem_id)))

    def resolve(self, *, task_id: str, task_bundle_sha256: str, problem_id: str) -> ResolvedTask:
        return _resolve_exact(
            self.tasks, task_id=task_id, task_bundle_sha256=task_bundle_sha256, problem_id=problem_id
        )


def load_task_resolver(
    *,
    tasks_root: Path,
    allowlist_path: Path,
    pool_root: Path,
    environment: Instance,
) -> PoolTaskResolver | VersionedTaskResolver:
    """The registry when the tasks release has one; otherwise its allowlist, for its own commit only.

    Both come from one committed state of the checkout (`read_coherently`).
    """
    assert_no_pending_publication(tasks_root)

    def resolve(snapshot: CheckoutSnapshot) -> PoolTaskResolver | VersionedTaskResolver:
        if snapshot.registry is not None:
            return VersionedTaskResolver.from_snapshot(
                snapshot, tasks_root=tasks_root, environment=environment, check_allowlist=True
            )
        if snapshot.allowlist is None:
            raise TaskNotAllowed(f"task allowlist is missing: {allowlist_path}")
        return PoolTaskResolver.from_allowlist(TaskPoolRegistry.from_bytes(snapshot.allowlist), pool_root=pool_root)

    resolver = read_coherently(tasks_root, resolve, allowlist_path=allowlist_path)
    if isinstance(resolver, VersionedTaskResolver):
        return resolver
    if resolver.repository_commit != environment.repository_commit:
        raise TaskNotAllowed(
            f"allowlist is for source {resolver.repository_commit} but the verifier environment runs "
            f"{environment.repository_commit}; refusing to claim another environment's paid work"
        )
    return resolver


@dataclass(frozen=True)
class StaticTaskResolver:
    """Resolver over explicit tasks. Used by tests, which need no audited pool on disk."""

    repository_commit: str
    tasks: Mapping[ClaimKey, ResolvedTask]

    def served_keys(self) -> tuple[ClaimKey, ...]:
        return tuple(sorted(self.tasks, key=lambda key: (key.task_id, key.problem_id)))

    def resolve(self, *, task_id: str, task_bundle_sha256: str, problem_id: str) -> ResolvedTask:
        return _resolve_exact(
            self.tasks, task_id=task_id, task_bundle_sha256=task_bundle_sha256, problem_id=problem_id
        )


def resolver_from_tasks(
    *, repository_commit: str, tasks: tuple[ResolvedTask, ...]
) -> StaticTaskResolver:
    """Build a resolver directly. Used by tests, which need no audited pool on disk.

    Each task must carry the `problem_id` of the submissions it serves: routing is exact.
    """
    return StaticTaskResolver(
        repository_commit=repository_commit,
        tasks={_key(task): task for task in tasks},
    )


__all__ = [
    "ClaimKey",
    "PoolTaskResolver",
    "ResolvedTask",
    "StaticTaskResolver",
    "TaskNotAllowed",
    "TaskResolver",
    "VersionedTaskResolver",
    "load_task_resolver",
    "resolver_from_tasks",
]
