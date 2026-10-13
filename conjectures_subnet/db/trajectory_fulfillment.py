"""Manual bonus obligations and private buyer delivery. No signing or public storage."""
import datetime as dt
import uuid

from sqlalchemy import DDL, event, BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from conjectures_subnet.db.models import Base, SHA256, SS58


class TrajectoryPayment(Base):
    __tablename__ = "trajectory_payments"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trajectory_claims.id"), nullable=False)
    prepared_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    prepared_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claim_state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_usd_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_rao: Mapped[int] = mapped_column(BigInteger, nullable=False)
    alpha_usd: Mapped[object] = mapped_column(Numeric(38, 18), nullable=False)
    price_source: Mapped[str] = mapped_column(Text, nullable=False)
    price_observed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    destination_coldkey: Mapped[str] = mapped_column(SS58, nullable=False)
    origin_coldkey: Mapped[str] = mapped_column(SS58, nullable=False)
    origin_hotkey: Mapped[str] = mapped_column(SS58, nullable=False)
    destination_evidence: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    treasury_payout_id: Mapped[int | None] = mapped_column(ForeignKey("treasury_payouts.id"), unique=True)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    completed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("accounts.id"))
    completion_evidence: Mapped[dict | None] = mapped_column(JSONB)
    __table_args__ = (
        CheckConstraint("status IN ('PREPARED','CONFIRMED','CANCELLED')", name="trajectory_payment_status"),
        CheckConstraint("amount_usd_cents IN (25000,50000) AND amount_rao > 0 AND alpha_usd > 0", name="trajectory_payment_amount"),
        CheckConstraint("claim_state_version > 0", name="trajectory_payment_version"),
        CheckConstraint("length(price_source) BETWEEN 1 AND 1000 AND length(destination_evidence) BETWEEN 10 AND 2000", name="trajectory_payment_evidence"),
        CheckConstraint("price_observed_at <= prepared_at AND price_observed_at >= prepared_at - interval '10 minutes'", name="trajectory_payment_price_time"),
        CheckConstraint("(status = 'CONFIRMED') = (treasury_payout_id IS NOT NULL)", name="trajectory_payment_chain"),
        CheckConstraint("(status = 'PREPARED' AND completed_at IS NULL AND completed_by IS NULL AND completion_evidence IS NULL) OR (status <> 'PREPARED' AND completed_at IS NOT NULL AND completed_at >= prepared_at AND completed_by IS NOT NULL AND completion_evidence IS NOT NULL)", name="trajectory_payment_completion"),
        Index("trajectory_payment_one_live_claim", "claim_id", unique=True, postgresql_where=text("status <> 'CANCELLED'")),
    )


class TrajectoryDelivery(Base):
    __tablename__ = "trajectory_deliveries"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trajectory_claims.id"), nullable=False)
    buyer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claim_state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    package_digest: Mapped[bytes] = mapped_column(SHA256, nullable=False)
    agreement_reference: Mapped[str] = mapped_column(Text, nullable=False)
    agreement_sha256: Mapped[bytes] = mapped_column(SHA256, nullable=False)
    accepted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    assent_text: Mapped[str | None] = mapped_column(Text)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("accounts.id"))
    revocation_reason: Mapped[str | None] = mapped_column(Text)
    takedown_recorded_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    takedown_recorded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("accounts.id"))
    takedown_reference: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        CheckConstraint("claim_state_version > 0", name="trajectory_delivery_version"),
        CheckConstraint("length(agreement_reference) BETWEEN 10 AND 2000", name="trajectory_delivery_agreement"),
        CheckConstraint("(accepted_at IS NULL) = (assent_text IS NULL) AND (assent_text IS NULL OR length(assent_text) BETWEEN 1 AND 2000)", name="trajectory_delivery_assent"),
        CheckConstraint("accepted_at IS NULL OR accepted_at >= created_at", name="trajectory_delivery_consent_time"),
        CheckConstraint("(revoked_at IS NULL AND revoked_by IS NULL AND revocation_reason IS NULL) OR (revoked_at IS NOT NULL AND revoked_at >= created_at AND revoked_by IS NOT NULL AND revocation_reason IS NOT NULL AND length(revocation_reason) BETWEEN 10 AND 2000)", name="trajectory_delivery_revocation"),
        CheckConstraint("(takedown_recorded_at IS NULL AND takedown_recorded_by IS NULL AND takedown_reference IS NULL) OR (revoked_at IS NOT NULL AND takedown_recorded_at IS NOT NULL AND takedown_recorded_at >= revoked_at AND takedown_recorded_by IS NOT NULL AND takedown_reference IS NOT NULL AND length(takedown_reference) BETWEEN 10 AND 2000)", name="trajectory_delivery_takedown"),
        Index("trajectory_delivery_one_live_buyer", "claim_id", "buyer_id", unique=True, postgresql_where=text("revoked_at IS NULL")),
    )


class TrajectoryExport(Base):
    __tablename__ = "trajectory_exports"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    delivery_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trajectory_deliveries.id"), nullable=False)
    exported_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    proof_review_decision_id: Mapped[int] = mapped_column(ForeignKey("review_decisions.id"), nullable=False)
    claim_state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (Index("trajectory_exports_delivery_idx", "delivery_id"),)

# Mirrored verbatim in V048; the migration is the schema authority.
GUARDS = """
CREATE FUNCTION protect_trajectory_fulfillment() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF TG_TABLE_NAME = 'trajectory_payments' THEN
            IF NEW.status <> 'PREPARED' OR NOT EXISTS (
                SELECT 1 FROM trajectory_claims c WHERE c.id = NEW.claim_id
                  AND c.status = 'APPROVED_MANUAL_PAYMENT' AND c.amount_usd_cents = NEW.amount_usd_cents
                  AND c.state_version = NEW.claim_state_version AND c.account_id <> NEW.prepared_by
            ) OR NEW.amount_rao <> ceil(NEW.amount_usd_cents::numeric * 10000000 / NEW.alpha_usd) THEN
                RAISE EXCEPTION 'bonus preparation must match the accepted claim and conversion' USING ERRCODE = '23514';
            END IF;
        ELSIF TG_TABLE_NAME = 'trajectory_deliveries' THEN
            IF NEW.accepted_at IS NOT NULL OR NEW.revoked_at IS NOT NULL OR NEW.takedown_recorded_at IS NOT NULL THEN
                RAISE EXCEPTION 'buyer consent and revocation are separate actions' USING ERRCODE = '23514';
            END IF;
        END IF;
        RETURN NEW;
    END IF;
    -- Expiry may remove unfulfilled records after seven years; settled payments stay permanent.
    IF TG_OP = 'DELETE' THEN
        IF TG_TABLE_NAME = 'trajectory_payments' THEN
            IF OLD.status = 'CANCELLED' AND OLD.prepared_at < now() - interval '2555 days'
               AND EXISTS (SELECT 1 FROM trajectory_claims c WHERE c.id = OLD.claim_id
                   AND c.audit_expires_at < now() AND c.status NOT IN ('APPROVED_MANUAL_PAYMENT','APPROVED_BOUNTY_EVIDENCE')) THEN
                RETURN OLD;
            END IF;
        ELSIF TG_TABLE_NAME = 'trajectory_deliveries' THEN
            IF OLD.created_at < now() - interval '2555 days'
               AND NOT EXISTS (SELECT 1 FROM trajectory_exports e WHERE e.delivery_id = OLD.id)
               AND EXISTS (SELECT 1 FROM trajectory_claims c WHERE c.id = OLD.claim_id
                   AND c.audit_expires_at < now() AND c.status NOT IN ('APPROVED_MANUAL_PAYMENT','APPROVED_BOUNTY_EVIDENCE')) THEN
                RETURN OLD;
            END IF;
        ELSIF TG_TABLE_NAME = 'trajectory_exports' THEN
            IF OLD.exported_at < now() - interval '2555 days' AND EXISTS (
                SELECT 1 FROM trajectory_deliveries d JOIN trajectory_claims c ON c.id = d.claim_id
                WHERE d.id = OLD.delivery_id AND d.revoked_at IS NOT NULL AND d.takedown_recorded_at IS NOT NULL
                  AND c.audit_expires_at < now() AND c.status NOT IN ('APPROVED_MANUAL_PAYMENT','APPROVED_BOUNTY_EVIDENCE')) THEN
                RETURN OLD;
            END IF;
        END IF;
    END IF;
    IF TG_OP IN ('DELETE', 'TRUNCATE') OR TG_TABLE_NAME = 'trajectory_exports' THEN
        RAISE EXCEPTION 'fulfillment records are permanent' USING ERRCODE = '23514';
    END IF;
    IF TG_TABLE_NAME = 'trajectory_payments' THEN
        IF OLD.status <> 'PREPARED' OR NEW.status NOT IN ('CONFIRMED','CANCELLED')
           OR (to_jsonb(NEW) - ARRAY['status','treasury_payout_id','completed_at','completed_by','completion_evidence'])
              IS DISTINCT FROM
              (to_jsonb(OLD) - ARRAY['status','treasury_payout_id','completed_at','completed_by','completion_evidence']) THEN
            RAISE EXCEPTION 'bonus instructions and completed records are immutable' USING ERRCODE = '23514';
        END IF;
        IF NEW.status = 'CONFIRMED' AND NOT EXISTS (
            SELECT 1 FROM treasury_payouts p WHERE p.id = NEW.treasury_payout_id
            AND p.reward_event_id IS NULL AND p.status <> 'CLAIMED'
            AND p.amount_rao = NEW.amount_rao AND p.destination_coldkey = NEW.destination_coldkey
            AND p.origin_coldkey = NEW.origin_coldkey AND p.origin_hotkey = NEW.origin_hotkey
            AND p.destination_hotkey = NEW.origin_hotkey AND p.origin_netuid = 66 AND p.destination_netuid = 66
            AND p.block_timestamp >= NEW.prepared_at - interval '60 seconds'
        ) THEN
            RAISE EXCEPTION 'bonus confirmation requires its exact finalized transfer' USING ERRCODE = '23514';
        END IF;
    ELSE
        IF (to_jsonb(NEW) - ARRAY['accepted_at','assent_text','revoked_at','revoked_by','revocation_reason','takedown_recorded_at','takedown_recorded_by','takedown_reference'])
           IS DISTINCT FROM
           (to_jsonb(OLD) - ARRAY['accepted_at','assent_text','revoked_at','revoked_by','revocation_reason','takedown_recorded_at','takedown_recorded_by','takedown_reference'])
           OR (OLD.accepted_at IS NOT NULL AND ROW(NEW.accepted_at,NEW.assent_text) IS DISTINCT FROM ROW(OLD.accepted_at,OLD.assent_text))
           OR (OLD.revoked_at IS NOT NULL AND ROW(NEW.revoked_at,NEW.revoked_by,NEW.revocation_reason)
               IS DISTINCT FROM ROW(OLD.revoked_at,OLD.revoked_by,OLD.revocation_reason))
           OR (OLD.takedown_recorded_at IS NOT NULL AND ROW(NEW.takedown_recorded_at,NEW.takedown_recorded_by,NEW.takedown_reference)
               IS DISTINCT FROM ROW(OLD.takedown_recorded_at,OLD.takedown_recorded_by,OLD.takedown_reference)) THEN
            RAISE EXCEPTION 'delivery terms, consent and revocations are immutable' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trajectory_payments_immutable BEFORE INSERT OR UPDATE OR DELETE ON trajectory_payments
FOR EACH ROW EXECUTE FUNCTION protect_trajectory_fulfillment();
CREATE TRIGGER trajectory_payments_no_truncate BEFORE TRUNCATE ON trajectory_payments
FOR EACH STATEMENT EXECUTE FUNCTION protect_trajectory_fulfillment();
CREATE TRIGGER trajectory_deliveries_immutable BEFORE INSERT OR UPDATE OR DELETE ON trajectory_deliveries
FOR EACH ROW EXECUTE FUNCTION protect_trajectory_fulfillment();
CREATE TRIGGER trajectory_deliveries_no_truncate BEFORE TRUNCATE ON trajectory_deliveries
FOR EACH STATEMENT EXECUTE FUNCTION protect_trajectory_fulfillment();
CREATE TRIGGER trajectory_exports_immutable BEFORE UPDATE OR DELETE ON trajectory_exports
FOR EACH ROW EXECUTE FUNCTION protect_trajectory_fulfillment();
CREATE TRIGGER trajectory_exports_no_truncate BEFORE TRUNCATE ON trajectory_exports
FOR EACH STATEMENT EXECUTE FUNCTION protect_trajectory_fulfillment();

CREATE FUNCTION protect_bonus_chain_binding() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    IF NEW.reward_event_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM public.trajectory_payments WHERE treasury_payout_id = NEW.id
    ) THEN
        RAISE EXCEPTION 'this chain transfer already paid a historical bonus' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER treasury_payout_not_also_bonus BEFORE UPDATE ON treasury_payouts
FOR EACH ROW EXECUTE FUNCTION protect_bonus_chain_binding();

CREATE FUNCTION trajectory_bonus_transfer_reserved(origin_key text, hotkey text, destination_key text,
    amount bigint, transferred_at timestamptz) RETURNS boolean
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
    SELECT EXISTS (SELECT 1 FROM public.trajectory_payments
        WHERE origin_coldkey::text = origin_key AND origin_hotkey::text = hotkey
          AND destination_coldkey::text = destination_key AND amount_rao = amount
          AND status IN ('PREPARED','CONFIRMED')
          AND prepared_at <= transferred_at + interval '60 seconds');
$$;
REVOKE ALL ON FUNCTION trajectory_bonus_transfer_reserved(text,text,text,bigint,timestamptz) FROM PUBLIC;
CREATE FUNCTION trajectory_bonus_oldest_pending(origin_key text, hotkey text) RETURNS timestamptz
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
    SELECT min(prepared_at) FROM public.trajectory_payments
    WHERE status = 'PREPARED' AND origin_coldkey::text = origin_key AND origin_hotkey::text = hotkey;
$$;
REVOKE ALL ON FUNCTION trajectory_bonus_oldest_pending(text,text) FROM PUBLIC;
"""
event.listen(TrajectoryExport.__table__, "after_create", DDL(GUARDS))
event.listen(TrajectoryExport.__table__, "before_drop", DDL("""
DROP FUNCTION IF EXISTS trajectory_bonus_oldest_pending(text,text) CASCADE;
DROP FUNCTION IF EXISTS protect_trajectory_fulfillment() CASCADE;
DROP FUNCTION IF EXISTS protect_bonus_chain_binding() CASCADE;
DROP FUNCTION IF EXISTS trajectory_bonus_transfer_reserved(text,text,text,bigint,timestamptz) CASCADE;
"""))
