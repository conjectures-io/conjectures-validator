from __future__ import annotations

import json
from pathlib import Path

import pytest

from verifier.catalog import load_catalog
from verifier.task_registry import TaskNotAllowed, TaskPoolRegistry
from verifier.repository import tasks_repository_root
from verifier.task_generator import problem_id
from verifier.task_pool import (
    DEFAULT_TASK_TIER,
    DEFAULT_TIER_SIZE,
    DEFAULT_TIER_TASK_COUNT,
    ERDOS_SOURCE_PREFIX,
    EXCLUDED_SOURCE_PREFIXES,
    GREENS_OPEN_PROBLEMS_SOURCE_PREFIX,
    MINIMUM_ERDOS_TASKS,
    SOURCE_FAMILY_STATUSES,
    TASK_POOL_GROUPING,
    TASK_POOL_SCHEMA_VERSION,
    TASK_POOL_SELECTION,
    TASK_POOL_TASK_SCOPE,
    REWARD_TARGET_POLICY,
    group_task_declarations,
    load_retired_conjectures,
    load_retired_sources,
    load_selection_audit,
    load_task_grouping,
    load_task_targets,
    select_task_declarations,
)
from verifier.task_loader import load_task_bundle
from verifier.task_policy import (
    COUNTEREXAMPLE_TASK_MODE,
    EXACT_TASK_MODE,
    PRODUCTION_TASK_MODES,
)


ROOT = Path(__file__).resolve().parents[1]
TASKS_ROOT = tasks_repository_root(ROOT)


TIER_METADATA = TASKS_ROOT / "tiers/tier-1"


def test_task_selection_is_new_and_audited_across_source_families():
    catalog = load_catalog(ROOT / "data/catalog.json")
    retired = load_retired_sources(TIER_METADATA / "retired-source-theorems.json")
    audit = load_selection_audit(TIER_METADATA / "selection-audit.json")
    targets = load_task_targets(TIER_METADATA / "task-targets.json")
    selected = select_task_declarations(
        catalog=catalog,
        retired=retired,
        selection_audit=audit,
        task_targets=targets,
        pool_size=DEFAULT_TIER_SIZE,
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
    ) == MINIMUM_ERDOS_TASKS
    assert sum(
        item.source_path.startswith(GREENS_OPEN_PROBLEMS_SOURCE_PREFIX)
        for item in selected
    ) == 18
    assert all(
        not item.source_path.startswith(EXCLUDED_SOURCE_PREFIXES)
        for item in selected
    )
    assert tuple(item.theorem for item in selected) == targets.theorems
    assert set(targets.theorems) <= set(audit.theorems)
    assert targets.task_scope == TASK_POOL_TASK_SCOPE
    assert len({item.source_path for item in selected}) == 115
    assert all(
        entry.source_status in SOURCE_FAMILY_STATUSES[entry.source_family]
        for entry in audit.entries
    )
    grouping = load_task_grouping(TIER_METADATA / "task-groups.json")
    groups = group_task_declarations(selected, grouping)
    assert len(groups) == DEFAULT_TIER_SIZE
    assert not grouping.groups
    assert all(len(group) == 1 for group in groups)


def test_checked_in_task_pool_is_paired_single_tier_and_allowlisted():
    allowlist = TASKS_ROOT / "allowlist.json"
    policy = json.loads(allowlist.read_text(encoding="utf-8"))
    registry = TaskPoolRegistry.load(allowlist)
    task_directories = tuple(
        sorted(
            path
            for path in (TASKS_ROOT / "pool" / DEFAULT_TASK_TIER).iterdir()
            if path.is_dir()
        )
    )

    assert policy["schema_version"] == TASK_POOL_SCHEMA_VERSION
    assert policy["tier_order"] == [DEFAULT_TASK_TIER]
    tier_policy = policy["tier_policies"][DEFAULT_TASK_TIER]
    audit = load_selection_audit(TIER_METADATA / "selection-audit.json")
    targets = load_task_targets(TIER_METADATA / "task-targets.json")
    assert tier_policy["modes"] == list(PRODUCTION_TASK_MODES)
    assert tier_policy["target_relations"] == {
        COUNTEREXAMPLE_TASK_MODE: "logical-negation",
        EXACT_TASK_MODE: "definitionally-equal",
    }
    assert tier_policy["outcomes_per_problem"] == len(PRODUCTION_TASK_MODES)
    assert tier_policy["one_reward_per_problem"] is True
    assert tier_policy["one_reward_per_reward_target"] is True
    assert tier_policy["reward_target_policy"] == REWARD_TARGET_POLICY
    assert tier_policy["reward_target_count"] == DEFAULT_TIER_SIZE
    assert tier_policy["source_theorem_count"] == DEFAULT_TIER_SIZE
    assert tier_policy["pool_size"] == DEFAULT_TIER_TASK_COUNT
    assert tier_policy["selection"] == TASK_POOL_SELECTION
    assert tier_policy["selection_audit_sha256"] == audit.sha256
    assert tier_policy["task_targets_sha256"] == targets.sha256
    assert tier_policy["minimum_erdos_tasks"] == MINIMUM_ERDOS_TASKS
    assert tier_policy["source_families"] == ["erdos", "greens-open-problems"]
    assert tier_policy["task_scope"] == TASK_POOL_TASK_SCOPE
    assert tier_policy["multi_target_tasks"] == 0
    assert tier_policy["excluded_source_prefixes"] == list(EXCLUDED_SOURCE_PREFIXES)
    assert len(registry.tasks) == DEFAULT_TIER_TASK_COUNT
    assert len(registry.tasks_for_tier(DEFAULT_TASK_TIER)) == DEFAULT_TIER_TASK_COUNT
    assert len(task_directories) == DEFAULT_TIER_TASK_COUNT
    assert (TASKS_ROOT / "pool").stat().st_mode & 0o005 == 0o005
    assert allowlist.stat().st_mode & 0o004 == 0o004
    assert {row["tier"] for row in policy["allowed_source_theorems"]} == {
        DEFAULT_TASK_TIER
    }

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
            reference.strip()
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
    reward_targets = {task.reward_target_id for task in registry.tasks.values()}
    assert len(reward_targets) == DEFAULT_TIER_SIZE
    assert len(
        registry.tasks_for_reward_target("fc-target:Erdos340.erdos_340")
    ) == len(PRODUCTION_TASK_MODES)
    assert len(
        registry.tasks_for_reward_target(
            "fc-target:Erdos340.erdos_340.variants.sub_hasPosDensity"
        )
    ) == len(PRODUCTION_TASK_MODES)


def test_newly_retired_targets_are_recorded_but_not_admitted():
    newly_retired = {
        # 2026-08-05: defective or exploitable formalizations found by audit.
        "Erdos1055.erdos_1055.variants.erdos_limit",
        "Erdos1055.erdos_1055.variants.selfridge_limit",
        "Erdos1093.erdos_1093.parts.ii",
        "Erdos15.erdos_15",
        "Green54.green_54",
        "Green77.green_77",
        # 2026-08-06: targets a verified submission settled; see the task
        # repository's RETIREMENTS.md for the reason attached to each.
        "Erdos10.erdos_10.variants.grechuk",
        "Erdos939.erdos_939",
        "Green29.green_29",
        "Green42.green_42",
    }
    policy = json.loads((TASKS_ROOT / "allowlist.json").read_text(encoding="utf-8"))
    retired = load_retired_sources(TIER_METADATA / "retired-source-theorems.json")
    targets = load_task_targets(TIER_METADATA / "task-targets.json")
    retirement_log = (TIER_METADATA / "RETIREMENTS.md").read_text(encoding="utf-8")

    assert newly_retired <= retired.theorems
    assert newly_retired.isdisjoint(targets.theorems)
    assert newly_retired.isdisjoint(
        row["theorem"] for row in policy["allowed_source_theorems"]
    )
    assert all(
        newly_retired.isdisjoint(row["theorems"])
        for row in policy["allowed_task_bundles"]
    )
    assert all(f"`{theorem}`" in retirement_log for theorem in newly_retired)


def test_retired_conjectures_are_readable_but_never_admissible():
    """The display payload must cover every retired target and admit none of them.

    This is the whole point of keeping it in a separate file: a retired conjecture stays
    readable on the website forever, while `allowed_task_bundles` — the only list the
    submission and verification paths consult — never grows a single entry for it.
    """
    retired = load_retired_conjectures(TIER_METADATA / "retired-conjectures.json")
    sources = load_retired_sources(TIER_METADATA / "retired-source-theorems.json")
    policy = json.loads((TASKS_ROOT / "allowlist.json").read_text(encoding="utf-8"))

    assert retired.entries
    assert policy["tier_policies"][DEFAULT_TASK_TIER][
        "retired_conjectures_sha256"
    ] == retired.sha256

    theorems = {entry["theorem"] for entry in retired.entries.values()}
    # Everything on display is genuinely retired, so a live target can never be shown as closed.
    assert theorems <= sources.theorems
    assert theorems.isdisjoint(row["theorem"] for row in policy["allowed_source_theorems"])
    assert all(
        theorems.isdisjoint(row["theorems"]) for row in policy["allowed_task_bundles"]
    )

    # Each entry carries what a problem page renders, for both attack directions.
    for entry in retired.entries.values():
        assert entry["source"]["theorem"] == entry["theorem"]
        assert entry["source"]["repository_commit"] == retired.repository_commit
        assert {task["task_mode"] for task in entry["tasks"]} == set(PRODUCTION_TASK_MODES)
        assert all(task["challenge_lean"].strip() for task in entry["tasks"])


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
                    "reward_target_id": f"fc-target:{source['theorem']}",
                    "source_indices": [source["index"]],
                    "source_path": source["source_path"],
                    "task_id": f"fc-test-{source['index'] + 1}-{mode}-v1",
                    "task_bundle_sha256": (
                        f"sha256:{source['index'] + 1001 + mode_index * 1000:064x}"
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
                "one_reward_per_reward_target": True,
                "pool_size": MINIMUM_ERDOS_TASKS * len(PRODUCTION_TASK_MODES),
                "reward_target_count": MINIMUM_ERDOS_TASKS,
                "reward_target_policy": REWARD_TARGET_POLICY,
                "retired_conjectures_sha256": "sha256:" + "e" * 64,
                "retired_source_theorems_sha256": "sha256:" + "d" * 64,
                "selection": TASK_POOL_SELECTION,
                "selection_audit_sha256": "sha256:" + "e" * 64,
                "source_category": "research open",
                "source_families": ["erdos"],
                "source_theorem_count": MINIMUM_ERDOS_TASKS,
                "task_scope": TASK_POOL_TASK_SCOPE,
                "target_relations": {
                    COUNTEREXAMPLE_TASK_MODE: "logical-negation",
                    EXACT_TASK_MODE: "definitionally-equal",
                },
                "task_groups_sha256": "sha256:" + "f" * 64,
                "outcomes_per_problem": len(PRODUCTION_TASK_MODES),
                "task_targets_sha256": "sha256:" + "1" * 64,
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

    unapproved_source_family = json.loads(json.dumps(base))
    unapproved_source_family["allowed_source_theorems"][0]["source_path"] = (
        "FormalConjectures/GreensOpenProblems/3.lean"
    )
    unapproved_path = tmp_path / "unapproved-source-family.json"
    unapproved_path.write_text(
        json.dumps(unapproved_source_family), encoding="utf-8"
    )
    with pytest.raises(TaskNotAllowed):
        TaskPoolRegistry.load(unapproved_path)

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
