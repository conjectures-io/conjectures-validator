"""The API's view of the audited task pool, loaded from the checked-out task repository.

The rest of the API tests build a synthetic catalog with `catalog_from_entries`, because they
are about endpoint behaviour rather than about the pool. That leaves `TaskCatalog.load` — the
call `submission_api/app.py` makes at startup — covered only here, against the real bytes.
"""

from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from submission_api.taskpool import TaskCatalog, TaskNotAllowed
from verifier.repository import tasks_repository_root
from verifier.task_policy import COUNTEREXAMPLE_TASK_MODE, EXACT_TASK_MODE
from verifier.task_versions import is_v2_task_id
from verifier.task_pool import (
    DEFAULT_TASK_TIER,
    DEFAULT_TIER_SIZE,
)

ROOT = Path(__file__).resolve().parents[1]
TASKS_ROOT = tasks_repository_root(ROOT)

ALLOWLIST = TASKS_ROOT / "allowlist.json"
POOL_ROOT = TASKS_ROOT / "pool"


@pytest.mark.needs_checkouts
def test_api_catalog_loads_every_allowlisted_task_from_the_checked_in_pool():
    catalog = TaskCatalog.load(allowlist_path=ALLOWLIST, pool_root=POOL_ROOT)

    modes = [entry.manifest.task_mode for entry in catalog.summaries()]
    assert modes.count(EXACT_TASK_MODE) == DEFAULT_TIER_SIZE
    assert modes.count(COUNTEREXAMPLE_TASK_MODE) == DEFAULT_TIER_SIZE
    assert {entry.tier for entry in catalog.summaries()} == {DEFAULT_TASK_TIER}
    assert all(
        entry.task_id == entry.manifest.task_id for entry in catalog.summaries()
    )


@pytest.mark.needs_checkouts
def test_api_catalog_resolves_legacy_names_and_version_store_ids():
    """Legacy directories are labels; immutable v2 directories are keyed by task ID."""
    catalog = TaskCatalog.load(allowlist_path=ALLOWLIST, pool_root=POOL_ROOT)

    for entry in catalog.summaries():
        if is_v2_task_id(entry.task_id):
            assert entry.task_dir == TASKS_ROOT / "versions" / entry.task_id
        else:
            assert entry.task_dir.name != entry.task_id
        assert entry.task_dir.is_dir()


@pytest.mark.needs_checkouts
def test_api_catalog_refuses_an_allowlisted_task_with_no_bytes_on_disk(tmp_path: Path):
    """A paid submission must never meet a task the pool cannot produce."""
    complete = TaskCatalog.load(allowlist_path=ALLOWLIST, pool_root=POOL_ROOT)
    kept = complete.summaries()[0]
    shutil.copy2(ALLOWLIST, tmp_path / "allowlist.json")
    shutil.copy2(TASKS_ROOT / "task-versions.json", tmp_path / "task-versions.json")
    shutil.copytree(kept.task_dir, tmp_path / kept.task_dir.relative_to(TASKS_ROOT))

    with pytest.raises(TaskNotAllowed, match="missing from the (pool|version store)"):
        TaskCatalog.load(allowlist_path=tmp_path / "allowlist.json", pool_root=tmp_path / "pool")


def test_a_catalog_entry_carries_the_source_and_challenge_the_public_detail_serves(
    tmp_path: Path,
):
    """`TaskEntry` keeps the two fields `/v1/catalog/conjectures/{slug}` publishes.

    Asserted against a generated task rather than the checked-out pool, so it holds without the
    pinned task checkout: the tests above need `conjectures-tasks/pool` materialized by
    `scripts/pin_dependencies.sh`, and this covers the projection `TaskCatalog.load` performs.

    What matters is that both come from the bundle whose bytes were hash-verified against the
    allowlist. Re-reading `Challenge.lean` off disk per request would let the published statement
    drift from the audited one between startup and the request, and reading the statement from
    anywhere but `source-metadata.json` would publish something no commitment covers.
    """
    from conftest import catalog as fixture_catalog
    from conftest import declaration
    from verifier.task_generator import generate_task
    from verifier.task_loader import load_task_bundle

    from submission_api.taskpool import CHALLENGE_NAME

    item = declaration()
    destination = tmp_path / "generated-task"
    generate_task(
        catalog=fixture_catalog(item),
        declaration=item,
        mode="formalized",
        output=destination,
        validate_target=lambda *_: item.type_hash,
    )

    bundle = load_task_bundle(destination)

    # The two reads TaskCatalog.load makes, by the names it uses.
    assert bundle.source == item
    challenge = bundle.files[CHALLENGE_NAME].decode("utf-8")
    assert 'theorem target : fcTypeOfName% "VerifierFixtures.direct"' in challenge
    # A trusted file, so the bytes served publicly are the bytes the digest covers.
    assert bundle.manifest.trusted_file_hashes[CHALLENGE_NAME].startswith("sha256:")
    assert challenge == (destination / CHALLENGE_NAME).read_text(encoding="utf-8")
