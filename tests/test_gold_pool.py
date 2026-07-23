from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from frontier_subnet.task_registry import GoldTaskRegistry
from verifier.catalog import load_catalog
from verifier.gold_pool import (
    DEFAULT_GOLD_POOL_SIZE,
    load_retired_sources,
    repository_area,
    select_gold_declarations,
)
from verifier.task_loader import load_task_bundle
from verifier.task_policy import GOLD_TASK_MODE


ROOT = Path(__file__).resolve().parents[1]


def test_gold_selection_is_new_balanced_and_one_per_source_file():
    catalog = load_catalog(ROOT / "data/catalog.json")
    retired = load_retired_sources(ROOT / "gold/retired-source-theorems.json")
    selected = select_gold_declarations(catalog=catalog, retired=retired)
    by_theorem = {item.theorem: item for item in catalog.declarations}
    retired_paths = {
        by_theorem[theorem].source_path
        for theorem in retired.theorems
    }
    retired_types = {
        by_theorem[theorem].type_hash
        for theorem in retired.theorems
    }

    assert len(selected) == DEFAULT_GOLD_POOL_SIZE
    assert not ({item.theorem for item in selected} & retired.theorems)
    assert not ({item.source_path for item in selected} & retired_paths)
    assert not ({item.type_hash for item in selected} & retired_types)
    assert len({item.source_path for item in selected}) == len(selected)
    assert len({item.type_hash for item in selected}) == len(selected)
    assert all(item.category == "research open" for item in selected)
    assert all(item.classification.value == "DIRECT_PROP" for item in selected)
    assert all(not item.contains_sorry_in_type for item in selected)
    areas = Counter(repository_area(item) for item in selected)
    assert len(areas) == 13
    assert max(areas.values()) - min(areas.values()) <= 7


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

    assert policy["schema_version"] == 2
    assert policy["pool_policy"]["mode"] == GOLD_TASK_MODE
    assert policy["pool_policy"]["synthetic_negation"] is False
    assert len(registry.tasks) == DEFAULT_GOLD_POOL_SIZE
    assert len(task_directories) == DEFAULT_GOLD_POOL_SIZE
    assert (ROOT / "gold").stat().st_mode & 0o005 == 0o005
    assert (ROOT / "gold/allowlist.json").stat().st_mode & 0o004 == 0o004

    source_paths = set()
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
        assert manifest.source_path not in source_paths
        assert manifest.source_type_hash not in source_types
        assert bundle.sha256 not in task_hashes
        source_paths.add(manifest.source_path)
        source_types.add(manifest.source_type_hash)
        task_hashes.add(bundle.sha256)
