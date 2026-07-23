from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from verifier.hashing import is_sha256
from verifier.task_loader import TaskBundle


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
        if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
            raise TaskNotAllowed("task allowlist schema version is invalid")
        if value.get("default") != "DENY":
            raise TaskNotAllowed("task allowlist must be deny-by-default")
        repository_commit = value.get("repository_commit")
        rows = value.get("allowed_task_bundles")
        if (
            not isinstance(repository_commit, str)
            or len(repository_commit) != 40
            or any(char not in "0123456789abcdef" for char in repository_commit)
            or not isinstance(rows, list)
        ):
            raise TaskNotAllowed("task allowlist identity is invalid")
        tasks: dict[str, AllowedTask] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise TaskNotAllowed("task allowlist contains a non-object entry")
            task_id = row.get("task_id")
            bundle_hash = row.get("task_bundle_sha256")
            target_hash = row.get("target_type_sha256")
            if (
                not isinstance(task_id, str)
                or TASK_ID.fullmatch(task_id) is None
                or not is_sha256(bundle_hash)
                or not is_sha256(target_hash)
                or task_id in tasks
            ):
                raise TaskNotAllowed("task allowlist contains an invalid or duplicate entry")
            tasks[task_id] = AllowedTask(
                task_id=task_id,
                task_bundle_sha256=bundle_hash,
                target_type_sha256=target_hash,
            )
        if not tasks:
            raise TaskNotAllowed("task allowlist is empty")
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
