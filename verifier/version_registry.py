"""The version registry: every published task version, its state, and where it may be verified.

`task-versions.json` in the tasks repository is the admission, selection and retirement record,
kept separate from the immutable bundles. Bundles never change. The registry is append-only and
records three things:

* **instances**: verification environments, each the pair (source commit, environment identity).
  A source change, or a toolchain, Mathlib, Comparator, exporter or policy change at the same
  source commit, is a new instance. Legacy instances, built before environment identities
  existed, have no environment identity.
* **publications**: the ordered list of releases. Each names the instance it publishes for and
  the exact `allowlist.json` digest it opened for intake. A release that only admits, holds,
  retires or reinstates targets is a new publication of an existing instance: no new commit, no
  rotated task ID, no rebuilt bundle.
* **versions**: every task version ever published, with its current state and its admissions —
  the instances where its identity was re-derived and where it may be verified. Admissions are
  only ever added.

A paid submission records `task_id`, `task_bundle_sha256` and `problem_id`, all fixed at intake.
`problem_id(commit, theorems)` commits to the accepting instance's source commit, and a v2 task ID
commits to its environment identity. A legacy version has exactly one admission. So for every
submission exactly one instance is its original environment, fixed by values that no later
release can change, and `served_keys` gives each instance exactly the submissions it accepted
itself. Two instances never serve the same key. There is no "equivalent environment" rule and no
inference from the current publication: older paid work waits for, and is claimed by, the
environment that accepted it; nothing else is charged an attempt for it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from verifier.errors import ReasonCode, VerifierError
from verifier.hashing import is_sha256, pretty_json, sha256_bytes
from verifier.task_generator import problem_id
from verifier.task_pool import reward_target_identity
from verifier.task_versions import (
    LEGACY_PROVENANCE,
    V2_PROVENANCE,
    EnvironmentIdentity,
    is_v2_task_id,
    strict_json,
)

REGISTRY_NAME = "task-versions.json"
REGISTRY_SCHEMA_VERSION = 1
MAX_REGISTRY_BYTES = 64 * 1024 * 1024
STATES = frozenset({"active", "retired", "held", "superseded"})
LEGACY_LOCATIONS = ("pool/", "history/legacy/")
V2_LOCATION = "versions/"


class RegistryError(VerifierError):
    def __init__(self, message: str) -> None:
        super().__init__(ReasonCode.INVALID_MANIFEST, f"task version registry: {message}")


def _is_commit(value: object) -> bool:
    return isinstance(value, str) and len(value) == 40 and all(c in "0123456789abcdef" for c in value)


@dataclass(frozen=True, order=True)
class Instance:
    """One verification environment: a source snapshot built under one environment identity."""

    repository_commit: str
    environment_identity_sha256: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "environment_identity_sha256": self.environment_identity_sha256,
            "repository_commit": self.repository_commit,
        }


@dataclass(frozen=True)
class Publication:
    sequence: int
    instance: Instance
    allowlist_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {"allowlist_sha256": self.allowlist_sha256, "sequence": self.sequence, **self.instance.to_dict()}


@dataclass(frozen=True)
class Admission:
    """A version validated in one instance; that instance may verify submissions it accepted."""

    instance: Instance
    problem_id: str
    # The instance's build provenance for the version's source module (v2 only), recomputed by
    # the verifier in its container. Current build/cache provenance, not task identity.
    build_provenance_sha256: str | None
    first_publication: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "build_provenance_sha256": self.build_provenance_sha256,
            "first_publication": self.first_publication,
            "problem_id": self.problem_id,
            **self.instance.to_dict(),
        }


@dataclass(frozen=True)
class VersionRecord:
    task_id: str
    task_bundle_sha256: str
    provenance: str
    reward_target_id: str
    theorems: tuple[str, ...]
    mode: str
    tier: str
    location: str
    environment_identity_sha256: str | None
    dependency_identity_sha256: str | None
    state: str
    admissions: tuple[Admission, ...]

    def admission_at(self, instance: Instance) -> Admission | None:
        return next((item for item in self.admissions if item.instance == instance), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "admissions": [item.to_dict() for item in self.admissions],
            "dependency_identity_sha256": self.dependency_identity_sha256,
            "environment_identity_sha256": self.environment_identity_sha256,
            "location": self.location,
            "mode": self.mode,
            "provenance": self.provenance,
            "reward_target_id": self.reward_target_id,
            "state": self.state,
            "task_bundle_sha256": self.task_bundle_sha256,
            "task_id": self.task_id,
            "theorems": list(self.theorems),
            "tier": self.tier,
        }


@dataclass(frozen=True)
class VersionRegistry:
    environments: Mapping[str, EnvironmentIdentity]
    instances: tuple[Instance, ...]
    publications: tuple[Publication, ...]
    versions: Mapping[str, VersionRecord]
    sha256: str = ""

    @property
    def current(self) -> Publication:
        return self.publications[-1]

    def to_bytes(self) -> bytes:
        return pretty_json(
            {
                "default": "DENY",
                "environments": [
                    {"environment_identity_sha256": key, "identity": value.to_dict()}
                    for key, value in sorted(self.environments.items())
                ],
                "instances": [item.to_dict() for item in self.instances],
                "publications": [item.to_dict() for item in self.publications],
                "schema_version": REGISTRY_SCHEMA_VERSION,
                "versions": [self.versions[key].to_dict() for key in sorted(self.versions)],
            }
        ).encode("utf-8")

    @classmethod
    def empty(cls) -> "VersionRegistry":
        return cls(environments={}, instances=(), publications=(), versions={})

    @classmethod
    def load(cls, path: Path) -> "VersionRegistry":
        from verifier.task_versions import read_tree_file

        return cls.from_bytes(read_tree_file(path.parent, (path.name,), MAX_REGISTRY_BYTES))

    @classmethod
    def from_bytes(cls, content: bytes) -> "VersionRegistry":
        try:
            value = strict_json(content, REGISTRY_NAME)
        except VerifierError as exc:
            raise RegistryError(str(exc)) from exc
        if not isinstance(value, dict) or set(value) != {
            "default", "environments", "instances", "publications", "schema_version", "versions",
        }:
            raise RegistryError("field set is invalid")
        if value["schema_version"] != REGISTRY_SCHEMA_VERSION or value["default"] != "DENY":
            raise RegistryError("schema or default policy is invalid")
        if not all(isinstance(value[key], list) for key in ("environments", "instances", "publications", "versions")):
            raise RegistryError("environments, instances, publications and versions must be lists")
        environments: dict[str, EnvironmentIdentity] = {}
        for row in value["environments"]:
            if not isinstance(row, dict) or set(row) != {"environment_identity_sha256", "identity"}:
                raise RegistryError("environment row is invalid")
            identity = EnvironmentIdentity.from_dict(row["identity"])
            if identity.sha256 != row["environment_identity_sha256"] or identity.sha256 in environments:
                raise RegistryError("environment identity digest is wrong or duplicated")
            environments[identity.sha256] = identity
        if list(environments) != sorted(environments):
            raise RegistryError("environments must be sorted")
        instances = tuple(_instance(row, environments) for row in value["instances"])
        if len(set(instances)) != len(instances):
            raise RegistryError("an instance is listed twice")
        publications = []
        for position, row in enumerate(value["publications"], start=1):
            if (
                not isinstance(row, dict)
                or set(row) != {"allowlist_sha256", "environment_identity_sha256", "repository_commit", "sequence"}
                or row["sequence"] != position
                or not is_sha256(row["allowlist_sha256"])
            ):
                raise RegistryError("publication row is invalid or out of sequence")
            instance = _instance(
                {key: row[key] for key in ("environment_identity_sha256", "repository_commit")}, environments
            )
            if instance not in instances:
                raise RegistryError("a publication names an unlisted instance")
            publications.append(Publication(position, instance, row["allowlist_sha256"]))
        if not publications:
            raise RegistryError("a registry has at least one publication")
        if {item.instance for item in publications} != set(instances):
            raise RegistryError("every instance must be introduced by a publication")
        versions: dict[str, VersionRecord] = {}
        digests: set[str] = set()
        for row in value["versions"]:
            record = _version(row, environments=environments, instances=instances, publications=len(publications))
            if record.task_id in versions or record.task_bundle_sha256 in digests:
                raise RegistryError(f"version or digest is listed twice: {record.task_id}")
            versions[record.task_id] = record
            digests.add(record.task_bundle_sha256)
        if list(versions) != sorted(versions):
            raise RegistryError("versions must be sorted by task ID")
        _check_current(versions.values(), current=publications[-1].instance)
        return cls(environments, instances, tuple(publications), versions, sha256_bytes(content))


def _instance(row: object, environments: Mapping[str, Any]) -> Instance:
    if (
        not isinstance(row, dict)
        or set(row) != {"environment_identity_sha256", "repository_commit"}
        or not _is_commit(row["repository_commit"])
        or (row["environment_identity_sha256"] is not None and row["environment_identity_sha256"] not in environments)
    ):
        raise RegistryError("instance is invalid")
    return Instance(row["repository_commit"], row["environment_identity_sha256"])


def _version(
    row: object, *, environments: Mapping[str, Any], instances: tuple[Instance, ...], publications: int
) -> VersionRecord:
    fields = {
        "admissions", "dependency_identity_sha256", "environment_identity_sha256", "location", "mode",
        "provenance", "reward_target_id", "state", "task_bundle_sha256", "task_id", "theorems", "tier",
    }
    if not isinstance(row, dict) or set(row) != fields:
        raise RegistryError("version row field set is invalid")
    task_id = row["task_id"]
    theorems = row["theorems"]
    if (
        not isinstance(task_id, str)
        or not is_sha256(row["task_bundle_sha256"])
        or not isinstance(theorems, list)
        or len(theorems) != 1
        or not all(isinstance(item, str) and item for item in theorems)
        or row["reward_target_id"] != reward_target_identity(theorems[0])
        or row["state"] not in STATES
        or not isinstance(row["mode"], str)
        or not isinstance(row["tier"], str)
        or not isinstance(row["location"], str)
        or ".." in Path(row["location"]).parts
        or Path(row["location"]).is_absolute()
        or not isinstance(row["admissions"], list)
        or not row["admissions"]
    ):
        raise RegistryError(f"version row is invalid: {task_id!r}")
    admissions = []
    for item in row["admissions"]:
        if not isinstance(item, dict) or set(item) != {
            "build_provenance_sha256", "environment_identity_sha256", "first_publication", "problem_id",
            "repository_commit",
        }:
            raise RegistryError(f"admission is invalid: {task_id}")
        instance = _instance(
            {key: item[key] for key in ("environment_identity_sha256", "repository_commit")}, environments
        )
        if (
            instance not in instances
            or type(item["first_publication"]) is not int
            or not 1 <= item["first_publication"] <= publications
            or item["problem_id"] != problem_id(instance.repository_commit, tuple(theorems))
        ):
            raise RegistryError(f"admission is invalid: {task_id}")
        admissions.append(Admission(instance, item["problem_id"], item["build_provenance_sha256"], item["first_publication"]))
    if len({item.instance for item in admissions}) != len(admissions):
        raise RegistryError(f"version is admitted twice in one instance: {task_id}")
    if [item.first_publication for item in admissions] != sorted(item.first_publication for item in admissions):
        raise RegistryError(f"admissions are not in publication order: {task_id}")
    provenance = row["provenance"]
    if provenance == LEGACY_PROVENANCE:
        if (
            is_v2_task_id(task_id)
            or row["environment_identity_sha256"] is not None
            or row["dependency_identity_sha256"] is not None
            or not row["location"].startswith(LEGACY_LOCATIONS)
            or len(admissions) != 1
            or not task_id.startswith(f"fc-{admissions[0].instance.repository_commit[:8]}-")
            or admissions[0].build_provenance_sha256 is not None
        ):
            # A legacy task ID embeds its source commit and binds no toolchain: it is valid in
            # exactly the one instance that published it.
            raise RegistryError(f"legacy version is not bound to exactly one instance: {task_id}")
    elif provenance == V2_PROVENANCE:
        if (
            not is_v2_task_id(task_id)
            or row["environment_identity_sha256"] not in environments
            or not is_sha256(row["dependency_identity_sha256"])
            or row["location"] != f"{V2_LOCATION}{task_id}"
            or any(item.instance.environment_identity_sha256 != row["environment_identity_sha256"] for item in admissions)
            or not all(is_sha256(item.build_provenance_sha256) for item in admissions)
        ):
            raise RegistryError(f"v2 version identity is invalid: {task_id}")
    else:
        raise RegistryError(f"unknown provenance for {task_id}: {provenance!r}")
    return VersionRecord(
        task_id=task_id,
        task_bundle_sha256=row["task_bundle_sha256"],
        provenance=provenance,
        reward_target_id=row["reward_target_id"],
        theorems=tuple(theorems),
        mode=row["mode"],
        tier=row["tier"],
        location=row["location"],
        environment_identity_sha256=row["environment_identity_sha256"],
        dependency_identity_sha256=row["dependency_identity_sha256"],
        state=row["state"],
        admissions=tuple(admissions),
    )


def _check_current(versions: Iterable[VersionRecord], *, current: Instance) -> None:
    """Active versions are validated in the current instance, one per (target, mode)."""
    open_now: dict[tuple[str, str], str] = {}
    for record in versions:
        if record.state != "active":
            continue
        if record.admission_at(current) is None:
            raise RegistryError(f"active version is not admitted in the current instance: {record.task_id}")
        key = (record.reward_target_id, record.mode)
        if key in open_now:
            raise RegistryError(f"two versions of {key} are active: {open_now[key]}, {record.task_id}")
        open_now[key] = record.task_id


# --- Routing ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ServedKey:
    """One (task, digest, intake problem) a worker in this instance may claim and verify."""

    task_id: str
    task_bundle_sha256: str
    problem_id: str
    location: str
    provenance: str
    # v2 only: this instance's build provenance, recomputed by the verifier in its container.
    expected_build_provenance_sha256: str | None = None


def served_keys(registry: VersionRegistry, instance: Instance) -> tuple[ServedKey, ...]:
    """Exactly the submissions `instance` accepted itself: its original-environment work."""
    if not _is_commit(instance.repository_commit):
        raise RegistryError("instance source commit is invalid")
    if instance not in registry.instances:
        return ()
    result = []
    for record in registry.versions.values():
        own = record.admission_at(instance)
        if own is None:
            # Not validated in this instance: another statement, closure or environment.
            continue
        result.append(
            ServedKey(
                record.task_id,
                record.task_bundle_sha256,
                own.problem_id,
                record.location,
                record.provenance,
                own.build_provenance_sha256,
            )
        )
    return tuple(sorted(result, key=lambda key: (key.task_id, key.problem_id)))


def assert_append_only(previous: VersionRegistry, current: VersionRegistry) -> None:
    """A later registry may add environments, instances, publications, versions and admissions,
    and change a version's state. Nothing published may be removed or altered.

    In particular no version may lose an admission, gain one dated to an earlier publication, or
    have an admission's problem ID or build provenance changed: that is what keeps every paid
    submission's original environment fixed after later releases.
    """
    if current.publications[: len(previous.publications)] != previous.publications:
        raise RegistryError("a later registry rewrote or reordered publications")
    if current.instances[: len(previous.instances)] != previous.instances:
        raise RegistryError("a later registry rewrote or reordered instances")
    for key, identity in previous.environments.items():
        if current.environments.get(key) != identity:
            raise RegistryError("a later registry removed or changed a published environment")
    boundary = len(previous.publications)
    fixed = (
        "task_bundle_sha256", "provenance", "reward_target_id", "theorems", "mode", "tier",
        "location", "environment_identity_sha256", "dependency_identity_sha256",
    )
    for task_id, old in previous.versions.items():
        new = current.versions.get(task_id)
        if new is None:
            raise RegistryError(f"a later registry dropped published version {task_id}")
        if any(getattr(old, name) != getattr(new, name) for name in fixed):
            raise RegistryError(f"a later registry changed the commitment of {task_id}")
        if new.admissions[: len(old.admissions)] != old.admissions or any(
            item.first_publication <= boundary for item in new.admissions[len(old.admissions):]
        ):
            raise RegistryError(f"a later registry rewrote the admissions of {task_id}")
    for task_id, new in current.versions.items():
        if task_id not in previous.versions and any(item.first_publication <= boundary for item in new.admissions):
            raise RegistryError(f"new version {task_id} claims an admission in an earlier publication")


def assert_matches_allowlist(registry: VersionRegistry, allowlist_path: Path) -> None:
    """The current publication's allowlist is exactly the registry's active versions."""
    try:
        content = allowlist_path.read_bytes()
        allowlist = json.loads(content)
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryError(f"cannot read the allowlist: {exc}") from exc
    current = registry.current
    if sha256_bytes(content) != current.allowlist_sha256:
        raise RegistryError("allowlist is not the one the current publication opened")
    if allowlist.get("repository_commit") != current.instance.repository_commit:
        raise RegistryError("allowlist and current publication name different source commits")
    expected = {
        (row["task_id"], row["task_bundle_sha256"], row["problem_id"])
        for row in allowlist.get("allowed_task_bundles", ())
    }
    actual = {
        (record.task_id, record.task_bundle_sha256, admission.problem_id)
        for record in registry.versions.values()
        if record.state == "active" and (admission := record.admission_at(current.instance)) is not None
    }
    if expected != actual:
        missing = sorted(task for task, _digest, _problem in expected - actual)[:5]
        extra = sorted(task for task, _digest, _problem in actual - expected)[:5]
        raise RegistryError(f"registry active versions disagree with the allowlist; missing={missing} extra={extra}")


@dataclass
class RegistryBuilder:
    """Appends one publication for `instance` to a previous registry, never rewriting history.

    The instance may be new (a source, toolchain or policy change) or already published (an
    admission, hold, retirement or reinstatement change): the latter adds no instance and
    leaves every unchanged version's ID, bundle and admissions as they were.
    """

    previous: VersionRegistry
    instance: Instance
    environment: EnvironmentIdentity | None
    versions: dict[str, VersionRecord] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (self.environment is None) != (self.instance.environment_identity_sha256 is None) or (
            self.environment is not None and self.environment.sha256 != self.instance.environment_identity_sha256
        ):
            raise RegistryError("instance environment identity does not match the environment")
        self.versions = dict(self.previous.versions)
        self.sequence = len(self.previous.publications) + 1

    def admit(
        self,
        *,
        task_id: str,
        task_bundle_sha256: str,
        provenance: str,
        theorem: str,
        mode: str,
        tier: str,
        location: str,
        state: str,
        dependency_identity_sha256: str | None = None,
        build_provenance_sha256: str | None = None,
    ) -> None:
        """Record that `task_id` was validated in this instance, and set its state."""
        environment = self.instance.environment_identity_sha256 if provenance == V2_PROVENANCE else None
        admission = Admission(
            self.instance,
            problem_id(self.instance.repository_commit, (theorem,)),
            build_provenance_sha256,
            self.sequence,
        )
        existing = self.versions.get(task_id)
        if existing is None:
            self.versions[task_id] = VersionRecord(
                task_id=task_id,
                task_bundle_sha256=task_bundle_sha256,
                provenance=provenance,
                reward_target_id=reward_target_identity(theorem),
                theorems=(theorem,),
                mode=mode,
                tier=tier,
                location=location,
                environment_identity_sha256=environment,
                dependency_identity_sha256=dependency_identity_sha256,
                state=state,
                admissions=(admission,),
            )
            return
        if (
            existing.task_bundle_sha256 != task_bundle_sha256
            or existing.provenance != provenance
            or existing.dependency_identity_sha256 != dependency_identity_sha256
            or existing.environment_identity_sha256 != environment
        ):
            # Same ID, different bytes or identity: poisoning or a non-deterministic build.
            raise RegistryError(f"{task_id} is already published with a different commitment")
        prior = existing.admission_at(self.instance)
        if prior is not None and prior.build_provenance_sha256 != build_provenance_sha256:
            raise RegistryError(f"{task_id}: this instance's build provenance changed since it was published")
        admissions = existing.admissions if prior is not None else (*existing.admissions, admission)
        self.versions[task_id] = replace(existing, state=state, admissions=admissions)

    def set_state(self, task_id: str, state: str) -> None:
        if state not in STATES:
            raise RegistryError(f"unknown state {state!r}")
        self.versions[task_id] = replace(self.versions[task_id], state=state)

    def build(self, *, allowlist_sha256: str) -> VersionRegistry:
        environments = dict(self.previous.environments)
        if self.environment is not None:
            environments.setdefault(self.environment.sha256, self.environment)
        instances = self.previous.instances
        if self.instance not in instances:
            instances = (*instances, self.instance)
        registry = VersionRegistry(
            environments=environments,
            instances=instances,
            publications=(*self.previous.publications, Publication(self.sequence, self.instance, allowlist_sha256)),
            versions=dict(sorted(self.versions.items())),
        )
        # Round-trip through the strict loader: what is written is exactly what will be read.
        built = VersionRegistry.from_bytes(registry.to_bytes())
        if self.previous.publications:
            assert_append_only(self.previous, built)
        return built


def publish_legacy(
    previous: VersionRegistry,
    *,
    instance: Instance,
    environment: EnvironmentIdentity | None,
    allowlist_path: Path,
    tasks_root: Path,
    locations: Mapping[str, str],
    states: Mapping[str, str] | None = None,
) -> VersionRegistry:
    """Register the legacy versions a pre-registry allowlist opened, as one publication.

    This is how an existing release enters the registry. Every bundle is loaded and checked
    against that allowlist and against the instance's source commit, and is recorded at exactly
    this one instance: the instance the operator designates to verify its paid work.
    """
    from verifier.task_loader import load_task_bundle
    from verifier.task_registry import TaskPoolRegistry

    allowlist = TaskPoolRegistry.load(allowlist_path)
    if allowlist.repository_commit != instance.repository_commit:
        raise RegistryError("legacy allowlist and instance name different source commits")
    builder = RegistryBuilder(previous=previous, instance=instance, environment=environment)
    for task_id, allowed in sorted(allowlist.tasks.items()):
        location = locations.get(task_id)
        if location is None:
            raise RegistryError(f"no location for legacy task {task_id}")
        bundle = load_task_bundle(tasks_root / location)
        allowlist.assert_bundle(bundle)
        if bundle.manifest.provenance != LEGACY_PROVENANCE or bundle.manifest.repository_commit != instance.repository_commit:
            raise RegistryError(f"{task_id} is not a legacy bundle of {instance.repository_commit}")
        builder.admit(
            task_id=task_id,
            task_bundle_sha256=bundle.sha256,
            provenance=LEGACY_PROVENANCE,
            theorem=allowed.source_theorems[0],
            mode=allowed.mode,
            tier=allowed.tier,
            location=location,
            state=(states or {}).get(task_id, "active"),
        )
    return builder.build(allowlist_sha256=sha256_bytes(allowlist_path.read_bytes()))


__all__ = [
    "Admission",
    "Instance",
    "Publication",
    "REGISTRY_NAME",
    "RegistryBuilder",
    "RegistryError",
    "ServedKey",
    "VersionRecord",
    "VersionRegistry",
    "assert_append_only",
    "assert_matches_allowlist",
    "publish_legacy",
    "served_keys",
]
