"""Incremental publication: build only the task versions whose identity changed.

One call publishes one release for one verification instance (source commit, environment):

1. The caller supplies a `DependencyIndex` derived by `derive_dependency_index` in that very
   environment. Every selected statement's version is planned from it without running Lean.
2. A planned version whose task ID the previous registry already published is **reused**: its
   stored bundle must load, re-validate and have exactly the published digest, or publication
   stops. No caller assertion, cache entry or unpublished bundle is ever trusted for reuse.
3. Every other version is **built**: staged in the store, compiled and inspected in the pinned
   environment (which re-derives its dependency identity and build provenance and must agree
   with the index), then atomically published to the store.
4. The registry gains one publication. Previously active versions that are no longer selected
   become `superseded`, `retired` or `held`; they keep every admission, so paid work they
   accepted stays verifiable in its original environment. A retained version whose identity is
   unchanged here is also admitted in this instance.
5. The audited allowlist for the new publication is produced by the unchanged
   `build_task_allowlist`, and the registry is cross-checked against it.

A toolchain, Mathlib, Comparator, exporter, generator or policy change alters the environment
identity, hence every task ID: everything is rebuilt and validated, never relabelled. A
publication that only changes admission (a hold, retirement or reinstatement) for the same
instance rebuilds nothing and rotates nothing.
"""

from __future__ import annotations

import multiprocessing
import shutil
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from verifier.errors import ReasonCode, VerifierError
from verifier.hashing import sha256_bytes
from verifier.models import Catalog, CatalogDeclaration
from verifier.task_generator import PlannedTaskVersion, build_task_version, plan_task_version
from verifier.task_loader import TaskBundle
from verifier.task_store import TaskVersionStore
from verifier.task_versions import V2_PROVENANCE, DependencyIndex, EnvironmentIdentity
from verifier.version_registry import Instance, RegistryBuilder, VersionRegistry, assert_record_matches_bundle

EXIT_STATES = frozenset({"retired", "held"})


@dataclass(frozen=True)
class SelectedTarget:
    declaration: CatalogDeclaration
    tier: str
    modes: tuple[str, ...]


@dataclass(frozen=True)
class VersionDecision:
    task_id: str
    theorem: str
    mode: str
    action: str  # "reused" | "built"
    seconds: float


@dataclass
class ReleaseResult:
    registry: VersionRegistry
    bundles: tuple[TaskBundle, ...]
    decisions: tuple[VersionDecision, ...]
    state_changes: Mapping[str, str]
    retained_admissions: tuple[str, ...]
    counts: dict[str, int] = field(default_factory=dict)


def _validator_factory(project_root, index: DependencyIndex) -> Callable[..., str]:
    from verifier.workspace import target_validator

    return target_validator(project_root, dependency_index=index)


def plan_release(
    *,
    catalog: Catalog,
    targets: Iterable[SelectedTarget],
    index: DependencyIndex,
) -> tuple[PlannedTaskVersion, ...]:
    plans = []
    for target in targets:
        dependency = index.dependencies.get(target.declaration.theorem)
        if dependency is None:
            raise VerifierError(
                ReasonCode.DEPENDENCY_IDENTITY_MISMATCH,
                f"no derived dependency identity for {target.declaration.theorem}",
            )
        for mode in target.modes:
            plans.append(
                plan_task_version(
                    catalog=catalog,
                    declaration=target.declaration,
                    mode=mode,
                    environment=index.environment,
                    dependency=dependency,
                )
            )
    ids = [plan.task_id for plan in plans]
    if len(ids) != len(set(ids)):
        raise VerifierError(ReasonCode.INVALID_ARGUMENT, "two selected targets plan the same task ID")
    return tuple(plans)


# The challenge build and inspector each bound themselves (1800 s and 600 s), but only once their
# child process exists. This outer bound also covers a worker stuck before that point.
DEFAULT_JOB_TIMEOUT_SECONDS = 3 * 3600
_WORKER: dict[str, Any] = {}


def _worker_initialise(project_root: Any, index: DependencyIndex, catalog: Catalog, validate_target: Any) -> None:
    _WORKER["catalog"] = catalog
    _WORKER["validate"] = validate_target or _validator_factory(project_root, index)


def _worker_build(plan: PlannedTaskVersion, output: str) -> float:
    started = time.monotonic()
    build_task_version(plan, catalog=_WORKER["catalog"], output=Path(output), validate_target=_WORKER["validate"])
    return round(time.monotonic() - started, 3)


def _build_all(
    plans: list[PlannedTaskVersion],
    *,
    store: TaskVersionStore,
    catalog: Catalog,
    index: DependencyIndex,
    validate_target: Callable[..., str] | None,
    project_root: Any,
    jobs: int,
    job_timeout_seconds: int,
) -> Iterable[tuple[PlannedTaskVersion, TaskBundle, float]]:
    """Build versions, then publish each to the store from this (single-threaded) process.

    Parallel builds run in `spawn`-started worker processes, one build at a time each, never in
    threads: `run_process` applies resource limits through `preexec_fn`, which is only safe to
    fork from a process with no other running threads. A worker that does not finish within
    `job_timeout_seconds` is terminated with the whole pool and publication fails closed.
    """
    stagings = {plan.task_id: store.staging_directory(plan.task_id) for plan in plans}
    try:
        if jobs <= 1 or len(plans) <= 1:
            _worker_initialise(project_root, index, catalog, validate_target)
            for plan in plans:
                seconds = _worker_build(plan, str(stagings[plan.task_id] / "bundle"))
                yield plan, store.publish(stagings[plan.task_id] / "bundle", plan.task_id), seconds
            return
        context = multiprocessing.get_context("spawn")
        pool = context.Pool(
            processes=min(jobs, len(plans)),
            initializer=_worker_initialise,
            initargs=(project_root, index, catalog, validate_target),
        )
        try:
            pending = [
                (plan, pool.apply_async(_worker_build, (plan, str(stagings[plan.task_id] / "bundle"))))
                for plan in plans
            ]
            pool.close()
            # Dispatch is first in, first out, so when the wait for one build begins every
            # earlier build has finished and this one is running: the bound is per build.
            for plan, result in pending:
                try:
                    seconds = result.get(timeout=job_timeout_seconds)
                except multiprocessing.TimeoutError as exc:
                    raise VerifierError(
                        ReasonCode.INTERNAL_ERROR,
                        f"building {plan.task_id} exceeded {job_timeout_seconds}s; workers terminated",
                    ) from exc
                yield plan, store.publish(stagings[plan.task_id] / "bundle", plan.task_id), seconds
        finally:
            pool.terminate()
            pool.join()
    finally:
        for staging in stagings.values():
            shutil.rmtree(staging, ignore_errors=True)


def publish_release(
    *,
    catalog: Catalog,
    targets: Iterable[SelectedTarget],
    index: DependencyIndex,
    store: TaskVersionStore,
    previous: VersionRegistry,
    instance: Instance,
    allowlist_for: Callable[[tuple[TaskBundle, ...]], bytes],
    exit_states: Mapping[str, str] = {},
    validate_target: Callable[..., str] | None = None,
    project_root: Any = None,
    jobs: int = 1,
    job_timeout_seconds: int = DEFAULT_JOB_TIMEOUT_SECONDS,
) -> tuple[ReleaseResult, bytes]:
    """Publish one release; returns the new registry and the allowlist bytes it opened."""
    environment: EnvironmentIdentity = index.environment
    if instance != Instance(index.repository_commit, environment.sha256):
        raise VerifierError(
            ReasonCode.ENVIRONMENT_MISMATCH,
            "the dependency index was not derived in the instance being published",
        )
    if any(state not in EXIT_STATES for state in exit_states.values()):
        raise VerifierError(ReasonCode.INVALID_ARGUMENT, "exit states are retired or held")
    selected = tuple(targets)
    plans = plan_release(catalog=catalog, targets=selected, index=index)
    tiers = {target.declaration.theorem: target.tier for target in selected}

    def reuse(plan: PlannedTaskVersion) -> TaskBundle | None:
        record = previous.versions.get(plan.task_id)
        if record is None:
            return None
        # Published before: reuse is exact or publication stops. A missing or different store
        # entry is never silently rebuilt over.
        bundle = store.load(plan.task_id, expected_sha256=record.task_bundle_sha256)
        if bundle is None:
            raise VerifierError(
                ReasonCode.TRUSTED_FILE_MODIFIED,
                f"published version {plan.task_id} is missing from the store; restore it from "
                "the tasks repository instead of rebuilding a published commitment",
            )
        assert_record_matches_bundle(record, bundle)
        return bundle

    realised: dict[str, tuple[TaskBundle, VersionDecision]] = {}
    to_build: list[PlannedTaskVersion] = []
    for plan in plans:
        started = time.monotonic()
        bundle = reuse(plan)
        if bundle is None:
            to_build.append(plan)
        else:
            realised[plan.task_id] = (
                bundle,
                VersionDecision(plan.task_id, plan.declaration.theorem, plan.mode, "reused", round(time.monotonic() - started, 3)),
            )
    for plan, bundle, seconds in _build_all(
        to_build,
        store=store,
        catalog=catalog,
        index=index,
        validate_target=validate_target,
        project_root=project_root,
        jobs=jobs,
        job_timeout_seconds=job_timeout_seconds,
    ):
        realised[plan.task_id] = (
            bundle, VersionDecision(plan.task_id, plan.declaration.theorem, plan.mode, "built", seconds)
        )
    bundles = tuple(realised[plan.task_id][0] for plan in plans)
    decisions = tuple(realised[plan.task_id][1] for plan in plans)

    builder = RegistryBuilder(previous=previous, instance=instance, environment=environment)
    for plan, bundle in zip(plans, bundles, strict=True):
        theorem = plan.declaration.theorem
        builder.admit(
            task_id=plan.task_id,
            task_bundle_sha256=bundle.sha256,
            provenance=V2_PROVENANCE,
            theorem=theorem,
            mode=plan.mode,
            tier=tiers[theorem],
            location=f"versions/{plan.task_id}",
            state="active",
            dependency_identity_sha256=index.dependencies[theorem].sha256,
            build_provenance_sha256=index.provenance[theorem].sha256,
        )
    planned = {plan.task_id for plan in plans}
    selected_keys = {(plan.declaration.theorem, plan.mode) for plan in plans}
    state_changes: dict[str, str] = {}
    retained: list[str] = []
    for task_id, record in previous.versions.items():
        if task_id in planned or record.state != "active":
            continue
        theorem = record.theorems[0]
        if (theorem, record.mode) in selected_keys:
            state = "superseded"
        else:
            state = exit_states.get(theorem)
            if state is None:
                raise VerifierError(
                    ReasonCode.INVALID_ARGUMENT,
                    f"{theorem} left the selection without a recorded retirement or hold",
                )
        builder.set_state(task_id, state)
        state_changes[task_id] = state
        dependency = index.dependencies.get(theorem)
        if (
            state in EXIT_STATES
            and record.provenance == V2_PROVENANCE
            and record.environment_identity_sha256 == environment.sha256
            and dependency is not None
            and dependency.sha256 == record.dependency_identity_sha256
        ):
            # Unchanged here, only closed to intake: this instance may verify it too.
            builder.admit(
                task_id=task_id,
                task_bundle_sha256=record.task_bundle_sha256,
                provenance=V2_PROVENANCE,
                theorem=theorem,
                mode=record.mode,
                tier=record.tier,
                location=record.location,
                state=state,
                dependency_identity_sha256=record.dependency_identity_sha256,
                build_provenance_sha256=index.provenance[theorem].sha256,
            )
            retained.append(task_id)
    allowlist = allowlist_for(bundles)
    registry = builder.build(allowlist_sha256=sha256_bytes(allowlist))
    counts = {
        "planned_versions": len(plans),
        "reused": sum(item.action == "reused" for item in decisions),
        "built": sum(item.action == "built" for item in decisions),
        "superseded": sum(state == "superseded" for state in state_changes.values()),
        "retired": sum(state == "retired" for state in state_changes.values()),
        "held": sum(state == "held" for state in state_changes.values()),
        "retained_admissions": len(retained),
    }
    return (
        ReleaseResult(
            registry=registry,
            bundles=bundles,
            decisions=decisions,
            state_changes=state_changes,
            retained_admissions=tuple(retained),
            counts=counts,
        ),
        allowlist,
    )




def publish_tasks_checkout(
    *,
    project_root: Any,
    catalog: Catalog,
    tasks_root: Any,
    pool_size: int,
    audit_date_utc: str,
    exit_states: Mapping[str, str] | None = None,
    jobs: int = 1,
) -> tuple[ReleaseResult, bytes]:
    """Publish the next release of a tasks checkout from its audited selection metadata.

    Reads `tiers/tier-1/` exactly as `rebuild_task_pool.py` does, derives the dependency index
    in this environment, publishes into `versions/`, and commits the new `task-versions.json`
    and `allowlist.json` through `verifier.publication`: under the checkout's writer lock, from
    the registry read inside that lock, journaled so an interrupted commit is never half-visible.
    """
    import tempfile

    from verifier.publication import checkout_writer, commit_publication
    from verifier.task_pool import (
        build_task_allowlist,
        group_task_declarations,
        load_held_sources,
        load_retired_conjectures,
        load_retired_sources,
        load_selection_audit,
        load_task_grouping,
        load_task_targets,
        select_task_declarations,
    )
    from verifier.task_policy import PRODUCTION_TASK_MODES
    from verifier.task_registry import TaskPoolRegistry
    from verifier.task_versions import derive_dependency_index
    from verifier.version_registry import REGISTRY_NAME, assert_matches_allowlist

    root = Path(tasks_root)
    metadata = root / "tiers" / "tier-1"
    with checkout_writer(root):
        retired = load_retired_sources(metadata / "retired-source-theorems.json")
        held = load_held_sources(metadata / "held-source-theorems.json")
        retired_conjectures = load_retired_conjectures(metadata / "retired-conjectures.json")
        selection_audit = load_selection_audit(metadata / "selection-audit.json")
        task_targets = load_task_targets(metadata / "task-targets.json")
        grouping = load_task_grouping(metadata / "task-groups.json")
        selected = select_task_declarations(
            catalog=catalog, retired=retired, held=held, selection_audit=selection_audit,
            task_targets=task_targets, pool_size=pool_size,
        )
        groups = group_task_declarations(selected, grouping)
        if any(len(group) != 1 for group in groups):
            raise VerifierError(ReasonCode.UNSUPPORTED_DECLARATION, "v2 publication supports single-target tasks")
        # A target leaves intake only through a recorded retirement or hold.
        states = (
            dict(exit_states)
            if exit_states is not None
            else {**{name: "retired" for name in retired.theorems}, **{name: "held" for name in held.theorems}}
        )
        by_name = {item.theorem: item for item in catalog.declarations}
        indexed = [*selected, *(by_name[name] for name in sorted(states) if name in by_name)]
        index = derive_dependency_index(project_root=Path(project_root), declarations=indexed)
        registry_path = root / REGISTRY_NAME
        previous_bytes = registry_path.read_bytes() if registry_path.is_file() else None
        previous = VersionRegistry.from_bytes(previous_bytes) if previous_bytes is not None else VersionRegistry.empty()
        (root / "versions").mkdir(exist_ok=True)

        def allowlist_for(bundles: tuple[TaskBundle, ...]) -> bytes:
            return build_task_allowlist(
                catalog=catalog, retired=retired, held=held, retired_conjectures=retired_conjectures,
                selection_audit=selection_audit, task_targets=task_targets, grouping=grouping,
                selected=groups, bundles=bundles, audit_date_utc=audit_date_utc, tier="tier-1",
            )

        result, allowlist = publish_release(
            catalog=catalog,
            targets=[SelectedTarget(item, "tier-1", PRODUCTION_TASK_MODES) for item in selected],
            index=index,
            store=TaskVersionStore(root / "versions"),
            previous=previous,
            instance=Instance(index.repository_commit, index.environment.sha256),
            allowlist_for=allowlist_for,
            exit_states=states,
            project_root=Path(project_root),
            jobs=jobs,
        )
        with tempfile.TemporaryDirectory(prefix=".publication-check-", dir=root) as temporary:
            candidate = Path(temporary) / "allowlist.json"
            candidate.write_bytes(allowlist)
            checked = TaskPoolRegistry.load(candidate)
            for bundle in result.bundles:
                checked.assert_bundle(bundle)
            assert_matches_allowlist(result.registry, candidate)
        commit_publication(
            root,
            previous_registry_sha256=sha256_bytes(previous_bytes) if previous_bytes is not None else None,
            registry=result.registry.to_bytes(),
            allowlist=allowlist,
        )
    return result, allowlist

__all__ = [
    "ReleaseResult",
    "SelectedTarget",
    "VersionDecision",
    "plan_release",
    "publish_release",
    "publish_tasks_checkout",
]
