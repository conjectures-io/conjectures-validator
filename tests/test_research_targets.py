from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from verifier import task_pool
from verifier.errors import VerifierError
from verifier.repository import tasks_repository_root
from verifier.research_targets import (
    PENDING_OWNER_APPROVAL,
    PENDING_RELEASE_GATE,
    RELEASE_APPROVED,
    RELEASE_PROPOSED,
    load_research_target_decisions,
)
from verifier.task_generator import problem_id
from verifier.task_policy import (
    COUNTEREXAMPLE_TASK_MODE,
    EXACT_TASK_MODE,
    PRODUCTION_TASK_MODES,
)
from verifier.task_pool import (
    ACTIVATED_RESEARCH_TARGETS,
    DEFAULT_TIER_SIZE,
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
# Held in every activation profile, so it exercises the staged path.
THEOREM = "Math30Catalog.source17"
# Activated in every activation profile.
ACTIVE = "Math30Catalog.source16"
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
# The reviewed activation subset (release-candidate/activation-preparation): the twelve
# source-review-accepted targets, plus the first-batch targets whose gates closed at release time
# (4, 5, 6, 10, 11 in the proposed-17 profile).
CORE = ACCEPTED
FIRST_BATCH_READY = {4, 5, 6, 10, 11}
NEVER_ACTIVATED = {1, 2, 3, 7, 8, 9, 12, 13, 14, 15, 17, 19, 24}


def theorem_of(number: int) -> str:
    return f"{'Math15' if number <= 15 else 'Math30'}Catalog.source{number:02d}"


def test_research_target_sources_are_exactly_the_registered_catalogs():
    assert source_family_from_path(MATH15) == "research-targets"
    assert source_family_from_path(MATH30) == "research-targets"
    assert source_family_from_path("FormalConjectures/ResearchTargets/Math45.lean") is None
    assert source_family_from_path("FormalConjectures/ResearchTargets/1.lean") is None
    assert _valid_source_identity("research-targets", "Math15", MATH15, "Math15Catalog.source01")
    assert not _valid_source_identity("research-targets", "Math15", MATH15, "Math30Catalog.source16")
    assert not _valid_source_identity("research-targets", 15, MATH15, "Math15Catalog.source01")


def test_this_candidate_activates_only_the_reviewed_ready_subset():
    activated = {int(name[-2:]) for name in ACTIVATED_RESEARCH_TARGETS}
    assert {theorem_of(number) for number in activated} == ACTIVATED_RESEARCH_TARGETS
    assert CORE <= activated <= CORE | FIRST_BATCH_READY
    assert not activated & NEVER_ACTIVATED
    assert DEFAULT_TIER_SIZE == 258 + len(activated)
    assert task_pool.staged_research_target(THEOREM, MATH30)
    assert not task_pool.staged_research_target(ACTIVE, MATH30)
    # The gate concerns this family only; an Erdős source is unaffected by it.
    assert not task_pool.staged_research_target(
        "Erdos1.erdos_1", "FormalConjectures/ErdosProblems/1.lean"
    )


def research_allowlist(theorem: str = THEOREM) -> dict:
    source = {
        "index": 0,
        "source_path": MATH30,
        "source_type_sha256": "sha256:" + "1" * 64,
        "theorem": theorem,
        "tier": "tier-2",
    }
    rows = [
        {
            "completion_policy": "all_of",
            "mode": mode,
            "problem_id": problem_id("a" * 40, (theorem,)),
            "reward_target_id": f"fc-target:{theorem}",
            "source_indices": [0],
            "source_path": MATH30,
            "target_type_sha256s": [
                source["source_type_sha256"] if mode == EXACT_TASK_MODE else "sha256:" + "2" * 64
            ],
            "task_bundle_sha256": "sha256:" + ("3" if mode == EXACT_TASK_MODE else "4") * 64,
            "task_id": f"fc-test-research-{mode}-v1",
            "theorems": [theorem],
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


def test_registry_refuses_a_staged_research_target_and_admits_an_activated_one(tmp_path):
    path = tmp_path / "allowlist.json"
    path.write_text(json.dumps(research_allowlist(THEOREM)), encoding="utf-8")
    with pytest.raises(TaskNotAllowed, match="staged research target"):
        TaskPoolRegistry.load(path)

    # Positive control with the real constant: an activated target is otherwise well formed.
    path.write_text(json.dumps(research_allowlist(ACTIVE)), encoding="utf-8")
    registry = TaskPoolRegistry.load(path)
    assert {task.reward_target_id for task in registry.tasks.values()} == {f"fc-target:{ACTIVE}"}
    assert {task.mode for task in registry.tasks.values()} == set(PRODUCTION_TASK_MODES)


def test_registry_refuses_an_unregistered_research_catalog_even_when_activated(tmp_path):
    value = research_allowlist(ACTIVE)
    unregistered = "FormalConjectures/ResearchTargets/Math31.lean"
    value["allowed_source_theorems"][0]["source_path"] = unregistered
    for row in value["allowed_task_bundles"]:
        row["source_path"] = unregistered
    path = tmp_path / "allowlist.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(TaskNotAllowed):
        TaskPoolRegistry.load(path)


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
        theorem = theorem_of(number)
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


def write(directory: Path, value: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "decision-matrix.json"
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def no_activation(monkeypatch):
    """Schema version 1 is consistent only with an empty constant; simulate that release."""
    monkeypatch.setattr(task_pool, "ACTIVATED_RESEARCH_TARGETS", frozenset())


def test_decision_matrix_records_holds_without_activating_anything(tmp_path, no_activation):
    decisions = load_research_target_decisions(write(tmp_path, decision_matrix()))
    assert decisions.admissible == frozenset()
    assert decisions.schema_version == 1
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
        # Version 1 has no activated decision at all.
        lambda value: mutate(value, 16, decision="activated"),
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
def test_decision_matrix_fails_closed_on_invalid_or_activating_edits(
    tmp_path, no_activation, change
):
    with pytest.raises(VerifierError):
        load_research_target_decisions(write(tmp_path, change(decision_matrix())))


def test_decision_matrix_rejects_duplicate_type_hashes_and_keys(tmp_path, no_activation):
    value = decision_matrix()
    for number in (16, 18):
        value["targets"][number - 1]["source_type_sha256"] = "sha256:" + "d" * 64
    with pytest.raises(VerifierError):
        load_research_target_decisions(write(tmp_path, value))
    path = tmp_path / "duplicate.json"
    path.write_text('{"schema_version": 1, "schema_version": 1}', encoding="utf-8")
    with pytest.raises(VerifierError):
        load_research_target_decisions(path)


def test_a_version_1_matrix_cannot_coexist_with_an_activated_target(tmp_path):
    # The real constant of this candidate is not empty.
    with pytest.raises(VerifierError, match="activated"):
        load_research_target_decisions(write(tmp_path, decision_matrix()))


# --- schema version 2: explicit activation ------------------------------------------------------


def v2_matrix(directory: Path, activated=(16,), *, status=RELEASE_PROPOSED) -> dict:
    """A version-2 matrix activating `activated`, with its policy written beside it."""
    value = decision_matrix()
    value["schema_version"] = 2
    value["global_gates"] = [
        {
            "description": "maintainer review of the activation commit",
            "evidence": None,
            "id": "activation-code-review",
            "kind": "approval",
            "status": PENDING_OWNER_APPROVAL,
        },
        {
            "description": "the verifier image built from the validator that pins this file",
            "evidence": None,
            "id": "production-sandbox-equivalence",
            "kind": "release",
            "status": PENDING_RELEASE_GATE,
        },
        {
            "description": "candidate source builds",
            "evidence": "evidence/build.log sha256:" + "e" * 64,
            "id": "toolchain-migration-validated",
            "kind": "technical",
            "status": "closed",
        },
    ]
    value["target_gates"] = [dict(gate, kind="technical") for gate in value["target_gates"]]
    for number in activated:
        row = value["targets"][number - 1]
        row["decision"] = "activated"
        row["source_type_sha256"] = "sha256:" + f"{number:064d}"
    directory.mkdir(parents=True, exist_ok=True)
    policy = directory / "activation-policy.json"
    policy.write_bytes(b'{"policy": "reviewed activation policy fixture"}\n')
    value["activation"] = {
        "activated": sorted(activated),
        "approval_record_sha256": None,
        "policy_sha256": "sha256:" + hashlib.sha256(policy.read_bytes()).hexdigest(),
        "release_status": status,
    }
    return value


def activate(monkeypatch, *numbers: int) -> None:
    monkeypatch.setattr(
        task_pool,
        "ACTIVATED_RESEARCH_TARGETS",
        frozenset(theorem_of(number) for number in numbers),
    )


def test_a_version_2_matrix_activates_exactly_the_validator_constant(tmp_path, monkeypatch):
    activate(monkeypatch, 16)
    decisions = load_research_target_decisions(write(tmp_path, v2_matrix(tmp_path)))
    assert decisions.schema_version == 2
    assert decisions.admissible == frozenset({ACTIVE})
    assert decisions.release_status == RELEASE_PROPOSED
    assert decisions.pending_approval_gates == ("activation-code-review",)
    assert decisions.pending_release_gates == ("production-sandbox-equivalence",)
    assert decisions.open_global_gates == ()


def global_gate(value: dict, identifier: str) -> dict:
    return next(gate for gate in value["global_gates"] if gate["id"] == identifier)


def test_an_approved_release_closes_approvals_but_may_await_a_release_gate(tmp_path, monkeypatch):
    activate(monkeypatch, 16)
    value = v2_matrix(tmp_path)
    global_gate(value, "activation-code-review").update(
        status="closed", evidence="evidence/owner-approval.json sha256:" + "b" * 64
    )
    value["activation"].update(
        release_status=RELEASE_APPROVED, approval_record_sha256="sha256:" + "b" * 64
    )
    decisions = load_research_target_decisions(write(tmp_path, value))
    assert decisions.release_status == RELEASE_APPROVED
    assert decisions.pending_approval_gates == ()
    assert decisions.pending_release_gates == ("production-sandbox-equivalence",)


def test_a_first_batch_target_activates_only_with_its_gate_closed(tmp_path, monkeypatch):
    activate(monkeypatch, 4, 16)
    value = v2_matrix(tmp_path, (4, 16))
    with pytest.raises(VerifierError):
        load_research_target_decisions(write(tmp_path, value))
    for gate in value["target_gates"]:
        if gate["id"] == "relabel-package-authored":
            gate.update(status="closed", evidence="primary-check record sha256:" + "f" * 64)
    decisions = load_research_target_decisions(write(tmp_path, value))
    assert decisions.admissible == frozenset({theorem_of(4), ACTIVE})


def close_all_target_gates(value: dict) -> dict:
    for gate in value["target_gates"]:
        gate.update(status="closed", evidence="evidence sha256:" + "a" * 64)
    return value


@pytest.mark.parametrize(
    ("numbers", "change"),
    [
        # Every technical global gate must be closed.
        (
            (16,),
            lambda value: global_gate(value, "toolchain-migration-validated").update(
                status="open", evidence=None
            ),
        ),
        # Each pending status belongs to one gate kind.
        (
            (16,),
            lambda value: global_gate(value, "toolchain-migration-validated").update(
                status=PENDING_OWNER_APPROVAL, evidence=None
            ),
        ),
        (
            (16,),
            lambda value: global_gate(value, "toolchain-migration-validated").update(
                status=PENDING_RELEASE_GATE, evidence=None
            ),
        ),
        (
            (16,),
            lambda value: global_gate(value, "production-sandbox-equivalence").update(
                status=PENDING_OWNER_APPROVAL
            ),
        ),
        (
            (16,),
            lambda value: global_gate(value, "activation-code-review").update(
                status=PENDING_RELEASE_GATE
            ),
        ),
        # Neither an approval nor a release gate can simply stay open.
        ((16,), lambda value: global_gate(value, "activation-code-review").update(status="open")),
        (
            (16,),
            lambda value: global_gate(value, "production-sandbox-equivalence").update(
                status="open"
            ),
        ),
        # A release gate is global; a target gate is always technical.
        ((16,), lambda value: value["target_gates"][0].update(kind="release")),
        # An owner-approved release cannot leave an approval pending, and needs its record.
        ((16,), lambda value: value["activation"].update(release_status=RELEASE_APPROVED)),
        (
            (16,),
            lambda value: value["activation"].update(
                release_status=RELEASE_APPROVED, approval_record_sha256="sha256:" + "b" * 64
            ),
        ),
        # The matrix commits to the exact policy bytes beside it.
        ((16,), lambda value: value["activation"].update(policy_sha256="sha256:" + "0" * 64)),
        # Activation needs a recorded type hash.
        ((16,), lambda value: value["targets"][15].update(source_type_sha256=None)),
        # Matrix and decision rows must agree on the activated set.
        ((16,), lambda value: value["activation"].update(activated=[16, 18])),
        (
            (16,),
            lambda value: value["targets"][17].update(
                decision="activated", source_type_sha256="sha256:" + "8" * 64
            ),
        ),
        # A target gate is technical; it cannot be an owner approval.
        ((16,), lambda value: value["target_gates"][0].update(kind="approval")),
        # A held target needs an open gate; closing its only gate without a decision is refused.
        ((16,), lambda value: close_all_target_gates(value)),
    ],
)
def test_a_version_2_matrix_fails_closed(tmp_path, monkeypatch, numbers, change):
    activate(monkeypatch, *numbers)
    value = v2_matrix(tmp_path, numbers)
    change(value)
    with pytest.raises(VerifierError):
        load_research_target_decisions(write(tmp_path, value))


@pytest.mark.parametrize("number", [7, 8, 9, 12])
def test_targets_12_and_7_to_9_can_never_be_activated(tmp_path, monkeypatch, number):
    value = v2_matrix(tmp_path, (16,))
    for gate in value["target_gates"]:
        if gate["id"] == "correlated-reward-decision":
            # Even with the group's own gate closed, activation stays refused.
            gate.update(status="closed", evidence="decision record sha256:" + "a" * 64)
    value["targets"][number - 1].update(
        decision="activated", source_type_sha256="sha256:" + "9" * 64
    )
    value["activation"]["activated"] = sorted({16, number})
    activate(monkeypatch, 16, number)
    with pytest.raises(VerifierError):
        load_research_target_decisions(write(tmp_path, value))


def test_the_constant_and_the_matrix_must_agree(tmp_path, monkeypatch):
    activate(monkeypatch, 16, 18)
    with pytest.raises(VerifierError, match="differ"):
        load_research_target_decisions(write(tmp_path, v2_matrix(tmp_path, (16,))))


# --- the selection audit commits to the matrix ----------------------------------------------------


def selection_audit(revision: str, theorem: str = ACTIVE) -> dict:
    return {
        "audit_date_utc": "2026-10-07",
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
                "theorem": theorem,
                "upstream_status": "research open",
            }
        ],
        "source_main_commit": "b" * 40,
        "source_repository": "google-deepmind/formal-conjectures",
        "source_status_sources": [
            {
                "family": "research-targets",
                "locator": RESEARCH_TARGET_DECISIONS_LOCATOR,
                "revision": revision,
            }
        ],
    }


def tasks_layout(tmp_path: Path, monkeypatch) -> tuple[Path, str]:
    """A tasks-repository layout with a version-2 matrix activating target 16."""
    activate(monkeypatch, 16)
    staged = tmp_path / "tasks/staged/research-targets"
    matrix = write(staged, v2_matrix(staged))
    tier = tmp_path / "tasks/tiers/tier-1"
    tier.mkdir(parents=True)
    return tier / "selection-audit.json", hashlib.sha256(matrix.read_bytes()).hexdigest()


def test_selection_audit_admits_an_activated_target_committed_by_the_matrix(tmp_path, monkeypatch):
    path, digest = tasks_layout(tmp_path, monkeypatch)
    path.write_text(json.dumps(selection_audit(digest)), encoding="utf-8")
    assert load_selection_audit(path).theorems == (ACTIVE,)


def test_selection_audit_refuses_a_staged_target_or_a_stale_matrix_digest(tmp_path, monkeypatch):
    path, digest = tasks_layout(tmp_path, monkeypatch)
    path.write_text(json.dumps(selection_audit(digest, THEOREM)), encoding="utf-8")
    with pytest.raises(VerifierError, match="staged research target"):
        load_selection_audit(path)
    path.write_text(json.dumps(selection_audit("0" * 64)), encoding="utf-8")
    with pytest.raises(VerifierError, match="digest"):
        load_selection_audit(path)


# --- the public API names the collection honestly -------------------------------------------------


def test_research_targets_are_named_as_package_authored():
    from submission_api.naming import problem_name

    name = problem_name(module="FormalConjectures.ResearchTargets.Math30", theorem=ACTIVE)
    assert name.collection == "research_targets"
    assert name.collection_label == "Package-authored research targets"
    assert name.display_title.startswith("Package-authored research target Math30")


# --- the checked-in candidate ---------------------------------------------------------------------


@pytest.mark.needs_checkouts
def test_checked_in_matrix_activates_exactly_the_pool_research_targets():
    tasks_root = tasks_repository_root(ROOT)
    decisions = load_research_target_decisions(
        tasks_root / "staged/research-targets/decision-matrix.json"
    )
    assert decisions.schema_version == 2
    assert decisions.admissible == ACTIVATED_RESEARCH_TARGETS
    assert decisions.release_status in {RELEASE_PROPOSED, RELEASE_APPROVED}
    assert decisions.open_global_gates == ()
    assert {target.number for target in decisions.targets if target.decision == "excluded"} == {12}
    assert all(
        target.decision == "hold" for target in decisions.targets if target.number in (7, 8, 9)
    )
    policy = json.loads((tasks_root / "allowlist.json").read_text(encoding="utf-8"))
    research_rows = {
        row["theorem"]: row
        for row in policy["allowed_source_theorems"]
        if row["source_path"].startswith("FormalConjectures/ResearchTargets/")
    }
    assert set(research_rows) == ACTIVATED_RESEARCH_TARGETS
    by_theorem = {target.theorem: target for target in decisions.targets}
    assert all(
        row["source_type_sha256"] == by_theorem[theorem].source_type_sha256
        for theorem, row in research_rows.items()
    )
    assert all(
        "research-targets" in tier["source_families"] for tier in policy["tier_policies"].values()
    )
    rewards = {
        row["reward_target_id"]
        for row in policy["allowed_task_bundles"]
        if row["theorems"][0] in research_rows
    }
    assert rewards == {f"fc-target:{theorem}" for theorem in research_rows}
