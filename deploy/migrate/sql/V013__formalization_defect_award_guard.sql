-- Harden the V012 automatic-event trigger against JSON null semantics. Every fixed-USD award
-- field is mandatory, and a missing JSON key must fail closed just like an incorrect value.

CREATE OR REPLACE FUNCTION enforce_locked_reward_event() RETURNS TRIGGER AS $$
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
           OR NEW.pricing_inputs ->> 'award_code'
                IS DISTINCT FROM 'FORMALIZATION_DEFECT_AWARD'
           OR NEW.pricing_inputs ->> 'review_decision_id'
                IS DISTINCT FROM latest_decision.id::TEXT
           OR NEW.pricing_inputs ->> 'netuid' IS DISTINCT FROM '66'
           OR COALESCE(NEW.pricing_inputs ->> 'price_source', '') = ''
           OR COALESCE(NEW.pricing_inputs ->> 'price_observed_at', '') = ''
           OR jsonb_typeof(NEW.pricing_inputs -> 'price_source_urls')
                IS DISTINCT FROM 'array'
           OR NEW.pricing_inputs ->> 'rounding'
                IS DISTINCT FROM 'ROUND_HALF_UP to nearest integer Alpha rao' THEN
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

        IF award_usd IS NULL
           OR alpha_usd IS NULL
           OR award_usd <> 750.00
           OR alpha_usd <= 0 THEN
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
