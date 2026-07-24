from __future__ import annotations

import json
import os
import re
import stat
from datetime import date
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from verifier.gold_pool import (
    ERDOS_SOURCE_PREFIX,
    EXCLUDED_SOURCE_PREFIXES,
    GOLD_POOL_SCHEMA_VERSION,
    GOLD_POOL_SELECTION,
    MINIMUM_ERDOS_TASKS,
)
from verifier.hashing import is_sha256
from verifier.task_loader import TaskBundle
from verifier.task_policy import GOLD_TASK_MODE


MAX_ALLOWLIST_BYTES = 4 * 1024 * 1024
TASK_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,254}$")


class TaskNotAllowed(ValueError):
    """The selected task is not the exact audited public task."""


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


@dataclass(frozen=True)
class AllowedTask:
    task_id: str
    task_bundle_sha256: str
    target_type_sha256: str


@dataclass(frozen=True)
class GoldTaskRegistry:
    repository_commit: str
    tasks: dict[str, AllowedTask]

    @classmethod
    def load(cls, path: Path) -> "GoldTaskRegistry":
        value = _json_object(_read_regular(path, MAX_ALLOWLIST_BYTES))
        expected_fields = {
            "allowed_source_theorems",
            "allowed_task_bundles",
            "audit_date_utc",
            "default",
            "pool_policy",
            "repository_commit",
            "schema_version",
        }
        if set(value) != expected_fields:
            raise TaskNotAllowed("task allowlist field set is invalid")
        if (
            type(value.get("schema_version")) is not int
            or value["schema_version"] != GOLD_POOL_SCHEMA_VERSION
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
        policy = value.get("pool_policy")
        if (
            not isinstance(repository_commit, str)
            or len(repository_commit) != 40
            or any(char not in "0123456789abcdef" for char in repository_commit)
            or not isinstance(sources, list)
            or not isinstance(rows, list)
            or not isinstance(policy, dict)
        ):
            raise TaskNotAllowed("task allowlist identity is invalid")
        expected_policy_fields = {
            "classification",
            "compiled_target_validation",
            "exact_source_type",
            "excluded_source_prefixes",
            "minimum_erdos_tasks",
            "mode",
            "one_task_per_source_path",
            "pool_size",
            "retired_source_theorems_sha256",
            "selection",
            "selection_audit_sha256",
            "source_category",
            "synthetic_negation",
        }
        if (
            set(policy) != expected_policy_fields
            or policy.get("classification") != "DIRECT_PROP"
            or policy.get("compiled_target_validation") is not True
            or policy.get("exact_source_type") is not True
            or policy.get("excluded_source_prefixes") != list(EXCLUDED_SOURCE_PREFIXES)
            or policy.get("minimum_erdos_tasks") != MINIMUM_ERDOS_TASKS
            or policy.get("mode") != GOLD_TASK_MODE
            or policy.get("one_task_per_source_path") is not False
            or type(policy.get("pool_size")) is not int
            or policy["pool_size"] <= 0
            or not is_sha256(policy.get("retired_source_theorems_sha256"))
            or policy.get("selection") != GOLD_POOL_SELECTION
            or not is_sha256(policy.get("selection_audit_sha256"))
            or policy.get("source_category") != "research open"
            or policy.get("synthetic_negation") is not False
        ):
            raise TaskNotAllowed("task allowlist pool policy is invalid")

        source_by_index: dict[int, tuple[str, str, str]] = {}
        source_theorems: set[str] = set()
        source_types: set[str] = set()
        for source in sources:
            if not isinstance(source, dict) or set(source) != {
                "index",
                "source_path",
                "source_type_sha256",
                "theorem",
            }:
                raise TaskNotAllowed("task allowlist contains an invalid source entry")
            index = source.get("index")
            theorem = source.get("theorem")
            source_path = source.get("source_path")
            source_type = source.get("source_type_sha256")
            parsed_path = Path(source_path) if isinstance(source_path, str) else Path()
            if (
                type(index) is not int
                or index < 0
                or index in source_by_index
                or not isinstance(theorem, str)
                or not theorem
                or theorem in source_theorems
                or not isinstance(source_path, str)
                or not source_path.startswith(ERDOS_SOURCE_PREFIX)
                or parsed_path.is_absolute()
                or ".." in parsed_path.parts
                or parsed_path.suffix != ".lean"
                or not is_sha256(source_type)
                or source_type in source_types
            ):
                raise TaskNotAllowed("task allowlist source identity is invalid or duplicate")
            source_by_index[index] = (theorem, source_path, source_type)
            source_theorems.add(theorem)
            source_types.add(source_type)

        tasks: dict[str, AllowedTask] = {}
        used_source_indices: set[int] = set()
        bundle_hashes: set[str] = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != {
                "mode",
                "source_index",
                "source_path",
                "target_type_sha256",
                "task_bundle_sha256",
                "task_id",
                "theorem",
            }:
                raise TaskNotAllowed("task allowlist contains a non-object entry")
            task_id = row.get("task_id")
            bundle_hash = row.get("task_bundle_sha256")
            target_hash = row.get("target_type_sha256")
            source_index = row.get("source_index")
            source = (
                source_by_index.get(source_index)
                if type(source_index) is int
                else None
            )
            if (
                not isinstance(task_id, str)
                or TASK_ID.fullmatch(task_id) is None
                or not is_sha256(bundle_hash)
                or bundle_hash in bundle_hashes
                or not is_sha256(target_hash)
                or task_id in tasks
                or row.get("mode") != GOLD_TASK_MODE
                or source is None
                or source_index in used_source_indices
                or row.get("theorem") != source[0]
                or row.get("source_path") != source[1]
                or target_hash != source[2]
            ):
                raise TaskNotAllowed("task allowlist contains an invalid or duplicate entry")
            tasks[task_id] = AllowedTask(
                task_id=task_id,
                task_bundle_sha256=bundle_hash,
                target_type_sha256=target_hash,
            )
            used_source_indices.add(source_index)
            bundle_hashes.add(bundle_hash)
        if (
            not tasks
            or len(tasks) != policy["pool_size"]
            or len(tasks) != len(source_by_index)
            or used_source_indices != set(source_by_index)
        ):
            raise TaskNotAllowed("task allowlist is empty or not one-to-one")
        return cls(repository_commit=repository_commit, tasks=tasks)

    def assert_bundle(self, bundle: TaskBundle) -> AllowedTask:
        allowed = self.tasks.get(bundle.manifest.task_id)
        if allowed is None:
            raise TaskNotAllowed("task is not on the audited gold allowlist")
        if bundle.manifest.repository_commit != self.repository_commit:
            raise TaskNotAllowed("task repository commit does not match the allowlist")
        if bundle.sha256 != allowed.task_bundle_sha256:
            raise TaskNotAllowed("task bundle digest does not match the allowlist")
        if bundle.manifest.generated_target_type_hash != allowed.target_type_sha256:
            raise TaskNotAllowed("task target digest does not match the allowlist")
        return allowed
