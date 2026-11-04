"""Public proof confirmation is separate from reward and chat-log approval."""
from sqlalchemy import and_, exists, or_, select

from conjectures_subnet.db.models import ManualReviewState, ReviewDecision, ReviewerKind, Submission, VerificationState
from conjectures_subnet.db.trajectory_models import TrajectoryClaim


def documentation_pending():
    latest_reason = (select(ReviewDecision.reason_code)
                     .where(ReviewDecision.submission_id == Submission.id,
                            ReviewDecision.kind != ReviewerKind.ADVISORY)
                     .order_by(ReviewDecision.id.desc()).limit(1).scalar_subquery())
    return (
        Submission.verification_status == VerificationState.VERIFIED,
        or_(Submission.manual_review_status == ManualReviewState.UNREVIEWED,
            and_(Submission.manual_review_status == ManualReviewState.REJECTED,
                 latest_reason == "CHAT_LOGS_NOT_ACCEPTED")),
        exists(select(TrajectoryClaim.id).where(
            TrajectoryClaim.submission_id == Submission.id,
            TrajectoryClaim.amount_usd_cents == 0,
            TrajectoryClaim.history.contains([{"action": "PROOF_CONFIRMED"}]),
        )).correlate(Submission),
    )
