import json

import pytest

from verifier.errors import VerifierError
from verifier.hashing import sha256_bytes
from verifier.task_pool import CLASSICAL_STATUS_LOCATOR, SCREENING_STATEMENT, load_selection_audit


def evidence_pair(tmp_path):
    retained = {
        "active_resolution_prs": [], "feasibility_signals": ["compact-formal-target"],
        "open_prs_touching_source": [], "source_family": "erdos",
        "source_path": "FormalConjectures/ErdosProblems/1.lean", "source_problem_number": 1,
        "source_status": "open", "theorem": "Erdos1.erdos_1", "upstream_status": "research open",
    }
    variant = {**retained, "source_problem_number": 2,
               "source_path": "FormalConjectures/ErdosProblems/2.lean",
               "theorem": "Erdos2.erdos_2.variants.remaining"}
    evidence = {
        "repository_commit": "a" * 40, "upstream_commit": "b" * 40,
        "schema_version": 1, "retained_selection": [retained], "targets": [{
            "theorem": variant["theorem"], "number": 2, "source_path": variant["source_path"],
            "parent_problem_status": "proved", "target_source_status": "open",
            "decision": "eligible", "review_note": "The proof covers the main question only.",
            "production_type_sha256": "sha256:" + "d" * 64,
            "references": ["https://www.erdosproblems.com/2"],
        }],
    }
    audit = {
        "schema_version": 3, "audit_date_utc": "2026-10-28", "github_open_pr_count": 1,
        "repository_commit": "a" * 40, "source_main_commit": "b" * 40,
        "source_repository": "google-deepmind/formal-conjectures",
        "screening_statement": SCREENING_STATEMENT, "selected": [retained.copy(), variant],
        "source_status_sources": [{"family": "erdos", "locator": "teorth/erdosproblems", "revision": "c" * 40}],
    }
    return audit, evidence


def write_pair(tmp_path, audit, evidence):
    content = json.dumps(evidence).encode()
    (tmp_path / "erdos-admission-audit.json").write_bytes(content)
    audit["target_status_audit_sha256"] = sha256_bytes(content)
    path = tmp_path / "selection-audit.json"
    path.write_text(json.dumps(audit))
    return path


def test_open_variant_has_a_separate_bound_status(tmp_path):
    audit, evidence = evidence_pair(tmp_path)
    loaded = load_selection_audit(write_pair(tmp_path, audit, evidence))
    assert len(loaded.entries) == 2


@pytest.mark.parametrize("mutation", [None, "wrong_family", "bad_hash", "held", "missing_classical"])
def test_new_named_target_requires_both_type_and_classical_status_review(tmp_path, mutation):
    audit, evidence = evidence_pair(tmp_path)
    row = audit["selected"][1]
    row.update(theorem="GRH.generalized_riemann_hypothesis", source_family="millennium",
               source_path="FormalConjectures/Millennium/RiemannHypothesis.lean",
               source_problem_number="RiemannHypothesis")
    target = evidence["targets"][0]
    target.update(theorem=row["theorem"], source_family="millennium",
                  source_path=row["source_path"], number=row["source_problem_number"])
    classical = {"schema_version": 1, "candidates": [{
        "theorem": row["theorem"], "source_path": row["source_path"],
        "disposition": "retain_provisionally_open",
    }]}
    if mutation == "wrong_family":
        target["source_family"] = "wikipedia"
    elif mutation == "bad_hash":
        target["production_type_sha256"] = "unbound"
    elif mutation == "held":
        classical["candidates"][0]["disposition"] = "hold_recent_claim"
    elif mutation == "missing_classical":
        classical["candidates"] = []
    content = json.dumps(classical).encode()
    (tmp_path / "classical-status-audit.json").write_bytes(content)
    audit["source_status_sources"].append({"family": "millennium",
        "locator": CLASSICAL_STATUS_LOCATOR, "revision": sha256_bytes(content).removeprefix("sha256:")})
    path = write_pair(tmp_path, audit, evidence)
    if mutation:
        with pytest.raises(VerifierError):
            load_selection_audit(path)
    else:
        loaded = load_selection_audit(path)
        assert loaded.reviewed_type_hashes[row["theorem"]] == target["production_type_sha256"]


@pytest.mark.parametrize("mutation", ["held", "missing", "renamed", "wrong_path", "retained_changed", "bad_hash", "duplicate_review", "source_revision", "upstream_revision"])
def test_target_review_cannot_be_bypassed(tmp_path, mutation):
    audit, evidence = evidence_pair(tmp_path)
    target = evidence["targets"][0]
    if mutation == "held":
        target["decision"] = "hold_resolution_claim"
    elif mutation == "missing":
        evidence["targets"] = []
    elif mutation == "renamed":
        target["theorem"] += "_different"
    elif mutation == "wrong_path":
        target["source_path"] = "FormalConjectures/ErdosProblems/3.lean"
    elif mutation == "bad_hash":
        target["production_type_sha256"] = "unbound"
    elif mutation == "duplicate_review":
        evidence["targets"].append({**target, "decision": "hold_resolution_claim"})
    elif mutation == "source_revision":
        evidence["repository_commit"] = "e" * 40
    elif mutation == "upstream_revision":
        evidence["upstream_commit"] = "e" * 40
    else:
        audit["selected"][0]["open_prs_touching_source"] = [42]
    with pytest.raises(VerifierError):
        load_selection_audit(write_pair(tmp_path, audit, evidence))


def test_changed_review_bytes_are_rejected(tmp_path):
    audit, evidence = evidence_pair(tmp_path)
    path = write_pair(tmp_path, audit, evidence)
    with (tmp_path / "erdos-admission-audit.json").open("a") as stream:
        stream.write("\n")
    with pytest.raises(VerifierError, match="digest"):
        load_selection_audit(path)


def test_legacy_selection_audits_still_load(tmp_path):
    audit, evidence = evidence_pair(tmp_path)
    audit["schema_version"] = 2
    path = tmp_path / "selection-audit.json"
    path.write_text(json.dumps(audit))
    assert len(load_selection_audit(path).entries) == 2


def test_changed_production_statement_requires_a_new_review(tmp_path):
    from dataclasses import replace
    from conftest import catalog, declaration
    from verifier.task_pool import HeldSources, RetiredSources, TaskTarget, TaskTargets, select_task_declarations

    audit, evidence = evidence_pair(tmp_path)
    audit["selected"] = audit["selected"][1:]
    evidence["retained_selection"] = []
    loaded = load_selection_audit(write_pair(tmp_path, audit, evidence))
    row = audit["selected"][0]
    item = replace(declaration(theorem=row["theorem"]), source_path=row["source_path"])
    cat = replace(catalog(item), repository_commit=loaded.repository_commit)
    targets = TaskTargets(loaded.repository_commit, "unused", "unused", (
        TaskTarget(row["theorem"], row["source_path"], "erdos", 2, "fc-target:" + row["theorem"]),
    ), "unused")
    with pytest.raises(VerifierError, match="reviewed target type differs"):
        select_task_declarations(catalog=cat,
            retired=RetiredSources(loaded.repository_commit, frozenset(), frozenset(), "unused"),
            held=HeldSources(loaded.repository_commit, (), "unused"),
            selection_audit=loaded, task_targets=targets, pool_size=1)
