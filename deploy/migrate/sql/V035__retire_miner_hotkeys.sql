-- Miners sign with a coldkey. Every miner hotkey stops being an input.
--
-- Keep colon-prefixed words out of these comments. scripts/check_schema_drift.py feeds each
-- migration through SQLAlchemy's text() construct, which treats a colon followed by an
-- identifier as a bind parameter even inside a SQL comment.
--
-- WHAT A HOTKEY WAS DOING HERE, AND WHY EACH USE GOES AWAY.
--
-- The hotkey/coldkey split gave a miner an operational key that could sit unencrypted on a
-- mining box. This schema leaned on it in five separate places, and they are unrelated problems
-- that happened to share a column name:
--
--   1. Submission attribution -- `submissions.hotkey` plus the 64 bytes in `hotkey_signature`.
--   2. Miner identity -- the `linked_hotkeys` table and the HOTKEY_LINK challenge.
--   3. CLI session scope -- `account_sessions.hotkey_scope` and the HOTKEY_SESSION challenge.
--   4. Payout destination -- `accounts.payout_hotkey` and `reward_events.destination_hotkey`.
--   5. Public solver identity -- `submissions.hotkey`, republished by db/public.py.
--
-- Two of those had already started to unwind. V028 added `submissions.signer_coldkey` so a
-- browser wallet could authorise a submission with no hotkey to sign with, and V032 made
-- `hotkey` and `hotkey_signature` nullable so a session could authorise one with no key at all.
-- This migration finishes the job for the remaining three and closes the door behind it.
--
-- THE PAYOUT IS WHAT MADE THE FOURTH USE LOOK MANDATORY, AND IT IS NOT.
--
-- Alpha is held as stake, and a stake position is addressed by (coldkey, hotkey, netuid) -- so
-- "pay a coldkey" reads like it needs a hotkey. It does not, because the payout no longer moves
-- the stake to a *new* position. `SubtensorModule.transfer_stake` changes the *owner* of alpha
-- already staked at the validator's own hotkey, leaving the hotkey where it is. The destination
-- coldkey ends up owning that stake and may restake or unstake it freely. So the destination
-- hotkey stops being something a miner supplies, or that this schema stores for a new payout --
-- it is the validator's own key, and it lives in configuration.
--
-- That also deletes an entire class of intake failure. `POST /v1/submissions/web` used to demand
-- that a *declared* hotkey be registered on chain, because `transfer_stake_and_hotkey` cannot
-- stake to a hotkey nobody owns, and an unknown one would have produced a submission that
-- verifies, wins, and then strands a payout a human signs and watches fail. With no destination
-- hotkey there is nothing to declare and nothing to check.
--
-- WHAT IS KEPT, AND WHY IT IS NOT AN OVERSIGHT.
--
-- The validator's *own* hotkey is untouched everywhere it appears -- the bounty wallet, the
-- emissions worker, the deposit watcher's uid check, and the payout watcher's origin. That key
-- is this validator's neuron identity on the subnet, not a miner's credential, and a subnet
-- validator cannot exist without one.
--
-- Historical rows keep their hotkeys. Nine submissions were attributed by a hotkey signature and
-- past reward events record where the money actually went; dropping those columns would destroy
-- an audit trail that is still the only proof those payouts were owed. The new CHECKs are
-- therefore added NOT VALID -- they bind every row written from here on and leave history alone.


-- --- 1. The account's two coldkeys ---------------------------------------------------------
--
-- `submission_coldkey` and `payout_coldkey` answer different questions and have deliberately
-- different proof requirements. This is the asymmetry to keep in mind when reading the rest:
--
--   * The SUBMISSION coldkey must be PROVED, because it is the key that can have transferred
--     funds. On the extrinsic path the submitter cites a transfer and claims the credit for it;
--     without proof of control anyone could cite somebody else's payment. It is also what
--     authorises spending an account's credits.
--
--   * The PAYOUT coldkey needs NO proof, because naming it can only give money away. An address
--     the account cannot open is an account paying a stranger, which harms nobody but itself and
--     is indistinguishable from a deliberate donation. Demanding a signature would instead lock
--     out the common legitimate case -- paying to a hardware wallet, an exchange deposit, or a
--     multisig the account does not solely control.
--
-- So the payout key is a plain nullable column with no referential tie, and the submission key
-- is pinned to a row in `account_wallets`, which is where the proving signature is already kept.

-- The pair CHECK from V003 goes first: it tied `payout_coldkey` to `payout_hotkey` as
-- both-or-neither, and the hotkey half is about to stop existing.
ALTER TABLE accounts
    DROP CONSTRAINT payout_is_a_complete_pair;

ALTER TABLE accounts
    DROP COLUMN payout_hotkey;

ALTER TABLE accounts
    ADD COLUMN submission_coldkey ss58;

-- The FK target. `coldkey` is already the primary key of `account_wallets`, so this adds no
-- uniqueness that was not already true -- it exists because a composite foreign key needs a
-- unique constraint on exactly the columns it references.
ALTER TABLE account_wallets
    ADD CONSTRAINT account_wallets_account_coldkey_unique UNIQUE (account_id, coldkey);

-- A composite FK, not a single-column one, and that is the whole point: a plain
-- `REFERENCES account_wallets (coldkey)` would prove the key is linked to SOME account and let
-- an account designate a coldkey another account had proved. Including `id` on the referencing
-- side means the designated key must be linked to THIS account or the row will not write.
--
-- MATCH SIMPLE -- the default -- is what makes the column optional. With any referencing column
-- NULL the constraint is satisfied outright, and `submission_coldkey` is the only nullable one,
-- so "no submission coldkey designated" needs no exemption written anywhere.
--
-- ON DELETE SET NULL names its column explicitly (PostgreSQL 15 and later). Unlinking a wallet
-- must clear a designation that points at it rather than being refused -- an account removing a
-- key should not have to remember to undesignate it first -- and the unqualified form would try
-- to null `id` as well, which is the primary key.
ALTER TABLE accounts
    ADD CONSTRAINT account_submission_coldkey_is_linked
        FOREIGN KEY (id, submission_coldkey)
        REFERENCES account_wallets (account_id, coldkey)
        ON DELETE SET NULL (submission_coldkey);

COMMENT ON COLUMN accounts.submission_coldkey IS
    'The one coldkey this account submits and spends credits under. Proved by signature, and '
    'therefore constrained to a row in account_wallets belonging to this same account.';

COMMENT ON COLUMN accounts.payout_coldkey IS
    'Where rewards go. Deliberately unproved and unconstrained: naming an address the account '
    'does not control can only give its own money away.';


-- --- 2. Miner identity ---------------------------------------------------------------------
--
-- `linked_hotkeys` existed to answer "which miner identity may submit" and to attribute a
-- deposit by way of the coldkey that owned one of these. Both questions are now answered by
-- `account_wallets` directly -- a deposit's sender IS a linked coldkey, with no ownership hop
-- through the chain -- so the table has no remaining reader.
--
-- Dropped outright rather than retained as history, unlike the submission columns below. It
-- holds no fact about anything that happened: a row here recorded only that an address was
-- claimed, and every submission that was actually made under one of these hotkeys keeps its own
-- `submissions.hotkey` and the signature that proved it.
DROP TABLE linked_hotkeys;


-- --- 3. CLI session scope ------------------------------------------------------------------
--
-- A bearer session is bounded to the key that minted it, and that key is now a coldkey. The
-- column is renamed rather than replaced because the constraint it carries is unchanged in
-- shape -- a BEARER session has a scope, a COOKIE session does not.
--
-- Live bearer sessions are revoked FIRST, and this is not tidying. Every existing bearer token
-- was minted by a hotkey signature and its `hotkey_scope` names a hotkey; renaming the column
-- underneath it would leave a live credential asserting that a hotkey address is a coldkey
-- scope, which `dependencies.py` would then check against `account_wallets` and never find.
-- Revoking says what actually happened -- the basis for the token is gone -- instead of leaving
-- a token that fails in a way no error message explains.
UPDATE account_sessions
    SET revoked_at = now()
    WHERE kind = 'BEARER' AND revoked_at IS NULL;

ALTER TABLE account_sessions
    DROP CONSTRAINT session_scope_belongs_to_bearer_sessions;

ALTER TABLE account_sessions
    RENAME COLUMN hotkey_scope TO coldkey_scope;

ALTER TABLE account_sessions
    ADD CONSTRAINT session_scope_belongs_to_bearer_sessions
        CHECK ((kind = 'BEARER') = (coldkey_scope IS NOT NULL));

COMMENT ON COLUMN account_sessions.coldkey_scope IS
    'Where a BEARER session''s authority stops: the account coldkey that minted it.';


-- --- 4. Login challenges -------------------------------------------------------------------
--
-- COLDKEY_SESSION joins the signature flows, which all require an address and the verbatim
-- message. Recreated rather than added alongside, because two overlapping CHECKs on one column
-- set is how a rule ends up half-enforced.
ALTER TABLE login_challenges
    DROP CONSTRAINT challenge_wallet_present;

ALTER TABLE login_challenges
    ADD CONSTRAINT challenge_wallet_present
        CHECK (kind NOT IN ('WALLET', 'HOTKEY_LINK', 'HOTKEY_SESSION', 'COLDKEY_LINK',
                            'COLDKEY_SESSION')
               OR (ss58 IS NOT NULL AND message IS NOT NULL));

-- Deliberately NOT added to `challenge_link_has_account`, for the reason V015 gives about
-- HOTKEY_SESSION -- a session challenge is minted before the account is known. The coldkey is
-- looked up in `account_wallets` at verify time, which is what makes an unlinked coldkey a 403
-- rather than an unmintable challenge, and keeps the challenge endpoint from disclosing whether
-- a given address has an account here.

-- The two retired kinds stay in the enum. PostgreSQL has no DROP VALUE, and recreating the type
-- would rewrite the column for no gain: what matters is that nothing new can be minted with
-- them, which is a CHECK rather than a type change. NOT VALID so any consumed history survives.
ALTER TABLE login_challenges
    ADD CONSTRAINT challenge_kind_is_not_retired
        CHECK (kind NOT IN ('HOTKEY_LINK', 'HOTKEY_SESSION')) NOT VALID;


-- --- 5. Submission authorisation -----------------------------------------------------------
--
-- The coldkey web path stored its coldkey signature in `hotkey_signature` -- V028 says so
-- outright, and notes that `signer_coldkey` is what keeps those 64 bytes checkable at all,
-- since they verify against a different key than the column name claims. With the hotkey
-- columns closing to new writes, that borrowed home has to become a real one.
ALTER TABLE submissions
    ADD COLUMN signer_signature BYTEA
        CONSTRAINT signer_signature_len CHECK (octet_length(signer_signature) = 64);

COMMENT ON COLUMN submissions.signer_signature IS
    'The 64 bytes signed by signer_coldkey that authorised this submission. Replaces the '
    'coldkey signature that V028 kept in hotkey_signature, which is now history only.';

-- Both signer columns are set once at insert and never again, so the immutability trigger V028
-- added for `signer_coldkey` has to cover the signature too. Without it the audit trail is only
-- as good as the absence of an UPDATE somewhere.
CREATE OR REPLACE FUNCTION submissions_protect_signer_coldkey() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.signer_coldkey IS DISTINCT FROM OLD.signer_coldkey THEN
        RAISE EXCEPTION 'submission signer is immutable'
            USING ERRCODE = '23514', CONSTRAINT = 'submission_signer_coldkey_immutable';
    END IF;
    IF NEW.signer_signature IS DISTINCT FROM OLD.signer_signature THEN
        RAISE EXCEPTION 'submission signer signature is immutable'
            USING ERRCODE = '23514', CONSTRAINT = 'submission_signer_signature_immutable';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER submissions_protect_signer_coldkey ON submissions;

CREATE TRIGGER submissions_protect_signer_coldkey
    BEFORE UPDATE OF signer_coldkey, signer_signature ON submissions
    FOR EACH ROW EXECUTE FUNCTION submissions_protect_signer_coldkey();

-- Three ways in, exactly one of which authorised any given row. V032 had two branches; the
-- coldkey-signed path is promoted out of the hotkey branch it was sharing.
--
-- This constraint stays VALID, so it must keep admitting the nine historical rows on the legacy
-- branch. Blocking that branch for NEW rows is a separate NOT VALID CHECK below -- one
-- constraint per rule, rather than a single expression that has to be read twice to see which
-- half applies to history.
ALTER TABLE submissions
    DROP CONSTRAINT submission_authorised_exactly_once;

ALTER TABLE submissions
    ADD CONSTRAINT submission_authorised_exactly_once CHECK (
        -- Legacy, history only: a hotkey and the signature that proved it. Covers the old
        -- extrinsic path, the old intent flow, and the V028 coldkey web rows whose signature
        -- sat in `hotkey_signature`.
        (hotkey IS NOT NULL AND hotkey_signature IS NOT NULL)
        -- Coldkey-signed: the extrinsic path and the intent flow as they now stand, plus the
        -- website. The signature proves control of `signer_coldkey`, and that key is what the
        -- payment or the credit is attributed to.
        OR (signer_coldkey IS NOT NULL AND signer_signature IS NOT NULL)
        -- Session-authorised, from V032: no key, no signature, and therefore necessarily an
        -- account that spent a credit. Unchanged.
        OR (hotkey IS NULL
            AND hotkey_signature IS NULL
            AND signer_coldkey IS NULL
            AND signer_signature IS NULL
            AND account_id IS NOT NULL
            AND credit_ledger_id IS NOT NULL)
    );

-- The door, closed. NOT VALID binds every insert and update from here on and exempts the rows
-- already written, which is exactly the retention policy this migration is built around.
ALTER TABLE submissions
    ADD CONSTRAINT submission_names_no_hotkey
        CHECK (hotkey IS NULL AND hotkey_signature IS NULL) NOT VALID;

-- V028's constraint spoke of the coldkey path as "always credit-funded and always account-owned"
-- because the website was the only way to reach it. The extrinsic path is now coldkey-signed
-- too, and it has no account and no credit at all -- it cites a transfer. Rewritten to say what
-- is actually invariant: a coldkey-signed row is either credit-funded by an account or backed by
-- a payment that coldkey itself sent.
ALTER TABLE submissions
    DROP CONSTRAINT submission_signer_coldkey_is_account_owned;

ALTER TABLE submissions
    ADD CONSTRAINT submission_signer_coldkey_is_funded CHECK (
        signer_coldkey IS NULL
        OR (account_id IS NOT NULL AND credit_ledger_id IS NOT NULL)
        OR signer_coldkey = payment_sender
    );

COMMENT ON COLUMN submissions.hotkey IS
    'History only. The miner hotkey that authorised this row before V035; no new row may set '
    'it. Still published as the solver identity for these rows when the account has no display '
    'name -- see db/public.py.';


-- --- 6. Intents ----------------------------------------------------------------------------
--
-- An intent carries the authorising identity from the moment a credit is held until `confirm`
-- copies it onto the submission, so it needs the same door closed on the same column. V028
-- already gave it `signer_coldkey`; the signature arrives at confirm time and was never stored
-- here, so there is no `signer_signature` counterpart to add.
ALTER TABLE submission_intents
    ADD CONSTRAINT intent_names_no_hotkey
        CHECK (hotkey IS NULL) NOT VALID;


-- --- 7. Reward destinations ----------------------------------------------------------------
--
-- A new payout has no destination hotkey to record. `transfer_stake` moves ownership of alpha
-- that stays staked at the validator's own hotkey, so the only hotkey in the call is ours and it
-- comes from configuration -- storing it per reward event would be recording a constant.
--
-- Nullable rather than dropped, and this is the retention that matters most in this file. These
-- rows are the record of money that actually left, matched against the chain by their economic
-- fingerprint; `db/payouts.py` still reconciles a historical `StakeAndHotkeyTransferred` event
-- against the pair it was sent to. Dropping the column would make those past payouts
-- unverifiable.
ALTER TABLE reward_events
    ALTER COLUMN destination_hotkey DROP NOT NULL;

COMMENT ON COLUMN reward_events.destination_hotkey IS
    'History only. The delegated hotkey a pre-V035 payout was staked to. NULL for every payout '
    'made by transfer_stake, where the stake never leaves the validator''s own hotkey.';

COMMENT ON COLUMN reward_events.destination_coldkey IS
    'Where the money went, and now the whole destination: transfer_stake makes this coldkey the '
    'owner of alpha already staked at the validator''s hotkey.';


-- --- 8. Idempotency for the coldkey-signed paths -------------------------------------------
--
-- This is the part that would have broken quietly, and it is the same trap V032 documented one
-- step earlier. `submissions_idempotency_unique` is UNIQUE (hotkey, idempotency_key), and
-- PostgreSQL treats NULLs as distinct -- so now that `hotkey` is null on every new row, that
-- constraint constrains nothing at all. Two identical retries would both insert.
--
-- V032 closed half of this hole with `submissions_session_idempotency_unique`, over
-- (account_id, idempotency_key) WHERE hotkey IS NULL. That covers every account-owned row,
-- which after this migration means the website and intent paths. What it does not cover is the
-- extrinsic path, which has no account at all -- it cites a transfer, and its caller is
-- identified only by the coldkey that signed.
--
-- So this index is the direct successor to the original: the same rule, re-keyed from the
-- hotkey that used to identify a miner to the coldkey that now does. Partial on
-- `signer_coldkey IS NOT NULL` because a session-authorised row has no signer and is already
-- covered by the account-scoped index.
CREATE UNIQUE INDEX submissions_signer_idempotency_unique
    ON submissions (signer_coldkey, idempotency_key)
    WHERE signer_coldkey IS NOT NULL;

COMMENT ON INDEX submissions_signer_idempotency_unique IS
    'Replay protection for the coldkey-signed paths, re-keyed from the hotkey that used to '
    'identify a miner. Without it the retired hotkey constraint would silently stop '
    'constraining anything, because PostgreSQL treats NULLs in a unique index as distinct.';


-- --- 9. The rejection log ------------------------------------------------------------------
--
-- `api_rejection_log.hotkey_claimed` records which address a refused caller claimed to be, so
-- that a miner who paid and was turned away leaves a trace. Callers now claim a coldkey, and
-- writing a coldkey into a column called `hotkey_claimed` would recreate exactly the naming lie
-- this migration is removing from `hotkey_signature`.
--
-- Renamed to a type-neutral name rather than to `coldkey_claimed`, because the existing rows
-- really do hold hotkeys and would be mislabelled by the more specific name. `claimed_ss58` is
-- true of every row before and after: an address a rejected request asserted, unvalidated and
-- unproved either way. That is also why the column has no `ss58` domain and stays TEXT --
-- see the table's own comment in V001.
ALTER TABLE api_rejection_log
    RENAME COLUMN hotkey_claimed TO claimed_ss58;

ALTER INDEX api_rejection_log_hotkey_idx RENAME TO api_rejection_log_claimed_idx;

COMMENT ON COLUMN api_rejection_log.claimed_ss58 IS
    'The address a refused caller claimed, unvalidated and unproved. A hotkey on rows written '
    'before V035 and a coldkey after it, which is why the name commits to neither.';
