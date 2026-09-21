from __future__ import annotations

import hashlib
import json

import pytest

from verifier.errors import VerifierError
from verifier.task_pool import (
    CLASSICAL_STATUS_LOCATOR,
    SCREENING_STATEMENT,
    _valid_source_identity,
    _valid_source_status_sources,
    load_selection_audit,
    load_task_targets,
    source_family_from_path,
)


@pytest.mark.parametrize("family,slug,namespace", [
    ("wikipedia", "NormalityOfPi", "NormalNumber"),
    ("wikipedia", "BrocardConjecture", "Brocard"),
    ("millennium", "RiemannHypothesis", "RiemannHypothesis"),
])
def test_named_identity_uses_canonical_file_and_actual_namespace(family, slug, namespace):
    directory = "Wikipedia" if family == "wikipedia" else "Millennium"
    path = f"FormalConjectures/{directory}/{slug}.lean"
    assert source_family_from_path(path) == family
    assert _valid_source_identity(family, slug, path, namespace + ".target")
    assert not _valid_source_identity(family, slug, path, "Erdos1.target")
    assert not _valid_source_identity(family, 1, path, namespace + ".target")
    assert not _valid_source_identity(family, slug, path.replace(slug, "../" + slug), namespace + ".target")


@pytest.mark.parametrize("path", [
    "FormalConjectures/Wikipedia/Unknown.lean",
    "FormalConjectures/Wikipedia/../Wikipedia/NormalityOfPi.lean",
    "FormalConjectures/Millennium/NormalityOfPi.lean",
    "FormalConjectures/ErdosProblems/01.lean/../1.lean",
])
def test_named_family_does_not_admit_arbitrary_or_noncanonical_paths(path):
    assert source_family_from_path(path) is None


def test_named_status_cannot_borrow_an_erdos_tracker_revision():
    erdos = {"family": "erdos", "locator": "teorth/erdosproblems", "revision": "a" * 40}
    classical = {"family": "wikipedia", "locator": CLASSICAL_STATUS_LOCATOR, "revision": "b" * 64}
    assert _valid_source_status_sources([erdos], {"erdos"})
    assert not _valid_source_status_sources([erdos], {"wikipedia"})
    assert _valid_source_status_sources([erdos, classical], {"wikipedia", "erdos"})
    assert not _valid_source_status_sources([dict(classical, revision="a" * 40)], {"wikipedia"})
    assert not _valid_source_status_sources([classical, classical], {"wikipedia"})


def test_named_targets_load_without_relabeling_as_numbered_problems(tmp_path):
    target = {
        "theorem": "NormalNumber.pi_normal_base_ten",
        "source_path": "FormalConjectures/Wikipedia/NormalityOfPi.lean",
        "source_family": "wikipedia", "source_problem_number": "NormalityOfPi",
        "reward_target_id": "fc-target:NormalNumber.pi_normal_base_ten",
    }
    value = {"schema_version": 3, "repository_commit": "a" * 40,
             "policy": "one_task_one_audited_proposition", "task_scope": "direct_proposition",
             "targets": [target]}
    path = tmp_path / "targets.json"
    path.write_text(json.dumps(value))
    assert load_task_targets(path).targets[0].source_problem_number == "NormalityOfPi"
    target["source_family"] = "millennium"
    path.write_text(json.dumps(value))
    with pytest.raises(VerifierError):
        load_task_targets(path)


def test_named_admission_requires_unchanged_retained_review_evidence(tmp_path):
    theorem = "NormalNumber.pi_normal_base_ten"
    source = "FormalConjectures/Wikipedia/NormalityOfPi.lean"
    evidence = {"schema_version": 1, "candidates": [{"theorem": theorem,
        "source_path": source, "disposition": "retain_provisionally_open"}]}
    evidence_path = tmp_path / "classical-status-audit.json"
    evidence_path.write_text(json.dumps(evidence))
    audit = {"schema_version": 2, "audit_date_utc": "2026-09-21",
        "github_open_pr_count": 1, "repository_commit": "a" * 40,
        "source_main_commit": "b" * 40, "source_repository": "google-deepmind/formal-conjectures",
        "screening_statement": SCREENING_STATEMENT,
        "source_status_sources": [{"family": "wikipedia", "locator": CLASSICAL_STATUS_LOCATOR,
            "revision": hashlib.sha256(evidence_path.read_bytes()).hexdigest()}],
        "selected": [{"theorem": theorem, "source_path": source, "source_family": "wikipedia",
            "source_problem_number": "NormalityOfPi", "source_status": "open",
            "upstream_status": "research open", "active_resolution_prs": [],
            "open_prs_touching_source": [], "feasibility_signals": ["standard-mathlib-surface"]}]}
    path = tmp_path / "selection-audit.json"
    path.write_text(json.dumps(audit))
    assert load_selection_audit(path).theorems == (theorem,)
    evidence["candidates"][0]["disposition"] = "hold_recent_claim"
    evidence_path.write_text(json.dumps(evidence))
    with pytest.raises(VerifierError, match="digest"):
        load_selection_audit(path)
    audit["source_status_sources"][0]["revision"] = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    path.write_text(json.dumps(audit))
    with pytest.raises(VerifierError, match="matching retained"):
        load_selection_audit(path)
