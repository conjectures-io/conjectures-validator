-- V035 froze history instead of exempting it, and left the coldkey signature in its borrowed
-- home. Both are fixed here.
--
-- Keep colon-prefixed words out of these comments. scripts/check_schema_drift.py feeds each
-- migration through SQLAlchemy's text() construct, which treats a colon followed by an
-- identifier as a bind parameter even inside a SQL comment.
--
-- WHAT V035 GOT WRONG, AND IT IS ONE MISREADING WITH TWO CONSEQUENCES.
--
-- V035 said of its three retirement CHECKs that they "bind every row written from here on and
-- leave history alone", and added them NOT VALID to say so. NOT VALID does not mean that. It
-- exempts existing rows from *validation at creation time* and from nothing else -- the
-- constraint is still evaluated against every new row version, and an UPDATE produces a new row
-- version whether or not it touches the constrained columns. So the retired shapes were not
-- exempted, they were frozen. Any historical submission that names a hotkey now rejects every
-- UPDATE it will ever receive, including a reward transition, a re-review, a verification lease
-- and a correction.
--
-- The second consequence is the one that surfaced first. V035 gave the coldkey signature a real
-- column, `signer_signature`, and said outright that the borrowed home in `hotkey_signature`
-- "has to become a real one" -- but nothing ever moved the bytes. The V028 web rows therefore
-- carry `signer_coldkey` with a NULL `signer_signature`, which matches no branch of
-- `submission_authorised_exactly_once`, so those rows reject every UPDATE too. Two rows already
-- carried a REWARDED verdict that could not be written.
--
-- A CHECK is the wrong instrument for "no new row may do this", because SQL gives a CHECK no way
-- to know an insert from an update. The rule is about intake, so it is enforced where intake
-- happens -- a BEFORE INSERT trigger, raising with the same constraint name the CHECK used, so
-- callers matching on that name are unaffected.


-- --- 1. Intake is enforced at intake -------------------------------------------------------
--
-- One function per table rather than one shared one. A trigger function that had to branch on
-- TG_TABLE_NAME to know which columns to read and which constraint name to raise would be three
-- rules in one body, and the drift check would compare it as one object.

CREATE FUNCTION submissions_reject_hotkey() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.hotkey IS NOT NULL OR NEW.hotkey_signature IS NOT NULL THEN
        RAISE EXCEPTION 'submission may not name a hotkey'
            USING ERRCODE = '23514', CONSTRAINT = 'submission_names_no_hotkey';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER submissions_reject_hotkey
    BEFORE INSERT ON submissions
    FOR EACH ROW EXECUTE FUNCTION submissions_reject_hotkey();

ALTER TABLE submissions
    DROP CONSTRAINT submission_names_no_hotkey;


CREATE FUNCTION submission_intents_reject_hotkey() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.hotkey IS NOT NULL THEN
        RAISE EXCEPTION 'submission intent may not name a hotkey'
            USING ERRCODE = '23514', CONSTRAINT = 'intent_names_no_hotkey';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER submission_intents_reject_hotkey
    BEFORE INSERT ON submission_intents
    FOR EACH ROW EXECUTE FUNCTION submission_intents_reject_hotkey();

ALTER TABLE submission_intents
    DROP CONSTRAINT intent_names_no_hotkey;


-- The same fix for the same reason, and the reason is not hypothetical here either -- a
-- challenge row is minted and then UPDATEd when it is consumed, so a CHECK on `kind` freezes
-- any HOTKEY_LINK or HOTKEY_SESSION challenge that was still open when V035 landed.
CREATE FUNCTION login_challenges_reject_retired_kind() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.kind IN ('HOTKEY_LINK', 'HOTKEY_SESSION') THEN
        RAISE EXCEPTION 'login challenge kind is retired'
            USING ERRCODE = '23514', CONSTRAINT = 'challenge_kind_is_not_retired';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER login_challenges_reject_retired_kind
    BEFORE INSERT ON login_challenges
    FOR EACH ROW EXECUTE FUNCTION login_challenges_reject_retired_kind();

ALTER TABLE login_challenges
    DROP CONSTRAINT challenge_kind_is_not_retired;


-- --- 2. The signature moves to the column that names the key it verifies against -----------
--
-- V028 is explicit that on the web path the 64 bytes in `hotkey_signature` were made by
-- `signer_coldkey` and verify against nothing else on the row. A non-null `signer_coldkey` is
-- therefore exactly the marker for "these bytes are the coldkey's", and it is the whole
-- predicate below. Rows with no `signer_coldkey` are untouched, because for those the signature
-- genuinely does belong to the hotkey beside it.
--
-- The hotkey itself stays where it is. On these rows it was declared and never proved -- a
-- payout nomination, not an authorisation -- and V035's retention argument for it is unchanged.
-- Once the signature has moved, such a row satisfies the coldkey branch, which is what it should
-- have satisfied all along.
--
-- This has to come after part 1 and not before it. A row with a declared hotkey cannot accept
-- any UPDATE at all while `submission_names_no_hotkey` stands, this backfill included -- which
-- is the same trap the CHECK set for the reward transition, sprung on the fix for it.
--
-- `submission_authorised_exactly_once` stays armed throughout. Every row the statement touches
-- lands on its coldkey branch, so the constraint is what proves the backfill correct rather
-- than something to be worked around.
--
-- Two triggers stand down for the one statement. `submissions_protect_signer_coldkey` guards
-- `signer_signature` against exactly this kind of write, and correctly so for any caller that is
-- not this migration. `submissions_touch_updated_at` would restamp `updated_at` on rows whose
-- state is not changing, which would read as a status transition that never happened.
ALTER TABLE submissions DISABLE TRIGGER submissions_protect_signer_coldkey;
ALTER TABLE submissions DISABLE TRIGGER submissions_touch_updated_at;

UPDATE submissions
   SET signer_signature = hotkey_signature,
       hotkey_signature = NULL
 WHERE signer_coldkey   IS NOT NULL
   AND signer_signature IS NULL
   AND hotkey_signature IS NOT NULL;

ALTER TABLE submissions ENABLE TRIGGER submissions_touch_updated_at;
ALTER TABLE submissions ENABLE TRIGGER submissions_protect_signer_coldkey;

-- Proof rather than assertion, and it is also a catalog repair. `submission_authorised_exactly_once`
-- is declared VALID, yet rows exist that satisfy no branch of it -- which means the copy actually
-- in force somewhere was added NOT VALID and the catalog has been overstating what it knows.
-- VALIDATE is a no-op on a constraint already marked valid and a full scan otherwise, and either
-- way this migration stops here rather than proceeding if any row is still unauthorised.
ALTER TABLE submissions VALIDATE CONSTRAINT submission_authorised_exactly_once;


COMMENT ON COLUMN submissions.hotkey IS
    'History only. The miner hotkey a row named before V035 -- proved by hotkey_signature on '
    'the extrinsic and intent paths, merely declared as a payout nomination on the V028 web '
    'path. No new row may set it, enforced by submissions_reject_hotkey rather than by a CHECK '
    'so that these rows stay updatable. Still published as the solver identity when the '
    'account has no display name -- see db/public.py.';

COMMENT ON COLUMN submissions.hotkey_signature IS
    'History only, and only for rows the hotkey itself signed. V038 moved the V028 web rows '
    'signatures to signer_signature, where the key they verify against is named.';
