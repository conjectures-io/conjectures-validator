-- Private chat-log review. Accepted logs are licensed for sale to buyers, never published.
-- V045 adds a minimal boolean authorization projection for new bounties.
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
COMMENT ON TABLE trajectory_claims IS 'Private trajectory review. Contains untrusted sensitive exports. Never expose exports to public result or payout roles; V045 supplies only a boolean authorization projection.';

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
