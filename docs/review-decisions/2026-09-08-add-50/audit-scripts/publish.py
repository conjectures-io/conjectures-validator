#!/usr/bin/env python3
"""Incrementally publish the reviewed 2026-09-08 fifty-target task release."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from collections import defaultdict
from pathlib import Path


TASKS_ROOT = Path("/tmp/add-50-20260908/tasks")
VALIDATOR_ROOT = Path("/tmp/conjectures-validator-a8d559d-erdos564")
CATALOG_PATH = Path("/root/conjectures-validator/data/catalog.json")
METADATA_ROOT = TASKS_ROOT / "tiers/tier-1"
POOL_ROOT = TASKS_ROOT / "pool/tier-1"
ALLOWLIST_PATH = TASKS_ROOT / "allowlist.json"
OPEN_PRS_PATH = Path("/tmp/add-50-20260908/open-prs.json")
TRACKER_ROOT = Path("/tmp/add-50-20260908/erdosproblems")
FORMAL_CONJECTURES_MAIN_ROOT = Path(
    "/tmp/add-50-20260908/formal-conjectures"
)
STAGING_ROOTS = (Path("/tmp/add-50-20260908/bundles"),)

BASE_TASKS_COMMIT = "8dd81458db1cdcd2ec4fd3e6f866aa1907006858"
VALIDATOR_COMMIT = "a8d559db1d4d6ccdd2f7cc07e7d7dd5d45a8afb2"
FORMAL_CONJECTURES_COMMIT = "379fc0298dc146df549e7061c3ede0353a5bb51f"
FORMAL_CONJECTURES_MAIN_COMMIT = "2c817e975be7a95478b72a8429155ca568e1a3de"
TRACKER_COMMIT = "5308c57c700559416b9f205df274b136784203e7"
AUDIT_DATE = "2026-09-08"
EXPECTED_OPEN_PR_COUNT = 359
EXPECTED_TARGETS = 259
EXPECTED_BUNDLES = 518
EXPECTED_ERDOS_TARGETS = 234
EXPECTED_GREEN_TARGETS = 25
EXPECTED_SOURCE_PATHS = 222

FINAL_THEOREMS = tuple(row["theorem"] for row in json.loads(Path("/tmp/add-50-20260908/selection.json").read_text()))

sys.path.insert(0, str(VALIDATOR_ROOT))

from verifier.catalog import load_catalog  # noqa: E402
from verifier.task_loader import TASK_FILE_NAMES, load_task_bundle  # noqa: E402
from verifier.task_policy import PRODUCTION_TASK_MODES  # noqa: E402
from verifier.task_registry import TaskPoolRegistry  # noqa: E402
import verifier.task_pool as task_pool  # noqa: E402


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_json(value) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_output(root: Path, *arguments: str) -> str:
    import subprocess

    return subprocess.check_output(
        ("git", "-C", str(root), *arguments), text=True
    ).strip()


def parse_tracker_statuses(path: Path) -> dict[int, str]:
    text = path.read_text(encoding="utf-8")
    blocks = re.split(r"(?m)(?=^- number:)", text)
    result: dict[int, str] = {}
    for block in blocks:
        number = re.match(r'- number: "([0-9]+)"', block)
        status = re.search(
            r'(?ms)^  status:\n    state: "([^"]+)"', block
        )
        if number is not None and status is not None:
            result[int(number.group(1))] = status.group(1)
    if len(result) < 1000:
        raise RuntimeError(f"tracker parse returned only {len(result)} rows")
    return result


def atomic_write(path: Path, content: bytes) -> None:
    temporary = path.with_name(path.name + ".add-50.tmp")
    if temporary.exists():
        raise RuntimeError(f"refusing stale temporary output: {temporary}")
    temporary.write_bytes(content)
    temporary.chmod(0o644)
    os.replace(temporary, path)


def preflight_repository() -> None:
    if git_output(TASKS_ROOT, "rev-parse", "HEAD") != BASE_TASKS_COMMIT:
        raise RuntimeError("task repository is not at the reviewed baseline")
    if git_output(VALIDATOR_ROOT, "rev-parse", "HEAD") != VALIDATOR_COMMIT:
        raise RuntimeError("compatible validator checkout drifted")
    if (
        git_output(FORMAL_CONJECTURES_MAIN_ROOT, "rev-parse", "HEAD")
        != FORMAL_CONJECTURES_MAIN_COMMIT
    ):
        raise RuntimeError("reviewed Formal Conjectures main checkout drifted")
    if git_output(TRACKER_ROOT, "rev-parse", "HEAD") != TRACKER_COMMIT:
        raise RuntimeError("reviewed Erdős tracker checkout drifted")
    status = git_output(TASKS_ROOT, "status", "--porcelain")
    if status:
        raise RuntimeError(f"task repository must start clean:\n{status}")
    if len(FINAL_THEOREMS) != 50 or len(set(FINAL_THEOREMS)) != 50:
        raise RuntimeError("final theorem slate must contain 50 unique entries")


def staged_bundles() -> dict[tuple[str, str], Path]:
    wanted = set(FINAL_THEOREMS)
    result: dict[tuple[str, str], Path] = {}
    for root in STAGING_ROOTS:
        for directory in sorted(root.iterdir()):
            manifest_path = directory / "manifest.json"
            if not directory.is_dir() or not manifest_path.is_file():
                continue
            manifest = read_json(manifest_path)
            theorem = manifest.get("source_theorem")
            mode = manifest.get("task_mode")
            if theorem not in wanted or mode not in PRODUCTION_TASK_MODES:
                continue
            identity = (theorem, mode)
            if identity in result:
                raise RuntimeError(f"duplicate staged identity: {identity}")
            actual_files = {path.name for path in directory.iterdir()}
            if actual_files != set(TASK_FILE_NAMES):
                raise RuntimeError(
                    f"staged bundle has wrong file set: {directory}: {actual_files}"
                )
            for path in directory.iterdir():
                metadata = path.lstat()
                if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
                    raise RuntimeError(f"unsafe staged bundle entry: {path}")
            result[identity] = directory
    expected = {
        (theorem, mode)
        for theorem in FINAL_THEOREMS
        for mode in PRODUCTION_TASK_MODES
    }
    if set(result) != expected:
        missing = sorted(expected - set(result))
        extra = sorted(set(result) - expected)
        raise RuntimeError(f"staged set mismatch: missing={missing}, extra={extra}")
    if len({path.name for path in result.values()}) != 100:
        raise RuntimeError("staged bundle directory names are not unique")
    return result


def build_metadata(catalog, old_targets, old_audit, open_prs, tracker_statuses):
    declarations = {item.theorem: item for item in catalog.declarations}
    existing_theorems = {item["theorem"] for item in old_targets["targets"]}
    if existing_theorems & set(FINAL_THEOREMS):
        raise RuntimeError("a final target is already active")

    prs_by_path: dict[str, set[int]] = defaultdict(set)
    for pull_request in open_prs:
        number = pull_request["number"]
        for file_row in pull_request["files"]:
            prs_by_path[file_row["path"]].add(number)

    new_target_rows = []
    new_audit_rows = []
    for theorem in FINAL_THEOREMS:
        declaration = declarations.get(theorem)
        if declaration is None:
            raise RuntimeError(f"final theorem is absent from catalog: {theorem}")
        match = re.fullmatch(r"(Erdos|Green)([0-9]+)\..+", theorem)
        if match is None:
            raise RuntimeError(f"noncanonical Erdős theorem name: {theorem}")
        number = int(match.group(2))
        family = "erdos" if match.group(1) == "Erdos" else "greens-open-problems"
        folder = "ErdosProblems" if family == "erdos" else "GreensOpenProblems"
        expected_path = f"FormalConjectures/{folder}/{number}.lean"
        if declaration.source_path != expected_path:
            raise RuntimeError(f"source identity mismatch for {theorem}")
        source_status = tracker_statuses.get(number) if family == "erdos" else "open"
        if source_status not in {"open", "falsifiable", "verifiable", "decidable"}:
            raise RuntimeError(
                f"ineligible live tracker status for {theorem}: {source_status}"
            )
        new_target_rows.append(
            {
                "reward_target_id": f"fc-target:{theorem}",
                "source_family": family,
                "source_path": expected_path,
                "source_problem_number": number,
                "theorem": theorem,
            }
        )
        new_audit_rows.append(
            {
                "active_resolution_prs": [],
                "feasibility_signals": [
                    "compact-formal-target",
                    "standard-mathlib-surface",
                ],
                "open_prs_touching_source": sorted(prs_by_path[expected_path]),
                "source_family": family,
                "source_path": expected_path,
                "source_problem_number": number,
                "source_status": source_status,
                "theorem": theorem,
                "upstream_status": "research open",
            }
        )

    target_document = dict(old_targets)
    target_document["targets"] = sorted(
        old_targets["targets"] + new_target_rows,
        key=lambda item: item["theorem"],
    )
    audit_document = dict(old_audit)
    # Preserve the global 2026-08-12 provenance header and all retained rows. The
    # later candidate-only freshness review is committed in the validator report;
    # advancing this global header would incorrectly claim every retained target
    # received the same 2026-09-08 PR-resolution review.
    audit_document["selected"] = sorted(
        old_audit["selected"] + new_audit_rows,
        key=lambda item: item["theorem"],
    )
    if len(target_document["targets"]) != EXPECTED_TARGETS:
        raise RuntimeError("target metadata count is not 259")
    if len(audit_document["selected"]) != EXPECTED_TARGETS:
        raise RuntimeError("selection audit count is not 259")
    return target_document, audit_document


def main() -> int:
    gates = read_json(Path("/tmp/add-50-20260908/admission-gates.json"))
    assert gates["status"] == "passed" and gates["added_targets"] == 50
    preflight_repository()
    old_targets_document = read_json(METADATA_ROOT / "task-targets.json")
    old_audit_document = read_json(METADATA_ROOT / "selection-audit.json")
    old_allowlist = read_json(ALLOWLIST_PATH)
    open_prs = read_json(OPEN_PRS_PATH)
    if len(open_prs) != EXPECTED_OPEN_PR_COUNT:
        raise RuntimeError("open-PR review snapshot count drifted")
    tracker_statuses = parse_tracker_statuses(
        TRACKER_ROOT / "data/problems.yaml"
    )
    catalog = load_catalog(CATALOG_PATH)
    if catalog.repository_commit != FORMAL_CONJECTURES_COMMIT:
        raise RuntimeError("catalog Formal Conjectures commit drifted")
    staged = staged_bundles()

    old_directories = tuple(
        sorted(path for path in POOL_ROOT.iterdir() if path.is_dir())
    )
    if len(old_directories) != 418:
        raise RuntimeError("baseline does not have 418 task directories")
    old_pool_files = {
        path.relative_to(POOL_ROOT): sha256_file(path)
        for directory in old_directories
        for path in directory.iterdir()
        if path.is_file()
    }
    if len(old_pool_files) != 2926:
        raise RuntimeError("baseline does not have 2,926 task files")

    destinations: dict[tuple[str, str], Path] = {}
    for identity, source in staged.items():
        destination = POOL_ROOT / source.name
        if destination.exists():
            raise RuntimeError(f"new destination already exists: {destination}")
        destinations[identity] = destination

    target_document, audit_document = build_metadata(
        catalog,
        old_targets_document,
        old_audit_document,
        open_prs,
        tracker_statuses,
    )

    with tempfile.TemporaryDirectory(prefix="add-50-metadata-") as temporary_name:
        temporary = Path(temporary_name)
        targets_path = temporary / "task-targets.json"
        audit_path = temporary / "selection-audit.json"
        targets_content = canonical_json(target_document)
        audit_content = canonical_json(audit_document)
        targets_path.write_bytes(targets_content)
        audit_path.write_bytes(audit_content)

        retired = task_pool.load_retired_sources(
            METADATA_ROOT / "retired-source-theorems.json"
        )
        retired_conjectures = task_pool.load_retired_conjectures(
            METADATA_ROOT / "retired-conjectures.json"
        )
        selection_audit = task_pool.load_selection_audit(audit_path)
        task_targets = task_pool.load_task_targets(targets_path)
        grouping = task_pool.load_task_grouping(
            METADATA_ROOT / "task-groups.json"
        )

        task_pool.DEFAULT_TIER_SIZE = EXPECTED_TARGETS
        task_pool.DEFAULT_TIER_TASK_COUNT = EXPECTED_BUNDLES
        task_pool.MINIMUM_ERDOS_TASKS = EXPECTED_ERDOS_TARGETS
        selected_declarations = task_pool.select_task_declarations(
            catalog=catalog,
            retired=retired,
            selection_audit=selection_audit,
            task_targets=task_targets,
            pool_size=EXPECTED_TARGETS,
        )
        selected = task_pool.group_task_declarations(
            selected_declarations, grouping
        )

        existing_bundles = [
            load_task_bundle(directory) for directory in old_directories
        ]
        new_bundles = [
            load_task_bundle(staged[identity])
            for identity in sorted(staged)
        ]
        bundles = existing_bundles + new_bundles
        if len({bundle.sha256 for bundle in bundles}) != EXPECTED_BUNDLES:
            raise RuntimeError("bundle digests are not globally unique")
        if len({bundle.manifest.task_id for bundle in bundles}) != EXPECTED_BUNDLES:
            raise RuntimeError("task IDs are not globally unique")

        allowlist_content = task_pool.build_task_allowlist(
            catalog=catalog,
            retired=retired,
            retired_conjectures=retired_conjectures,
            selection_audit=selection_audit,
            task_targets=task_targets,
            grouping=grouping,
            selected=selected,
            bundles=bundles,
            audit_date_utc=AUDIT_DATE,
            tier="tier-1",
        )
        new_allowlist = json.loads(allowlist_content)

    old_sources = {
        row["theorem"]: row for row in old_allowlist["allowed_source_theorems"]
    }
    new_sources = {
        row["theorem"]: row for row in new_allowlist["allowed_source_theorems"]
    }
    old_tasks = {
        row["task_id"]: row for row in old_allowlist["allowed_task_bundles"]
    }
    new_tasks = {
        row["task_id"]: row for row in new_allowlist["allowed_task_bundles"]
    }
    if any(new_sources.get(key) != row for key, row in old_sources.items()):
        raise RuntimeError("canonical rebuild changed an existing source row")
    if any(new_tasks.get(key) != row for key, row in old_tasks.items()):
        raise RuntimeError("canonical rebuild changed an existing task row")
    if set(new_sources) - set(old_sources) != set(FINAL_THEOREMS):
        raise RuntimeError("allowlist source delta is not the reviewed slate")
    if len(new_sources) != EXPECTED_TARGETS or len(new_tasks) != EXPECTED_BUNDLES:
        raise RuntimeError("allowlist counts are wrong")

    policy = new_allowlist["tier_policies"]["tier-1"]
    expected_policy_counts = {
        "minimum_erdos_tasks": EXPECTED_ERDOS_TARGETS,
        "pool_size": EXPECTED_BUNDLES,
        "reward_target_count": EXPECTED_TARGETS,
        "source_theorem_count": EXPECTED_TARGETS,
    }
    for key, expected in expected_policy_counts.items():
        if policy[key] != expected:
            raise RuntimeError(f"allowlist policy {key} is not {expected}")
    for unchanged_hash in (
        "retired_conjectures_sha256",
        "retired_source_theorems_sha256",
        "task_groups_sha256",
    ):
        if (
            policy[unchanged_hash]
            != old_allowlist["tier_policies"]["tier-1"][unchanged_hash]
        ):
            raise RuntimeError(f"unchanged policy hash drifted: {unchanged_hash}")

    for identity in sorted(staged):
        source = staged[identity]
        destination = destinations[identity]
        shutil.copytree(source, destination, copy_function=shutil.copy2)
        destination.chmod(0o755)
        for path in destination.iterdir():
            path.chmod(0o644)
    (TASKS_ROOT / "pool").chmod(0o755)
    POOL_ROOT.chmod(0o755)
    atomic_write(METADATA_ROOT / "task-targets.json", targets_content)
    atomic_write(METADATA_ROOT / "selection-audit.json", audit_content)
    atomic_write(ALLOWLIST_PATH, allowlist_content)

    if any(
        not path.is_file() or sha256_file(path) != digest
        for relative, digest in old_pool_files.items()
        for path in (POOL_ROOT / relative,)
    ):
        raise RuntimeError("an existing pool file changed during publication")
    all_directories = tuple(
        sorted(path for path in POOL_ROOT.iterdir() if path.is_dir())
    )
    all_files = [
        path
        for directory in all_directories
        for path in directory.iterdir()
        if path.is_file()
    ]
    if len(all_directories) != EXPECTED_BUNDLES or len(all_files) != 3626:
        raise RuntimeError("published pool shape is wrong")
    if any(path.is_symlink() for directory in all_directories for path in directory.iterdir()):
        raise RuntimeError("published pool contains a symlink")

    registry = TaskPoolRegistry.load(ALLOWLIST_PATH)
    for directory in all_directories:
        registry.assert_bundle(load_task_bundle(directory))
    source_paths = {row["source_path"] for row in new_sources.values()}
    families = defaultdict(int)
    for row in target_document["targets"]:
        families[row["source_family"]] += 1
    if (
        families != {"erdos": EXPECTED_ERDOS_TARGETS, "greens-open-problems": EXPECTED_GREEN_TARGETS}
        or len(source_paths) != EXPECTED_SOURCE_PATHS
    ):
        raise RuntimeError(
            f"published family/path counts are wrong: {dict(families)}, {len(source_paths)}"
        )

    print(
        json.dumps(
            {
                "added_bundle_directories": 100,
                "added_pool_files": 700,
                "added_targets": 50,
                "audit_date": AUDIT_DATE,
                "bundle_count": EXPECTED_BUNDLES,
                "existing_pool_files_unchanged": len(old_pool_files),
                "source_path_count": len(source_paths),
                "target_count": EXPECTED_TARGETS,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
