"""The API's view of the audited task pool, loaded from the checked-out task repository.

The rest of the API tests build a synthetic catalog with `catalog_from_entries`, because they
are about endpoint behaviour rather than about the pool. That leaves `TaskCatalog.load` — the
call `submission_api/app.py` makes at startup — covered only here, against the real bytes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from submission_api.taskpool import TaskCatalog, TaskNotAllowed
from verifier.task_policy import COUNTEREXAMPLE_TASK_MODE, EXACT_TASK_MODE
from verifier.task_pool import DEFAULT_TASK_TIER, DEFAULT_TIER_SIZE

ROOT = Path(__file__).resolve().parents[1]

ALLOWLIST = ROOT / "task_pool/allowlist.json"
POOL_ROOT = ROOT / "tasks/pool"


def test_api_catalog_loads_every_allowlisted_task_from_the_checked_in_pool():
    catalog = TaskCatalog.load(allowlist_path=ALLOWLIST, pool_root=POOL_ROOT)

    modes = [entry.manifest.task_mode for entry in catalog.summaries()]
    assert modes.count(EXACT_TASK_MODE) == DEFAULT_TIER_SIZE
    assert modes.count(COUNTEREXAMPLE_TASK_MODE) == DEFAULT_TIER_SIZE
    assert all(entry.tier == DEFAULT_TASK_TIER for entry in catalog.summaries())
    assert all(
        entry.task_id == entry.manifest.task_id for entry in catalog.summaries()
    )


def test_api_catalog_identifies_tasks_by_manifest_not_directory_name():
    """The task repository names directories for humans and renames them freely.

    A task is its manifest's task ID; the directory name is a label. This asserts the two
    genuinely differ in the checked-in pool, so a loader that rebuilt the path from the task
    ID would fail this test rather than fail at startup in production.
    """
    catalog = TaskCatalog.load(allowlist_path=ALLOWLIST, pool_root=POOL_ROOT)

    assert all(
        entry.task_dir.name != entry.task_id for entry in catalog.summaries()
    )
    assert all(entry.task_dir.is_dir() for entry in catalog.summaries())


def test_api_catalog_refuses_an_allowlisted_task_with_no_bytes_on_disk(tmp_path: Path):
    """A paid submission must never meet a task the pool cannot produce."""
    tier = tmp_path / DEFAULT_TASK_TIER
    tier.mkdir(parents=True)
    complete = TaskCatalog.load(allowlist_path=ALLOWLIST, pool_root=POOL_ROOT)
    kept = complete.summaries()[0]
    for source in kept.task_dir.iterdir():
        if source.is_file():
            destination = tier / kept.task_dir.name / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())

    with pytest.raises(TaskNotAllowed, match="missing from the pool"):
        TaskCatalog.load(allowlist_path=ALLOWLIST, pool_root=tmp_path)
