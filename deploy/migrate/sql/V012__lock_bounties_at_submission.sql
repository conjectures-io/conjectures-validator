-- A bounty quote becomes a durable promise when a submission is accepted after this migration.
-- The proof must still verify and win its reward target, but its amount is no longer repriced
-- while review is pending. Existing submissions remain NULL and keep the payout-time policy
-- under which they were accepted; changing that promise retroactively would be a policy rewrite.

ALTER TABLE submissions
    ADD COLUMN bounty_locked_at TIMESTAMPTZ;

ALTER TABLE submissions
    ALTER COLUMN bounty_locked_at SET DEFAULT now(),
    ADD CONSTRAINT bounty_locked_not_before_submission
        CHECK (bounty_locked_at IS NULL OR bounty_locked_at >= created_at);

COMMENT ON COLUMN submissions.bounty_amount_rao IS
    'V012+ payout lock; legacy rows retain their intake audit quote until payout-time pricing';
COMMENT ON COLUMN submissions.bounty_policy_version IS
    'Pricing policy for the V012+ lock or the legacy intake audit quote';
COMMENT ON COLUMN submissions.bounty_inputs IS
    'Pricing inputs behind the V012+ lock or the legacy intake audit quote';
COMMENT ON COLUMN submissions.bounty_locked_at IS
    'Timestamp at which the submission bounty became immutable; NULL means legacy payout-time pricing';

-- Automatically generated payout instructions need a deduplication key.  Manual retries remain
-- representable because their key is NULL; only the decision-driven first instruction uses it.
ALTER TABLE reward_events
    ADD COLUMN generation_key TEXT,
    ADD CONSTRAINT reward_generation_key_nonempty
        CHECK (generation_key IS NULL OR length(generation_key) BETWEEN 1 AND 128);

CREATE UNIQUE INDEX reward_events_generation_key_idx
    ON reward_events (generation_key)
    WHERE generation_key IS NOT NULL;

COMMENT ON COLUMN reward_events.generation_key IS
    'Idempotency key for an automatically generated payout instruction; NULL for manual attempts';

-- Enabling the notifier must not replay operator-owned payout instructions that predate its
-- durable outbox. Those events were handled manually (including their signer coordination), so
-- establish the migration boundary as already delivered. New events receive ordinary PENDING
-- outbox rows from the worker after V012 commits.
INSERT INTO payout_discord_deliveries (
    reward_event_id,
    signer_wallet,
    discord_user_id,
    status,
    delivered_at
)
SELECT
    reward.id,
    signer.signer_wallet,
    signer.discord_user_id,
    'SENT',
    now()
FROM reward_events AS reward
CROSS JOIN (VALUES
    ('5DkFoRP1gaKrq1LRqWbG1SCHuhHgDELUuRXdGLsv2rU1spsX', '1103995314299490425'),
    ('5CvtfodyyJWU2pxa25QpC2DTvnNuwQEp5HNS4ntMF8Be8BJL', '213454129819942912')
) AS signer(signer_wallet, discord_user_id)
WHERE reward.status = 'PENDING'
  AND reward.extrinsic_reference IS NULL
ON CONFLICT (reward_event_id, signer_wallet) DO NOTHING;

-- A payout instruction must carry the already locked submission facts.  The database enforces
-- this for automatically generated rows so a worker bug cannot silently reprice a reward.
CREATE FUNCTION enforce_locked_reward_event() RETURNS TRIGGER AS $$
DECLARE
    submission_lock submissions%ROWTYPE;
    latest_decision RECORD;
    award_usd NUMERIC;
    alpha_usd NUMERIC;
    calculated_rao BIGINT;
BEGIN
    IF NEW.generation_key IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT * INTO STRICT submission_lock
    FROM submissions
    WHERE id = NEW.submission_id;

    IF submission_lock.reward_status <> 'ELIGIBLE' THEN
        RAISE EXCEPTION 'automatic reward event requires an eligible submission'
            USING ERRCODE = '23514', CONSTRAINT = 'reward_event_requires_eligible_submission';
    END IF;

    IF NEW.generation_key <> 'submission:' || NEW.submission_id::TEXT THEN
        RAISE EXCEPTION 'automatic reward event has the wrong submission generation key'
            USING ERRCODE = '23514', CONSTRAINT = 'reward_event_generation_key_matches_submission';
    END IF;

    SELECT * INTO latest_decision
    FROM review_decisions
    WHERE submission_id = NEW.submission_id
      AND kind <> 'ADVISORY'
    ORDER BY id DESC
    LIMIT 1;

    IF latest_decision.reason_code = 'FORMALIZATION_DEFECT_AWARD' THEN
        IF latest_decision.decision <> 'APPROVED'
           OR NEW.eligibility_reason <> 'FORMALIZATION_DEFECT_AWARD'
           OR NEW.pricing_policy_version <> 'formalization-defect-usd-v1' THEN
            RAISE EXCEPTION 'defect award must match the latest approved review decision'
                USING ERRCODE = '23514', CONSTRAINT = 'reward_event_matches_defect_decision';
        END IF;

        IF NEW.pricing_inputs IS NULL
           OR NEW.pricing_inputs ->> 'award_code' <> 'FORMALIZATION_DEFECT_AWARD'
           OR NEW.pricing_inputs ->> 'review_decision_id' <> latest_decision.id::TEXT
           OR NEW.pricing_inputs ->> 'netuid' <> '66'
           OR COALESCE(NEW.pricing_inputs ->> 'price_source', '') = ''
           OR COALESCE(NEW.pricing_inputs ->> 'price_observed_at', '') = ''
           OR jsonb_typeof(NEW.pricing_inputs -> 'price_source_urls') <> 'array'
           OR NEW.pricing_inputs ->> 'rounding'
                <> 'ROUND_HALF_UP to nearest integer Alpha rao' THEN
            RAISE EXCEPTION 'defect award is missing its required pricing audit inputs'
                USING ERRCODE = '23514', CONSTRAINT = 'reward_event_has_defect_pricing_inputs';
        END IF;

        BEGIN
            award_usd := (NEW.pricing_inputs ->> 'award_usd')::NUMERIC;
            alpha_usd := (NEW.pricing_inputs ->> 'alpha_usd')::NUMERIC;
        EXCEPTION WHEN OTHERS THEN
            RAISE EXCEPTION 'defect award contains invalid numeric pricing inputs'
                USING ERRCODE = '23514', CONSTRAINT = 'reward_event_has_valid_defect_price';
        END;

        IF award_usd <> 750.00 OR alpha_usd <= 0 THEN
            RAISE EXCEPTION 'defect award must price exactly $750 at a positive Alpha/USD rate'
                USING ERRCODE = '23514', CONSTRAINT = 'reward_event_has_valid_defect_price';
        END IF;
        calculated_rao := round(award_usd * 1000000000 / alpha_usd)::BIGINT;
        IF NEW.amount_rao <> calculated_rao THEN
            RAISE EXCEPTION 'defect award amount does not match its recorded Alpha/USD rate'
                USING ERRCODE = '23514', CONSTRAINT = 'reward_event_matches_defect_price';
        END IF;
        RETURN NEW;
    END IF;

    IF submission_lock.bounty_locked_at IS NULL THEN
        RAISE EXCEPTION 'automatic full-bounty event requires a submission-time bounty lock'
            USING ERRCODE = '23514', CONSTRAINT = 'reward_event_requires_bounty_lock';
    END IF;

    IF NEW.amount_rao <> submission_lock.bounty_amount_rao
       OR NEW.pricing_policy_version <> submission_lock.bounty_policy_version
       OR NEW.pricing_inputs IS DISTINCT FROM submission_lock.bounty_inputs THEN
        RAISE EXCEPTION 'automatic full-bounty event must copy the submission bounty lock'
            USING ERRCODE = '23514', CONSTRAINT = 'reward_event_matches_bounty_lock';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER reward_events_enforce_locked_amount
    BEFORE INSERT OR UPDATE OF submission_id, amount_rao, pricing_policy_version, pricing_inputs,
        generation_key
    ON reward_events
    FOR EACH ROW EXECUTE FUNCTION enforce_locked_reward_event();
