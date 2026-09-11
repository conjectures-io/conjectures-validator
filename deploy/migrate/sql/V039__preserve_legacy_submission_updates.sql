-- V038 replaced the retirement CHECKs with insert guards and moved legacy web
-- signatures to signer_signature. Preserve that repaired historical attribution:
-- reject changes to hotkey fields, including adding them to a modern submission,
-- while allowing review, verification and reward status updates.
-- No historical values are rewritten by this migration.

CREATE FUNCTION submissions_protect_legacy_hotkey() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.hotkey IS DISTINCT FROM OLD.hotkey
       OR NEW.hotkey_signature IS DISTINCT FROM OLD.hotkey_signature THEN
        RAISE EXCEPTION 'submission historical hotkey and signature are immutable'
            USING ERRCODE = '23514', CONSTRAINT = 'submission_legacy_hotkey_immutable';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER submissions_protect_legacy_hotkey
    BEFORE UPDATE ON submissions
    FOR EACH ROW EXECUTE FUNCTION submissions_protect_legacy_hotkey();
