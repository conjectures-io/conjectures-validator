from __future__ import annotations

import json
from pathlib import Path

from frontier_subnet.task_registry import GoldTaskRegistry
from verifier.catalog import load_catalog
from verifier.gold_pool import (
    DEFAULT_GOLD_POOL_SIZE,
    ERDOS_SOURCE_PREFIX,
    EXCLUDED_SOURCE_PREFIXES,
    GOLD_POOL_SCHEMA_VERSION,
    MINIMUM_ERDOS_TASKS,
    UNSOLVED_ERDOS_STATUSES,
    load_retired_sources,
    load_selection_audit,
    select_gold_declarations,
)
from verifier.task_loader import load_task_bundle
from verifier.task_policy import GOLD_TASK_MODE


ROOT = Path(__file__).resolve().parents[1]


def test_gold_selection_is_new_and_audited_erdos_only():
    catalog = load_catalog(ROOT / "data/catalog.json")
    retired = load_retired_sources(ROOT / "gold/retired-source-theorems.json")
    audit = load_selection_audit(ROOT / "gold/selection-audit.json")
    selected = select_gold_declarations(
        catalog=catalog,
        retired=retired,
        selection_audit=audit,
    )
    assert len(selected) == DEFAULT_GOLD_POOL_SIZE
    assert not ({item.theorem for item in selected} & retired.theorems)
    assert not ({item.type_hash for item in selected} & retired.type_hashes)
    assert len({item.type_hash for item in selected}) == len(selected)
    assert all(item.category == "research open" for item in selected)
    assert all(item.classification.value == "DIRECT_PROP" for item in selected)
    assert all(not item.contains_sorry_in_type for item in selected)
    assert sum(
        item.source_path.startswith(ERDOS_SOURCE_PREFIX)
        for item in selected
    ) >= MINIMUM_ERDOS_TASKS
    assert all(
        not item.source_path.startswith(EXCLUDED_SOURCE_PREFIXES)
        for item in selected
    )
    assert tuple(item.theorem for item in selected) == audit.theorems
    assert all(
        entry.problem_tracker_status in UNSOLVED_ERDOS_STATUSES
        for entry in audit.entries
    )


def test_checked_in_gold_pool_is_exact_and_one_to_one():
    policy = json.loads((ROOT / "gold/allowlist.json").read_text(encoding="utf-8"))
    registry = GoldTaskRegistry.load(ROOT / "gold/allowlist.json")
    task_directories = tuple(
        sorted(
            path
            for path in (ROOT / "tasks/gold").iterdir()
            if path.is_dir()
        )
    )

    audit = load_selection_audit(ROOT / "gold/selection-audit.json")
    assert policy["schema_version"] == GOLD_POOL_SCHEMA_VERSION
    assert policy["pool_policy"]["mode"] == GOLD_TASK_MODE
    assert policy["pool_policy"]["synthetic_negation"] is False
    assert policy["pool_policy"]["selection_audit_sha256"] == audit.sha256
    assert policy["pool_policy"]["minimum_erdos_tasks"] == MINIMUM_ERDOS_TASKS
    assert policy["pool_policy"]["excluded_source_prefixes"] == list(
        EXCLUDED_SOURCE_PREFIXES
    )
    assert len(registry.tasks) == DEFAULT_GOLD_POOL_SIZE
    assert len(task_directories) == DEFAULT_GOLD_POOL_SIZE
    assert (ROOT / "gold").stat().st_mode & 0o005 == 0o005
    assert (ROOT / "gold/allowlist.json").stat().st_mode & 0o004 == 0o004

    source_types = set()
    task_hashes = set()
    for task_directory in task_directories:
        assert task_directory.stat().st_mode & 0o005 == 0o005
        assert all(
            path.stat().st_mode & 0o004 == 0o004
            for path in task_directory.iterdir()
            if path.is_file()
        )
        bundle = load_task_bundle(task_directory)
        manifest = bundle.manifest
        assert manifest.task_id in registry.tasks
        assert manifest.task_mode == GOLD_TASK_MODE
        assert manifest.production_eligible
        assert manifest.classification.value == "DIRECT_PROP"
        assert manifest.source_type_hash == manifest.generated_target_type_hash
        challenge = (task_directory / "Challenge.lean").read_text(encoding="utf-8")
        assert "theorem target : fcTypeOfName%" in challenge
        assert "theorem target : ¬" not in challenge
        assert manifest.source_type_hash not in source_types
        assert bundle.sha256 not in task_hashes
        assert not manifest.source_path.startswith(EXCLUDED_SOURCE_PREFIXES)
        source_types.add(manifest.source_type_hash)
        task_hashes.add(bundle.sha256)
