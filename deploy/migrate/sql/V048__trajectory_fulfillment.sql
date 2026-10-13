-- Manual bonus accounting and private buyer delivery; no transfer or publication occurs.

CREATE TABLE trajectory_payments (
	id UUID NOT NULL, 
	claim_id UUID NOT NULL, 
	prepared_by UUID NOT NULL, 
	prepared_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	claim_state_version INTEGER NOT NULL, 
	amount_usd_cents INTEGER NOT NULL, 
	amount_rao BIGINT NOT NULL, 
	alpha_usd NUMERIC(38, 18) NOT NULL, 
	price_source TEXT NOT NULL, 
	price_observed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	destination_coldkey ss58 NOT NULL, 
	origin_coldkey ss58 NOT NULL, 
	origin_hotkey ss58 NOT NULL, 
	destination_evidence TEXT NOT NULL, 
	status TEXT NOT NULL, 
	treasury_payout_id BIGINT, 
	completed_at TIMESTAMP WITH TIME ZONE, 
	completed_by UUID, 
	completion_evidence JSONB, 
	PRIMARY KEY (id), 
	CONSTRAINT trajectory_payment_status CHECK (status IN ('PREPARED','CONFIRMED','CANCELLED')), 
	CONSTRAINT trajectory_payment_amount CHECK (amount_usd_cents IN (25000,50000) AND amount_rao > 0 AND alpha_usd > 0), 
	CONSTRAINT trajectory_payment_version CHECK (claim_state_version > 0), 
	CONSTRAINT trajectory_payment_evidence CHECK (length(price_source) BETWEEN 1 AND 1000 AND length(destination_evidence) BETWEEN 10 AND 2000), 
	CONSTRAINT trajectory_payment_price_time CHECK (price_observed_at <= prepared_at AND price_observed_at >= prepared_at - interval '10 minutes'), 
	CONSTRAINT trajectory_payment_chain CHECK ((status = 'CONFIRMED') = (treasury_payout_id IS NOT NULL)), 
	CONSTRAINT trajectory_payment_completion CHECK ((status = 'PREPARED' AND completed_at IS NULL AND completed_by IS NULL AND completion_evidence IS NULL) OR (status <> 'PREPARED' AND completed_at IS NOT NULL AND completed_at >= prepared_at AND completed_by IS NOT NULL AND completion_evidence IS NOT NULL)), 
	FOREIGN KEY(claim_id) REFERENCES trajectory_claims (id), 
	FOREIGN KEY(prepared_by) REFERENCES accounts (id), 
	UNIQUE (treasury_payout_id), 
	FOREIGN KEY(treasury_payout_id) REFERENCES treasury_payouts (id), 
	FOREIGN KEY(completed_by) REFERENCES accounts (id)
);

CREATE UNIQUE INDEX trajectory_payment_one_live_claim ON trajectory_payments (claim_id) WHERE status <> 'CANCELLED';

CREATE TABLE trajectory_deliveries (
	id UUID NOT NULL, 
	claim_id UUID NOT NULL, 
	buyer_id UUID NOT NULL, 
	created_by UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	claim_state_version INTEGER NOT NULL, 
	package_digest sha256 NOT NULL, 
	agreement_reference TEXT NOT NULL, 
	agreement_sha256 sha256 NOT NULL, 
	accepted_at TIMESTAMP WITH TIME ZONE, 
	assent_text TEXT, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	revoked_by UUID, 
	revocation_reason TEXT, 
	takedown_recorded_at TIMESTAMP WITH TIME ZONE, 
	takedown_recorded_by UUID, 
	takedown_reference TEXT, 
	PRIMARY KEY (id), 
	CONSTRAINT trajectory_delivery_version CHECK (claim_state_version > 0), 
	CONSTRAINT trajectory_delivery_agreement CHECK (length(agreement_reference) BETWEEN 10 AND 2000), 
	CONSTRAINT trajectory_delivery_assent CHECK ((accepted_at IS NULL) = (assent_text IS NULL) AND (assent_text IS NULL OR length(assent_text) BETWEEN 1 AND 2000)), 
	CONSTRAINT trajectory_delivery_consent_time CHECK (accepted_at IS NULL OR accepted_at >= created_at), 
	CONSTRAINT trajectory_delivery_revocation CHECK ((revoked_at IS NULL AND revoked_by IS NULL AND revocation_reason IS NULL) OR (revoked_at IS NOT NULL AND revoked_at >= created_at AND revoked_by IS NOT NULL AND revocation_reason IS NOT NULL AND length(revocation_reason) BETWEEN 10 AND 2000)), 
	CONSTRAINT trajectory_delivery_takedown CHECK ((takedown_recorded_at IS NULL AND takedown_recorded_by IS NULL AND takedown_reference IS NULL) OR (revoked_at IS NOT NULL AND takedown_recorded_at IS NOT NULL AND takedown_recorded_at >= revoked_at AND takedown_recorded_by IS NOT NULL AND takedown_reference IS NOT NULL AND length(takedown_reference) BETWEEN 10 AND 2000)), 
	FOREIGN KEY(claim_id) REFERENCES trajectory_claims (id), 
	FOREIGN KEY(buyer_id) REFERENCES accounts (id), 
	FOREIGN KEY(created_by) REFERENCES accounts (id), 
	FOREIGN KEY(revoked_by) REFERENCES accounts (id), 
	FOREIGN KEY(takedown_recorded_by) REFERENCES accounts (id)
);

CREATE UNIQUE INDEX trajectory_delivery_one_live_buyer ON trajectory_deliveries (claim_id, buyer_id) WHERE revoked_at IS NULL;

CREATE TABLE trajectory_exports (
	id UUID NOT NULL, 
	delivery_id UUID NOT NULL, 
	exported_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	proof_review_decision_id BIGINT NOT NULL, 
	claim_state_version INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(delivery_id) REFERENCES trajectory_deliveries (id), 
	FOREIGN KEY(proof_review_decision_id) REFERENCES review_decisions (id)
);

CREATE INDEX trajectory_exports_delivery_idx ON trajectory_exports (delivery_id);


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

