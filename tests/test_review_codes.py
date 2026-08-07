"""The manual-review policy and the API must offer exactly the codes reviewers need."""

from datetime import date
from pathlib import Path

from submission_api.credits import (
    APPROVAL_CODES,
    DISQUALIFICATION_CODES,
    SubmissionTerms,
)

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "docs" / "MANUAL_REVIEW_CRITERIA.md"


def test_every_available_review_code_is_documented():
    policy = POLICY.read_text(encoding="utf-8")
    for code in APPROVAL_CODES | DISQUALIFICATION_CODES:
        assert f"`{code}`" in policy


def test_submission_terms_make_both_approval_codes_available():
    terms = SubmissionTerms.load(
        ROOT / "docs" / "SUBMISSION_TERMS.md",
        version="v3",
        effective_from=date(2026, 8, 7),
    )
    assert {code for code, _ in terms.approval_reasons} == {
        "REVIEW_APPROVED",
        "FORMALIZATION_DEFECT_AWARD",
    }


def test_prior_external_formalization_is_a_published_disqualification():
    terms = SubmissionTerms.load(
        ROOT / "docs" / "SUBMISSION_TERMS.md",
        version="v3",
        effective_from=date(2026, 8, 7),
    )

    reasons = dict(terms.disqualification_reasons)
    assert "PRIOR_EXTERNAL_FORMALIZATION" in reasons
    assert "external proof system" in reasons["PRIOR_EXTERNAL_FORMALIZATION"]


def test_not_novel_covers_exact_prior_public_solutions_used_by_the_submission():
    terms = SubmissionTerms.load(
        ROOT / "docs" / "SUBMISSION_TERMS.md",
        version="v3",
        effective_from=date(2026, 8, 7),
    )

    reason = dict(terms.disqualification_reasons)["NOT_NOVEL"]
    assert "dated public source" in reason
    assert "same direct problem" in reason
    assert "substantially implements" in reason
    assert "exact target" in reason
