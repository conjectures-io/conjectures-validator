-- Every outbound treasury payout the chain has finalized, whether or not anything claims it.
--
-- The incoming side has recorded this since V005: `chain_transfers` holds every arrival at the
-- watched address, attributed or not, because an arrival nobody can match is exactly the row an
-- operator has to be able to see. The outbound side had no equivalent. A decoded payout that
-- matched no obligation produced a log line and nothing else, and because the watcher's cursor
-- only advanced while obligations were outstanding, the block was never re-read. The observation
-- was unrecoverable.
--
-- That asymmetry is what let a hand-made payment vanish: the payout was real, finalized, and for
-- the exact bounty of a submission that was later seeded an obligation -- but the obligation was
-- created after the payment, `reward_events.created_at <= block_timestamp + tolerance` refused the
-- match, and the only trace was a warning nobody was watching. A second payout command was then
-- rendered for money already sent.
--
-- So this table is the outbound twin of `chain_transfers`, and it inverts the dependency between
-- the two watchers. The payout watcher now records what it sees unconditionally; matching becomes
-- a re-runnable join between two durable sides rather than a race that must be won on the first
-- pass. A payment seen before its obligation exists is no longer lost, only unclaimed.
--
-- FINALIZED ONLY. A best-chain observation is deliberately not written here. `SUBMITTED` exists to
-- make the site's "Paying" label literal and is reversible by definition; a row in this table is a
-- finalized chain fact that nothing rolls back, and mixing the two would cost that guarantee for a
-- transient label the existing best-chain pass already provides.

CREATE TYPE treasury_payout_state AS ENUM (
    -- Seen and finalized, bound to nothing. Either its obligation has not been created yet, or it
    -- was paid outside the system and needs an operator to say what it was for.
    'UNCLAIMED',
    'CLAIMED',
    -- An operator has ruled it out, and said why. The escape hatch that keeps `UNCLAIMED` a queue
    -- somebody can actually empty rather than a list that only grows.
    'DISREGARDED'
);

CREATE TABLE treasury_payouts (
    id                      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- `block-extrinsic-event`, the same canonical identity `chain_transfers` uses and for the same
    -- reason: one `utility.batch` can carry several payouts, and keying on the extrinsic alone
    -- would collapse two of them into one row.
    extrinsic_reference     TEXT NOT NULL
        CONSTRAINT treasury_payout_reference_length
            CHECK (length(extrinsic_reference) BETWEEN 1 AND 128),

    block                   BIGINT NOT NULL
        CONSTRAINT treasury_payout_block_positive CHECK (block > 0),
    -- The block's own `Timestamp.set` inherent. What the obligation's `created_at` is compared
    -- against when deciding whether a match may be made automatically.
    block_timestamp         TIMESTAMPTZ NOT NULL,
    extrinsic_index         INTEGER NOT NULL
        CONSTRAINT treasury_payout_extrinsic_index_nonnegative CHECK (extrinsic_index >= 0),
    event_index             INTEGER NOT NULL
        CONSTRAINT treasury_payout_event_index_nonnegative CHECK (event_index >= 0),

    -- The whole economic fingerprint, stored rather than re-derived. The watcher already filtered
    -- on the origin side before writing, but an operator auditing this table must be able to see
    -- what was matched on without trusting that the filter was configured the way they assume.
    origin_coldkey          ss58 NOT NULL,
    origin_hotkey           ss58 NOT NULL,
    destination_coldkey     ss58 NOT NULL,
    -- Equal to `origin_hotkey` for a `transfer_stake` payout, which is every payout since V035:
    -- the stake changes owner without leaving the validator's hotkey. They differ only on a
    -- historical `StakeAndHotkeyTransferred`, where both genuinely name a stake position.
    destination_hotkey      ss58 NOT NULL,
    origin_netuid           INTEGER NOT NULL
        CONSTRAINT treasury_payout_origin_netuid_nonnegative CHECK (origin_netuid >= 0),
    destination_netuid      INTEGER NOT NULL
        CONSTRAINT treasury_payout_destination_netuid_nonnegative
            CHECK (destination_netuid >= 0),

    -- Subnet Alpha in its integer base unit, taken from the companion `StakeAdded` event. The
    -- payout event's own `amount` is TAO-equivalent and price-dependent; comparing that to
    -- `reward_events.amount_rao` would be a unit bug.
    amount_rao              BIGINT NOT NULL
        CONSTRAINT treasury_payout_amount_positive CHECK (amount_rao > 0),

    status                  treasury_payout_state NOT NULL DEFAULT 'UNCLAIMED',

    -- The obligation this payout settled. UNIQUE is the load-bearing constraint of the whole
    -- design: one chain event settles at most one reward, enforced by the schema instead of by
    -- whichever scan happened to reach it first.
    reward_event_id         BIGINT UNIQUE REFERENCES reward_events (id),

    -- Why a payout was disregarded, or how an operator justified binding one by hand.
    note                    TEXT
        CONSTRAINT treasury_payout_note_length
            CHECK (note IS NULL OR length(note) BETWEEN 1 AND 500),

    observed_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- The two halves of "claimed means bound", so neither state can drift from the FK.
    CONSTRAINT treasury_payout_claimed_names_its_reward
        CHECK (status <> 'CLAIMED' OR reward_event_id IS NOT NULL),
    CONSTRAINT treasury_payout_unbound_unless_claimed
        CHECK (status = 'CLAIMED' OR reward_event_id IS NULL),
    CONSTRAINT treasury_payout_disregarded_needs_a_reason
        CHECK (status <> 'DISREGARDED' OR note IS NOT NULL),
    CONSTRAINT treasury_payouts_updated_not_before_observed
        CHECK (updated_at >= observed_at)
);

-- The idempotency of the whole watcher. A block re-read after a restart, a crash between recording
-- a payout and advancing the cursor, or a deliberate rescan all land here rather than recording one
-- chain event twice.
CREATE UNIQUE INDEX treasury_payouts_reference_idx
    ON treasury_payouts (extrinsic_reference);
-- The same fact by its parts, so a malformed reference cannot smuggle a duplicate past the index
-- above. `chain_transfers` carries the identical pair for the identical reason.
CREATE UNIQUE INDEX treasury_payouts_position_idx
    ON treasury_payouts (block, extrinsic_index, event_index);
-- "What has this solver actually been sent", which is the question an operator asks before
-- authorising anything by hand.
CREATE INDEX treasury_payouts_destination_idx
    ON treasury_payouts (destination_coldkey, block DESC);
-- The reconciler's working set and the operator's queue: finalized money that settles nothing yet.
CREATE INDEX treasury_payouts_unclaimed_idx
    ON treasury_payouts (block)
    WHERE status = 'UNCLAIMED';

COMMENT ON TABLE treasury_payouts IS
    'Every finalized outbound treasury payout, claimed or not. The outbound twin of '
    'chain_transfers: recording is unconditional so that matching can be retried, and a payment '
    'observed before its obligation exists is unclaimed rather than lost.';

COMMENT ON COLUMN treasury_payouts.reward_event_id IS
    'The obligation this payout settled. UNIQUE: one chain event settles at most one reward.';

-- `updated_at` is maintained by the database, matching `chain_transfers`. The chain facts in this
-- table are written once and never change; only the disposition columns move, and a caller that
-- forgets to touch the timestamp must not be able to hide when that happened.
CREATE FUNCTION treasury_payouts_touch_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER treasury_payouts_touch_updated_at
    BEFORE UPDATE ON treasury_payouts
    FOR EACH ROW EXECUTE FUNCTION treasury_payouts_touch_updated_at();
