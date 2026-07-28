from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from verifier.catalog import load_catalog
from verifier.gold_registry import GoldTaskRegistry, TaskNotAllowed
from verifier.gold_pool import (
    DEFAULT_GOLD_POOL_SIZE,
    DEFAULT_GOLD_TASK_COUNT,
    ERDOS_SOURCE_PREFIX,
    EXCLUDED_SOURCE_PREFIXES,
    GOLD_POOL_GROUPING,
    GOLD_POOL_SCHEMA_VERSION,
    GOLD_POOL_SELECTION,
    GOLD_POOL_TASK_SCOPE,
    MINIMUM_ERDOS_TASKS,
    UNSOLVED_ERDOS_STATUSES,
    group_gold_declarations,
    load_retired_sources,
    load_selection_audit,
    load_task_grouping,
    load_whole_problem_targets,
    select_gold_declarations,
)
from verifier.task_loader import load_task_bundle
from verifier.task_policy import GOLD_TASK_MODE


ROOT = Path(__file__).resolve().parents[1]


def test_gold_selection_is_new_and_audited_erdos_only():
    catalog = load_catalog(ROOT / "data/catalog.json")
    retired = load_retired_sources(ROOT / "gold/retired-source-theorems.json")
    audit = load_selection_audit(ROOT / "gold/selection-audit.json")
    whole_problem_targets = load_whole_problem_targets(
        ROOT / "gold/whole-problem-targets.json"
    )
    selected = select_gold_declarations(
        catalog=catalog,
        retired=retired,
        selection_audit=audit,
        whole_problem_targets=whole_problem_targets,
    )
    assert len(selected) == DEFAULT_GOLD_POOL_SIZE
    assert not ({item.theorem for item in selected} & retired.theorems)
    assert not ({item.type_hash for item in selected} & retired.type_hashes)
    assert len({item.type_hash for item in selected}) == len(selected)
    assert all(item.category == "research open" for item in selected)
    assert all(item.classification.value == "DIRECT_PROP" for item in selected)
    assert all(not item.contains_sorry_in_type for item in selected)
    assert all(item.references for item in selected)
    assert sum(
        item.source_path.startswith(ERDOS_SOURCE_PREFIX)
        for item in selected
    ) >= MINIMUM_ERDOS_TASKS
    assert all(
        not item.source_path.startswith(EXCLUDED_SOURCE_PREFIXES)
        for item in selected
    )
    assert tuple(item.theorem for item in selected) == whole_problem_targets.theorems
    assert set(whole_problem_targets.theorems) <= set(audit.theorems)
    assert len({item.source_path for item in selected}) == len(selected)
    assert all(
        entry.problem_tracker_status in UNSOLVED_ERDOS_STATUSES
        for entry in audit.entries
    )
    grouping = load_task_grouping(ROOT / "gold/task-groups.json")
    groups = group_gold_declarations(selected, grouping)
    assert len(groups) == DEFAULT_GOLD_TASK_COUNT
    assert not grouping.groups
    assert all(len(group) == 1 for group in groups)


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
    whole_problem_targets = load_whole_problem_targets(
        ROOT / "gold/whole-problem-targets.json"
    )
    assert policy["schema_version"] == GOLD_POOL_SCHEMA_VERSION
    assert policy["pool_policy"]["mode"] == GOLD_TASK_MODE
    assert policy["pool_policy"]["synthetic_negation"] is False
    assert policy["pool_policy"]["source_theorem_count"] == DEFAULT_GOLD_POOL_SIZE
    assert policy["pool_policy"]["pool_size"] == DEFAULT_GOLD_TASK_COUNT
    assert policy["pool_policy"]["selection_audit_sha256"] == audit.sha256
    assert (
        policy["pool_policy"]["whole_problem_targets_sha256"]
        == whole_problem_targets.sha256
    )
    assert policy["pool_policy"]["minimum_erdos_tasks"] == MINIMUM_ERDOS_TASKS
    assert policy["pool_policy"]["task_scope"] == GOLD_POOL_TASK_SCOPE
    assert policy["pool_policy"]["multi_target_tasks"] == 0
    assert policy["pool_policy"]["one_task_per_source_path"] is True
    assert policy["pool_policy"]["excluded_source_prefixes"] == list(
        EXCLUDED_SOURCE_PREFIXES
    )
    assert len(registry.tasks) == DEFAULT_GOLD_TASK_COUNT
    assert len(task_directories) == DEFAULT_GOLD_TASK_COUNT
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
        assert all(source.references for source in bundle.sources)
        assert all(
            re.search(r"\[[^\]]+\]\(https?://[^)\s]+\)", reference)
            for source in bundle.sources
            for reference in source.references
        )
        challenge = (task_directory / "Challenge.lean").read_text(encoding="utf-8")
        assert all(
            f"theorem {name.rsplit('.', 1)[-1]} : fcTypeOfName%" in challenge
            for name in manifest.theorem_names
        )
        assert "theorem target : ¬" not in challenge
        assert not ({source.type_hash for source in bundle.sources} & source_types)
        assert bundle.sha256 not in task_hashes
        assert not manifest.source_path.startswith(EXCLUDED_SOURCE_PREFIXES)
        source_types.update(source.type_hash for source in bundle.sources)
        task_hashes.add(bundle.sha256)
    assert len(source_types) == DEFAULT_GOLD_POOL_SIZE


def test_gold_registry_rejects_non_deny_or_unknown_schema(tmp_path):
    source_rows = [
        {
            "index": index,
            "source_path": f"FormalConjectures/ErdosProblems/{index + 1}.lean",
            "source_type_sha256": f"sha256:{index + 1:064x}",
            "theorem": f"Fixture.test_{index + 1}",
        }
        for index in range(MINIMUM_ERDOS_TASKS)
    ]
    task_rows = [
        {
            "completion_policy": "all_of",
            "mode": "formalized",
            "source_indices": [source["index"]],
            "source_path": source["source_path"],
            "task_id": f"fc-test-{source['index'] + 1}-formalized-v1",
            "task_bundle_sha256": f"sha256:{source['index'] + 1001:064x}",
            "target_type_sha256s": [source["source_type_sha256"]],
            "theorems": [source["theorem"]],
        }
        for source in source_rows
    ]
    base = {
        "schema_version": GOLD_POOL_SCHEMA_VERSION,
        "default": "DENY",
        "repository_commit": "a" * 40,
        "audit_date_utc": "2026-07-23",
        "pool_policy": {
            "classification": "DIRECT_PROP",
            "compiled_target_validation": True,
            "exact_source_type": True,
            "excluded_source_prefixes": list(EXCLUDED_SOURCE_PREFIXES),
            "grouping": GOLD_POOL_GROUPING,
            "minimum_erdos_tasks": MINIMUM_ERDOS_TASKS,
            "mode": "formalized",
            "multi_target_tasks": 0,
            "one_task_per_source_path": True,
            "pool_size": MINIMUM_ERDOS_TASKS,
            "retired_source_theorems_sha256": "sha256:" + "d" * 64,
            "selection": GOLD_POOL_SELECTION,
            "selection_audit_sha256": "sha256:" + "e" * 64,
            "source_category": "research open",
            "source_theorem_count": MINIMUM_ERDOS_TASKS,
            "synthetic_negation": False,
            "task_scope": GOLD_POOL_TASK_SCOPE,
            "task_groups_sha256": "sha256:" + "f" * 64,
            "whole_problem_targets_sha256": "sha256:" + "1" * 64,
        },
        "allowed_source_theorems": source_rows,
        "allowed_task_bundles": task_rows,
    }
    valid = tmp_path / "valid.json"
    valid.write_text(json.dumps(base), encoding="utf-8")
    assert len(GoldTaskRegistry.load(valid).tasks) == MINIMUM_ERDOS_TASKS

    incorrect_multi_target_count = json.loads(json.dumps(base))
    incorrect_multi_target_count["pool_policy"]["multi_target_tasks"] = 1
    incorrect_count = tmp_path / "incorrect-multi-target-count.json"
    incorrect_count.write_text(
        json.dumps(incorrect_multi_target_count),
        encoding="utf-8",
    )
    with pytest.raises(TaskNotAllowed):
        GoldTaskRegistry.load(incorrect_count)

    for name, update in (
        ("schema", {"schema_version": 1}),
        ("boolean-schema", {"schema_version": True}),
        ("default", {"default": "ALLOW"}),
    ):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps({**base, **update}), encoding="utf-8")
        with pytest.raises(TaskNotAllowed):
            GoldTaskRegistry.load(path)
