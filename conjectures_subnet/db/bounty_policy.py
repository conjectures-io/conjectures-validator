"""Database enforcement for new bounties, shared with the migration mirror."""
from sqlalchemy import DDL, event

from conjectures_subnet.db.models import Base

GUARDS_SQL = """
CREATE FUNCTION protect_bounty_target_policy() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
        RAISE EXCEPTION 'a reward target keeps its original pricing policy' USING ERRCODE = '23514';
    END IF;
    IF NEW.pricing_policy IS DISTINCT FROM OLD.pricing_policy
       OR NEW.reward_target_id IS DISTINCT FROM OLD.reward_target_id THEN
        RAISE EXCEPTION 'a reward target keeps its original pricing policy'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER bounty_target_policy_immutable BEFORE UPDATE OR DELETE ON bounty_tasks
FOR EACH ROW EXECUTE FUNCTION protect_bounty_target_policy();
CREATE TRIGGER bounty_target_policy_no_truncate BEFORE TRUNCATE ON bounty_tasks
FOR EACH STATEMENT EXECUTE FUNCTION protect_bounty_target_policy();

-- Shared by instruction creation, notification and command rendering. Chain history is
-- recorded independently: a later review correction cannot undo a transfer on chain.
CREATE FUNCTION bounty_trajectory_eligible(proof_id uuid, reward_reason text) RETURNS boolean
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
    SELECT COALESCE((SELECT
        proof.bounty_policy_version IS DISTINCT FROM 'treasury-share-2p5-chat-v1' OR (
            proof.verification_status = 'VERIFIED' AND proof.manual_review_status = 'APPROVED'
            AND proof.reward_status IN ('ELIGIBLE', 'REWARDED', 'FAILED')
            AND binding.kind = 'HUMAN' AND binding.decision = 'APPROVED'
            AND binding.reason_code = reward_reason
            AND (binding.reason_code = 'FORMALIZATION_DEFECT_AWARD' OR (
                binding.reason_code = 'REVIEW_APPROVED'
                AND evidence.status = 'APPROVED_BOUNTY_EVIDENCE'
                AND evidence.amount_usd_cents = 0
                AND evidence.proof_digest = proof.proof_digest
                AND evidence.reward_target_id = proof.reward_target_id
                AND evidence.proof_review_decision_id = binding.id
                AND evidence.proof_policy_snapshot->>'bounty_policy_version' = proof.bounty_policy_version
                AND evidence.proof_policy_snapshot->>'review_policy_version' = proof.review_policy_version
            ))
        )
        FROM public.submissions proof
        LEFT JOIN LATERAL (
            SELECT * FROM public.review_decisions WHERE submission_id = proof.id AND kind <> 'ADVISORY'
            ORDER BY id DESC LIMIT 1
        ) binding ON true
        LEFT JOIN public.trajectory_claims evidence ON evidence.submission_id = proof.id
        WHERE proof.id = proof_id
    ), false);
$$;

REVOKE ALL ON FUNCTION bounty_trajectory_eligible(uuid, text) FROM PUBLIC;

CREATE FUNCTION require_bounty_trajectory() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    -- Serialize authorization against proof corrections and claim mutations.
    PERFORM id FROM public.submissions WHERE id = NEW.submission_id FOR UPDATE;
    IF NOT bounty_trajectory_eligible(NEW.submission_id, NEW.eligibility_reason) THEN
        RAISE EXCEPTION 'this bounty requires reviewed chat logs matching the accepted proof'
            USING ERRCODE = '23514', CONSTRAINT = 'reward_requires_reviewed_chat_logs';
    END IF;
    RETURN NEW;
END;
$$;
-- Authorization applies to new/revised instructions, not observations of money already sent.
CREATE TRIGGER reward_requires_trajectory BEFORE INSERT OR UPDATE OF
    submission_id, eligibility_reason, amount_rao, pricing_policy_version, pricing_inputs,
    destination_coldkey, destination_hotkey, generation_key ON reward_events
FOR EACH ROW EXECUTE FUNCTION require_bounty_trajectory();

"""

event.listen(Base.metadata, "after_create", DDL(GUARDS_SQL.replace("%", "%%")))
event.listen(Base.metadata, "before_drop", DDL("""
DROP FUNCTION IF EXISTS require_bounty_trajectory() CASCADE;
DROP FUNCTION IF EXISTS bounty_trajectory_eligible(uuid, text) CASCADE;
DROP FUNCTION IF EXISTS protect_bounty_target_policy() CASCADE;
"""))
