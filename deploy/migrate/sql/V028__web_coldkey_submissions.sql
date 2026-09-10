-- Website submissions are authorised by a coldkey, because a browser wallet cannot sign with a
-- hotkey. Talisman and the tao.com extension hold coldkeys only; a hotkey lives on a mining box
-- and never reaches the browser. So `POST /v1/submissions/web` verifies a coldkey signature and
-- has to record which coldkey made it: without that, the 64 bytes in `hotkey_signature` verify
-- against nothing on the row and the audit trail is a dead end.
--
-- Nullable, and it stays null on both existing paths. The extrinsic path and the three-call
-- intent path are both signed by the row's own `hotkey`, which is where a reader should keep
-- looking; a non-null value here is what says "look at this key instead".

ALTER TABLE submissions
    ADD COLUMN signer_coldkey ss58,
    -- A coldkey-authorised submission is always credit-funded and always account-owned: there
    -- is no browser path to the extrinsic endpoint, and the signature is only meaningful
    -- against a wallet linked to the account that spent the credit.
    ADD CONSTRAINT submission_signer_coldkey_is_account_owned CHECK (
        signer_coldkey IS NULL
        OR (account_id IS NOT NULL AND credit_ledger_id IS NOT NULL)
    );

-- The intent carries it from the moment the credit is held until `confirm` copies it across,
-- for the same reason it carries the public credit: the submission row is written in one
-- statement at the end, and everything it needs has to survive until then.
ALTER TABLE submission_intents
    ADD COLUMN signer_coldkey ss58;

-- Who authorised an attempt is part of the historical record of what was submitted, exactly like
-- the public credit V019 froze. Status, review and payout columns keep moving; the signer does
-- not, so an ordinary UPDATE must not be able to re-attribute a submission to another wallet.
-- `hotkey_signature` is deliberately left out: nothing writes it after the insert either, and
-- adding a trigger over a column both existing intake paths already own is a change to those
-- paths rather than to this one.
CREATE FUNCTION submissions_protect_signer_coldkey() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.signer_coldkey IS DISTINCT FROM OLD.signer_coldkey THEN
        RAISE EXCEPTION 'submission signer is immutable'
            USING ERRCODE = '23514', CONSTRAINT = 'submission_signer_coldkey_immutable';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER submissions_protect_signer_coldkey
    BEFORE UPDATE OF signer_coldkey ON submissions
    FOR EACH ROW EXECUTE FUNCTION submissions_protect_signer_coldkey();
