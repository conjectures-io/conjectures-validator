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

import shutil
import time
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from verifier.errors import ReasonCode, VerifierError
from verifier.hashing import sha256_bytes
from verifier.models import Catalog, CatalogDeclaration
from verifier.task_generator import PlannedTaskVersion, build_task_version, plan_task_version
from verifier.task_loader import TaskBundle
from verifier.task_store import TaskVersionStore
from verifier.task_versions import V2_PROVENANCE, DependencyIndex, EnvironmentIdentity
from verifier.version_registry import Instance, RegistryBuilder, VersionRegistry

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
    validator = validate_target or _validator_factory(project_root, index)
    tiers = {target.declaration.theorem: target.tier for target in selected}

    def realise(plan: PlannedTaskVersion) -> tuple[TaskBundle, VersionDecision]:
        started = time.monotonic()
        record = previous.versions.get(plan.task_id)
        if record is not None:
            # Published before: reuse is exact or publication stops. A missing or different
            # store entry is never silently rebuilt over.
            bundle = store.load(plan.task_id, expected_sha256=record.task_bundle_sha256)
            if bundle is None:
                raise VerifierError(
                    ReasonCode.TRUSTED_FILE_MODIFIED,
                    f"published version {plan.task_id} is missing from the store; restore it from "
                    "the tasks repository instead of rebuilding a published commitment",
                )
            action = "reused"
        else:
            staging = store.staging_directory(plan.task_id)
            try:
                build_task_version(plan, catalog=catalog, output=staging / "bundle", validate_target=validator)
                bundle = store.publish(staging / "bundle", plan.task_id)
            finally:
                shutil.rmtree(staging, ignore_errors=True)
            action = "built"
        return bundle, VersionDecision(
            plan.task_id, plan.declaration.theorem, plan.mode, action, round(time.monotonic() - started, 3)
        )

    if jobs <= 1:
        realised = [realise(plan) for plan in plans]
    else:
        with ThreadPoolExecutor(max_workers=jobs) as executor:
            realised = list(executor.map(realise, plans))
    bundles = tuple(bundle for bundle, _decision in realised)
    decisions = tuple(decision for _bundle, decision in realised)

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


__all__ = ["ReleaseResult", "SelectedTarget", "VersionDecision", "plan_release", "publish_release"]
