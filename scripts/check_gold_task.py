#!/usr/bin/env python3
"""Fail closed unless a task matches the published gold-task allowlist."""

from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
ALLOWLIST = ROOT / "gold" / "allowlist.json"


def fail(message: str) -> None:
    print(json.dumps({"allowed": False, "reason": message}, sort_keys=True))
    raise SystemExit(1)


def main() -> None:
    if len(sys.argv) != 2:
        fail("usage: scripts/check_gold_task.py TASK_DIR")
    task_dir = Path(sys.argv[1]).resolve()
    if not task_dir.is_dir():
        fail(f"not a task directory: {task_dir}")

    sys.path.insert(0, str(ROOT))
    from verifier.gold_pool import GOLD_POOL_SCHEMA_VERSION
    from verifier.task_loader import load_task_bundle
    from verifier.task_policy import GOLD_TASK_MODE

    try:
        policy = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
        bundle = load_task_bundle(task_dir)
    except Exception as error:
        fail(f"task bundle failed immutable-bundle validation: {error}")
    if (
        policy.get("schema_version") != GOLD_POOL_SCHEMA_VERSION
        or policy.get("default") != "DENY"
        or policy.get("pool_policy", {}).get("mode") != GOLD_TASK_MODE
        or policy.get("pool_policy", {}).get("exact_source_type") is not True
        or policy.get("pool_policy", {}).get("synthetic_negation") is not False
    ):
        fail("gold allowlist policy is invalid")
    allowed = {row["task_id"]: row for row in policy["allowed_task_bundles"]}
    row = allowed.get(bundle.manifest.task_id)
    if row is None:
        fail("task ID is not on the gold allowlist")
    if bundle.manifest.repository_commit != policy["repository_commit"]:
        fail("repository commit does not match the audited commit")
    if bundle.sha256 != row["task_bundle_sha256"]:
        fail("task-bundle digest does not match the audited digest")
    if bundle.manifest.generated_target_type_hash != row["target_type_sha256"]:
        fail("generated target-type digest does not match the audited digest")
    if (
        bundle.manifest.task_mode != GOLD_TASK_MODE
        or bundle.manifest.generated_target_type_hash != bundle.manifest.source_type_hash
    ):
        fail("task is not the exact source formalization")
    print(
        json.dumps(
            {
                "allowed": True,
                "source_index": row["source_index"],
                "task_id": row["task_id"],
                "task_bundle_sha256": row["task_bundle_sha256"],
                "theorem": row["theorem"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
