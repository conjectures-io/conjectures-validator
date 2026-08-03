-- Accounts, credits, and the two-phase submission intent flow.
--
-- V001 modelled one thing: a paid submission, funded by one finalized extrinsic. This adds the
-- account layer the website needs, and with it a second way to fund a submission.
--
-- Keep colon-prefixed words out of these comments. scripts/check_schema_drift.py feeds each
-- migration through SQLAlchemy's text() construct, which treats a colon followed by an
-- identifier as a bind parameter even inside a SQL comment.
--
-- THE FUNDING INVARIANT CHANGES HERE. V001 made every payment column on `submissions` NOT NULL,
-- so a row existed only for a confirmed transfer. A credit-funded submission has no single
-- extrinsic behind it: the transfer paid for credits, and the credits were spent later. So the
-- invariant weakens from "always has a payment" to "always has exactly one funding source",
-- enforced by submission_funded_exactly_once at the bottom of this file. Both paths still
-- require money to have arrived and been confirmed before the row exists; what changes is
-- whether the money is named by an extrinsic or by a ledger entry.


-- --- Accounts ---------------------------------------------------------------------------

-- Roles are a TEXT[] with a containment CHECK rather than a domain over an array or a join
-- table. MINER is the default and needs no grant; REVIEWER and ADMIN gate the Stage 3 review
-- queue and exist here so the session layer has something to carry. A join table would be the
-- textbook shape, but there are three values, no attributes on the relation, and every read
-- wants all of them at once.
CREATE TABLE accounts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- NULL for a wallet-only account. Uniqueness is enforced case-insensitively by the index
    -- below rather than by a constraint here, because two addresses differing only in case are
    -- the same mailbox and one of them would otherwise be able to claim the other's account.
    email           TEXT
        CONSTRAINT account_email_shape CHECK (email IS NULL OR email ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'),
    email_verified  BOOLEAN NOT NULL DEFAULT false,

    display_name    TEXT
        CONSTRAINT display_name_length CHECK (display_name IS NULL OR length(display_name) BETWEEN 1 AND 64),

    roles           TEXT[] NOT NULL DEFAULT ARRAY['MINER']::TEXT[],

    -- Where rewards go. Alpha is held as stake, so a payout needs both keys, and the pair is
    -- set together or not at all: half a destination cannot be paid to.
    payout_coldkey  ss58,
    payout_hotkey   ss58,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT account_roles_are_known
        CHECK (roles <@ ARRAY['MINER', 'REVIEWER', 'ADMIN']::TEXT[]),
    CONSTRAINT payout_is_a_complete_pair
        CHECK ((payout_coldkey IS NULL) = (payout_hotkey IS NULL)),
    CONSTRAINT accounts_updated_not_before_created CHECK (updated_at >= created_at)
);

CREATE UNIQUE INDEX accounts_email_idx ON accounts (lower(email)) WHERE email IS NOT NULL;

CREATE FUNCTION accounts_touch_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER accounts_touch_updated_at
    BEFORE UPDATE ON accounts
    FOR EACH ROW EXECUTE FUNCTION accounts_touch_updated_at();


-- A coldkey the account signs in with. Separate from linked_hotkeys because the two answer
-- different questions: this one is "who is logging in", that one is "which miner identity may
-- submit". One coldkey signs in to exactly one account, so the address is globally unique.
CREATE TABLE account_wallets (
    account_id      UUID NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    coldkey         ss58 PRIMARY KEY,
    -- The signature that proved control of the key, kept so the link stays auditable.
    signature       BYTEA NOT NULL
        CONSTRAINT wallet_signature_len CHECK (octet_length(signature) = 64),
    linked_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX account_wallets_account_idx ON account_wallets (account_id);


-- A hotkey the account may submit proofs under, proven by a signature over a server nonce.
-- Also how a deposit is attributed: a transfer's sender is a coldkey, and that coldkey owning
-- one of these is what ties the money to an account.
CREATE TABLE linked_hotkeys (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id      UUID NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    -- Globally unique: two accounts claiming one hotkey would make submission attribution
    -- ambiguous, and the reward would have no single owner.
    hotkey          ss58 NOT NULL UNIQUE,
    signature       BYTEA NOT NULL
        CONSTRAINT hotkey_link_signature_len CHECK (octet_length(signature) = 64),
    linked_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX linked_hotkeys_account_idx ON linked_hotkeys (account_id, linked_at DESC);


-- --- Sessions ---------------------------------------------------------------------------

-- Only the SHA-256 of the session token is stored, never the token. A database read — a dump, a
-- backup, a replica, an over-broad SELECT — must not yield anything that can be replayed as a
-- credential, exactly as for a password.
CREATE TABLE account_sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id      UUID NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,

    token_sha256    sha256 NOT NULL UNIQUE,
    -- The CSRF token for this session, also stored hashed. Bound to the session rather than
    -- being a bare double-submit cookie, so a value the client can set is not itself the proof.
    csrf_sha256     sha256 NOT NULL,

    issued_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Rolling: extended on use, so an active session does not expire under someone and an
    -- abandoned one does.
    expires_at      TIMESTAMPTZ NOT NULL,
    revoked_at      TIMESTAMPTZ,

    user_agent      TEXT,
    source_ip       INET,

    CONSTRAINT session_expires_after_issue CHECK (expires_at > issued_at)
);

-- The lookup every authenticated request makes, and the one that has to stay fast.
CREATE INDEX account_sessions_live_idx ON account_sessions (expires_at)
    WHERE revoked_at IS NULL;
CREATE INDEX account_sessions_account_idx ON account_sessions (account_id, issued_at DESC);


CREATE TYPE login_challenge_kind AS ENUM (
    'EMAIL',        -- a magic link token
    'WALLET',       -- a coldkey sign-in nonce
    'HOTKEY_LINK'   -- a nonce for attaching a hotkey to an existing account
);

-- One table for all three, because the rules are identical: single use, short lived, rate
-- limited, and the secret stored only as a digest.
CREATE TABLE login_challenges (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind            login_challenge_kind NOT NULL,

    -- Set for HOTKEY_LINK, which attaches to a known account. NULL for the two sign-in kinds,
    -- where the account may not exist yet.
    account_id      UUID REFERENCES accounts (id) ON DELETE CASCADE,
    email           TEXT,
    ss58            TEXT,

    -- Hash of the emailed token or the signing nonce. Never the value itself.
    secret_sha256   sha256 NOT NULL UNIQUE,
    -- The exact bytes the client is asked to sign, stored verbatim so verification never
    -- reconstructs them and cannot reconstruct them differently.
    message         TEXT,

    expires_at      TIMESTAMPTZ NOT NULL,
    consumed_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT challenge_email_present
        CHECK (kind <> 'EMAIL' OR email IS NOT NULL),
    CONSTRAINT challenge_wallet_present
        CHECK (kind NOT IN ('WALLET', 'HOTKEY_LINK') OR (ss58 IS NOT NULL AND message IS NOT NULL)),
    CONSTRAINT challenge_link_has_account
        CHECK (kind <> 'HOTKEY_LINK' OR account_id IS NOT NULL),
    CONSTRAINT challenge_expires_after_creation CHECK (expires_at > created_at)
);

-- Rate-limit lookups: how many challenges this mailbox or address asked for recently.
CREATE INDEX login_challenges_email_idx ON login_challenges (lower(email), created_at DESC)
    WHERE email IS NOT NULL;
CREATE INDEX login_challenges_ss58_idx ON login_challenges (ss58, created_at DESC)
    WHERE ss58 IS NOT NULL;
CREATE INDEX login_challenges_expiry_idx ON login_challenges (expires_at)
    WHERE consumed_at IS NULL;


-- --- Credits ----------------------------------------------------------------------------

CREATE TYPE credit_entry_kind AS ENUM (
    'DEPOSIT',      -- confirmed transfer into the treasury
    'SPEND',        -- one verification attempt
    'REFUND',       -- an attempt that was never made, or was the validator's fault
    'ADJUSTMENT',   -- an operator correction, always with a reason
    'BONUS'         -- a package bonus or promotion
);

-- Append-only. Make it so in deployment: REVOKE UPDATE, DELETE from the service role. A ledger
-- that can be rewritten is not a ledger, and the balance is derived from it rather than cached
-- anywhere, so a rewrite would silently restate history.
--
-- Amounts are in rao, signed. Credits are DERIVED, not stored: a credit is one verification
-- attempt at the price in force, so `credits_available` is the balance divided by that price.
-- Storing a credit count as well would create two numbers that can disagree.
CREATE TABLE credit_ledger (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id          UUID NOT NULL REFERENCES accounts (id),

    kind                credit_entry_kind NOT NULL,
    amount_rao          BIGINT NOT NULL,
    -- The price in force when an attempt was spent or refunded, so a later reprice does not
    -- restate what a past attempt cost.
    credit_price_rao    BIGINT
        CONSTRAINT ledger_price_positive CHECK (credit_price_rao IS NULL OR credit_price_rao > 0),

    -- What caused the entry. The foreign keys to `deposits` and `submission_intents` are added
    -- by ALTER further down, because those tables are created after this one.
    --
    -- A SPEND names its INTENT, not its submission. That is a correctness requirement, not a
    -- preference: `submissions.credit_ledger_id` points here, so if a SPEND also pointed at its
    -- submission the two rows would reference each other and neither could be inserted first —
    -- and since this table is append-only, the usual insert-then-backfill is not available. The
    -- intent already exists when the credit is spent, so pointing at it breaks the cycle. The
    -- submission is still reachable, via submission_intents.submission_id.
    deposit_id          UUID,
    intent_id           UUID,
    -- Free text for ADJUSTMENT and BONUS, which have no other row behind them.
    reason              TEXT,
    created_by          TEXT NOT NULL,   -- 'system', or the operator who made an adjustment

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ledger_amount_nonzero CHECK (amount_rao <> 0),
    -- Signs are fixed per kind, so a debit can never be recorded as a credit by passing the
    -- wrong sign. ADJUSTMENT is the only kind allowed either direction, and it must say why.
    CONSTRAINT ledger_credits_are_positive
        CHECK (kind NOT IN ('DEPOSIT', 'REFUND', 'BONUS') OR amount_rao > 0),
    CONSTRAINT ledger_spends_are_negative
        CHECK (kind <> 'SPEND' OR amount_rao < 0),
    CONSTRAINT ledger_adjustments_explain_themselves
        CHECK (kind <> 'ADJUSTMENT' OR reason IS NOT NULL),
    CONSTRAINT ledger_spend_names_its_intent
        CHECK (kind <> 'SPEND' OR (intent_id IS NOT NULL AND credit_price_rao IS NOT NULL)),
    CONSTRAINT ledger_deposit_names_its_deposit
        CHECK (kind <> 'DEPOSIT' OR deposit_id IS NOT NULL)
);

-- The balance query: every entry for one account. Ordered so the ledger page reads newest
-- first off the index.
CREATE INDEX credit_ledger_account_idx ON credit_ledger (account_id, id DESC);
-- One SPEND per intent. A second debit for the same attempt is double-charging, and an intent
-- is exactly one attempt.
CREATE UNIQUE INDEX credit_ledger_spend_idx ON credit_ledger (intent_id)
    WHERE kind = 'SPEND';


CREATE TYPE deposit_state AS ENUM (
    'AWAITING_TRANSFER',    -- created, nothing seen on chain yet
    'SEEN_UNFINALIZED',     -- the transfer is visible but not final; credits not yet issued
    'CREDITED',             -- finalized and the ledger entry written
    'EXPIRED',              -- the window closed with no transfer
    'FAILED'                -- seen, but wrong recipient or amount
);

CREATE TABLE deposits (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id              UUID NOT NULL REFERENCES accounts (id),

    -- What the account said it would send, and where. Recorded at creation so the confirmation
    -- has something to check against rather than crediting whatever arrived.
    amount_rao              BIGINT NOT NULL
        CONSTRAINT deposit_amount_positive CHECK (amount_rao > 0),
    treasury_address        ss58 NOT NULL,
    credit_price_rao        BIGINT NOT NULL
        CONSTRAINT deposit_price_positive CHECK (credit_price_rao > 0),

    status                  deposit_state NOT NULL DEFAULT 'AWAITING_TRANSFER',

    -- Observed on chain. NULL until something is seen.
    extrinsic_reference     TEXT
        CONSTRAINT deposit_reference_length CHECK (extrinsic_reference IS NULL OR length(extrinsic_reference) BETWEEN 1 AND 128),
    sender_coldkey          ss58,
    observed_amount_rao     BIGINT
        CONSTRAINT deposit_observed_positive CHECK (observed_amount_rao IS NULL OR observed_amount_rao > 0),
    block                   BIGINT
        CONSTRAINT deposit_block_positive CHECK (block IS NULL OR block > 0),
    failure_reason          TEXT,

    -- The ledger entry this deposit produced. One deposit credits at most once.
    credited_ledger_id      BIGINT UNIQUE REFERENCES credit_ledger (id),

    expires_at              TIMESTAMPTZ NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT deposit_credited_needs_ledger_and_finality
        CHECK (status <> 'CREDITED'
               OR (credited_ledger_id IS NOT NULL
                   AND extrinsic_reference IS NOT NULL
                   AND observed_amount_rao IS NOT NULL
                   AND block IS NOT NULL)),
    CONSTRAINT deposit_seen_needs_a_reference
        CHECK (status <> 'SEEN_UNFINALIZED' OR extrinsic_reference IS NOT NULL),
    CONSTRAINT deposit_failed_needs_a_reason
        CHECK (status <> 'FAILED' OR failure_reason IS NOT NULL),
    CONSTRAINT deposits_updated_not_before_created CHECK (updated_at >= created_at)
);

-- One transfer funds one deposit, the same rule submissions.payment_reference follows. Partial,
-- so the many rows with no reference yet do not collide on NULL.
CREATE UNIQUE INDEX deposits_extrinsic_idx ON deposits (extrinsic_reference)
    WHERE extrinsic_reference IS NOT NULL;
CREATE INDEX deposits_account_idx ON deposits (account_id, created_at DESC);
-- The reconciliation worker's queue: everything not yet resolved.
CREATE INDEX deposits_open_idx ON deposits (created_at)
    WHERE status IN ('AWAITING_TRANSFER', 'SEEN_UNFINALIZED');

-- The other half of the mutual reference: credit_ledger.deposit_id names the deposit that
-- produced the entry, and deposits.credited_ledger_id names the entry the deposit produced.
-- Both are NULLable, so neither insert order deadlocks.
ALTER TABLE credit_ledger
    ADD CONSTRAINT credit_ledger_deposit_fkey
        FOREIGN KEY (deposit_id) REFERENCES deposits (id);

CREATE FUNCTION deposits_touch_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER deposits_touch_updated_at
    BEFORE UPDATE ON deposits
    FOR EACH ROW EXECUTE FUNCTION deposits_touch_updated_at();


-- --- Submission intents -----------------------------------------------------------------

CREATE TYPE intent_state AS ENUM (
    'OPEN',             -- credit held, no bundle yet
    'BUNDLE_ATTACHED',  -- bundle admitted, digest returned, awaiting a signature
    'CONFIRMED',        -- credit debited and the submission written
    'EXPIRED',          -- the window closed; the held credit is released
    'CANCELLED'
);

-- A held credit and, once uploaded, the bundle it will be spent on.
--
-- The hold is state on this row rather than a ledger entry, because a hold is not a movement of
-- money: it is a claim on the balance that either becomes a SPEND or evaporates. Writing holds
-- to the ledger would put entries there that later have to be undone, which is what an
-- append-only ledger cannot do. `credits_available` is therefore the balance minus the sum of
-- live holds.
CREATE TABLE submission_intents (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id          UUID NOT NULL REFERENCES accounts (id),
    -- Which linked hotkey will own the resulting submission. Checked against linked_hotkeys at
    -- creation, and it is what the confirming signature must come from.
    hotkey              ss58 NOT NULL,

    task_id             TEXT NOT NULL
        CONSTRAINT intent_task_id_nonempty CHECK (length(task_id) BETWEEN 1 AND 255),
    task_bundle_sha256  sha256 NOT NULL,

    status              intent_state NOT NULL DEFAULT 'OPEN',
    credits_held        INTEGER NOT NULL DEFAULT 1
        CONSTRAINT intent_holds_something CHECK (credits_held > 0),
    credit_price_rao    BIGINT NOT NULL
        CONSTRAINT intent_price_positive CHECK (credit_price_rao > 0),

    -- The admitted proof, held between the bundle upload and the confirmation. It lives here
    -- rather than in `proofs` because `proofs` is the record of what was verified, and an
    -- unconfirmed intent has not paid for verification yet. Moved across in the confirming
    -- transaction, and discarded with the row on expiry.
    proof_content       BYTEA,
    proof_sha256        sha256,
    -- The canonical request digest the client signs. Computed by the server from the bundle, so
    -- the client never chooses what it is signing.
    request_digest      sha256,

    submission_id       UUID UNIQUE REFERENCES submissions (id),

    expires_at          TIMESTAMPTZ NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT intent_proof_is_paired
        CHECK ((proof_content IS NULL) = (proof_sha256 IS NULL)),
    CONSTRAINT intent_proof_digest_matches
        CHECK (proof_content IS NULL OR proof_sha256 = pg_catalog.sha256(proof_content)),
    CONSTRAINT intent_attached_has_a_proof
        CHECK (status <> 'BUNDLE_ATTACHED' OR (proof_sha256 IS NOT NULL AND request_digest IS NOT NULL)),
    CONSTRAINT intent_confirmed_has_a_submission
        CHECK (status <> 'CONFIRMED' OR submission_id IS NOT NULL),
    CONSTRAINT intent_expires_after_creation CHECK (expires_at > created_at),
    CONSTRAINT intents_updated_not_before_created CHECK (updated_at >= created_at)
);

-- The live-hold sum behind `credits_available`, and the expiry sweeper's queue.
CREATE INDEX submission_intents_live_idx ON submission_intents (account_id, expires_at)
    WHERE status IN ('OPEN', 'BUNDLE_ATTACHED');
CREATE INDEX submission_intents_account_idx ON submission_intents (account_id, created_at DESC);

-- The deferred half of the SPEND linkage described on credit_ledger.intent_id.
ALTER TABLE credit_ledger
    ADD CONSTRAINT credit_ledger_intent_fkey
        FOREIGN KEY (intent_id) REFERENCES submission_intents (id);

CREATE FUNCTION submission_intents_touch_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER submission_intents_touch_updated_at
    BEFORE UPDATE ON submission_intents
    FOR EACH ROW EXECUTE FUNCTION submission_intents_touch_updated_at();


-- --- Submission timeline ----------------------------------------------------------------

-- What the miner sees while waiting. The status columns on `submissions` say where a submission
-- is now; this says how it got there, which is the question a miner asks when nothing appears to
-- be happening. Append-only, like the ledger.
CREATE TABLE submission_events (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    submission_id   UUID NOT NULL REFERENCES submissions (id),

    kind            TEXT NOT NULL
        CONSTRAINT event_kind_shape CHECK (kind ~ '^[A-Z][A-Z0-9_]{0,63}$'),
    -- Miner-visible prose. Optional: the kind is the machine-readable part.
    detail          TEXT,
    -- Structured context, and the only place a reason code appears on the timeline.
    context         JSONB,
    actor           TEXT NOT NULL,   -- 'system', a worker name, or an operator identity

    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The timeline read: one submission, oldest first. btree scans either direction, so no DESC
-- variant is needed.
CREATE INDEX submission_events_submission_idx ON submission_events (submission_id, id);


-- --- Submissions: the second funding source ---------------------------------------------

-- The change described at the top of this file. Payment columns become NULLable and a credit
-- ledger reference is added, with a CHECK that exactly one of the two is present. Neither path
-- admits an unfunded submission; they differ only in what names the money.
ALTER TABLE submissions
    ALTER COLUMN payment_reference  DROP NOT NULL,
    ALTER COLUMN payment_sender     DROP NOT NULL,
    ALTER COLUMN payment_amount_rao DROP NOT NULL,
    ALTER COLUMN payment_block      DROP NOT NULL;

ALTER TABLE submissions
    -- Which account owns this submission. NULLable because the extrinsic-funded path
    -- authenticates a hotkey and need not involve an account at all.
    ADD COLUMN account_id       UUID REFERENCES accounts (id),
    ADD COLUMN credit_ledger_id BIGINT UNIQUE REFERENCES credit_ledger (id),
    ADD COLUMN intent_id        UUID UNIQUE REFERENCES submission_intents (id);

ALTER TABLE submissions
    ADD CONSTRAINT submission_funded_exactly_once CHECK (
        (payment_reference IS NOT NULL)::int + (credit_ledger_id IS NOT NULL)::int = 1
    ),
    -- The extrinsic path is all-or-nothing: a row with a reference but no sender, amount or
    -- block would be a payment nobody confirmed.
    ADD CONSTRAINT submission_payment_is_complete CHECK (
        payment_reference IS NULL
        OR (payment_sender IS NOT NULL
            AND payment_amount_rao IS NOT NULL
            AND payment_block IS NOT NULL)
    ),
    -- A credit-funded submission came from an intent, and the intent belongs to an account.
    ADD CONSTRAINT submission_credit_path_is_complete CHECK (
        credit_ledger_id IS NULL OR (account_id IS NOT NULL AND intent_id IS NOT NULL)
    );

CREATE INDEX submissions_account_idx ON submissions (account_id, created_at DESC)
    WHERE account_id IS NOT NULL;
