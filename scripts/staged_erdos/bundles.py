#!/usr/bin/env python3
"""Compile proof/refutation pairs for unblocked staged statements; never admit them."""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from prepare import digest, read, write


def build(stage: Path, project: Path, jobs: int) -> dict:
    sys.path.insert(0, str(project.resolve()))
    from verifier.catalog import load_catalog
    from verifier.task_generator import generate_task
    from verifier.task_loader import load_task_bundle
    from validation import StagedValidator

    if not 1 <= jobs <= 16:
        raise ValueError("jobs must be between 1 and 16")
    manifest = read(stage / "manifest.json")
    if manifest["deployment_allowed"] is not False:
        raise ValueError("expected an inactive staged import")
    tasks = stage.parent.parent
    for name, expected in manifest["baseline_files"].items():
        if digest((tasks / name).read_bytes()) != expected:
            raise ValueError(f"baseline changed: {name}")
    catalog = load_catalog(stage / "catalog.json")
    declarations = {d.theorem: d for d in catalog.declarations}
    validate = StagedValidator(project)
    targets = [target for problem in manifest["problems"] for target in problem["targets"]]
    pending = []
    for target in targets:
        if target["blockers"]:
            target["bundle_status"] = "blocked"
        elif target["bundle_status"] != "compiled":
            pending.append(target)

    def generate_pair(target):
        declaration = declarations[target["theorem"]]
        outputs = []
        for mode in ("formalized", "counterexample"):
            path = stage / "bundles" / declaration.theorem / mode
            if not path.exists():
                generate_task(
                    catalog=catalog,
                    declaration=declaration,
                    mode=mode,
                    output=path,
                    validate_target=validate,
                )
            bundle = load_task_bundle(path)
            if (
                bundle.manifest.source_theorem != declaration.theorem
                or bundle.manifest.repository_commit != catalog.repository_commit
                or bundle.manifest.source_type_hash != declaration.type_hash
                or bundle.manifest.task_mode != mode
            ):
                raise ValueError("existing staged bundle does not match source")
            outputs.append(
                {
                    "path": str(path.relative_to(stage)),
                    "task_id": bundle.manifest.task_id,
                    "manifest_sha256": digest((path / "manifest.json").read_bytes()),
                }
            )
        return outputs

    with ThreadPoolExecutor(max_workers=jobs) as executor:
        futures = {executor.submit(generate_pair, target): target for target in pending}
        for index, future in enumerate(as_completed(futures), 1):
            target = futures[future]
            try:
                target["bundles"] = future.result()
                target["bundle_status"] = "compiled"
                target.pop("build_error", None)
            except Exception as error:
                target["bundle_status"] = "build_failed"
                target["build_error"] = str(error)
            print(
                f"[{index}/{len(pending)}] {target['theorem']}: {target['bundle_status']}",
                flush=True,
            )
            write(stage / "manifest.json", manifest)
    validate.close()
    result = {
        "problems": len(manifest["problems"]),
        "open_targets": len(targets),
        "compiled_targets": sum(t["bundle_status"] == "compiled" for t in targets),
        "blocked_targets": sum(t["bundle_status"] == "blocked" for t in targets),
        "failed_targets": sum(t["bundle_status"] == "build_failed" for t in targets),
        "compiled_bundles": sum(len(t["bundles"]) for t in targets),
        "problems_with_compiled_bundles": sum(
            any(t["bundle_status"] == "compiled" for t in p["targets"])
            for p in manifest["problems"]
        ),
        "admitted": False,
        "deployed": False,
    }
    write(stage / "build-results.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True, type=Path)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--jobs", type=int, default=4)
    result = build(**vars(parser.parse_args()))
    print(json.dumps(result, indent=2))
    raise SystemExit(1 if result["failed_targets"] else 0)
