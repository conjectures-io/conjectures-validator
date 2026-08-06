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
        version="v1",
        effective_from=date(2026, 8, 5),
    )
    assert {code for code, _ in terms.approval_reasons} == {
        "REVIEW_APPROVED",
        "FORMALIZATION_DEFECT_AWARD",
    }
