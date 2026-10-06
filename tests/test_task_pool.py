from __future__ import annotations

import json
from pathlib import Path

import pytest

from verifier.catalog import load_catalog
from verifier.errors import VerifierError
from verifier.task_registry import TaskNotAllowed, TaskPoolRegistry
from verifier.repository import tasks_repository_root
from verifier.task_generator import problem_id
from verifier.task_pool import (
    DEFAULT_TASK_TIER,
    DEFAULT_TIER_SIZE,
    DEFAULT_TIER_TASK_COUNT,
    ERDOS_SOURCE_PREFIX,
    EXCLUDED_SOURCE_PREFIXES,
    EXTERNAL_SOLUTION_REASON_CODE,
    GREENS_OPEN_PROBLEMS_SOURCE_PREFIX,
    HOLD_REASON_CODES,
    MINIMUM_ERDOS_TASKS,
    OWNER_ACCEPTED_CLOSURE_REASON_CODE,
    POOL_STATUS_HELD,
    POOL_STATUS_RETIRED,
    SOURCE_FAMILY_STATUSES,
    TASK_POOL_GROUPING,
    TASK_POOL_SCHEMA_VERSION,
    TASK_POOL_SELECTION,
    TASK_POOL_TASK_SCOPE,
    REWARD_TARGET_POLICY,
    group_task_declarations,
    load_held_sources,
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


@pytest.mark.needs_checkouts
def test_classical_admissions_match_review_and_exclude_held_or_resolved_targets():
    review = json.loads((TIER_METADATA / "classical-status-audit.json").read_text())
    admitted = {row["theorem"] for row in review["candidates"]}
    assert len(admitted) == 36
    assert all(
        row["disposition"] == "retain_provisionally_open" for row in review["candidates"]
    )
    policy = json.loads((TASKS_ROOT / "allowlist.json").read_text())
    tasks = policy["allowed_task_bundles"]
    withdrawn = load_retired_sources(TIER_METADATA / "retired-source-theorems.json").theorems
    for theorem in admitted - set(withdrawn):
        paired = [row for row in tasks if theorem in row["theorems"]]
        assert len(paired) == 2
        assert {row["mode"] for row in paired} == set(PRODUCTION_TASK_MODES)
    excluded = {
        "Koethe.KotheConjecture",
        "EulerBrick.perfect_euler_brick_existence",
        "Irrational.irrational_catalanConstant",
        "ScholzConjecture.scholz_conjecture",
    }
    assert not excluded & {name for row in tasks for name in row["theorems"]}


@pytest.mark.needs_checkouts
def test_reinstated_erdos96_preserves_reward_identity_and_both_modes():
    theorem = "Erdos96.erdos_96"
    policy = json.loads((TASKS_ROOT / "allowlist.json").read_text())
    rows = [row for row in policy["allowed_task_bundles"] if theorem in row["theorems"]]
    assert {row["mode"] for row in rows} == set(PRODUCTION_TASK_MODES)
    assert len(rows) == 2
    assert {row["reward_target_id"] for row in rows} == {f"fc-target:{theorem}"}
    assert theorem not in load_retired_sources(
        TIER_METADATA / "retired-source-theorems.json"
    ).theorems
    assert f"fc-target:{theorem}" not in load_retired_conjectures(
        TIER_METADATA / "retired-conjectures.json"
    ).entries


@pytest.mark.needs_checkouts
def test_task_selection_is_new_and_audited_across_source_families():
    catalog = load_catalog(ROOT / "data/catalog.json")
    retired = load_retired_sources(TIER_METADATA / "retired-source-theorems.json")
    held = load_held_sources(TIER_METADATA / "held-source-theorems.json")
    audit = load_selection_audit(TIER_METADATA / "selection-audit.json")
    targets = load_task_targets(TIER_METADATA / "task-targets.json")
    selected = select_task_declarations(
        catalog=catalog,
        retired=retired,
        held=held,
        selection_audit=audit,
        task_targets=targets,
        pool_size=DEFAULT_TIER_SIZE,
    )
    assert len(selected) == DEFAULT_TIER_SIZE
    assert not ({item.theorem for item in selected} & retired.theorems)
    assert not ({item.type_hash for item in selected} & retired.type_hashes)
    assert not ({item.theorem for item in selected} & held.theorems)
    assert not ({item.type_hash for item in selected} & held.type_hashes)
    assert len({item.type_hash for item in selected}) == len(selected)
    assert all(item.category == "research open" for item in selected)
    assert all(item.classification.value == "DIRECT_PROP" for item in selected)
    assert all(not item.contains_sorry_in_type for item in selected)
    # Package-authored research targets cite their source in the declaration docstring.
    assert all(
        item.references
        or (
            item.source_path.startswith("FormalConjectures/ResearchTargets/")
            and "Source: https://" in (item.docstring or "")
        )
        for item in selected
    )
    assert sum(
        item.source_path.startswith(ERDOS_SOURCE_PREFIX)
        for item in selected
    ) == MINIMUM_ERDOS_TASKS
    assert sum(
        item.source_path.startswith(GREENS_OPEN_PROBLEMS_SOURCE_PREFIX)
        for item in selected
    ) == 6
    assert all(
        not item.source_path.startswith(EXCLUDED_SOURCE_PREFIXES)
        for item in selected
    )
    assert tuple(item.theorem for item in selected) == targets.theorems
    assert set(targets.theorems) <= set(audit.theorems)
    assert targets.task_scope == TASK_POOL_TASK_SCOPE
    assert len({item.source_path for item in selected}) == 226
    assert all(
        entry.source_status in SOURCE_FAMILY_STATUSES[entry.source_family]
        for entry in audit.entries
    )
    grouping = load_task_grouping(TIER_METADATA / "task-groups.json")
    groups = group_task_declarations(selected, grouping)
    assert len(groups) == DEFAULT_TIER_SIZE
    assert not grouping.groups
    assert all(len(group) == 1 for group in groups)


@pytest.mark.needs_checkouts
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
    assert tier_policy["held_source_theorems_sha256"] == load_held_sources(
        TIER_METADATA / "held-source-theorems.json"
    ).sha256
    assert tier_policy["minimum_erdos_tasks"] == MINIMUM_ERDOS_TASKS
    assert tier_policy["source_families"] == ["erdos", "greens-open-problems", "millennium", "research-targets", "wikipedia"]
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
        assert all(
            source.references
            or (
                source.source_path.startswith("FormalConjectures/ResearchTargets/")
                and "Source: https://" in (source.docstring or "")
            )
            for source in bundle.sources
        )
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


@pytest.mark.needs_checkouts
def test_newly_retired_targets_are_recorded_but_not_admitted():
    newly_retired = {
        # 2026-09-10: a public Lean counterexample settles the exact target.
        "Green44.green_44",
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
        # 2026-08-12: one mechanically ineligible formalization and one
        # conjecture solved in the literature before admission.
        "Erdos510.erdos_510",
        "Erdos1199.erdos_1199",
        # Production retirements that predated this pool expansion.
        "Erdos567.erdos_567.parts.i",
        "Green3.green_3",
        # 2026-08-19: targets withdrawn from the active pool by maintainer request.
        "Erdos477.erdos_477.variants.X_pow_three",
        "Erdos477.erdos_477.variants.monomial",
        "Erdos536.erdos_536",
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


# 2026-10-06: the exits recorded by the version 1 catalog audit and the release owner's decision.
OCTOBER_EXTERNAL_SOLUTIONS = {
    "Erdos252.erdos_252",
    "Erdos70.erdos_70.variants.omega_times_two_four",
    "Erdos701.erdos_701",
}
OCTOBER_OWNER_CLOSURES = {
    "Erdos3.erdos_3",
    "Erdos138.erdos_138",
    "Erdos142.erdos_142.variants.lower",
    "Erdos172.erdos_172",
    "Erdos184.erdos_184",
    "Erdos304.upper_bound",
    "Erdos371.erdos_371",
    "Erdos821.erdos_821",
    "Erdos952.erdos_952",
    "Erdos978.erdos_978.parts.ii",
    "Erdos978.erdos_978.parts.iii",
}
OCTOBER_HOLDS = {
    "Erdos1004.erdos_1004",
    "Erdos1074.erdos_1074.variants.EHSNumbers_one_half",
    "Erdos282.erdos_282",
    "Erdos564.erdos_564",
    "Erdos887.erdos_887.parts.ii",
}


@pytest.mark.needs_checkouts
def test_october_exits_leave_admission_and_their_old_tasks_stay_readable():
    retired_now = OCTOBER_EXTERNAL_SOLUTIONS | OCTOBER_OWNER_CLOSURES
    exits = retired_now | OCTOBER_HOLDS
    policy = json.loads((TASKS_ROOT / "allowlist.json").read_text(encoding="utf-8"))
    retired = load_retired_sources(TIER_METADATA / "retired-source-theorems.json")
    held = load_held_sources(TIER_METADATA / "held-source-theorems.json")
    targets = load_task_targets(TIER_METADATA / "task-targets.json")
    audit = load_selection_audit(TIER_METADATA / "selection-audit.json")
    display = load_retired_conjectures(TIER_METADATA / "retired-conjectures.json")
    retirement_log = (TIER_METADATA / "RETIREMENTS.md").read_text(encoding="utf-8")
    hold_log = (TIER_METADATA / "HOLDS.md").read_text(encoding="utf-8")

    assert len(exits) == 19
    assert retired_now <= retired.theorems
    # A hold is not a retirement: the held names are refused through their own list only.
    assert held.theorems == OCTOBER_HOLDS
    assert held.theorems.isdisjoint(retired.theorems)
    assert all(hold.reason_code in HOLD_REASON_CODES for hold in held.holds)
    assert all(hold.held_on == "2026-10-06" for hold in held.holds)
    # The canonical types the previous release published stay denied as well as the names.
    assert {
        "sha256:ddba7c9c27b5665f6cfd200e37ee4ad2f1fbe6e1312d346bce5a2e5344bdbb11",
        "sha256:ca0ae541b657e550f538d41a62a6ebd5d370b8cbde8971ccc3066b1e8ff687b8",
        "sha256:8212d9ec59d89064b56f940bf51a9af36699a95555f245f9a8360c26a3efbd1f",
    } <= retired.type_hashes
    assert {
        "sha256:c5ce7570c520daced747e3045c85bdd574afe40897cce40d5a5f7d7c67d90664",
        "sha256:c0a204ad0ab84a91a8d339a473e94935cedf04eeb35b57e802858d7479fbdb47",
        "sha256:cd135cd026b706b808d2bc82d9a0c68331f01bdd960c495dcf9d7b8c84e5fc3d",
        "sha256:9d83ba030098650ee699b48e716cdf5f446226673c02b02f5b9e6af5c7e5b092",
        "sha256:869e68472c87758ed735ec9905e981c1948591269bcdf19fd92574021681991e",
    } <= held.type_hashes
    assert exits.isdisjoint(targets.theorems)
    assert exits.isdisjoint(audit.theorems)
    assert exits.isdisjoint(row["theorem"] for row in policy["allowed_source_theorems"])
    assert all(exits.isdisjoint(row["theorems"]) for row in policy["allowed_task_bundles"])
    for theorem in OCTOBER_EXTERNAL_SOLUTIONS:
        assert f"`{theorem}` — 2026-10-06 — `{EXTERNAL_SOLUTION_REASON_CODE} (" in retirement_log
    for theorem in OCTOBER_OWNER_CLOSURES:
        assert (
            f"`{theorem}` — 2026-10-06 — `{OWNER_ACCEPTED_CLOSURE_REASON_CODE} (" in retirement_log
        )
    assert all(f"`{theorem}`" in hold_log for theorem in OCTOBER_HOLDS)
    assert all(f"`{theorem}`" not in retirement_log for theorem in OCTOBER_HOLDS)

    # The display payload keeps the tasks the previous release offered for every exit, labelled
    # with the exit's own status, at the source revision that published them.
    for theorem in exits:
        entry = display.entries[f"fc-target:{theorem}"]
        expected = POOL_STATUS_HELD if theorem in OCTOBER_HOLDS else POOL_STATUS_RETIRED
        assert entry["pool_status"] == expected, theorem
        assert entry["source"]["repository_commit"] == "6a786f997e18e8f095762a2830d191b7e25e505e"
        assert {task["task_mode"] for task in entry["tasks"]} == set(PRODUCTION_TASK_MODES)
        assert all(task["task_id"].startswith("fc-6a786f99-") for task in entry["tasks"])
    offered = {
        task["task_id"]
        for theorem in ("Erdos252.erdos_252", "Erdos701.erdos_701")
        for task in display.entries[f"fc-target:{theorem}"]["tasks"]
    }
    assert offered == {
        "fc-6a786f99-erdos252-erdos-252-f4e9f7503d-formalized-v1",
        "fc-6a786f99-erdos252-erdos-252-27e45e279f-counterexample-v1",
        "fc-6a786f99-erdos701-erdos-701-5af5e2048d-formalized-v1",
        "fc-6a786f99-erdos701-erdos-701-41f9e00a83-counterexample-v1",
    }


@pytest.mark.needs_checkouts
def test_only_the_erdos70_variant_is_denied_and_closed_history_stays_in_the_pool():
    """The external retirement of Erdős 70 is the `omega_times_two_four` variant alone."""
    policy = json.loads((TASKS_ROOT / "allowlist.json").read_text(encoding="utf-8"))
    admitted = {row["theorem"] for row in policy["allowed_source_theorems"]}
    retired = load_retired_sources(TIER_METADATA / "retired-source-theorems.json")

    assert "Erdos70.erdos_70.variants.omega_times_two_four" in retired.theorems
    assert "Erdos70.erdos_70" not in retired.theorems
    # Historical closures stay where they were, Erdős 416(i) and the pending Green 24 included.
    assert {
        "Erdos416.erdos_416.parts.i",
        "Erdos579.erdos_579",
        "Green24.variants.conjecture",
    } <= admitted


@pytest.mark.needs_checkouts
def test_retired_conjectures_are_readable_but_never_admissible():
    """The display payload must cover every retired target and admit none of them.

    This is the whole point of keeping it in a separate file: a retired conjecture stays
    readable on the website forever, while `allowed_task_bundles` — the only list the
    submission and verification paths consult — never grows a single entry for it.
    """
    retired = load_retired_conjectures(TIER_METADATA / "retired-conjectures.json")
    sources = load_retired_sources(TIER_METADATA / "retired-source-theorems.json")
    held = load_held_sources(TIER_METADATA / "held-source-theorems.json")
    policy = json.loads((TASKS_ROOT / "allowlist.json").read_text(encoding="utf-8"))

    assert retired.entries
    assert policy["tier_policies"][DEFAULT_TASK_TIER][
        "retired_conjectures_sha256"
    ] == retired.sha256

    theorems = {entry["theorem"] for entry in retired.entries.values()}
    # Everything on display genuinely left the pool, so a live target can never be shown as
    # closed, and each entry's status matches the admission list that refuses it.
    by_status = {
        status: {
            entry["theorem"]
            for entry in retired.entries.values()
            if entry["pool_status"] == status
        }
        for status in (POOL_STATUS_RETIRED, POOL_STATUS_HELD)
    }
    assert by_status[POOL_STATUS_RETIRED] | by_status[POOL_STATUS_HELD] == theorems
    assert by_status[POOL_STATUS_RETIRED] <= sources.theorems
    assert by_status[POOL_STATUS_HELD] == held.theorems
    assert theorems.isdisjoint(row["theorem"] for row in policy["allowed_source_theorems"])
    assert all(
        theorems.isdisjoint(row["theorems"]) for row in policy["allowed_task_bundles"]
    )

    assert retired.repository_commit == policy["repository_commit"]
    assert {entry["source"]["repository_commit"] for entry in retired.entries.values()} == {
        "379fc0298dc146df549e7061c3ede0353a5bb51f",
        "6a786f997e18e8f095762a2830d191b7e25e505e",
        "8432eac998110a563e03df65a28c117e97c8c142",
    }
    # Each entry carries what a problem page renders, for both attack directions.
    for entry in retired.entries.values():
        assert entry["source"]["theorem"] == entry["theorem"]
        # Historical statements retain their actual source pin across later repins.
        source_commit = entry["source"]["repository_commit"]
        assert len(source_commit) == 40
        assert all(character in "0123456789abcdef" for character in source_commit)
        assert {task["task_mode"] for task in entry["tasks"]} == set(PRODUCTION_TASK_MODES)
        assert all(task["challenge_lean"].strip() for task in entry["tasks"])


def _held_row(theorem: str = "Erdos564.erdos_564", **overrides) -> dict:
    row = {
        "audit_reference": "catalog audit v1, record fc-target:" + theorem,
        "finding": "the constant elaborates as Nat",
        "held_on": "2026-10-06",
        "reason_code": "HOLD_STATEMENT_SOURCE_DISCREPANCY",
        "required_resolution": "a corrected statement and a new admission audit",
        "reward_target_id": "fc-target:" + theorem,
        "source_path": "FormalConjectures/ErdosProblems/564.lean",
        "source_type_sha256s": ["sha256:" + "a" * 64, "sha256:" + "b" * 64],
        "theorem": theorem,
    }
    row.update(overrides)
    return row


def _write_held(tmp_path: Path, rows: list[dict], **overrides) -> Path:
    value = {"holds": rows, "repository_commit": "c" * 40, "schema_version": 1}
    value.update(overrides)
    path = tmp_path / "held-source-theorems.json"
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def test_the_hold_list_loads_names_and_every_recorded_type(tmp_path):
    held = load_held_sources(_write_held(tmp_path, [_held_row()]))

    assert held.theorems == {"Erdos564.erdos_564"}
    assert held.type_hashes == {"sha256:" + "a" * 64, "sha256:" + "b" * 64}
    assert held.holds[0].reason_code in HOLD_REASON_CODES


@pytest.mark.parametrize(
    "rows",
    [
        # A retirement reason is not a hold reason; a hold must not be recorded as solved.
        [_held_row(reason_code="SOLVED_EXTERNALLY")],
        [_held_row(reward_target_id="fc-target:Erdos1.erdos_1")],
        [_held_row(source_type_sha256s=[])],
        [_held_row(source_type_sha256s=["sha256:" + "b" * 64, "sha256:" + "a" * 64])],
        [_held_row(finding=" ")],
        [_held_row(held_on="06/10/2026")],
        [{**_held_row(), "pool_status": "held"}],
        [_held_row("Erdos887.erdos_887.parts.ii"), _held_row()],
    ],
    ids=[
        "retirement-reason",
        "foreign-reward-target",
        "no-types",
        "unsorted-types",
        "blank-finding",
        "bad-date",
        "extra-field",
        "unsorted-theorems",
    ],
)
def test_a_malformed_hold_list_is_refused(tmp_path, rows):
    with pytest.raises(VerifierError):
        load_held_sources(_write_held(tmp_path, rows))


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
                "held_source_theorems_sha256": "sha256:" + "c" * 64,
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


@pytest.mark.needs_checkouts
def test_green_withdrawal_preserves_solved_entries_and_closes_open_tasks():
    from submission_api.retired import RetiredIndex
    from submission_api.taskpool import TaskCatalog

    catalog = TaskCatalog.load(
        allowlist_path=TASKS_ROOT / "allowlist.json", pool_root=TASKS_ROOT / "pool"
    )
    solved = {
        "Green15.green_15", "Green24.variants.conjecture", "Green39.green_39",
        "Green40.green_40.f_two_eq_one", "Green47.green_47", "Green51.green_51.one_half",
    }
    assert {
        entry.source.theorem for entry in catalog.entries.values()
        if entry.source.source_path.startswith(GREENS_OPEN_PROBLEMS_SOURCE_PREFIX)
    } == solved
    retired = RetiredIndex.load(allowlist_path=TASKS_ROOT / "allowlist.json")
    withdrawn = [
        item for item in retired.by_slug.values()
        if item.retired_on == "2026-09-30" and item.reason_code == "WITHDRAWN"
        and item.source.source_path.startswith(GREENS_OPEN_PROBLEMS_SOURCE_PREFIX)
    ]
    assert len(withdrawn) == 18
    for item in withdrawn:
        assert len(item.tasks) == 2
        for task in item.tasks:
            with pytest.raises(TaskNotAllowed):
                catalog.get(task.task_id)
    assert {"green29-green-29", "green42-green-42"} <= retired.by_slug.keys()
