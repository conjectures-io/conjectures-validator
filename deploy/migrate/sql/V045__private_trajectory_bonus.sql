-- Private chat-log review. Accepted logs are licensed for sale to buyers, never published.
-- V046 adds a minimal boolean authorization projection for new bounties.
CREATE TABLE trajectory_claims (
    id uuid PRIMARY KEY,
    -- NULL only while logs for a new bounty wait for the proof submission they must accompany.
    submission_id uuid UNIQUE REFERENCES submissions(id),
    account_id uuid NOT NULL REFERENCES accounts(id),
    proof_digest sha256 NOT NULL UNIQUE,
    reward_target_id text NOT NULL,
    task_bundle_sha256 sha256 NOT NULL,
    status text NOT NULL,
    amount_usd_cents integer NOT NULL,
    terms_version text NOT NULL,
    package bytea,
    package_digest sha256 NOT NULL,
    state_version integer NOT NULL,
    offer_start_at timestamptz NOT NULL,
    revision integer NOT NULL,
    proof_review_decision_id bigint REFERENCES review_decisions(id),
    reviewer_id uuid REFERENCES accounts(id),
    review_note text,
    consent jsonb NOT NULL,
    proof_policy_snapshot jsonb NOT NULL,
    checklist jsonb,
    history jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    audit_expires_at timestamptz NOT NULL,
    CONSTRAINT trajectory_status CHECK (status IN ('DEPOSITED','UNDER_REVIEW','NEEDS_CHANGES','REJECTED','APPROVED_MANUAL_PAYMENT','APPROVED_BOUNTY_EVIDENCE','WITHDRAWN','EXPIRED','REVOKED_PROOF_CHANGED')),
    CONSTRAINT trajectory_deposit CHECK ((status = 'DEPOSITED') = (submission_id IS NULL) AND (submission_id IS NOT NULL OR amount_usd_cents = 0)),
    CONSTRAINT trajectory_required_logs_fixed CHECK (amount_usd_cents > 0 OR (revision = 1 AND status <> 'NEEDS_CHANGES')),
    CONSTRAINT trajectory_no_self_review CHECK (reviewer_id IS NULL OR reviewer_id <> account_id),
    CONSTRAINT trajectory_fixed_bonus CHECK (amount_usd_cents IN (0, 25000, 50000)),
    CONSTRAINT trajectory_approval_purpose CHECK ((status <> 'APPROVED_BOUNTY_EVIDENCE' OR amount_usd_cents = 0) AND (status <> 'APPROVED_MANUAL_PAYMENT' OR amount_usd_cents IN (25000, 50000))),
    CONSTRAINT trajectory_historical_decision CHECK (amount_usd_cents = 0 OR proof_review_decision_id IS NOT NULL),
    CONSTRAINT trajectory_state_version CHECK (state_version > 0),
    CONSTRAINT trajectory_revision_bound CHECK (revision BETWEEN 1 AND 3),
    CONSTRAINT trajectory_package_bound CHECK (package IS NULL OR octet_length(package) <= 33554432),
    CONSTRAINT trajectory_package_hash CHECK (package IS NULL OR pg_catalog.sha256(package) = package_digest),
    CONSTRAINT trajectory_terms CHECK (terms_version LIKE 'chat-log-terms-%'),
    CONSTRAINT trajectory_retention CHECK (expires_at > created_at AND audit_expires_at > expires_at),
    CONSTRAINT trajectory_approval_reviewed CHECK (status NOT IN ('APPROVED_MANUAL_PAYMENT','APPROVED_BOUNTY_EVIDENCE') OR (reviewer_id IS NOT NULL AND checklist IS NOT NULL AND review_note IS NOT NULL AND proof_review_decision_id IS NOT NULL))
);
CREATE INDEX trajectory_account_idx ON trajectory_claims(account_id, created_at);
CREATE INDEX trajectory_queue_idx ON trajectory_claims(status, created_at);
CREATE UNIQUE INDEX trajectory_one_approved_target ON trajectory_claims(reward_target_id)
    WHERE status IN ('APPROVED_MANUAL_PAYMENT','APPROVED_BOUNTY_EVIDENCE');
CREATE TABLE trajectory_fingerprints (
    digest sha256 PRIMARY KEY,
    claim_id uuid NOT NULL REFERENCES trajectory_claims(id) ON DELETE CASCADE
);
COMMENT ON TABLE trajectory_claims IS 'Private trajectory review. Contains untrusted sensitive exports. Never expose exports to public result or payout roles; V046 supplies only a boolean authorization projection.';

CREATE INDEX trajectory_fingerprint_claim_idx ON trajectory_fingerprints(claim_id);
CREATE TABLE trajectory_access_log (
    id uuid PRIMARY KEY,
    claim_id uuid NOT NULL REFERENCES trajectory_claims(id) ON DELETE CASCADE,
    actor_id uuid NOT NULL REFERENCES accounts(id),
    accessed_at timestamptz NOT NULL
);
CREATE INDEX trajectory_access_claim_idx ON trajectory_access_log(claim_id);
REVOKE ALL ON trajectory_claims, trajectory_fingerprints, trajectory_access_log FROM PUBLIC;
-- No deployment-role names are guessed. Inspect pg_default_acl and explicit relation grants
-- before launch; remove any analytics/replica/public-reader grants with the actual role names.

CREATE FUNCTION forbid_trajectory_access_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'trajectory access records cannot be updated';
END;
$$;
CREATE TRIGGER trajectory_access_no_update BEFORE UPDATE ON trajectory_access_log
FOR EACH ROW EXECUTE FUNCTION forbid_trajectory_access_update();

-- The moment chat-log intake opened, stamped by scripts/open_chat_log_intake.py. Proofs
-- submitted before it qualify for the $500/$250 bonus for one year. It can be corrected only
-- together with a correction record in the same transaction, and never deleted.
CREATE TABLE chat_log_program (
    id smallint PRIMARY KEY,
    opened_at timestamptz NOT NULL,
    terms_version text NOT NULL,
    terms_sha256 sha256 NOT NULL,
    opened_by text NOT NULL,
    CONSTRAINT chat_log_program_single CHECK (id = 1),
    CONSTRAINT chat_log_program_terms CHECK (terms_version LIKE 'chat-log-terms-%'),
    CONSTRAINT chat_log_program_opened_by CHECK (length(opened_by) BETWEEN 1 AND 255)
);
REVOKE ALL ON chat_log_program FROM PUBLIC;

CREATE TABLE chat_log_program_corrections (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    previous_opened_at timestamptz NOT NULL,
    opened_at timestamptz NOT NULL,
    corrected_by text NOT NULL,
    reason text NOT NULL,
    corrected_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT chat_log_program_corrections_by CHECK (length(corrected_by) BETWEEN 1 AND 255),
    CONSTRAINT chat_log_program_corrections_reason CHECK (length(reason) BETWEEN 1 AND 2000)
);
REVOKE ALL ON chat_log_program_corrections FROM PUBLIC;

CREATE FUNCTION forbid_chat_log_program_change() RETURNS trigger
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
FOR EACH STATEMENT EXECUTE FUNCTION forbid_chat_log_program_change();
CREATE FUNCTION forbid_chat_log_correction_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'chat-log program corrections are permanent' USING ERRCODE = '23514';
END;
$$;
CREATE TRIGGER chat_log_program_corrections_permanent BEFORE UPDATE OR DELETE ON chat_log_program_corrections
FOR EACH ROW EXECUTE FUNCTION forbid_chat_log_correction_change();
CREATE TRIGGER chat_log_program_corrections_no_truncate BEFORE TRUNCATE ON chat_log_program_corrections
FOR EACH STATEMENT EXECUTE FUNCTION forbid_chat_log_correction_change();
