#!/usr/bin/env python3
"""Check coverage, source bytes, bundle commitments, and unchanged admission files."""

import argparse
import sys
from pathlib import Path

from prepare import digest, read, write


def verify(stage: Path, project: Path) -> dict:
    sys.path.insert(0, str(project.resolve()))
    from verifier.task_loader import load_task_bundle

    manifest = read(stage / "manifest.json")
    if manifest["deployment_allowed"] is not False:
        raise ValueError("staged package cannot allow deployment")
    tasks = stage.parent.parent
    for name, expected in manifest["baseline_files"].items():
        if digest((tasks / name).read_bytes()) != expected:
            raise ValueError(f"admission baseline changed: {name}")
    requested = read(stage / "requested-targets.json")
    numbers = {r["number"] for r in requested}
    problems = manifest["problems"]
    if len(problems) != 328 or {p["number"] for p in problems} != numbers:
        raise ValueError("staged package does not cover all 328 requested numbers")
    names = {r["theorem"] for r in requested}
    targets = [t for p in problems for t in p["targets"]]
    if len(targets) != 482 or {t["theorem"] for t in targets} != names:
        raise ValueError("staged targets differ from the compiled request")
    task_ids = set()
    bundle_paths = set()
    for problem in problems:
        source = stage / "sources" / problem["source_path"]
        if digest(source.read_bytes()) != problem["source_sha256"]:
            raise ValueError(f"source bytes changed: {source}")
        for target in problem["targets"]:
            if target["blockers"]:
                if target["bundle_status"] != "blocked" or target["bundles"]:
                    raise ValueError("blocked target has runnable bundles")
                continue
            if target["bundle_status"] != "compiled" or len(target["bundles"]) != 2:
                raise ValueError(f"target is not fully compiled: {target['theorem']}")
            modes = set()
            for item in target["bundles"]:
                path = stage / item["path"]
                if not path.resolve().is_relative_to((stage / "bundles").resolve()):
                    raise ValueError("bundle path escapes staged bundles")
                bundle = load_task_bundle(path)
                m = bundle.manifest
                if digest((path / "manifest.json").read_bytes()) != item["manifest_sha256"]:
                    raise ValueError("manifest changed after compilation")
                if (
                    m.source_theorem != target["theorem"]
                    or m.source_type_hash != target["type_hash"]
                    or m.repository_commit != manifest["upstream_commit"]
                    or m.task_id != item["task_id"]
                ):
                    raise ValueError("bundle does not commit to its recorded source")
                if (
                    m.task_mode == "formalized"
                    and m.generated_target_type_hash != target["type_hash"]
                ):
                    raise ValueError("proof target differs from complete source type")
                if (
                    m.task_mode == "counterexample"
                    and m.generated_target_type_hash == target["type_hash"]
                ):
                    raise ValueError("refutation target was not negated")
                if m.task_id in task_ids:
                    raise ValueError("duplicate task identity")
                task_ids.add(m.task_id)
                modes.add(m.task_mode)
                bundle_paths.add(path.resolve())
            if modes != {"formalized", "counterexample"}:
                raise ValueError("missing proof/refutation pair")
    actual_paths = {p.parent.resolve() for p in (stage / "bundles").rglob("manifest.json")}
    if actual_paths != bundle_paths:
        raise ValueError("unrecorded or partial bundle in staged directory")
    result = {
        "passed": True,
        "problems": len(problems),
        "open_targets": len(targets),
        "compiled_bundles": len(task_ids),
        "source_files_verified": len(problems),
        "unchanged_admission_files": len(manifest["baseline_files"]),
        "active_pool_changed": False,
        "deployed": False,
    }
    write(stage / "verification.json", result)
    return result


if __name__ == "__main__":
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    print(json.dumps(verify(**vars(parser.parse_args())), indent=2))
