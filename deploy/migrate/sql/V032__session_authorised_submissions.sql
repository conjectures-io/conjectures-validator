-- A fourth way into intake, where the browser session *is* the authorisation.
--
-- The three existing paths all end in a Bittensor signature: the extrinsic path and the intent
-- flow sign with a hotkey, and `POST /v1/submissions/web` signs with a coldkey from a browser
-- wallet. That last one already removed the hotkey requirement, which is why a wallet extension
-- is enough today -- but it is still a wallet. An account opened with an email address holds no
-- key of any kind, and `capabilities.submit` correctly told it so.
--
-- What changes: `hotkey` and `hotkey_signature` become nullable, and a submission must be
-- authorised in exactly one of the two ways rather than always by a key.
--
-- What does NOT change, and is worth stating because it is the whole safety argument:
--
--   * a submission that names a hotkey must still carry the signature that proved it, and
--     `admit_proof_bundle` still binds the bundle manifest to that address. Nothing about the
--     key-signed paths is relaxed -- see the CHECK below, which requires the two together.
--   * a session-authorised submission carries no key and no claim to one. It cannot borrow
--     somebody else's attribution, because there is nothing to borrow: `hotkey` is null and the
--     verifier refuses a bundle whose manifest names a miner nobody authenticated.
--   * the payout pipeline needs no change at all. `payout_notifier` resolves its destination
--     through `coalesce(Account.payout_hotkey, Submission.hotkey)` and filters on the result
--     being non-null, so a keyless submission is *skipped* rather than paid to nobody -- and the
--     moment its account links a payout pair, the coalesce resolves and the next poll picks it
--     up. "Awarded, waiting for somewhere to send it" is already the behaviour; this migration
--     is what lets a row reach it.


-- Nullable in the same expand-then-constrain shape V003 used for the payment columns: drop the
-- NOT NULLs, then add a CHECK that says which combinations are actually legal. A bare DROP NOT
-- NULL on its own would permit a submission with a hotkey and no signature, which is the one
-- combination that would matter.
ALTER TABLE submissions
    ALTER COLUMN hotkey           DROP NOT NULL,
    ALTER COLUMN hotkey_signature DROP NOT NULL;

ALTER TABLE submissions
    ADD CONSTRAINT submission_authorised_exactly_once CHECK (
        -- Key-signed: the extrinsic path, the intent flow, and the coldkey web path. The last
        -- stores the coldkey's signature in `hotkey_signature` and names the delegated hotkey
        -- in `hotkey`, so all three satisfy this branch unchanged.
        (hotkey IS NOT NULL AND hotkey_signature IS NOT NULL)
        -- Session-authorised: no key, no signature, and therefore necessarily an account that
        -- spent a credit. Requiring both of those here is what stops the nullability from
        -- reaching the extrinsic path, which has no account at all.
        OR (hotkey IS NULL
            AND hotkey_signature IS NULL
            AND account_id IS NOT NULL
            AND credit_ledger_id IS NOT NULL)
    );

-- The intent carries the hotkey from the moment the credit is held until `confirm` copies it
-- across, so it has to be able to carry its absence too.
ALTER TABLE submission_intents
    ALTER COLUMN hotkey DROP NOT NULL;


-- --- Idempotency, which is the part that would have broken quietly ------------------------
--
-- `submissions_idempotency_unique` is UNIQUE (hotkey, idempotency_key). PostgreSQL treats NULLs
-- as distinct in a unique constraint, so once `hotkey` can be null that constraint stops
-- constraining keyless rows entirely: two identical retries would both insert, and both would
-- spend a credit. The existing constraint is left exactly as it is -- it still covers every row
-- that names a hotkey -- and the hole it now leaves is closed by its own index.
--
-- Scoped to the account rather than made global. An idempotency key is client-generated and only
-- meaningful within the caller that chose it; a global unique index would let one account's UUID
-- collide with another's and refuse a legitimate submission.
CREATE UNIQUE INDEX submissions_session_idempotency_unique
    ON submissions (account_id, idempotency_key)
    WHERE hotkey IS NULL;

-- `submission_intents` needs no counterpart, because it holds no idempotency key: the key is
-- client-generated and lands on the submission at `confirm`. Two identical requests racing past
-- the pre-check therefore both open an intent and both hold a credit, and the loser's hold rolls
-- back with its transaction when the index above refuses its insert. That is the behaviour the
-- coldkey path already documents, and it is unchanged here.
