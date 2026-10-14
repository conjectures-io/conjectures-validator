"""The manual-review policy and the API must offer exactly the codes reviewers need."""

from datetime import date
from pathlib import Path

from submission_api.credits import (
    APPROVAL_CODES,
    DISQUALIFICATION_CODES,
    SubmissionTerms,
)
from submission_api.settings import DEFAULT_TERMS_DATE, DEFAULT_TERMS_VERSION

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "docs" / "MANUAL_REVIEW_CRITERIA.md"


def current_terms():
    return SubmissionTerms.load(
        ROOT / "docs" / "SUBMISSION_TERMS.md",
        version=DEFAULT_TERMS_VERSION,
        effective_from=date.fromisoformat(DEFAULT_TERMS_DATE),
    )


def test_every_available_review_code_is_documented():
    policy = POLICY.read_text(encoding="utf-8")
    for code in APPROVAL_CODES | DISQUALIFICATION_CODES:
        assert f"`{code}`" in policy


def test_submission_terms_make_both_approval_codes_available():
    terms = current_terms()
    assert {code for code, _ in terms.approval_reasons} == {
        "REVIEW_APPROVED",
        "FORMALIZATION_DEFECT_AWARD",
    }


def test_prior_external_formalization_is_a_published_disqualification():
    terms = current_terms()

    reasons = dict(terms.disqualification_reasons)
    assert "PRIOR_EXTERNAL_FORMALIZATION" in reasons
    assert "external proof system" in reasons["PRIOR_EXTERNAL_FORMALIZATION"]


def test_not_novel_requires_established_exact_prior_proof_even_if_independent():
    terms = current_terms()

    reason = dict(terms.disqualification_reasons)["NOT_NOVEL"]
    assert "dated public source" in reason
    assert "same direct problem" in reason
    assert "established proof" in reason
    assert "before acceptance" in reason
    assert "even without Lean formalization" in reason
    assert "independently rediscovered" in reason
    assert "unsupported claims and mismatched scope do not qualify" in reason
    assert "substantially implements" not in reason


def test_capped_defect_award_is_published_with_the_new_contract_versions():
    body = (ROOT / "docs" / "SUBMISSION_TERMS.md").read_text()
    assert f"**Terms version:** `{DEFAULT_TERMS_VERSION}`" in body
    assert f"**Effective date:** {DEFAULT_TERMS_DATE}" in body
    assert "**Manual review policy:** `v4`" in body
    assert "**Policy version:** `v4`" in POLICY.read_text()
    assert "whichever is less" in body
    assert "earlier review policies retain" in body
    terms = current_terms()
    reason = dict(terms.approval_reasons)["FORMALIZATION_DEFECT_AWARD"]
    assert "lesser of $750" in reason
    assert "locked task bounty" in reason


def test_archived_v3_and_v6_keep_the_original_substantial_use_requirement():
    legacy_terms = (ROOT / "docs/policies/submission-terms-v6.md").read_text()
    legacy_policy = (ROOT / "docs/policies/manual-review-v3.md").read_text()
    assert "**Terms version:** `v6`" in legacy_terms
    assert "**Manual review policy:** `v3`" in legacy_terms
    assert "submitted proof substantially implements" in legacy_terms
    assert "**Policy version:** `v3`" in legacy_policy
    assert "submission substantially uses, implements, or reproduces" in legacy_policy
