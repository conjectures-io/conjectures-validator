from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from verifier.hashing import is_sha256
from verifier.task_generator import problem_id
from verifier.task_loader import TaskBundle
from verifier.task_policy import (
    COUNTEREXAMPLE_TASK_MODE,
    EXACT_TASK_MODE,
    PRODUCTION_TASK_MODES,
)
from verifier.task_pool import (
    ERDOS_SOURCE_PREFIX,
    REWARD_TARGET_POLICY,
    TASK_POOL_SCHEMA_VERSION,
    reward_target_identity,
)


MAX_ALLOWLIST_BYTES = 4 * 1024 * 1024
TASK_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,254}$")
TIER_ID = re.compile(r"^tier-[1-9][0-9]*$")
SOURCE_PREFIX = "FormalConjectures/"


class TaskNotAllowed(ValueError):
    """The selected task is not an exact audited member of the public task pool."""


def _read_regular(path: Path, maximum: int) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise TaskNotAllowed("task allowlist is unavailable") from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise TaskNotAllowed("task allowlist must be a regular file")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise TaskNotAllowed("cannot open task allowlist safely") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise TaskNotAllowed("task allowlist changed while opening")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            content = handle.read(maximum + 1)
    finally:
        os.close(descriptor)
    if len(content) > maximum:
        raise TaskNotAllowed("task allowlist exceeds its size limit")
    return content


def _json_object(content: bytes) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(
            content.decode("utf-8", errors="strict"),
            object_pairs_hook=unique,
            parse_constant=lambda item: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant: {item}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise TaskNotAllowed("task allowlist is not valid strict JSON") from exc
    if not isinstance(value, dict):
        raise TaskNotAllowed("task allowlist must be a JSON object")
    return value


def _valid_source_prefixes(value: object) -> bool:
    if (
        not isinstance(value, list)
        or not all(isinstance(prefix, str) for prefix in value)
        or value != sorted(value)
        or len(value) != len(set(value))
    ):
        return False
    return all(
        prefix.startswith(SOURCE_PREFIX)
        and prefix.endswith("/")
        and ".." not in Path(prefix).parts
        for prefix in value
    )


def _valid_tier_policy(policy: object) -> bool:
    expected_fields = {
        "classification",
        "compiled_target_validation",
        "excluded_source_prefixes",
        "grouping",
        "minimum_erdos_tasks",
        "modes",
        "multi_target_tasks",
        "one_reward_per_problem",
        "one_reward_per_reward_target",
        "pool_size",
        "reward_target_count",
        "reward_target_policy",
        "retired_source_theorems_sha256",
        "selection",
        "selection_audit_sha256",
        "source_category",
        "source_theorem_count",
        "task_scope",
        "target_relations",
        "task_groups_sha256",
        "outcomes_per_problem",
        "task_targets_sha256",
    }
    return (
        isinstance(policy, dict)
        and set(policy) == expected_fields
        and policy.get("classification") == "DIRECT_PROP"
        and policy.get("compiled_target_validation") is True
        and _valid_source_prefixes(policy.get("excluded_source_prefixes"))
        and isinstance(policy.get("grouping"), str)
        and bool(policy["grouping"])
        and type(policy.get("minimum_erdos_tasks")) is int
        and policy["minimum_erdos_tasks"] >= 0
        and policy.get("modes") == list(PRODUCTION_TASK_MODES)
        and type(policy.get("multi_target_tasks")) is int
        and policy["multi_target_tasks"] >= 0
        and policy.get("one_reward_per_problem") is True
        and policy.get("one_reward_per_reward_target") is True
        and type(policy.get("pool_size")) is int
        and policy["pool_size"] > 0
        and type(policy.get("reward_target_count")) is int
        and policy["reward_target_count"] > 0
        and policy.get("reward_target_policy") == REWARD_TARGET_POLICY
        and is_sha256(policy.get("retired_source_theorems_sha256"))
        and isinstance(policy.get("selection"), str)
        and bool(policy["selection"])
        and is_sha256(policy.get("selection_audit_sha256"))
        and policy.get("source_category") == "research open"
        and type(policy.get("source_theorem_count")) is int
        and policy["source_theorem_count"] > 0
        and policy.get("task_scope")
        in {"whole_problem", "part_or_variant", "direct_proposition"}
        and policy.get("target_relations")
        == {
            COUNTEREXAMPLE_TASK_MODE: "logical-negation",
            EXACT_TASK_MODE: "definitionally-equal",
        }
        and is_sha256(policy.get("task_groups_sha256"))
        and policy.get("outcomes_per_problem") == len(PRODUCTION_TASK_MODES)
        and is_sha256(policy.get("task_targets_sha256"))
    )


@dataclass(frozen=True)
class AllowedTask:
    task_id: str
    problem_id: str
    reward_target_id: str
    tier: str
    mode: str
    source_theorems: tuple[str, ...]
    task_bundle_sha256: str
    target_type_sha256s: tuple[str, ...]


@dataclass(frozen=True)
class TaskPoolRegistry:
    repository_commit: str
    tier_order: tuple[str, ...]
    tasks: dict[str, AllowedTask]

    @classmethod
    def load(cls, path: Path) -> "TaskPoolRegistry":
        value = _json_object(_read_regular(path, MAX_ALLOWLIST_BYTES))
        expected_fields = {
            "allowed_source_theorems",
            "allowed_task_bundles",
            "audit_date_utc",
            "default",
            "repository_commit",
            "schema_version",
            "tier_order",
            "tier_policies",
        }
        if set(value) != expected_fields:
            raise TaskNotAllowed("task allowlist field set is invalid")
        if (
            type(value.get("schema_version")) is not int
            or value["schema_version"] != TASK_POOL_SCHEMA_VERSION
        ):
            raise TaskNotAllowed("task allowlist schema version is invalid")
        if value.get("default") != "DENY":
            raise TaskNotAllowed("task allowlist must be deny-by-default")
        audit_date = value.get("audit_date_utc")
        try:
            if (
                not isinstance(audit_date, str)
                or date.fromisoformat(audit_date).isoformat() != audit_date
            ):
                raise ValueError
        except ValueError as exc:
            raise TaskNotAllowed("task allowlist audit date is invalid") from exc

        repository_commit = value.get("repository_commit")
        sources = value.get("allowed_source_theorems")
        rows = value.get("allowed_task_bundles")
        tier_order = value.get("tier_order")
        tier_policies = value.get("tier_policies")
        if (
            not isinstance(repository_commit, str)
            or len(repository_commit) != 40
            or any(char not in "0123456789abcdef" for char in repository_commit)
            or not isinstance(sources, list)
            or not isinstance(rows, list)
            or not isinstance(tier_order, list)
            or not tier_order
            or not all(
                isinstance(tier, str) and TIER_ID.fullmatch(tier)
                for tier in tier_order
            )
            or len(tier_order) != len(set(tier_order))
            or not isinstance(tier_policies, dict)
            or set(tier_policies) != set(tier_order)
            or not all(_valid_tier_policy(tier_policies[tier]) for tier in tier_order)
        ):
            raise TaskNotAllowed("task allowlist identity or tier policy is invalid")

        source_by_index: dict[int, tuple[str, str, str, str]] = {}
        source_theorems: set[str] = set()
        source_types: set[str] = set()
        for source in sources:
            if not isinstance(source, dict) or set(source) != {
                "index",
                "source_path",
                "source_type_sha256",
                "theorem",
                "tier",
            }:
                raise TaskNotAllowed("task allowlist contains an invalid source entry")
            index = source.get("index")
            theorem = source.get("theorem")
            source_path = source.get("source_path")
            source_type = source.get("source_type_sha256")
            tier = source.get("tier")
            parsed_path = Path(source_path) if isinstance(source_path, str) else Path()
            excluded_prefixes = (
                tier_policies[tier]["excluded_source_prefixes"]
                if isinstance(tier, str) and tier in tier_policies
                else ()
            )
            if (
                type(index) is not int
                or index < 0
                or index in source_by_index
                or not isinstance(theorem, str)
                or not theorem
                or theorem in source_theorems
                or not isinstance(tier, str)
                or tier not in tier_policies
                or not isinstance(source_path, str)
                or not source_path.startswith(SOURCE_PREFIX)
                or source_path.startswith(tuple(excluded_prefixes))
                or parsed_path.is_absolute()
                or ".." in parsed_path.parts
                or parsed_path.suffix != ".lean"
                or not is_sha256(source_type)
                or source_type in source_types
            ):
                raise TaskNotAllowed("task allowlist source identity is invalid or duplicate")
            source_by_index[index] = (theorem, source_path, source_type, tier)
            source_theorems.add(theorem)
            source_types.add(source_type)

        tasks: dict[str, AllowedTask] = {}
        used_source_modes: set[tuple[int, str]] = set()
        bundle_hashes: set[str] = set()
        problem_sources: dict[str, tuple[str, ...]] = {}
        for row in rows:
            if not isinstance(row, dict) or set(row) != {
                "completion_policy",
                "mode",
                "problem_id",
                "reward_target_id",
                "source_indices",
                "source_path",
                "target_type_sha256s",
                "task_bundle_sha256",
                "task_id",
                "theorems",
                "tier",
            }:
                raise TaskNotAllowed("task allowlist contains a non-object entry")
            task_id = row.get("task_id")
            tier = row.get("tier")
            mode = row.get("mode")
            bundle_hash = row.get("task_bundle_sha256")
            target_hashes = row.get("target_type_sha256s")
            source_indices = row.get("source_indices")
            theorems = row.get("theorems")
            sources_for_task = (
                tuple(source_by_index.get(index) for index in source_indices)
                if isinstance(source_indices, list)
                and all(type(index) is int for index in source_indices)
                else ()
            )
            expected_problem_id = (
                problem_id(repository_commit, tuple(theorems))
                if isinstance(theorems, list)
                and all(isinstance(theorem, str) for theorem in theorems)
                and theorems
                else None
            )
            reward_target_id = row.get("reward_target_id")
            expected_reward_target_id = (
                reward_target_identity(theorems[0])
                if isinstance(theorems, list)
                and len(theorems) == 1
                and isinstance(theorems[0], str)
                else None
            )
            if (
                not isinstance(task_id, str)
                or TASK_ID.fullmatch(task_id) is None
                or not isinstance(tier, str)
                or tier not in tier_policies
                or not is_sha256(bundle_hash)
                or bundle_hash in bundle_hashes
                or task_id in tasks
                or row.get("completion_policy") != "all_of"
                or mode not in tier_policies[tier]["modes"]
                or not sources_for_task
                or any(source is None for source in sources_for_task)
                or any(source[3] != tier for source in sources_for_task)
                or len(set(source_indices)) != len(source_indices)
                or any((index, mode) in used_source_modes for index in source_indices)
                or not isinstance(theorems, list)
                or tuple(theorems) != tuple(source[0] for source in sources_for_task)
                or row.get("problem_id") != expected_problem_id
                or reward_target_id != expected_reward_target_id
                or (
                    expected_problem_id in problem_sources
                    and problem_sources[expected_problem_id] != tuple(theorems)
                )
                or not isinstance(target_hashes, list)
                or len(target_hashes) != len(sources_for_task)
                or not all(is_sha256(item) for item in target_hashes)
                or (
                    mode == EXACT_TASK_MODE
                    and tuple(target_hashes)
                    != tuple(source[2] for source in sources_for_task)
                )
                or (
                    mode == COUNTEREXAMPLE_TASK_MODE
                    and (
                        len(sources_for_task) != 1
                        or target_hashes[0] == sources_for_task[0][2]
                    )
                )
                or row.get("source_path") != sources_for_task[0][1]
                or any(source[1] != row.get("source_path") for source in sources_for_task)
            ):
                raise TaskNotAllowed("task allowlist contains an invalid or duplicate entry")
            tasks[task_id] = AllowedTask(
                task_id=task_id,
                problem_id=row["problem_id"],
                reward_target_id=reward_target_id,
                tier=tier,
                mode=mode,
                source_theorems=tuple(theorems),
                task_bundle_sha256=bundle_hash,
                target_type_sha256s=tuple(target_hashes),
            )
            problem_sources.setdefault(row["problem_id"], tuple(theorems))
            used_source_modes.update((index, mode) for index in source_indices)
            bundle_hashes.add(bundle_hash)

        for tier in tier_order:
            policy = tier_policies[tier]
            tier_sources = {
                index: source
                for index, source in source_by_index.items()
                if source[3] == tier
            }
            tier_tasks = [task for task in tasks.values() if task.tier == tier]
            if (
                len(tier_tasks) != policy["pool_size"]
                or len(tier_sources) != policy["source_theorem_count"]
                or len(tier_tasks)
                != len(tier_sources) * policy["outcomes_per_problem"]
                or (
                    policy["task_scope"] == "whole_problem"
                    and len({source[1] for source in tier_sources.values()})
                    != len(tier_sources)
                )
                or sum(
                    source[1].startswith(ERDOS_SOURCE_PREFIX)
                    for source in tier_sources.values()
                )
                < policy["minimum_erdos_tasks"]
                or sum(len(task.target_type_sha256s) > 1 for task in tier_tasks)
                != policy["multi_target_tasks"]
                or len({task.reward_target_id for task in tier_tasks})
                != policy["reward_target_count"]
            ):
                raise TaskNotAllowed("task tier is empty or violates its declared policy")
        expected_source_modes = {
            (index, mode)
            for index, source in source_by_index.items()
            for mode in tier_policies[source[3]]["modes"]
        }
        if not tasks or used_source_modes != expected_source_modes:
            raise TaskNotAllowed("task allowlist is empty or lacks a complete outcome pair")
        problem_modes: dict[str, set[str]] = {}
        for task in tasks.values():
            problem_modes.setdefault(task.problem_id, set()).add(task.mode)
        if any(modes != set(PRODUCTION_TASK_MODES) for modes in problem_modes.values()):
            raise TaskNotAllowed("task allowlist problem lacks a complete outcome pair")
        return cls(
            repository_commit=repository_commit,
            tier_order=tuple(tier_order),
            tasks=tasks,
        )

    def tasks_for_tier(self, tier: str) -> tuple[AllowedTask, ...]:
        if tier not in self.tier_order:
            raise TaskNotAllowed(f"unknown task tier: {tier}")
        return tuple(task for task in self.tasks.values() if task.tier == tier)

    def tasks_for_problem(self, problem_id: str) -> tuple[AllowedTask, ...]:
        result = tuple(
            task for task in self.tasks.values() if task.problem_id == problem_id
        )
        if not result:
            raise TaskNotAllowed(f"unknown problem: {problem_id}")
        return result

    def tasks_for_reward_target(self, reward_target_id: str) -> tuple[AllowedTask, ...]:
        result = tuple(
            task for task in self.tasks.values() if task.reward_target_id == reward_target_id
        )
        if not result:
            raise TaskNotAllowed(f"unknown reward target: {reward_target_id}")
        return result

    def assert_bundle(self, bundle: TaskBundle) -> AllowedTask:
        allowed = self.tasks.get(bundle.manifest.task_id)
        if allowed is None:
            raise TaskNotAllowed("task is not on the audited task allowlist")
        if bundle.manifest.repository_commit != self.repository_commit:
            raise TaskNotAllowed("task repository commit does not match the allowlist")
        if bundle.sha256 != allowed.task_bundle_sha256:
            raise TaskNotAllowed("task bundle digest does not match the allowlist")
        if bundle.manifest.task_mode != allowed.mode:
            raise TaskNotAllowed("task mode does not match the allowlist")
        if tuple(source.theorem for source in bundle.sources) != allowed.source_theorems:
            raise TaskNotAllowed("task sources do not match the allowlist")
        actual_target_hashes = (
            tuple(source.type_hash for source in bundle.sources)
            if allowed.mode == EXACT_TASK_MODE
            else (bundle.manifest.generated_target_type_hash,)
        )
        if actual_target_hashes != allowed.target_type_sha256s:
            raise TaskNotAllowed("task target digests do not match the allowlist")
        return allowed
