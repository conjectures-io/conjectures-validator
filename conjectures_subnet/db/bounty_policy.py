"""Database enforcement for new bounties, shared with the migration mirror."""
from sqlalchemy import DDL, event

from conjectures_subnet.db.models import Base

GUARDS_SQL = """
CREATE FUNCTION protect_bounty_target_policy() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.pricing_policy IS DISTINCT FROM OLD.pricing_policy THEN
        RAISE EXCEPTION 'a reward target keeps its original pricing policy'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER bounty_target_policy_immutable BEFORE UPDATE ON bounty_tasks
FOR EACH ROW EXECUTE FUNCTION protect_bounty_target_policy();

CREATE FUNCTION require_bounty_trajectory() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    proof submissions%ROWTYPE;
    binding review_decisions%ROWTYPE;
    evidence trajectory_claims%ROWTYPE;
BEGIN
    SELECT * INTO STRICT proof FROM submissions WHERE id = NEW.submission_id FOR UPDATE;
    IF proof.bounty_policy_version <> 'treasury-share-2p5-chat-v1' THEN
        RETURN NEW;
    END IF;
    SELECT * INTO binding FROM review_decisions
        WHERE submission_id = proof.id AND kind <> 'ADVISORY' ORDER BY id DESC LIMIT 1;
    -- Defect awards compensate an admitted defect, not a full conjecture bounty.
    -- Preserve their separately enforced policy cap and eligibility rules.
    IF binding.kind = 'HUMAN' AND binding.decision = 'APPROVED'
       AND binding.reason_code = 'FORMALIZATION_DEFECT_AWARD'
       AND NEW.eligibility_reason = 'FORMALIZATION_DEFECT_AWARD' THEN
        RETURN NEW;
    END IF;
    SELECT * INTO evidence FROM trajectory_claims
        WHERE submission_id = proof.id FOR UPDATE;
    IF proof.verification_status <> 'VERIFIED' OR proof.manual_review_status <> 'APPROVED'
       OR proof.reward_status NOT IN ('ELIGIBLE', 'REWARDED', 'FAILED')
       OR evidence.id IS NULL OR evidence.status <> 'APPROVED_BOUNTY_EVIDENCE'
       OR evidence.amount_usd_cents <> 0 OR evidence.proof_digest <> proof.proof_digest
       OR evidence.reward_target_id <> proof.reward_target_id
       OR evidence.proof_review_decision_id IS DISTINCT FROM binding.id
       OR binding.decision IS DISTINCT FROM 'APPROVED'
       OR binding.reason_code IS DISTINCT FROM 'REVIEW_APPROVED'
       OR binding.kind IS DISTINCT FROM 'HUMAN'
       OR evidence.proof_policy_snapshot->>'bounty_policy_version' IS DISTINCT FROM proof.bounty_policy_version
       OR evidence.proof_policy_snapshot->>'review_policy_version' IS DISTINCT FROM proof.review_policy_version
    THEN
        RAISE EXCEPTION 'this bounty requires reviewed chat logs matching the accepted proof'
            USING ERRCODE = '23514', CONSTRAINT = 'reward_requires_reviewed_chat_logs';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER reward_requires_trajectory BEFORE INSERT OR UPDATE ON reward_events
FOR EACH ROW EXECUTE FUNCTION require_bounty_trajectory();
"""

event.listen(Base.metadata, "after_create", DDL(GUARDS_SQL.replace("%", "%%")))
event.listen(Base.metadata, "before_drop", DDL("""
DROP FUNCTION IF EXISTS require_bounty_trajectory() CASCADE;
DROP FUNCTION IF EXISTS protect_bounty_target_policy() CASCADE;
"""))
