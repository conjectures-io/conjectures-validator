#!/usr/bin/env python3
"""Build paired proof/counterexample task commitments from the pinned catalog."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from verifier.catalog import load_catalog
from verifier.task_pool import (
    DEFAULT_TIER_SIZE,
    build_task_allowlist,
    group_task_declarations,
    load_retired_sources,
    load_selection_audit,
    load_task_grouping,
    load_task_targets,
    select_task_declarations,
)
from verifier.task_generator import generate_group_task, generate_task
from verifier.task_loader import load_task_bundle
from verifier.task_policy import PRODUCTION_TASK_MODES
from verifier.workspace import target_validator


TIER_NAME = "tier-1"

ERDOS_NUMBER = re.compile(r"/ErdosProblems/(?P<number>[0-9]+)\.lean$")


def task_directory_name(manifest) -> str:
    """A readable storage label; the manifest's task ID remains the protocol identity."""
    match = ERDOS_NUMBER.search("/" + manifest.source_path)
    if match is None:
        return manifest.task_id
    number = match.group("number")
    marker = f"erdos_{number}."
    local = (
        manifest.source_theorem.split(marker, 1)[1]
        if marker in manifest.source_theorem
        else ""
    )
    suffix = re.sub(r"[^A-Za-z0-9]+", "-", local).strip("-").lower()
    parts = [f"erdos-{number}"]
    if suffix:
        parts.append(suffix)
    parts.append(manifest.task_mode)
    return "-".join(parts)


def make_pool_readable(pool: Path) -> None:
    """Publish generated bundles with normal source-tree permissions."""
    pool.chmod(0o755)
    for path in pool.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--catalog", type=Path, default=ROOT / "data/catalog.json")
    result.add_argument(
        "--metadata-root",
        type=Path,
        default=ROOT / "tasks/tiers/tier-1",
    )
    result.add_argument(
        "--output",
        type=Path,
        default=ROOT / "tasks/pool",
    )
    result.add_argument(
        "--allowlist",
        type=Path,
        default=ROOT / "tasks/allowlist.json",
    )
    result.add_argument("--jobs", type=int, default=4)
    result.add_argument(
        "--audit-date",
        default=datetime.now(timezone.utc).date().isoformat(),
    )
    return result


def main() -> int:
    arguments = parser().parse_args()
    output = arguments.output.resolve()
    allowlist = arguments.allowlist.resolve()
    if output.exists() or allowlist.exists():
        raise SystemExit("output pool and allowlist must not already exist")
    if not 1 <= arguments.jobs <= 16:
        raise SystemExit("--jobs must be between 1 and 16")
    output.parent.mkdir(parents=True, exist_ok=True)
    allowlist.parent.mkdir(parents=True, exist_ok=True)

    catalog = load_catalog(arguments.catalog)
    validate_target = target_validator(ROOT)
    temporary = Path(tempfile.mkdtemp(prefix=".task-pool-", dir=output.parent))
    generated = temporary / "pool"
    generated.mkdir()
    try:
        metadata = arguments.metadata_root
        retired = load_retired_sources(metadata / "retired-source-theorems.json")
        selection_audit = load_selection_audit(metadata / "selection-audit.json")
        task_targets = load_task_targets(metadata / "task-targets.json")
        selected_declarations = select_task_declarations(
            catalog=catalog,
            retired=retired,
            selection_audit=selection_audit,
            task_targets=task_targets,
            pool_size=DEFAULT_TIER_SIZE,
        )
        grouping = load_task_grouping(metadata / "task-groups.json")
        selected = group_task_declarations(selected_declarations, grouping)
        tier_output = generated / TIER_NAME
        tier_output.mkdir()
        work = tuple(
            (index, declarations, mode)
            for index, declarations in enumerate(selected, start=1)
            for mode in PRODUCTION_TASK_MODES
        )

        def generate_one(item):
            index, declarations, mode = item
            names = ", ".join(declaration.theorem for declaration in declarations)
            print(
                f"[{index}/{len(selected)}] {names} ({mode})",
                flush=True,
            )
            pending = tier_output / f"pending-{index:03d}-{mode}"
            if len(declarations) == 1:
                manifest = generate_task(
                    catalog=catalog,
                    declaration=declarations[0],
                    mode=mode,
                    output=pending,
                    validate_target=validate_target,
                )
            else:
                manifest = generate_group_task(
                    catalog=catalog,
                    declarations=declarations,
                    mode=mode,
                    output=pending,
                    validate_target=validate_target,
                )
            destination = tier_output / task_directory_name(manifest)
            os.replace(pending, destination)
            return load_task_bundle(destination)

        with ThreadPoolExecutor(max_workers=arguments.jobs) as executor:
            bundles = list(executor.map(generate_one, work))
        allowlist_content = build_task_allowlist(
            catalog=catalog,
            retired=retired,
            selection_audit=selection_audit,
            task_targets=task_targets,
            grouping=grouping,
            selected=selected,
            bundles=bundles,
            audit_date_utc=arguments.audit_date,
            tier=TIER_NAME,
        )
        make_pool_readable(generated)
        temporary_allowlist = temporary / "allowlist.json"
        temporary_allowlist.write_bytes(allowlist_content)
        temporary_allowlist.chmod(0o644)
        os.replace(generated, output)
        os.replace(temporary_allowlist, allowlist)
    except Exception:
        if output.exists():
            shutil.rmtree(output)
        if allowlist.exists():
            allowlist.unlink()
        raise
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    print(f"wrote {len(bundles)} proof/counterexample tasks to {output}")
    print(f"wrote allowlist to {allowlist}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
