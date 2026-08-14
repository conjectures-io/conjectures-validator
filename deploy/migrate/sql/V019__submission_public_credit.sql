-- Opt-in authorship is submission data, not account profile data.  The submitting hotkey signs
-- these exact values as part of request_digest; copying them to the submission preserves the
-- credit even if an account later changes its display name or disappears.

ALTER TABLE submissions
    ADD COLUMN public_credit_name  TEXT,
    ADD COLUMN public_credit_url   TEXT,
    ADD COLUMN public_credit_orcid TEXT,
    ADD CONSTRAINT submission_public_credit_name_shape CHECK (
        public_credit_name IS NULL
        OR (length(public_credit_name) BETWEEN 1 AND 128
            AND public_credit_name = btrim(public_credit_name))
    ),
    ADD CONSTRAINT submission_public_credit_url_shape CHECK (
        public_credit_url IS NULL
        OR (public_credit_name IS NOT NULL
            AND length(public_credit_url) BETWEEN 1 AND 2048
            AND public_credit_url LIKE 'https://%')
    ),
    ADD CONSTRAINT submission_public_credit_orcid_shape CHECK (
        public_credit_orcid IS NULL
        OR (public_credit_name IS NOT NULL
            AND public_credit_orcid ~ '^[0-9]{4}-[0-9]{4}-[0-9]{4}-[0-9]{3}[0-9X]$')
    );

-- Credit-funded submissions are signed only after bundle upload, so the intent retains the chosen
-- credit while it computes that digest and then copies it to the submission atomically.
ALTER TABLE submission_intents
    ADD COLUMN public_credit_name  TEXT,
    ADD COLUMN public_credit_url   TEXT,
    ADD COLUMN public_credit_orcid TEXT,
    ADD CONSTRAINT intent_public_credit_name_shape CHECK (
        public_credit_name IS NULL
        OR (length(public_credit_name) BETWEEN 1 AND 128
            AND public_credit_name = btrim(public_credit_name))
    ),
    ADD CONSTRAINT intent_public_credit_url_shape CHECK (
        public_credit_url IS NULL
        OR (public_credit_name IS NOT NULL
            AND length(public_credit_url) BETWEEN 1 AND 2048
            AND public_credit_url LIKE 'https://%')
    ),
    ADD CONSTRAINT intent_public_credit_orcid_shape CHECK (
        public_credit_orcid IS NULL
        OR (public_credit_name IS NOT NULL
            AND public_credit_orcid ~ '^[0-9]{4}-[0-9]{4}-[0-9]{4}-[0-9]{3}[0-9X]$')
    );

-- Status, review and payout columns on a submission continue to move, but authorship never does.
-- A correction therefore needs a new, separately signed policy mechanism rather than an ordinary
-- UPDATE that silently rewrites the public historical record.
CREATE FUNCTION submissions_protect_public_credit() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.public_credit_name IS DISTINCT FROM OLD.public_credit_name
       OR NEW.public_credit_url IS DISTINCT FROM OLD.public_credit_url
       OR NEW.public_credit_orcid IS DISTINCT FROM OLD.public_credit_orcid THEN
        RAISE EXCEPTION 'submission public credit is immutable'
            USING ERRCODE = '23514', CONSTRAINT = 'submission_public_credit_immutable';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER submissions_protect_public_credit
    BEFORE UPDATE OF public_credit_name, public_credit_url, public_credit_orcid ON submissions
    FOR EACH ROW EXECUTE FUNCTION submissions_protect_public_credit();
