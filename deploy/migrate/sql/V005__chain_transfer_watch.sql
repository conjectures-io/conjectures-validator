-- The chain watcher's two tables: every incoming transfer it observed, and where it has read to.
--
-- V003 built the credit ledger and the `deposits` row a transfer funds, but left the thing that
-- actually looks at the chain unbuilt — docs/ACCOUNT_API.md calls it "the reconciler" and
-- submission_api/payments.py calls it "the finalized-transfer reader". This is the schema half of
-- it. Nothing here decides what a credit is worth: `credit_ledger` remains the only authority on a
-- balance, and a credit stays derived from it.
--
-- Keep colon-prefixed words out of these comments. scripts/check_schema_drift.py feeds each
-- migration through SQLAlchemy's text() construct, which treats a colon followed by an identifier
-- as a bind parameter even inside a SQL comment.


-- --- Observed transfers ------------------------------------------------------------------

CREATE TYPE chain_transfer_state AS ENUM (
    'UNATTRIBUTED',     -- observed, but no account owns the sending coldkey; nothing credited
    'CREDITED',         -- attributed to an account and the ledger entry written
    'IGNORED'           -- deliberately not credited, and it says why
);

-- Every `Balances.Transfer` event into the watched address, at or after the configured start.
--
-- WHY THIS TABLE EXISTS SEPARATELY FROM `deposits`. A deposit is something an account declared it
-- would send; a transfer is something that arrived. Most arrivals have no declaration behind them,
-- and an arrival nobody can be matched to is exactly the case an operator has to be able to see.
-- Folding the two together would mean either inventing a deposit for every stranger's transfer or
-- dropping the ones that do not match — the first pollutes an account-owned table with rows no
-- account owns, the second loses money silently.
--
-- Append-mostly: a row's chain facts (block, sender, amount, reference) are written once and never
-- change. Only the attribution columns move, and only ever from UNATTRIBUTED onwards.
CREATE TABLE chain_transfers (
    id                      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- The canonical identity of one transfer, `block-extrinsic-event`. The event index and not
    -- just the extrinsic: a `utility.batch` emits several Transfer events under one extrinsic, and
    -- keying on the extrinsic alone would collapse two payments into one row.
    extrinsic_reference     TEXT NOT NULL
        CONSTRAINT transfer_reference_length CHECK (length(extrinsic_reference) BETWEEN 1 AND 128),

    block                   BIGINT NOT NULL
        CONSTRAINT transfer_block_positive CHECK (block > 0),
    -- The block's own `Timestamp.set` inherent, not the time we read it. What "after the genesis
    -- timestamp" is judged against, so it has to come from the chain.
    block_timestamp         TIMESTAMPTZ NOT NULL,
    extrinsic_index         INTEGER NOT NULL
        CONSTRAINT transfer_extrinsic_index_nonnegative CHECK (extrinsic_index >= 0),
    event_index             INTEGER NOT NULL
        CONSTRAINT transfer_event_index_nonnegative CHECK (event_index >= 0),

    sender_coldkey          ss58 NOT NULL,
    recipient               ss58 NOT NULL,
    amount_rao              BIGINT NOT NULL
        CONSTRAINT transfer_amount_positive CHECK (amount_rao > 0),

    status                  chain_transfer_state NOT NULL DEFAULT 'UNATTRIBUTED',

    -- Attribution. NULL until the sending coldkey is matched to an account.
    account_id              UUID REFERENCES accounts (id),
    -- The deposit this transfer funded. One transfer funds at most one deposit, the rule
    -- `deposits_extrinsic_idx` already states from the other side.
    deposit_id              UUID UNIQUE REFERENCES deposits (id),

    -- The price in force when this transfer was credited, and what it bought on its own. Both are
    -- for an operator reading this table; NEITHER is authoritative. `credit_ledger` holds the rao
    -- and `credits.credit_balance` divides it, so a transfer of 0.7 TAO at 0.5 records
    -- credits_granted = 1 and leaves 0.2 in the balance towards the next one. Recomputing the
    -- balance from this column would throw those remainders away.
    credit_price_rao        BIGINT
        CONSTRAINT transfer_price_positive CHECK (credit_price_rao IS NULL OR credit_price_rao > 0),
    credits_granted         BIGINT NOT NULL DEFAULT 0
        CONSTRAINT transfer_credits_nonnegative CHECK (credits_granted >= 0),

    -- Why an IGNORED transfer was ignored, or how an UNATTRIBUTED one failed to match.
    note                    TEXT
        CONSTRAINT transfer_note_length CHECK (note IS NULL OR length(note) BETWEEN 1 AND 500),

    observed_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- A credited transfer has all of its attribution, or it is not credited. The deposit carries
    -- the ledger entry (deposits.credited_ledger_id), so naming the deposit names the money.
    CONSTRAINT transfer_credited_needs_attribution
        CHECK (status <> 'CREDITED'
               OR (account_id IS NOT NULL
                   AND deposit_id IS NOT NULL
                   AND credit_price_rao IS NOT NULL)),
    -- Nothing but a credited transfer may claim to have bought anything.
    CONSTRAINT transfer_uncredited_grants_nothing
        CHECK (status = 'CREDITED' OR credits_granted = 0),
    CONSTRAINT transfer_ignored_needs_a_reason
        CHECK (status <> 'IGNORED' OR note IS NOT NULL),
    CONSTRAINT chain_transfers_updated_not_before_observed CHECK (updated_at >= observed_at)
);

-- The idempotency of the whole watcher. A block re-read after a restart, a crash between
-- recording and advancing the cursor, or an overlapping rescan all land on this index instead of
-- crediting a transfer twice.
CREATE UNIQUE INDEX chain_transfers_reference_idx ON chain_transfers (extrinsic_reference);
-- The same fact by its parts, so a scan can ask "did I already record this event" without
-- reassembling the string, and a malformed reference cannot smuggle a duplicate past the index
-- above.
CREATE UNIQUE INDEX chain_transfers_position_idx
    ON chain_transfers (block, extrinsic_index, event_index);
-- The claim path: "which transfers did this coldkey send us".
CREATE INDEX chain_transfers_sender_idx ON chain_transfers (sender_coldkey, block DESC);
CREATE INDEX chain_transfers_account_idx ON chain_transfers (account_id, block DESC)
    WHERE account_id IS NOT NULL;
-- The operator's queue: money that arrived and belongs to nobody yet.
CREATE INDEX chain_transfers_unattributed_idx ON chain_transfers (block)
    WHERE status = 'UNATTRIBUTED';

CREATE FUNCTION chain_transfers_touch_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER chain_transfers_touch_updated_at
    BEFORE UPDATE ON chain_transfers
    FOR EACH ROW EXECUTE FUNCTION chain_transfers_touch_updated_at();


-- --- Where the watcher has read to -------------------------------------------------------

-- One row per watcher, holding the only piece of state a restart must not lose.
--
-- `watch_from`, `recipient`, `netuid` and `uid` are recorded rather than left to configuration
-- alone, because all four decide which money this validator considers its own. The worker compares
-- its environment against this row at startup and refuses to run when they disagree: silently
-- adopting a new address would leave the old one's arrivals uncredited, and silently moving
-- `watch_from` earlier would re-scan a range whose transfers were never meant to buy credits.
--
-- Deliberately not one row per block. A ledger of scanned heights is what ETL-Pipelines keeps,
-- because it backfills a decade of history in parallel; this worker follows the finalized head
-- forwards in one process, so its progress is a single high-water mark.
CREATE TABLE chain_watch_cursor (
    watcher                 TEXT PRIMARY KEY
        CONSTRAINT watcher_name_shape CHECK (watcher ~ '^[a-z][a-z0-9-]{0,63}$'),

    recipient               ss58 NOT NULL,
    netuid                  INTEGER NOT NULL
        CONSTRAINT cursor_netuid_nonnegative CHECK (netuid >= 0),
    uid                     INTEGER NOT NULL
        CONSTRAINT cursor_uid_nonnegative CHECK (uid >= 0),

    -- The genesis timestamp: transfers in blocks before this are not this validator's business.
    watch_from              TIMESTAMPTZ NOT NULL,
    -- The first block at or after `watch_from`, resolved once by bisecting on-chain block times
    -- and then never recomputed — a second bisection against a re-synced node could land a block
    -- either side and change which transfers exist.
    start_block             BIGINT NOT NULL
        CONSTRAINT cursor_start_block_positive CHECK (start_block > 0),
    -- That block's own timestamp, kept as the evidence the resolution was right.
    start_block_timestamp   TIMESTAMPTZ NOT NULL,

    -- The high-water mark. `start_block - 1` on a fresh cursor, meaning "nothing scanned yet".
    last_scanned_block      BIGINT NOT NULL,
    last_scanned_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT cursor_never_reads_before_its_start
        CHECK (last_scanned_block >= start_block - 1),
    CONSTRAINT cursor_start_block_is_at_or_after_watch_from
        CHECK (start_block_timestamp >= watch_from)
);
