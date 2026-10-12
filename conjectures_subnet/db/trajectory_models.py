"""Private bonus-review records. Deliberately unrelated to reward_events or payout workers."""
import datetime as dt
import uuid

from sqlalchemy import DDL, event, CheckConstraint, DateTime, ForeignKey, Index, Integer, LargeBinary, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from conjectures_subnet.db.models import Base, SHA256


class TrajectoryClaim(Base):
    __tablename__ = "trajectory_claims"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    submission_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("submissions.id"), unique=True, nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False)
    proof_digest: Mapped[bytes] = mapped_column(SHA256, unique=True, nullable=False)
    reward_target_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    amount_usd_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    terms_version: Mapped[str] = mapped_column(Text, nullable=False)
    package: Mapped[bytes | None] = mapped_column(LargeBinary)
    package_digest: Mapped[bytes] = mapped_column(SHA256, nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    offer_start_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    proof_review_decision_id: Mapped[int] = mapped_column(ForeignKey("review_decisions.id"), nullable=False)
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"))
    review_note: Mapped[str | None] = mapped_column(Text)
    consent: Mapped[dict] = mapped_column(JSONB, nullable=False)
    proof_policy_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    commercial_withdrawn_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    commercial_election: Mapped[dict] = mapped_column(JSONB, nullable=False)
    commercial_review: Mapped[dict | None] = mapped_column(JSONB)
    checklist: Mapped[dict | None] = mapped_column(JSONB)
    history: Mapped[list] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    audit_expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("status IN ('UNDER_REVIEW','NEEDS_CHANGES','REJECTED','APPROVED_MANUAL_PAYMENT','WITHDRAWN','EXPIRED','REVOKED_PROOF_CHANGED','APPROVED_BOUNTY_EVIDENCE')", name="trajectory_status"),
        CheckConstraint("amount_usd_cents IN (0, 25000, 50000)", name="trajectory_fixed_bonus"),
        CheckConstraint("(status <> 'APPROVED_BOUNTY_EVIDENCE' OR amount_usd_cents = 0) AND (status <> 'APPROVED_MANUAL_PAYMENT' OR amount_usd_cents IN (25000, 50000))", name="trajectory_approval_purpose"),
        CheckConstraint("reviewer_id IS NULL OR reviewer_id <> account_id", name="trajectory_no_self_review"),
        CheckConstraint("state_version > 0", name="trajectory_state_version"),
        CheckConstraint("revision BETWEEN 1 AND 3", name="trajectory_revision_bound"),
        CheckConstraint("package IS NULL OR octet_length(package) <= 33554432", name="trajectory_package_bound"),
        CheckConstraint("package IS NULL OR pg_catalog.sha256(package) = package_digest", name="trajectory_package_hash"),
        CheckConstraint("terms_version = 'trajectory-review-draft-v1'", name="trajectory_terms"),
        CheckConstraint("expires_at > created_at AND audit_expires_at > expires_at", name="trajectory_retention"),
        CheckConstraint("status NOT IN ('APPROVED_MANUAL_PAYMENT','APPROVED_BOUNTY_EVIDENCE') OR (reviewer_id IS NOT NULL AND checklist IS NOT NULL AND review_note IS NOT NULL)", name="trajectory_approval_reviewed"),
        Index("trajectory_account_idx", "account_id", "created_at"),
        Index("trajectory_queue_idx", "status", "created_at"),
        Index("trajectory_one_approved_target", "reward_target_id", unique=True, postgresql_where=text("status IN ('APPROVED_MANUAL_PAYMENT','APPROVED_BOUNTY_EVIDENCE')")),
    )


class TrajectoryFingerprint(Base):
    __tablename__ = "trajectory_fingerprints"
    digest: Mapped[bytes] = mapped_column(SHA256, primary_key=True)
    claim_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trajectory_claims.id", ondelete="CASCADE"), nullable=False)

    __table_args__ = (Index("trajectory_fingerprint_claim_idx", "claim_id"),)


class TrajectoryAccess(Base):
    __tablename__ = "trajectory_access_log"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    claim_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trajectory_claims.id", ondelete="CASCADE"), nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False)
    accessed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (Index("trajectory_access_claim_idx", "claim_id"),)


event.listen(TrajectoryAccess.__table__, "after_create", DDL("CREATE FUNCTION forbid_trajectory_access_update() RETURNS trigger\nLANGUAGE plpgsql AS $$\nBEGIN\n    RAISE EXCEPTION 'trajectory access records cannot be updated';\nEND;\n$$;\nCREATE TRIGGER trajectory_access_no_update BEFORE UPDATE ON trajectory_access_log\nFOR EACH ROW EXECUTE FUNCTION forbid_trajectory_access_update();"))
event.listen(TrajectoryAccess.__table__, "before_drop", DDL("DROP FUNCTION IF EXISTS forbid_trajectory_access_update() CASCADE;"))
