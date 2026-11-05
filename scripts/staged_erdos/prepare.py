#!/usr/bin/env python3
"""Prepare an inactive Erdős expansion from compiled upstream declarations.

This command only writes beneath a new staged directory. It never updates the
allowlist, task version registry, selection audit, or production source pins.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from collections import Counter
from pathlib import Path


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def read(path: Path):
    return json.loads(path.read_text())


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def problem_number(path: str) -> int | None:
    parts = Path(path).parts
    if len(parts) == 3 and parts[:2] == ("FormalConjectures", "ErdosProblems"):
        stem = Path(parts[2]).stem
        if stem.isdigit() and Path(parts[2]).suffix == ".lean":
            return int(stem)
    return None


def blockers(declaration, retired: set[str], held: set[str], proved_hashes: set[str]):
    reasons = []
    name = declaration["theorem"]
    if declaration.get("question_kind") == "value":
        reasons.append("value_answer_adapter_required")
    if name in retired:
        reasons.append("existing_retirement_requires_review")
    if name in held:
        reasons.append("existing_hold_requires_review")
    if declaration.get("contains_sorry_in_type") or "sorryAx" in declaration.get("type_axioms", []):
        reasons.append("type_depends_on_unfilled_definition")
    if declaration.get("formal_proof_kind") or declaration.get("formal_proof_link"):
        reasons.append("upstream_formal_proof_metadata")
    if not declaration.get("depends_on_sorry"):
        reasons.append("already_formally_proved")
    if declaration["type_hash"] in proved_hashes:
        reasons.append("compiled_proved_type_collision")
    if (
        declaration["classification"] != "DIRECT_PROP"
        and "value_answer_adapter_required" not in reasons
    ):
        reasons.append("unsupported_statement_format")
    return reasons


def prepare(
    *,
    raw_catalog: Path,
    source: Path,
    tasks: Path,
    output: Path,
    requested: Path,
    expected_commit: str,
    source_status: Path,
) -> dict:
    if output.resolve().parent != (tasks / "staged").resolve():
        raise ValueError("output must be a direct child of tasks/staged")
    if output.exists():
        raise ValueError("output already exists; use a fresh staged directory")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    if commit != expected_commit:
        raise ValueError("upstream source commit does not match requested pin")
    if subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=source
    ):
        raise ValueError("upstream tracked source is dirty")
    baseline_files = [
        "allowlist.json",
        "task-versions.json",
        "tiers/tier-1/task-targets.json",
        "tiers/tier-1/selection-audit.json",
        "tiers/tier-1/held-source-theorems.json",
        "tiers/tier-1/retired-source-theorems.json",
    ]
    baseline = {name: digest((tasks / name).read_bytes()) for name in baseline_files}
    active = read(tasks / "allowlist.json")["allowed_source_theorems"]
    active_numbers = {problem_number(d["source_path"]) for d in active}
    requested_rows = read(requested)
    requested_names = {r["theorem"] for r in requested_rows}
    requested_numbers = {r["number"] for r in requested_rows}
    status_evidence = read(source_status)
    statuses = {int(p["number"]): p for p in status_evidence["problems"]}
    if len(requested_numbers) != 328 or len(requested_rows) != len(requested_names):
        raise ValueError("expected 328 distinct numbered problems and unique target names")
    if requested_numbers & active_numbers:
        raise ValueError("requested problem is already represented in baseline pool")
    if set(statuses) != requested_numbers:
        raise ValueError("source status evidence does not cover the requested problems")
    declarations = read(raw_catalog)["declarations"]
    for d in declarations:
        d["type_hash"] = digest(d["type_canonical"].encode())
    selected = [
        d
        for d in declarations
        if d["category"] == "research open"
        and problem_number(d["source_path"]) in requested_numbers
    ]
    if {d["theorem"] for d in selected} != requested_names:
        raise ValueError("compiled open declarations do not match requested inventory")
    tier = tasks / "tiers/tier-1"
    retired = set(read(tier / "retired-source-theorems.json")["source_theorems"])
    held = {r["theorem"] for r in read(tier / "held-source-theorems.json")["holds"]}
    proved_hashes = {
        d["type_hash"]
        for d in declarations
        if d["declaration_kind"] == "theorem" and not d["depends_on_sorry"]
    }
    records = []
    for number in sorted(requested_numbers):
        relative = f"FormalConjectures/ErdosProblems/{number}.lean"
        target = output / "sources" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, target)
        targets = []
        for d in sorted(selected, key=lambda d: d["theorem"]):
            if problem_number(d["source_path"]) != number:
                continue
            targets.append(
                {
                    "theorem": d["theorem"],
                    "type_hash": d["type_hash"],
                    "question_kind": d.get("question_kind"),
                    "classification": d["classification"],
                    "blockers": blockers(d, retired, held, proved_hashes),
                    "bundle_status": "pending",
                    "bundles": [],
                }
            )
        records.append(
            {
                "number": number,
                "source_path": relative,
                "source_sha256": digest(target.read_bytes()),
                "source_url": f"https://www.erdosproblems.com/{number}",
                "numbered_problem_status": statuses[number]["status"]["state"],
                "status_scope_note": "Number-level status may differ from an open part or variant.",
                "status": "staged_inactive",
                "targets": targets,
            }
        )
    counts = Counter(reason for r in records for t in r["targets"] for reason in t["blockers"])
    upstream_manifest = read(source / "lake-manifest.json")
    mathlib = next(p["rev"] for p in upstream_manifest["packages"] if p["name"] == "mathlib")
    catalog = {
        "schema_version": 1,
        "repository_commit": commit,
        "lean_toolchain": (source / "lean-toolchain").read_text().strip(),
        "mathlib_commit": mathlib,
        "generated_by": "scripts/staged_erdos/Catalog.lean",
        "extraction_duration_ms": 0,
        "declarations": [
            {k: v for k, v in d.items() if k != "type_canonical"}
            for d in sorted(declarations, key=lambda d: d["theorem"])
        ],
    }
    manifest = {
        "schema_version": 1,
        "deployment_allowed": False,
        "upstream_commit": commit,
        "upstream_repository": "https://github.com/google-deepmind/formal-conjectures",
        "baseline_tasks_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=tasks, text=True
        ).strip(),
        "baseline_files": baseline,
        "problem_count": len(records),
        "open_target_count": len(selected),
        "blocker_counts": dict(sorted(counts.items())),
        "remaining_admission_checks": [
            "source_statement_semantic_review",
            "current_solution_status_and_resolution_claim_review",
            "open_upstream_pull_request_review",
            "full_catalog_collision_screen",
            "production_toolchain_compatibility",
            "version_2_dependency_commitments",
        ],
        "problems": records,
    }
    write(output / "catalog.json", catalog)
    write(output / "manifest.json", manifest)
    write(output / "requested-targets.json", requested_rows)
    shutil.copyfile(raw_catalog, output / "compiled-declarations.json")
    shutil.copyfile(source_status, output / "source-status.json")
    for name in baseline_files:
        target = output / "baseline" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(tasks / name, target)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("raw-catalog", "source", "tasks", "output", "requested", "source-status"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    result = prepare(**vars(parser.parse_args()))
    print(
        json.dumps(
            {k: result[k] for k in ("problem_count", "open_target_count", "blocker_counts")},
            indent=2,
        )
    )
