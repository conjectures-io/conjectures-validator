#!/usr/bin/env python3
"""Build the exact-formalization gold pool from the pinned catalog."""

from __future__ import annotations

import argparse
import os
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from verifier.catalog import load_catalog
from verifier.gold_pool import (
    DEFAULT_GOLD_POOL_SIZE,
    build_gold_allowlist,
    group_gold_declarations,
    load_retired_sources,
    load_selection_audit,
    load_task_grouping,
    load_whole_problem_targets,
    select_gold_declarations,
)
from verifier.task_generator import generate_group_task, generate_task
from verifier.task_loader import load_task_bundle
from verifier.task_policy import GOLD_TASK_MODE
from verifier.workspace import target_validator


ROOT = Path(__file__).resolve().parent.parent


def make_pool_readable(pool: Path) -> None:
    """Publish generated bundles with normal source-tree permissions."""
    pool.chmod(0o755)
    for path in pool.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--catalog", type=Path, default=ROOT / "data/catalog.json")
    result.add_argument(
        "--retired-sources",
        type=Path,
        default=ROOT / "gold/retired-source-theorems.json",
    )
    result.add_argument(
        "--selection-audit",
        type=Path,
        default=ROOT / "gold/selection-audit.json",
    )
    result.add_argument(
        "--task-groups",
        type=Path,
        default=ROOT / "gold/task-groups.json",
    )
    result.add_argument(
        "--whole-problem-targets",
        type=Path,
        default=ROOT / "gold/whole-problem-targets.json",
    )
    result.add_argument("--output", type=Path, default=ROOT / "tasks/gold")
    result.add_argument("--allowlist", type=Path, default=ROOT / "gold/allowlist.json")
    result.add_argument("--pool-size", type=int, default=DEFAULT_GOLD_POOL_SIZE)
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
    retired = load_retired_sources(arguments.retired_sources)
    selection_audit = load_selection_audit(arguments.selection_audit)
    whole_problem_targets = load_whole_problem_targets(
        arguments.whole_problem_targets
    )
    selected_declarations = select_gold_declarations(
        catalog=catalog,
        retired=retired,
        selection_audit=selection_audit,
        whole_problem_targets=whole_problem_targets,
        pool_size=arguments.pool_size,
    )
    grouping = load_task_grouping(arguments.task_groups)
    selected = group_gold_declarations(selected_declarations, grouping)
    validate_target = target_validator(ROOT)
    temporary = Path(tempfile.mkdtemp(prefix=".gold-pool-", dir=output.parent))
    generated = temporary / "gold"
    generated.mkdir()
    bundles = []
    try:
        def generate_one(item):
            index, declarations = item
            names = ", ".join(declaration.theorem for declaration in declarations)
            print(f"[{index}/{len(selected)}] {names}", flush=True)
            if len(declarations) == 1:
                manifest = generate_task(
                    catalog=catalog,
                    declaration=declarations[0],
                    mode=GOLD_TASK_MODE,
                    output=generated / f"pending-{index:03d}",
                    validate_target=validate_target,
                )
            else:
                manifest = generate_group_task(
                    catalog=catalog,
                    declarations=declarations,
                    mode=GOLD_TASK_MODE,
                    output=generated / f"pending-{index:03d}",
                    validate_target=validate_target,
                )
            destination = generated / manifest.task_id
            os.replace(generated / f"pending-{index:03d}", destination)
            return load_task_bundle(destination)

        with ThreadPoolExecutor(max_workers=arguments.jobs) as executor:
            bundles = list(
                executor.map(generate_one, enumerate(selected, start=1))
            )
        allowlist_content = build_gold_allowlist(
            catalog=catalog,
            retired=retired,
            selection_audit=selection_audit,
            whole_problem_targets=whole_problem_targets,
            grouping=grouping,
            selected=selected,
            bundles=bundles,
            audit_date_utc=arguments.audit_date,
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
    print(f"wrote {len(bundles)} exact-formalization tasks to {output}")
    print(f"wrote allowlist to {allowlist}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
