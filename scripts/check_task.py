#!/usr/bin/env python3
"""Fail closed unless a task matches the published tiered task allowlist."""

from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
ALLOWLIST = ROOT / "tasks" / "allowlist.json"


def fail(message: str) -> None:
    print(json.dumps({"allowed": False, "reason": message}, sort_keys=True))
    raise SystemExit(1)


def main() -> None:
    if len(sys.argv) != 2:
        fail("usage: scripts/check_task.py TASK_DIR")
    task_dir = Path(sys.argv[1]).resolve()
    if not task_dir.is_dir():
        fail(f"not a task directory: {task_dir}")

    sys.path.insert(0, str(ROOT))
    from verifier.task_pool import TASK_POOL_SCHEMA_VERSION
    from verifier.task_registry import TaskPoolRegistry
    from verifier.task_loader import load_task_bundle
    from verifier.task_policy import COUNTEREXAMPLE_TASK_MODE, EXACT_TASK_MODE

    try:
        policy = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
        bundle = load_task_bundle(task_dir)
    except Exception as error:
        fail(f"task bundle failed immutable-bundle validation: {error}")
    if (
        policy.get("schema_version") != TASK_POOL_SCHEMA_VERSION
        or policy.get("default") != "DENY"
    ):
        fail("task allowlist policy is invalid")
    try:
        registry = TaskPoolRegistry.load(ALLOWLIST)
    except Exception as error:
        fail(f"task allowlist failed validation: {error}")
    allowed = {row["task_id"]: row for row in policy["allowed_task_bundles"]}
    row = allowed.get(bundle.manifest.task_id)
    if row is None:
        fail("task ID is not on the task allowlist")
    try:
        admitted = registry.assert_bundle(bundle)
    except Exception as error:
        fail(str(error))
    if bundle.manifest.repository_commit != policy["repository_commit"]:
        fail("repository commit does not match the audited commit")
    if bundle.sha256 != row["task_bundle_sha256"]:
        fail("task-bundle digest does not match the audited digest")
    if bundle.manifest.task_mode == EXACT_TASK_MODE:
        relation_valid = all(
            source.type_hash == target_hash
            for source, target_hash in zip(
                bundle.sources,
                row["target_type_sha256s"],
                strict=True,
            )
        )
    elif bundle.manifest.task_mode == COUNTEREXAMPLE_TASK_MODE:
        relation_valid = (
            len(bundle.sources) == 1
            and row["target_type_sha256s"]
            == [bundle.manifest.generated_target_type_hash]
            and bundle.manifest.generated_target_type_hash
            != bundle.manifest.source_type_hash
        )
    else:
        relation_valid = False
    if not relation_valid:
        fail("task target relation is inconsistent with its mode")
    print(
        json.dumps(
            {
                "allowed": True,
                "mode": admitted.mode,
                "problem_id": admitted.problem_id,
                "reward_family_id": admitted.reward_family_id,
                "source_indices": row["source_indices"],
                "task_id": row["task_id"],
                "task_bundle_sha256": row["task_bundle_sha256"],
                "theorems": row["theorems"],
                "tier": row["tier"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
