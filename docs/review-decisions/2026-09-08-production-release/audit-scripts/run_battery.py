#!/usr/bin/env python3
"""Reproducible cheap-tactic sweep over the 16 replacement task bundles."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VALIDATOR_ROOT = Path("/opt/fc-verifier")
STAGING_ROOT = Path("/tmp/add-50-candidate-staging")
REPLACEMENT_ROOT = Path("/tmp/add-50-candidate-replacements")
INCLUDED_REPLACEMENT_SLUG_PREFIXES = (
    "erdos-295-",
    "erdos-600-parts-i-",
    "erdos-885-",
)
AUDIT_ROOT = Path("/audit/attack-audit")
RAW_ROOT = AUDIT_ROOT / "raw"
SOURCE_ROOT = AUDIT_ROOT / "sources"
WORKSPACE_PARENT = AUDIT_ROOT / "workspaces"
SUMMARY_PATH = AUDIT_ROOT / "sweep-summary.json"
VALIDATOR_COMMIT = "b2f0c3306e63e5861d2950223d154c3000ed9cf8 plus recorded release code"
FORMAL_CONJECTURES_COMMIT = "8432eac998110a563e03df65a28c117e97c8c142"

# `native_decide` is deliberately included as a compile-only policy probe. The static scanner is
# expected to reject it even if Lean can close a target.
ATTACKS = (
    ("simp", "simp", "simplifier"),
    ("simpa", "simpa", "simplifier"),
    ("simp_all", "simp_all", "simplifier"),
    ("aesop", "aesop", "automation"),
    ("omega", "omega", "arithmetic"),
    ("norm_num", "norm_num", "arithmetic"),
    ("decide", "decide", "decision"),
    ("native_decide", "native_decide", "policy_probe"),
    ("true_intro", "exact True.intro", "constructor"),
    ("false_elim_simp", "apply False.elim\nsimp_all", "contradiction"),
    ("constructor", "constructor", "constructor"),
    ("constructor_simp", "constructor <;> simp", "constructor"),
    ("constructor_aesop", "constructor <;> aesop", "constructor"),
    ("rfl", "rfl", "equality"),
    ("trivial", "trivial", "automation"),
    ("tauto", "tauto", "logic"),
    ("positivity", "positivity", "arithmetic"),
    ("linarith", "linarith", "arithmetic"),
    ("nlinarith", "nlinarith", "arithmetic"),
    ("intros_simp_all", "intros\nsimp_all", "vacuity"),
    ("intros_omega", "intros\nomega", "vacuity"),
    ("intros_norm_num", "intros\nnorm_num at *", "vacuity"),
    ("intros_aesop", "intros\naesop", "vacuity"),
    ("by_contra_simp_all", "by_contra h\nsimp_all", "contradiction"),
    ("classical_simp_all", "classical\nsimp_all", "simplifier"),
    ("classical_aesop", "classical\naesop", "automation"),
    ("refine_pair_simp", "refine ⟨?_, ?_⟩ <;> simp", "existential"),
    ("refine_pair_aesop", "refine ⟨?_, ?_⟩ <;> aesop", "existential"),
    ("use_zero_simp", "use 0 <;> simp", "existential"),
    ("use_one_simp", "use 1 <;> simp", "existential"),
    ("use_empty_simp", "use ∅ <;> simp", "existential"),
    ("assumption", "assumption", "logic"),
    ("infer_instance", "infer_instance", "typeclass"),
    ("contradiction", "contradiction", "contradiction"),
    ("solve_by_elim", "solve_by_elim", "search"),
    ("first_order", "first_order", "logic"),
    ("grind", "grind", "automation"),
    ("exact_search", "exact?", "declaration_search"),
    ("apply_search", "apply?", "declaration_search"),
    ("library_search", "library_search", "declaration_search"),
    ("aesop_search", "aesop?", "declaration_search"),
    ("simp_search", "simp?", "declaration_search"),
)

sys.path.insert(0, "/release")

from verifier.catalog import load_catalog  # noqa: E402
from verifier.environment import tool_path, trusted_environment  # noqa: E402
from verifier.static_checks import check_submission  # noqa: E402
from verifier.task_loader import load_task_bundle  # noqa: E402
from verifier.workspace import create_workspace  # noqa: E402


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def diagnostics(output: str) -> list[dict[str, Any]]:
    result = []
    for line in output.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and isinstance(value.get("severity"), str):
            result.append(value)
    return result


def target_expression(challenge: str) -> str:
    match = re.search(r"theorem target : (.+) := by\n  sorry", challenge)
    if match is None:
        raise RuntimeError("cannot extract generated target expression")
    return match.group(1)


def load_targets() -> tuple[Any, list[dict[str, Any]]]:
    catalog = load_catalog(Path("/release/data/catalog.json"))
    if catalog.repository_commit != FORMAL_CONJECTURES_COMMIT:
        raise RuntimeError("catalog source pin drifted")
    root = Path("/audit")
    names = json.loads((root / "replacement-targets.json").read_text())
    bundle_directories = [root / "bundles" / (n.replace(".", "-").lower() + "-" + mode)
                          for n in names for mode in ("formalized", "counterexample")]
    targets = []
    for bundle_directory in bundle_directories:
        bundle = load_task_bundle(bundle_directory)
        challenge = bundle.files["Challenge.lean"].decode("utf-8", errors="strict")
        expression = target_expression(challenge)
        targets.append(
            {
                "bundle": bundle,
                "bundle_directory": str(bundle_directory),
                "bundle_sha256": bundle.sha256,
                "expression": expression,
                "header": bundle.files["SolutionHeader.lean.txt"].decode(
                    "utf-8", errors="strict"
                ),
                "mode": bundle.manifest.task_mode,
                "module": bundle.source.module,
                "source_type_pretty": bundle.source.type_pretty,
                "target_type_sha256": bundle.manifest.generated_target_type_hash,
                "task_id": bundle.manifest.task_id,
                "theorem": bundle.manifest.source_theorem,
            }
        )
    targets.sort(key=lambda item: (item["theorem"], item["mode"]))
    if len(targets) != 16 or len({item["task_id"] for item in targets}) != 16:
        raise RuntimeError("staged task identities are not unique")
    return catalog, targets


def render_shard(
    shard_index: int,
    targets: list[dict[str, Any]],
) -> tuple[Path, list[dict[str, Any]]]:
    imports = sorted({item["module"] for item in targets})
    lines = [*(f"import {module}" for module in imports), "import TaskSupport", "", "set_option maxHeartbeats 20000", "namespace AttackBattery", ""]
    ranges: list[dict[str, Any]] = []
    for target in targets:
        bundle = target["bundle"]
        for attack_name, tactic, category in ATTACKS:
            lines.append(
                f"-- ATTACK {target['task_id']} {attack_name}"
            )
            start_line = len(lines) + 1
            proof_name = f"probe_{shard_index}_{len(ranges)}"
            lines.append(f"theorem {proof_name} : {target['expression']} := by")
            lines.extend(f"  {line}" for line in tactic.splitlines())
            lines.append(f"#print axioms {proof_name}")
            end_line = len(lines)
            lines.append("")
            proof = (
                f"theorem target : {target['expression']} := by\n"
                + "\n".join(f"  {line}" for line in tactic.splitlines())
                + "\n"
            )
            static = check_submission(proof, bundle.manifest)
            ranges.append(
                {
                    "attack": attack_name,
                    "category": category,
                    "end_line": end_line,
                    "mode": target["mode"],
                    "proof": proof,
                    "source_type_pretty": target["source_type_pretty"],
                    "start_line": start_line,
                    "static_policy_valid": static.valid,
                    "static_policy_violations": list(static.violations),
                    "tactic": tactic,
                    "target_type_sha256": target["target_type_sha256"],
                    "task_id": target["task_id"],
                    "theorem": target["theorem"],
                }
            )
    lines.extend(("end AttackBattery", ""))
    path = SOURCE_ROOT / f"BatteryShard{shard_index:02d}.lean"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path, ranges


def run_shard(
    shard_index: int,
    path: Path,
    ranges: list[dict[str, Any]],
    lean: Path,
    environment: dict[str, str],
    workspace: Path,
) -> dict[str, Any]:
    started = time.monotonic()
    process = subprocess.run(
        (str(lean), "-DmaxErrors=100000", str(path), "--json"),
        cwd=workspace,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=1800,
        check=False,
    )
    elapsed = time.monotonic() - started
    parsed = diagnostics(process.stdout + "\n" + process.stderr)
    effective_lines = []
    last_source_line = -1
    for item in parsed:
        line = int(item.get("pos", {}).get("line", -1))
        if (
            line == 1
            and item.get("severity") == "error"
            and "maximum number of heartbeats" in str(item.get("data", ""))
        ):
            line = last_source_line
        elif line > 1:
            last_source_line = line
        effective_lines.append(line)
    unmapped_errors = []
    for index, item in enumerate(parsed):
        if item.get("severity") != "error":
            continue
        # Lean's JSON source lines are already one-based.
        line = effective_lines[index]
        if not any(row["start_line"] <= line <= row["end_line"] for row in ranges):
            unmapped_errors.append(item)
    attempts = []
    for row in ranges:
        related = []
        for index, item in enumerate(parsed):
            line = effective_lines[index]
            if row["start_line"] <= line <= row["end_line"]:
                related.append(item)
        errors = [item for item in related if item.get("severity") == "error"]
        suggestions = [
            str(item.get("data", ""))
            for item in related
            if "Try this:" in str(item.get("data", ""))
        ]
        attempts.append(
            {
                **row,
                "compiled": not errors,
                "axioms_report": [item.get("data", "") for item in related if "depends on axioms" in str(item.get("data", "")) or "does not depend on any axioms" in str(item.get("data", ""))],
                "diagnostics": related,
                "suggestions": suggestions,
            }
        )
    raw = {
        "elapsed_seconds": round(elapsed, 3),
        "exit_code": process.returncode,
        "shard": shard_index,
        "stderr": process.stderr,
        "stdout": process.stdout,
    }
    atomic_json(RAW_ROOT / f"shard-{shard_index:02d}.json", raw)
    return {
        "attempts": attempts,
        "elapsed_seconds": round(elapsed, 3),
        "exit_code": process.returncode,
        "shard": shard_index,
        "source": str(path),
        "unmapped_errors": unmapped_errors,
    }


def main() -> int:
    if any(path.exists() for path in (RAW_ROOT, SOURCE_ROOT, WORKSPACE_PARENT, SUMMARY_PATH)):
        raise SystemExit("audit outputs already exist; refusing to overwrite")
    RAW_ROOT.mkdir(mode=0o755, parents=True)
    SOURCE_ROOT.mkdir(mode=0o755)
    WORKSPACE_PARENT.mkdir(mode=0o755)
    catalog, targets = load_targets()

    seed = targets[0]["bundle"]
    workspace_paths = create_workspace(
        task_files=seed.files,
        project_root=VALIDATOR_ROOT,
        workspace_parent=WORKSPACE_PARENT,
        retain=True,
    )
    workspace = workspace_paths.root
    environment = dict(trusted_environment(VALIDATOR_ROOT, workspace / ".home"))
    lake = tool_path(VALIDATOR_ROOT, "lake")
    lean = tool_path(VALIDATOR_ROOT, "lean")
    environment["LEAN_PATH"] = subprocess.check_output(
        (str(lake), "env", "printenv", "LEAN_PATH"),
        cwd=workspace,
        env=environment,
        text=True,
    ).strip()

    shard_count = 4
    target_shards = [targets[index::shard_count] for index in range(shard_count)]
    rendered = [
        render_shard(index, shard)
        for index, shard in enumerate(target_shards, start=1)
    ]
    started_at = utc_now()
    started = time.monotonic()
    shard_results = []
    with ThreadPoolExecutor(max_workers=shard_count) as executor:
        futures = {
            executor.submit(
                run_shard,
                index,
                path,
                ranges,
                lean,
                environment,
                workspace,
            ): index
            for index, (path, ranges) in enumerate(rendered, start=1)
        }
        for future in as_completed(futures):
            result = future.result()
            shard_results.append(result)
            hits = sum(item["compiled"] for item in result["attempts"])
            print(
                f"[{len(shard_results)}/{shard_count}] shard {result['shard']}: "
                f"attempts={len(result['attempts'])} compile_hits={hits} "
                f"elapsed={result['elapsed_seconds']:.3f}s",
                flush=True,
            )
    shard_results.sort(key=lambda item: item["shard"])
    attempts = [item for shard in shard_results for item in shard["attempts"]]
    duplicate_hashes = {}
    by_hash: dict[str, list[Any]] = {}
    for declaration in catalog.declarations:
        by_hash.setdefault(declaration.type_hash, []).append(declaration)
    for target in targets:
        matches = by_hash.get(target["target_type_sha256"], [])
        others = [
            {
                "category": item.category,
                "formal_proof_kind": item.formal_proof_kind,
                "source_path": item.source_path,
                "theorem": item.theorem,
            }
            for item in matches
            if item.theorem != target["theorem"]
        ]
        if others:
            duplicate_hashes[target["task_id"]] = others

    compiled = [item for item in attempts if item["compiled"]]
    summary = {
        "aggregate": {
            "attacks_per_task": len(ATTACKS),
            "compiled_hits_before_axiom_confirmation": len(compiled),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "failed_elaborations": len(attempts) - len(compiled),
            "shards": shard_count,
            "status": "sweep_complete",
            "tasks": len(targets),
            "theorems": len({item["theorem"] for item in targets}),
            "total_attempts": len(attempts),
        },
        "attempts": attempts,
        "baseline": {
            "formal_conjectures_commit": catalog.repository_commit,
            "staging_summary": "/audit/generation.json",
            "replacement_staging": "/audit/bundles",
            "validator_commit": VALIDATOR_COMMIT,
            "validator_root": str(VALIDATOR_ROOT),
            "workspace": str(workspace),
        },
        "duplicate_target_hashes": duplicate_hashes,
        "finished_at_utc": utc_now(),
        "schema_version": 1,
        "shards": shard_results,
        "started_at_utc": started_at,
    }
    atomic_json(SUMMARY_PATH, summary)
    print(json.dumps(summary["aggregate"], indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
