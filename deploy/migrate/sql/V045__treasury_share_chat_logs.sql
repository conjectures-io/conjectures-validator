-- New reward targets use a gross-treasury 2.5% share; existing targets and quotes keep their rules.
-- Before applying, prime bounty_tasks with the currently published catalog. This boundary is durable.
INSERT INTO bounty_tasks(reward_target_id, opened_at)
SELECT reward_target_id, min(created_at) FROM submissions GROUP BY reward_target_id
ON CONFLICT (reward_target_id) DO NOTHING;

ALTER TABLE bounty_tasks ADD COLUMN pricing_policy text NOT NULL DEFAULT 'legacy';
ALTER TABLE bounty_tasks ALTER COLUMN pricing_policy SET DEFAULT 'treasury-share-2p5-chat-v1';
ALTER TABLE bounty_tasks ADD CONSTRAINT bounty_tasks_pricing_policy
    CHECK (pricing_policy IN ('legacy', 'treasury-share-2p5-chat-v1'));
ALTER TABLE trajectory_claims DROP CONSTRAINT trajectory_fixed_bonus;
ALTER TABLE trajectory_claims ADD CONSTRAINT trajectory_fixed_bonus CHECK (amount_usd_cents IN (0, 25000, 50000));
ALTER TABLE trajectory_claims DROP CONSTRAINT trajectory_status;
ALTER TABLE trajectory_claims ADD CONSTRAINT trajectory_status CHECK
    (status IN ('UNDER_REVIEW','NEEDS_CHANGES','REJECTED','APPROVED_MANUAL_PAYMENT','WITHDRAWN','EXPIRED','REVOKED_PROOF_CHANGED','APPROVED_BOUNTY_EVIDENCE'));
ALTER TABLE trajectory_claims DROP CONSTRAINT trajectory_approval_reviewed;
ALTER TABLE trajectory_claims ADD CONSTRAINT trajectory_approval_reviewed CHECK
    (status NOT IN ('APPROVED_MANUAL_PAYMENT','APPROVED_BOUNTY_EVIDENCE') OR (reviewer_id IS NOT NULL AND checklist IS NOT NULL AND review_note IS NOT NULL));
ALTER TABLE trajectory_claims ADD CONSTRAINT trajectory_approval_purpose CHECK
    ((status <> 'APPROVED_BOUNTY_EVIDENCE' OR amount_usd_cents = 0) AND (status <> 'APPROVED_MANUAL_PAYMENT' OR amount_usd_cents IN (25000, 50000)));
DROP INDEX trajectory_one_approved_target;
CREATE UNIQUE INDEX trajectory_one_approved_target ON trajectory_claims(reward_target_id)
    WHERE status IN ('APPROVED_MANUAL_PAYMENT','APPROVED_BOUNTY_EVIDENCE');

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

-- Shared by instruction creation, notification and command rendering. Chain history is
-- recorded independently: a later review correction cannot undo a transfer on chain.
CREATE FUNCTION bounty_trajectory_eligible(proof_id uuid, reward_reason text) RETURNS boolean
LANGUAGE sql AS $$
    SELECT COALESCE((SELECT
        proof.bounty_policy_version <> 'treasury-share-2p5-chat-v1' OR (
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
        FROM submissions proof
        LEFT JOIN LATERAL (
            SELECT * FROM review_decisions WHERE submission_id = proof.id AND kind <> 'ADVISORY'
            ORDER BY id DESC LIMIT 1
        ) binding ON true
        LEFT JOIN trajectory_claims evidence ON evidence.submission_id = proof.id
        WHERE proof.id = proof_id
    ), false);
$$;

CREATE FUNCTION require_bounty_trajectory() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- Serialize authorization against proof corrections and claim mutations.
    PERFORM id FROM submissions WHERE id = NEW.submission_id FOR UPDATE;
    PERFORM id FROM trajectory_claims WHERE submission_id = NEW.submission_id FOR UPDATE;
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
