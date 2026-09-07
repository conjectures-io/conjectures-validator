-- Invitation links that carry free verification attempts.
--
-- The problem this solves is that every route to a credit today requires the buyer to already
-- hold TAO: a transfer to the treasury, or a TMC PAY invoice settled in crypto. A mathematician
-- invited to a mathaton has neither, and no amount of work on the submission path changes that.
-- An operator issues a link, the recipient signs in and redeems it, and the ledger gains a
-- GRANT that is indistinguishable from a purchase everywhere downstream.
--
-- Two tables and one column, and the shape of them is load-bearing:
--
--   * `invitations` stores a DIGEST of the code, never the code. The code is a credential worth
--     N attempts, so it is shown once at creation and cannot be read back -- the same rule the
--     CLI bearer token follows in V015, for the same reason. An admin session that is taken over
--     yields the list of invitations, not the ability to redeem them.
--   * `invitation_redemptions` is the durable fact that one account used one invitation.
--   * `credit_ledger.invitation_redemption_id` points AT that row, and not the other way round.
--     A `credit_ledger_id` on the redemption plus this column would make the two rows reference
--     each other with neither insertable first, and `credit_ledger` is append-only so the usual
--     insert-then-backfill is unavailable. This is the same cycle `ledger_spend_names_its_intent`
--     avoids by having a SPEND name its intent rather than its submission, resolved the same
--     way: the redemption row exists first, and the ledger entry names it.


CREATE TABLE invitations (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- SHA-256 of the code, raw bytes, never the code itself. UNIQUE because redemption looks a
    -- link up by digesting what was presented, so two invitations sharing a digest would make
    -- that lookup ambiguous -- and a collision here is a collision in SHA-256.
    code_sha256         sha256 NOT NULL UNIQUE,

    -- Attempts, not rao. The conversion happens at redemption against the price in force, so a
    -- reprice between issuing a link and clicking it cannot change what the recipient was
    -- promised. Capped so a typo cannot mint a fortune; the API refuses above its own ceiling
    -- first, and this is the floor under that.
    credits             INTEGER NOT NULL
        CONSTRAINT invitation_credits_in_range CHECK (credits BETWEEN 1 AND 100),

    -- How many accounts may redeem this link, and how many have. The counter is maintained in
    -- the same transaction as the redemption row and is what a caller races for; see
    -- `db/invitations.py`, which takes FOR UPDATE on this row before reading it.
    max_redemptions     INTEGER NOT NULL DEFAULT 1
        CONSTRAINT invitation_max_redemptions_positive CHECK (max_redemptions >= 1),
    redeemed_count      INTEGER NOT NULL DEFAULT 0
        CONSTRAINT invitation_redeemed_count_nonnegative CHECK (redeemed_count >= 0),

    -- Optional restriction to one mail domain, checked at redemption against the account's
    -- verified address. Nullable and unenforced when null, so existing behaviour is unchanged,
    -- but present from the first migration on purpose: adding the COLUMN later is cheap, while
    -- adding the SEMANTICS later means links issued before and after mean different things.
    email_domain        TEXT
        CONSTRAINT invitation_email_domain_shape
            CHECK (email_domain IS NULL OR email_domain ~ '^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$'),

    expires_at          TIMESTAMPTZ,
    -- Revocation is soft and always will be. Ledger entries reach this row through their
    -- redemption, so deleting an invitation would orphan the explanation for money already
    -- granted.
    revoked_at          TIMESTAMPTZ,

    -- Why this link exists, in an operator's words. Required rather than optional: a list of
    -- opaque invitations with counts is not something anyone can audit six weeks later, and the
    -- one moment the answer is known for certain is when the link is created.
    note                TEXT NOT NULL
        CONSTRAINT invitation_note_present CHECK (length(btrim(note)) BETWEEN 1 AND 200),

    created_by          UUID NOT NULL REFERENCES accounts (id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT invitation_redemptions_within_max
        CHECK (redeemed_count <= max_redemptions),
    CONSTRAINT invitation_revoked_after_created
        CHECK (revoked_at IS NULL OR revoked_at >= created_at)
);

-- The operator listing: newest first, filtered by state in the router rather than here.
CREATE INDEX invitations_created_idx ON invitations (created_at DESC, id DESC);


CREATE TABLE invitation_redemptions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invitation_id       UUID NOT NULL REFERENCES invitations (id),
    account_id          UUID NOT NULL REFERENCES accounts (id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One account cannot redeem the same link twice. This is the narrow policy: a second, different
-- invitation is still allowed, which keeps a legitimate repeat grant -- a returning attendee, a
-- replacement for a lost link -- an ordinary operation rather than an operator adjustment.
-- Tightening to one invitation per account ever is a unique index on `account_id` alone, and
-- would need the existing rows deduplicated first.
CREATE UNIQUE INDEX invitation_redemption_once_per_account
    ON invitation_redemptions (invitation_id, account_id);

-- The detail view for one invitation: who redeemed it, when.
CREATE INDEX invitation_redemptions_invitation_idx
    ON invitation_redemptions (invitation_id, created_at DESC);
CREATE INDEX invitation_redemptions_account_idx
    ON invitation_redemptions (account_id);


-- --- The ledger side --------------------------------------------------------------------

ALTER TABLE credit_ledger
    ADD COLUMN invitation_redemption_id UUID
        CONSTRAINT credit_ledger_invitation_redemption_fkey
        REFERENCES invitation_redemptions (id);

-- A GRANT names the redemption that caused it and the price that converted attempts to rao,
-- exactly as a SPEND names its intent. Both halves matter: without the redemption the grant has
-- no auditable source, and without the price a later reprice would restate what was given.
ALTER TABLE credit_ledger
    ADD CONSTRAINT ledger_grant_names_its_redemption
        CHECK (kind <> 'GRANT'
               OR (invitation_redemption_id IS NOT NULL AND credit_price_rao IS NOT NULL));

-- And nothing else may name one. Without this the column is merely usually right; with it, a
-- redemption id on a DEPOSIT or an ADJUSTMENT is rejected by the database rather than by review.
ALTER TABLE credit_ledger
    ADD CONSTRAINT ledger_redemption_only_on_grant
        CHECK (invitation_redemption_id IS NULL OR kind = 'GRANT');

-- GRANT joins the kinds whose sign is fixed positive. Dropped and recreated rather than
-- extended in place: a CHECK constraint has no ALTER, and leaving the old one alongside a new
-- one would let the weaker of the two look authoritative.
ALTER TABLE credit_ledger
    DROP CONSTRAINT ledger_credits_are_positive;

ALTER TABLE credit_ledger
    ADD CONSTRAINT ledger_credits_are_positive
        CHECK (kind NOT IN ('DEPOSIT', 'REFUND', 'BONUS', 'GRANT') OR amount_rao > 0);

-- One ledger entry per redemption, mirroring `credit_ledger_spend_idx`. The redemption row is
-- already unique per (invitation, account), so this is what stops a retry that got past the
-- uniqueness check from crediting the same redemption twice.
CREATE UNIQUE INDEX credit_ledger_grant_idx ON credit_ledger (invitation_redemption_id)
    WHERE invitation_redemption_id IS NOT NULL;
