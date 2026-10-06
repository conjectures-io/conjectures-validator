from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from verifier import task_pool
from verifier.errors import VerifierError
from verifier.repository import tasks_repository_root
from verifier.research_targets import load_research_target_decisions
from verifier.task_generator import problem_id
from verifier.task_policy import COUNTEREXAMPLE_TASK_MODE, EXACT_TASK_MODE, PRODUCTION_TASK_MODES
from verifier.task_pool import (
    ACTIVATED_RESEARCH_TARGETS,
    EXCLUDED_SOURCE_PREFIXES,
    RESEARCH_TARGET_DECISIONS_LOCATOR,
    REWARD_TARGET_POLICY,
    SCREENING_STATEMENT,
    TASK_POOL_GROUPING,
    TASK_POOL_SCHEMA_VERSION,
    TASK_POOL_SELECTION,
    TASK_POOL_TASK_SCOPE,
    _valid_source_identity,
    load_selection_audit,
    source_family_from_path,
)
from verifier.task_registry import TaskNotAllowed, TaskPoolRegistry


ROOT = Path(__file__).resolve().parents[1]
MATH15 = "FormalConjectures/ResearchTargets/Math15.lean"
MATH30 = "FormalConjectures/ResearchTargets/Math30.lean"
THEOREM = "Math30Catalog.source16"
DEFINITIONS = {
    **{number: f"Math15.Ramsey.Target{number:02d}" for number in range(1, 4)},
    **{number: f"Math15.LonelyRunner.Target{number:02d}" for number in range(4, 7)},
    **{number: f"Math15.Frankl.Target{number:02d}" for number in range(7, 10)},
    **{number: f"Math15.Graceful.Target{number:02d}" for number in range(10, 13)},
    **{number: f"Math15.Collatz.Target{number:02d}" for number in range(13, 16)},
    **{number: f"Math30.MatrixBoolean.Statement{number}" for number in range(16, 22)},
    **{number: f"Math30.Problem{number}" for number in (22, 23, 24, 28, 29, 30)},
    **{number: f"Math30.Polya.Target{number}" for number in range(25, 28)},
}
ACCEPTED = {16, 18, 20, 21, 22, 23, 25, 26, 27, 28, 29, 30}


def test_research_target_sources_are_exactly_the_registered_catalogs():
    assert source_family_from_path(MATH15) == "research-targets"
    assert source_family_from_path(MATH30) == "research-targets"
    assert source_family_from_path("FormalConjectures/ResearchTargets/Math45.lean") is None
    assert source_family_from_path("FormalConjectures/ResearchTargets/1.lean") is None
    assert _valid_source_identity("research-targets", "Math15", MATH15, "Math15Catalog.source01")
    assert not _valid_source_identity("research-targets", "Math15", MATH15, "Math30Catalog.source16")
    assert not _valid_source_identity("research-targets", 15, MATH15, "Math15Catalog.source01")


def test_no_research_target_is_activated_in_this_release():
    assert ACTIVATED_RESEARCH_TARGETS == frozenset()
    assert task_pool.staged_research_target(THEOREM, MATH30)
    # The gate concerns this family only; an Erdős source is unaffected by it.
    assert not task_pool.staged_research_target(
        "Erdos1.erdos_1", "FormalConjectures/ErdosProblems/1.lean"
    )


def research_allowlist() -> dict:
    source = {
        "index": 0,
        "source_path": MATH30,
        "source_type_sha256": "sha256:" + "1" * 64,
        "theorem": THEOREM,
        "tier": "tier-2",
    }
    rows = [
        {
            "completion_policy": "all_of",
            "mode": mode,
            "problem_id": problem_id("a" * 40, (THEOREM,)),
            "reward_target_id": f"fc-target:{THEOREM}",
            "source_indices": [0],
            "source_path": MATH30,
            "target_type_sha256s": [
                source["source_type_sha256"] if mode == EXACT_TASK_MODE else "sha256:" + "2" * 64
            ],
            "task_bundle_sha256": "sha256:" + ("3" if mode == EXACT_TASK_MODE else "4") * 64,
            "task_id": f"fc-test-research-{mode}-v1",
            "theorems": [THEOREM],
            "tier": "tier-2",
        }
        for mode in PRODUCTION_TASK_MODES
    ]
    return {
        "allowed_source_theorems": [source],
        "allowed_task_bundles": rows,
        "audit_date_utc": "2026-10-06",
        "default": "DENY",
        "repository_commit": "a" * 40,
        "schema_version": TASK_POOL_SCHEMA_VERSION,
        "tier_order": ["tier-2"],
        "tier_policies": {
            "tier-2": {
                "classification": "DIRECT_PROP",
                "compiled_target_validation": True,
                "excluded_source_prefixes": list(EXCLUDED_SOURCE_PREFIXES),
                "grouping": TASK_POOL_GROUPING,
                "held_source_theorems_sha256": "sha256:" + "4" * 64,
                "minimum_erdos_tasks": 0,
                "modes": list(PRODUCTION_TASK_MODES),
                "multi_target_tasks": 0,
                "one_reward_per_problem": True,
                "one_reward_per_reward_target": True,
                "outcomes_per_problem": len(PRODUCTION_TASK_MODES),
                "pool_size": len(PRODUCTION_TASK_MODES),
                "retired_conjectures_sha256": "sha256:" + "5" * 64,
                "retired_source_theorems_sha256": "sha256:" + "6" * 64,
                "reward_target_count": 1,
                "reward_target_policy": REWARD_TARGET_POLICY,
                "selection": TASK_POOL_SELECTION,
                "selection_audit_sha256": "sha256:" + "7" * 64,
                "source_category": "research open",
                "source_families": ["research-targets"],
                "source_theorem_count": 1,
                "target_relations": {
                    COUNTEREXAMPLE_TASK_MODE: "logical-negation",
                    EXACT_TASK_MODE: "definitionally-equal",
                },
                "task_groups_sha256": "sha256:" + "8" * 64,
                "task_scope": TASK_POOL_TASK_SCOPE,
                "task_targets_sha256": "sha256:" + "9" * 64,
            }
        },
    }


def test_registry_refuses_a_staged_research_target_and_admits_only_after_activation(
    tmp_path, monkeypatch
):
    path = tmp_path / "allowlist.json"
    path.write_text(json.dumps(research_allowlist()), encoding="utf-8")
    with pytest.raises(TaskNotAllowed, match="staged research target"):
        TaskPoolRegistry.load(path)

    # Positive control: the allowlist is otherwise well formed, so the activation gate is the
    # only thing refusing it. Activation itself is a reviewed code change, simulated here.
    monkeypatch.setattr(task_pool, "ACTIVATED_RESEARCH_TARGETS", frozenset({THEOREM}))
    registry = TaskPoolRegistry.load(path)
    assert {task.reward_target_id for task in registry.tasks.values()} == {f"fc-target:{THEOREM}"}
    assert {task.mode for task in registry.tasks.values()} == set(PRODUCTION_TASK_MODES)


def test_registry_refuses_an_unregistered_research_catalog_even_when_activated(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(task_pool, "ACTIVATED_RESEARCH_TARGETS", frozenset({THEOREM}))
    value = research_allowlist()
    unregistered = "FormalConjectures/ResearchTargets/Math31.lean"
    value["allowed_source_theorems"][0]["source_path"] = unregistered
    for row in value["allowed_task_bundles"]:
        row["source_path"] = unregistered
    path = tmp_path / "allowlist.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(TaskNotAllowed):
        TaskPoolRegistry.load(path)


def test_selection_audit_refuses_a_staged_research_target(tmp_path, monkeypatch):
    audit = {
        "audit_date_utc": "2026-10-06",
        "github_open_pr_count": 1,
        "repository_commit": "a" * 40,
        "schema_version": 2,
        "screening_statement": SCREENING_STATEMENT,
        "selected": [
            {
                "active_resolution_prs": [],
                "feasibility_signals": ["compact-formal-target"],
                "open_prs_touching_source": [],
                "source_family": "research-targets",
                "source_path": MATH30,
                "source_problem_number": "Math30",
                "source_status": "open",
                "theorem": THEOREM,
                "upstream_status": "research open",
            }
        ],
        "source_main_commit": "b" * 40,
        "source_repository": "google-deepmind/formal-conjectures",
        "source_status_sources": [
            {
                "family": "research-targets",
                "locator": RESEARCH_TARGET_DECISIONS_LOCATOR,
                "revision": hashlib.sha256(b"decisions").hexdigest(),
            }
        ],
    }
    path = tmp_path / "selection-audit.json"
    path.write_text(json.dumps(audit), encoding="utf-8")
    with pytest.raises(VerifierError, match="staged research target"):
        load_selection_audit(path)
    monkeypatch.setattr(task_pool, "ACTIVATED_RESEARCH_TARGETS", frozenset({THEOREM}))
    assert load_selection_audit(path).theorems == (THEOREM,)


def decision_matrix() -> dict:
    gates = {
        number: ["source-cell-verification", "witness-policy-benchmark"] for number in (1, 2, 3)
    }
    gates.update({4: ["relabel-package-authored"], 10: ["novelty-review"]})
    gates.update({5: ["cite-hypothesis-justification"], 6: ["document-primitivity-repair"]})
    gates.update({number: ["correlated-reward-decision"] for number in (7, 8, 9)})
    gates.update({11: ["novelty-review"], 13: ["feasibility-benchmark", "novelty-review"]})
    gates.update({14: ["external-claim-review", "feasibility-benchmark", "novelty-review"]})
    gates.update({15: ["certificate-feasibility", "novelty-review"]})
    gates.update({17: ["literature-refresh"], 19: ["certificate-feasibility"]})
    gates.update({24: ["literature-baseline-correction"]})
    target_gate_ids = sorted({gate for values in gates.values() for gate in values})

    def gate(identifier: str) -> dict:
        return {"description": identifier, "evidence": None, "id": identifier, "status": "open"}

    targets = []
    for number in range(1, 31):
        package = "Math15" if number <= 15 else "Math30"
        theorem = f"{package}Catalog.source{number:02d}"
        correlated = number in (7, 8, 9)
        excluded = number == 12
        decision = (
            "excluded" if excluded else "source-review-accepted" if number in ACCEPTED else "hold"
        )
        targets.append(
            {
                "decision": decision,
                "definition": DEFINITIONS[number],
                "gates": gates.get(number, []),
                "implied_by": 10 if excluded else None,
                "number": number,
                "review_disposition": f"review of target {number}",
                "reward_group": "frankl-group" if correlated else None,
                "reward_policy": (
                    "excluded-implied-by"
                    if excluded
                    else "correlated-group" if correlated else "independent"
                ),
                "reward_target_id": f"fc-target:{theorem}",
                "source_path": MATH15 if number <= 15 else MATH30,
                "source_type_sha256": None,
                "theorem": theorem,
                "title": f"target {number}",
            }
        )
    return {
        "activation": "none",
        "default": "DENY",
        "global_gates": [gate("activation-code-review"), gate("toolchain-migration-validated")],
        "review_archive_sha256": "sha256:" + "c" * 64,
        "reward_groups": [
            {
                "id": "frankl-group",
                "members": [7, 8, 9],
                "policy": "hold-until-explicit-decision",
                "rationale": "shared antecedent",
            }
        ],
        "reward_target_policy": REWARD_TARGET_POLICY,
        "schema_version": 1,
        "source_family": "research-targets",
        "target_gates": [gate(identifier) for identifier in target_gate_ids],
        "targets": targets,
    }


def write(tmp_path: Path, value: dict) -> Path:
    path = tmp_path / "decision-matrix.json"
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def test_decision_matrix_records_holds_without_activating_anything(tmp_path):
    decisions = load_research_target_decisions(write(tmp_path, decision_matrix()))
    assert decisions.admissible == frozenset()
    assert decisions.open_global_gates == (
        "activation-code-review",
        "toolchain-migration-validated",
    )
    by_decision: dict[str, set[int]] = {}
    for target in decisions.targets:
        by_decision.setdefault(target.decision, set()).add(target.number)
    assert by_decision["excluded"] == {12}
    assert by_decision["source-review-accepted"] == ACCEPTED
    assert {target.number for target in decisions.targets if target.reward_group} == {7, 8, 9}


def mutate(value: dict, number: int, **fields) -> dict:
    copy = json.loads(json.dumps(value))
    copy["targets"][number - 1].update(fields)
    return copy


@pytest.mark.parametrize(
    "change",
    [
        lambda value: {**value, "activation": "selected"},
        lambda value: {**value, "default": "ALLOW"},
        lambda value: {**value, "targets": value["targets"][:-1]},
        # Closing every global gate is activation, which version 1 cannot express.
        lambda value: {
            **value,
            "global_gates": [
                dict(gate, status="closed", evidence="log") for gate in value["global_gates"]
            ],
        },
        # A closed gate must cite evidence.
        lambda value: {
            **value,
            "global_gates": [
                dict(value["global_gates"][0], status="closed"),
                value["global_gates"][1],
            ],
        },
        # Target 12 may not be quietly turned into an independent reward.
        lambda value: mutate(
            value,
            12,
            decision="source-review-accepted",
            reward_policy="independent",
            implied_by=None,
        ),
        # Nor may target 7 leave the correlated hold group.
        lambda value: mutate(
            value,
            7,
            decision="hold",
            reward_policy="independent",
            reward_group=None,
            gates=["correlated-reward-decision"],
        ),
        lambda value: mutate(value, 16, reward_target_id="fc-target:Math30Catalog.source17"),
        lambda value: mutate(value, 16, source_path=MATH15),
        lambda value: mutate(value, 16, theorem="Math30Catalog.target16"),
        lambda value: mutate(value, 16, source_type_sha256="sha256:abc"),
        lambda value: mutate(value, 16, gates=["novelty-review"]),
        lambda value: mutate(value, 1, gates=["witness-policy-benchmark", "novelty-review"]),
        lambda value: mutate(value, 1, gates=["unknown-gate"]),
        lambda value: mutate(value, 1, gates=[], decision="hold"),
    ],
)
def test_decision_matrix_fails_closed_on_invalid_or_activating_edits(tmp_path, change):
    with pytest.raises(VerifierError):
        load_research_target_decisions(write(tmp_path, change(decision_matrix())))


def test_decision_matrix_rejects_duplicate_type_hashes_and_keys(tmp_path):
    value = decision_matrix()
    for number in (16, 18):
        value["targets"][number - 1]["source_type_sha256"] = "sha256:" + "d" * 64
    with pytest.raises(VerifierError):
        load_research_target_decisions(write(tmp_path, value))
    path = tmp_path / "duplicate.json"
    path.write_text('{"schema_version": 1, "schema_version": 1}', encoding="utf-8")
    with pytest.raises(VerifierError):
        load_research_target_decisions(path)


def test_decision_matrix_cannot_coexist_with_an_activated_target(tmp_path, monkeypatch):
    path = write(tmp_path, decision_matrix())
    monkeypatch.setattr(task_pool, "ACTIVATED_RESEARCH_TARGETS", frozenset({THEOREM}))
    with pytest.raises(VerifierError, match="activated"):
        load_research_target_decisions(path)


@pytest.mark.needs_checkouts
def test_staged_decision_matrix_is_inactive_and_absent_from_the_pool():
    tasks_root = tasks_repository_root(ROOT)
    decisions = load_research_target_decisions(
        tasks_root / "staged/research-targets/decision-matrix.json"
    )
    assert decisions.admissible == frozenset()
    assert decisions.open_global_gates
    assert {target.number for target in decisions.targets if target.decision == "excluded"} == {12}
    assert {
        target.number
        for target in decisions.targets
        if target.decision == "source-review-accepted"
    } == ACCEPTED
    policy = json.loads((tasks_root / "allowlist.json").read_text(encoding="utf-8"))
    staged = {target.theorem for target in decisions.targets}
    assert staged.isdisjoint(row["theorem"] for row in policy["allowed_source_theorems"])
    assert all(
        "research-targets" not in tier["source_families"]
        for tier in policy["tier_policies"].values()
    )
