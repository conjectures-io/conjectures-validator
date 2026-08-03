from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from verifier.catalog import load_catalog
from verifier.task_registry import TaskNotAllowed, TaskPoolRegistry
from verifier.task_generator import problem_id
from verifier.task_pool import (
    DEFAULT_TASK_TIER,
    DEFAULT_TIER_SIZE,
    DEFAULT_TIER_TASK_COUNT,
    ERDOS_SOURCE_PREFIX,
    EXCLUDED_SOURCE_PREFIXES,
    MINIMUM_ERDOS_TASKS,
    TASK_POOL_GROUPING,
    TASK_POOL_SCHEMA_VERSION,
    TASK_POOL_SELECTION,
    TASK_POOL_TASK_SCOPE,
    UNSOLVED_ERDOS_STATUSES,
    group_task_declarations,
    load_retired_sources,
    load_selection_audit,
    load_task_grouping,
    load_whole_problem_targets,
    select_task_declarations,
)
from verifier.task_loader import load_task_bundle
from verifier.task_policy import (
    COUNTEREXAMPLE_TASK_MODE,
    EXACT_TASK_MODE,
    PRODUCTION_TASK_MODES,
)


ROOT = Path(__file__).resolve().parents[1]


TIER_METADATA = ROOT / "task_pool/tiers/tier-1"


def test_task_selection_is_new_and_audited_erdos_only():
    catalog = load_catalog(ROOT / "data/catalog.json")
    retired = load_retired_sources(TIER_METADATA / "retired-source-theorems.json")
    audit = load_selection_audit(TIER_METADATA / "selection-audit.json")
    whole_problem_targets = load_whole_problem_targets(
        TIER_METADATA / "whole-problem-targets.json"
    )
    selected = select_task_declarations(
        catalog=catalog,
        retired=retired,
        selection_audit=audit,
        whole_problem_targets=whole_problem_targets,
    )
    assert len(selected) == DEFAULT_TIER_SIZE
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
    grouping = load_task_grouping(TIER_METADATA / "task-groups.json")
    groups = group_task_declarations(selected, grouping)
    assert len(groups) == DEFAULT_TIER_SIZE
    assert not grouping.groups
    assert all(len(group) == 1 for group in groups)


def test_checked_in_task_pool_is_paired_tiered_and_allowlisted():
    allowlist = ROOT / "task_pool/allowlist.json"
    policy = json.loads(allowlist.read_text(encoding="utf-8"))
    registry = TaskPoolRegistry.load(allowlist)
    task_directories = tuple(
        sorted(
            path
            for path in (ROOT / "tasks/pool/tier-1").iterdir()
            if path.is_dir()
        )
    )

    audit = load_selection_audit(TIER_METADATA / "selection-audit.json")
    whole_problem_targets = load_whole_problem_targets(
        TIER_METADATA / "whole-problem-targets.json"
    )
    tier_policy = policy["tier_policies"][DEFAULT_TASK_TIER]
    assert policy["schema_version"] == TASK_POOL_SCHEMA_VERSION
    assert policy["tier_order"] == [DEFAULT_TASK_TIER]
    assert tier_policy["modes"] == list(PRODUCTION_TASK_MODES)
    assert tier_policy["target_relations"] == {
        COUNTEREXAMPLE_TASK_MODE: "logical-negation",
        EXACT_TASK_MODE: "definitionally-equal",
    }
    assert tier_policy["outcomes_per_problem"] == len(PRODUCTION_TASK_MODES)
    assert tier_policy["one_reward_per_problem"] is True
    assert tier_policy["source_theorem_count"] == DEFAULT_TIER_SIZE
    assert tier_policy["pool_size"] == DEFAULT_TIER_TASK_COUNT
    assert tier_policy["selection_audit_sha256"] == audit.sha256
    assert (
        tier_policy["whole_problem_targets_sha256"]
        == whole_problem_targets.sha256
    )
    assert tier_policy["minimum_erdos_tasks"] == MINIMUM_ERDOS_TASKS
    assert tier_policy["task_scope"] == TASK_POOL_TASK_SCOPE
    assert tier_policy["multi_target_tasks"] == 0
    assert tier_policy["excluded_source_prefixes"] == list(EXCLUDED_SOURCE_PREFIXES)
    assert len(registry.tasks) == DEFAULT_TIER_TASK_COUNT
    assert len(registry.tasks_for_tier(DEFAULT_TASK_TIER)) == DEFAULT_TIER_TASK_COUNT
    assert len(task_directories) == DEFAULT_TIER_TASK_COUNT
    assert (ROOT / "task_pool").stat().st_mode & 0o005 == 0o005
    assert allowlist.stat().st_mode & 0o004 == 0o004
    assert all(
        row["tier"] == DEFAULT_TASK_TIER
        for row in policy["allowed_source_theorems"]
    )
    assert all(
        row["tier"] == DEFAULT_TASK_TIER
        for row in policy["allowed_task_bundles"]
    )

    source_occurrences = {}
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
        assert registry.assert_bundle(bundle).task_id == manifest.task_id
        assert manifest.task_mode in PRODUCTION_TASK_MODES
        assert manifest.production_eligible
        assert manifest.classification.value == "DIRECT_PROP"
        if manifest.task_mode == EXACT_TASK_MODE:
            assert manifest.source_type_hash == manifest.generated_target_type_hash
        else:
            assert manifest.source_type_hash != manifest.generated_target_type_hash
        assert all(source.references for source in bundle.sources)
        assert all(
            re.search(r"\[[^\]]+\]\(https?://[^)\s]+\)", reference)
            for source in bundle.sources
            for reference in source.references
        )
        challenge = (task_directory / "Challenge.lean").read_text(encoding="utf-8")
        if manifest.task_mode == EXACT_TASK_MODE:
            assert all(
                f"theorem {name.rsplit('.', 1)[-1]} : fcTypeOfName%" in challenge
                for name in manifest.theorem_names
            )
            assert "theorem target : ¬" not in challenge
        else:
            assert 'theorem target : ¬ (fcTypeOfName%' in challenge
        assert bundle.sha256 not in task_hashes
        assert not manifest.source_path.startswith(EXCLUDED_SOURCE_PREFIXES)
        source_occurrences.setdefault(manifest.source_theorem, set()).add(
            manifest.task_mode
        )
        task_hashes.add(bundle.sha256)
    assert len(source_occurrences) == DEFAULT_TIER_SIZE
    assert all(modes == set(PRODUCTION_TASK_MODES) for modes in source_occurrences.values())
    problem_modes = {}
    for task in registry.tasks.values():
        problem_modes.setdefault(task.problem_id, set()).add(task.mode)
    assert len(problem_modes) == DEFAULT_TIER_SIZE
    assert all(modes == set(PRODUCTION_TASK_MODES) for modes in problem_modes.values())


def test_task_registry_rejects_non_deny_unknown_schema_or_tier_mismatch(tmp_path):
    source_rows = [
        {
            "index": index,
            "source_path": f"FormalConjectures/ErdosProblems/{index + 1}.lean",
            "source_type_sha256": f"sha256:{index + 1:064x}",
            "theorem": f"Fixture.test_{index + 1}",
            "tier": DEFAULT_TASK_TIER,
        }
        for index in range(MINIMUM_ERDOS_TASKS)
    ]
    task_rows = []
    for source in source_rows:
        for mode_index, mode in enumerate(PRODUCTION_TASK_MODES):
            target_hash = (
                source["source_type_sha256"]
                if mode == EXACT_TASK_MODE
                else f"sha256:{source['index'] + 2001:064x}"
            )
            task_rows.append(
                {
                    "completion_policy": "all_of",
                    "mode": mode,
                    "problem_id": problem_id("a" * 40, (source["theorem"],)),
                    "source_indices": [source["index"]],
                    "source_path": source["source_path"],
                    "task_id": f"fc-test-{source['index'] + 1}-{mode}-v1",
                    "task_bundle_sha256": (
                        f"sha256:{source['index'] + 1001 + mode_index * 100:064x}"
                    ),
                    "target_type_sha256s": [target_hash],
                    "theorems": [source["theorem"]],
                    "tier": DEFAULT_TASK_TIER,
                }
            )
    base = {
        "schema_version": TASK_POOL_SCHEMA_VERSION,
        "default": "DENY",
        "repository_commit": "a" * 40,
        "audit_date_utc": "2026-07-23",
        "tier_order": [DEFAULT_TASK_TIER],
        "tier_policies": {
            DEFAULT_TASK_TIER: {
                "classification": "DIRECT_PROP",
                "compiled_target_validation": True,
                "excluded_source_prefixes": list(EXCLUDED_SOURCE_PREFIXES),
                "grouping": TASK_POOL_GROUPING,
                "minimum_erdos_tasks": MINIMUM_ERDOS_TASKS,
                "modes": list(PRODUCTION_TASK_MODES),
                "multi_target_tasks": 0,
                "one_reward_per_problem": True,
                "pool_size": MINIMUM_ERDOS_TASKS * len(PRODUCTION_TASK_MODES),
                "retired_source_theorems_sha256": "sha256:" + "d" * 64,
                "selection": TASK_POOL_SELECTION,
                "selection_audit_sha256": "sha256:" + "e" * 64,
                "source_category": "research open",
                "source_theorem_count": MINIMUM_ERDOS_TASKS,
                "task_scope": TASK_POOL_TASK_SCOPE,
                "target_relations": {
                    COUNTEREXAMPLE_TASK_MODE: "logical-negation",
                    EXACT_TASK_MODE: "definitionally-equal",
                },
                "task_groups_sha256": "sha256:" + "f" * 64,
                "outcomes_per_problem": len(PRODUCTION_TASK_MODES),
                "whole_problem_targets_sha256": "sha256:" + "1" * 64,
            }
        },
        "allowed_source_theorems": source_rows,
        "allowed_task_bundles": task_rows,
    }
    valid = tmp_path / "valid.json"
    valid.write_text(json.dumps(base), encoding="utf-8")
    registry = TaskPoolRegistry.load(valid)
    assert len(registry.tasks) == MINIMUM_ERDOS_TASKS * len(
        PRODUCTION_TASK_MODES
    )
    first_problem = task_rows[0]["problem_id"]
    assert {task.mode for task in registry.tasks_for_problem(first_problem)} == set(
        PRODUCTION_TASK_MODES
    )

    incorrect_multi_target_count = json.loads(json.dumps(base))
    incorrect_multi_target_count["tier_policies"][DEFAULT_TASK_TIER][
        "multi_target_tasks"
    ] = 1
    incorrect_count = tmp_path / "incorrect-multi-target-count.json"
    incorrect_count.write_text(
        json.dumps(incorrect_multi_target_count),
        encoding="utf-8",
    )
    with pytest.raises(TaskNotAllowed):
        TaskPoolRegistry.load(incorrect_count)

    missing_counterexample = json.loads(json.dumps(base))
    missing_counterexample["allowed_task_bundles"].pop()
    missing_path = tmp_path / "missing-counterexample.json"
    missing_path.write_text(json.dumps(missing_counterexample), encoding="utf-8")
    with pytest.raises(TaskNotAllowed):
        TaskPoolRegistry.load(missing_path)

    forged_relation = json.loads(json.dumps(base))
    forged = next(
        row
        for row in forged_relation["allowed_task_bundles"]
        if row["mode"] == COUNTEREXAMPLE_TASK_MODE
    )
    source = source_rows[forged["source_indices"][0]]
    forged["target_type_sha256s"] = [source["source_type_sha256"]]
    forged_path = tmp_path / "forged-counterexample-relation.json"
    forged_path.write_text(json.dumps(forged_relation), encoding="utf-8")
    with pytest.raises(TaskNotAllowed):
        TaskPoolRegistry.load(forged_path)

    mismatched_tier = json.loads(json.dumps(base))
    mismatched_tier["allowed_task_bundles"][0]["tier"] = "tier-2"
    mismatch = tmp_path / "mismatched-tier.json"
    mismatch.write_text(json.dumps(mismatched_tier), encoding="utf-8")
    with pytest.raises(TaskNotAllowed):
        TaskPoolRegistry.load(mismatch)

    for name, update in (
        ("schema", {"schema_version": 1}),
        ("boolean-schema", {"schema_version": True}),
        ("default", {"default": "ALLOW"}),
    ):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps({**base, **update}), encoding="utf-8")
        with pytest.raises(TaskNotAllowed):
            TaskPoolRegistry.load(path)
