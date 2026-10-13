"""Private chat-log records, licensed for sale once accepted. Payout workers see only a boolean projection."""
import datetime as dt
import uuid

from sqlalchemy import DDL, event, BigInteger, CheckConstraint, DateTime, Identity, ForeignKey, Index, Integer, LargeBinary, SmallInteger, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from conjectures_subnet.db.models import Base, SHA256


class TrajectoryClaim(Base):
    __tablename__ = "trajectory_claims"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    # Null only while logs for a new bounty wait for the proof submission they must accompany.
    submission_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("submissions.id"), unique=True)
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False)
    proof_digest: Mapped[bytes] = mapped_column(SHA256, unique=True, nullable=False)
    reward_target_id: Mapped[str] = mapped_column(Text, nullable=False)
    task_bundle_sha256: Mapped[bytes] = mapped_column(SHA256, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    amount_usd_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    terms_version: Mapped[str] = mapped_column(Text, nullable=False)
    package: Mapped[bytes | None] = mapped_column(LargeBinary)
    package_digest: Mapped[bytes] = mapped_column(SHA256, nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    offer_start_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    proof_review_decision_id: Mapped[int | None] = mapped_column(ForeignKey("review_decisions.id"))
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"))
    review_note: Mapped[str | None] = mapped_column(Text)
    consent: Mapped[dict] = mapped_column(JSONB, nullable=False)
    proof_policy_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    checklist: Mapped[dict | None] = mapped_column(JSONB)
    history: Mapped[list] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    audit_expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("status IN ('DEPOSITED','UNDER_REVIEW','NEEDS_CHANGES','REJECTED','APPROVED_MANUAL_PAYMENT','APPROVED_BOUNTY_EVIDENCE','WITHDRAWN','EXPIRED','REVOKED_PROOF_CHANGED')", name="trajectory_status"),
        CheckConstraint("(status = 'DEPOSITED') = (submission_id IS NULL) AND (submission_id IS NOT NULL OR amount_usd_cents = 0)", name="trajectory_deposit"),
        CheckConstraint("amount_usd_cents > 0 OR (revision = 1 AND status <> 'NEEDS_CHANGES')", name="trajectory_required_logs_fixed"),
        CheckConstraint("reviewer_id IS NULL OR reviewer_id <> account_id", name="trajectory_no_self_review"),
        CheckConstraint("amount_usd_cents IN (0, 25000, 50000)", name="trajectory_fixed_bonus"),
        CheckConstraint("(status <> 'APPROVED_BOUNTY_EVIDENCE' OR amount_usd_cents = 0) AND (status <> 'APPROVED_MANUAL_PAYMENT' OR amount_usd_cents IN (25000, 50000))", name="trajectory_approval_purpose"),
        CheckConstraint("amount_usd_cents = 0 OR proof_review_decision_id IS NOT NULL", name="trajectory_historical_decision"),
        CheckConstraint("state_version > 0", name="trajectory_state_version"),
        CheckConstraint("revision BETWEEN 1 AND 3", name="trajectory_revision_bound"),
        CheckConstraint("package IS NULL OR octet_length(package) <= 67108864", name="trajectory_package_bound"),
        CheckConstraint("package IS NULL OR pg_catalog.sha256(package) = package_digest", name="trajectory_package_hash"),
        CheckConstraint("terms_version LIKE 'chat-log-terms-%'", name="trajectory_terms"),
        CheckConstraint("expires_at > created_at AND audit_expires_at > expires_at", name="trajectory_retention"),
        CheckConstraint("status NOT IN ('APPROVED_MANUAL_PAYMENT','APPROVED_BOUNTY_EVIDENCE') OR (reviewer_id IS NOT NULL AND checklist IS NOT NULL AND review_note IS NOT NULL AND proof_review_decision_id IS NOT NULL)", name="trajectory_approval_reviewed"),
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


class ChatLogProgram(Base):
    """The moment intake opened: the earlier-proof bonus cutoff, moved only by a recorded correction."""
    __tablename__ = "chat_log_program"
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    opened_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    terms_version: Mapped[str] = mapped_column(Text, nullable=False)
    terms_sha256: Mapped[bytes] = mapped_column(SHA256, nullable=False)
    opened_by: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (
        CheckConstraint("id = 1", name="chat_log_program_single"),
        CheckConstraint("terms_version LIKE 'chat-log-terms-%'", name="chat_log_program_terms"),
        CheckConstraint("length(opened_by) BETWEEN 1 AND 255", name="chat_log_program_opened_by"),
    )


class ChatLogProgramCorrection(Base):
    """An append-only record of each change to the opening moment."""
    __tablename__ = "chat_log_program_corrections"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    previous_opened_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    opened_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    corrected_by: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    corrected_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    __table_args__ = (
        CheckConstraint("length(corrected_by) BETWEEN 1 AND 255", name="chat_log_program_corrections_by"),
        CheckConstraint("length(reason) BETWEEN 1 AND 2000", name="chat_log_program_corrections_reason"),
    )


event.listen(ChatLogProgramCorrection.__table__, "after_create", DDL("""CREATE FUNCTION forbid_chat_log_correction_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'chat-log program corrections are permanent' USING ERRCODE = '23514';
END;
$$;
CREATE TRIGGER chat_log_program_corrections_permanent BEFORE UPDATE OR DELETE ON chat_log_program_corrections
FOR EACH ROW EXECUTE FUNCTION forbid_chat_log_correction_change();
CREATE TRIGGER chat_log_program_corrections_no_truncate BEFORE TRUNCATE ON chat_log_program_corrections
FOR EACH STATEMENT EXECUTE FUNCTION forbid_chat_log_correction_change();"""))
event.listen(ChatLogProgramCorrection.__table__, "before_drop", DDL("DROP FUNCTION IF EXISTS forbid_chat_log_correction_change() CASCADE;"))
event.listen(ChatLogProgram.__table__, "after_create", DDL("""CREATE FUNCTION forbid_chat_log_program_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.id = OLD.id AND NEW.terms_version = OLD.terms_version
           AND NEW.terms_sha256 = OLD.terms_sha256 AND NEW.opened_by = OLD.opened_by
           AND EXISTS (SELECT 1 FROM chat_log_program_corrections AS correction
                       WHERE correction.corrected_at = now()
                         AND correction.previous_opened_at = OLD.opened_at
                         AND correction.opened_at = NEW.opened_at) THEN
            RETURN NEW;
        END IF;
    END IF;
    RAISE EXCEPTION 'the chat-log program opening is permanent unless a correction is recorded'
        USING ERRCODE = '23514';
END;
$$;
CREATE TRIGGER chat_log_program_permanent BEFORE UPDATE OR DELETE ON chat_log_program
FOR EACH ROW EXECUTE FUNCTION forbid_chat_log_program_change();
CREATE TRIGGER chat_log_program_no_truncate BEFORE TRUNCATE ON chat_log_program
FOR EACH STATEMENT EXECUTE FUNCTION forbid_chat_log_program_change();"""))
event.listen(ChatLogProgram.__table__, "before_drop", DDL("DROP FUNCTION IF EXISTS forbid_chat_log_program_change() CASCADE;"))
