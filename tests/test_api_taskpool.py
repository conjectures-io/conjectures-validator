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
from verifier.task_pool import (
    DEFAULT_TASK_TIER,
    DEFAULT_TIER_SIZE,
)

ROOT = Path(__file__).resolve().parents[1]

ALLOWLIST = ROOT / "tasks/allowlist.json"
POOL_ROOT = ROOT / "tasks/pool"


def test_api_catalog_loads_every_allowlisted_task_from_the_checked_in_pool():
    catalog = TaskCatalog.load(allowlist_path=ALLOWLIST, pool_root=POOL_ROOT)

    modes = [entry.manifest.task_mode for entry in catalog.summaries()]
    assert modes.count(EXACT_TASK_MODE) == DEFAULT_TIER_SIZE
    assert modes.count(COUNTEREXAMPLE_TASK_MODE) == DEFAULT_TIER_SIZE
    assert {entry.tier for entry in catalog.summaries()} == {DEFAULT_TASK_TIER}
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
    complete = TaskCatalog.load(allowlist_path=ALLOWLIST, pool_root=POOL_ROOT)
    kept = complete.summaries()[0]
    (tmp_path / DEFAULT_TASK_TIER).mkdir(parents=True)
    tier = tmp_path / kept.tier
    for source in kept.task_dir.iterdir():
        if source.is_file():
            destination = tier / kept.task_dir.name / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())

    with pytest.raises(TaskNotAllowed, match="missing from the pool"):
        TaskCatalog.load(allowlist_path=ALLOWLIST, pool_root=tmp_path)


def test_a_catalog_entry_carries_the_source_and_challenge_the_public_detail_serves(
    tmp_path: Path,
):
    """`TaskEntry` keeps the two fields `/v1/catalog/conjectures/{slug}` publishes.

    Asserted against a generated task rather than the checked-out pool, so it holds without the
    pinned task checkout: the tests above need `tasks/pool` materialized by
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
