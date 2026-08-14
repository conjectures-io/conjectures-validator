-- Outbound bounty settlement is derived from Subtensor events, not from an operator toggling a
-- status after running a command.  This single durable high-water mark lets the read-only payout
-- watcher resume after a restart without either missing an event or replaying chain history on
-- every poll.
--
-- It deliberately does not reuse chain_watch_cursor.  That table describes an incoming free-TAO
-- address by (recipient, netuid, uid); this one describes an outbound stake position by
-- (origin_coldkey, origin_hotkey, netuid).  Making either set of columns stand in for the other
-- would turn the startup configuration check into theatre.

-- SUBMITTED/CONFIRMED historically could be entered by an operator after running a payout
-- command.  Keep those records for audit, but do not let a status assertion stand in for the
-- successful Subtensor event.  The watcher sets this bit only while reconciling a decoded event.
ALTER TABLE reward_events
    ADD COLUMN chain_observed BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE reward_events
    ADD CONSTRAINT reward_chain_observation_needs_chain_state
        CHECK (NOT chain_observed OR status IN ('SUBMITTED', 'CONFIRMED'));

-- Old paid flags have no machine-verifiable provenance yet.  Move them back to the existing
-- eligible obligation while the cursor replays history; each one is promoted atomically when its
-- finalized event is found.  The existing reward row prevents the notifier from issuing a second
-- payout command during this catch-up.
UPDATE submissions
SET reward_status = 'ELIGIBLE'
WHERE reward_status = 'REWARDED';

DROP INDEX reward_events_pending_idx;
CREATE INDEX reward_events_pending_idx ON reward_events (created_at)
    WHERE status IN ('PENDING', 'SUBMITTED')
       OR (status = 'CONFIRMED' AND NOT chain_observed);

CREATE TABLE payout_watch_cursor (
    watcher                 TEXT PRIMARY KEY,
    network                 TEXT NOT NULL,
    origin_coldkey          ss58 NOT NULL,
    origin_hotkey           ss58 NOT NULL,
    netuid                  INTEGER NOT NULL,

    watch_from              TIMESTAMPTZ NOT NULL,
    start_block             BIGINT NOT NULL,
    start_block_timestamp   TIMESTAMPTZ NOT NULL,
    last_scanned_block      BIGINT NOT NULL,
    last_scanned_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT payout_watcher_name_shape
        CHECK (watcher ~ '^[a-z][a-z0-9-]{0,63}$'),
    CONSTRAINT payout_network_nonempty
        CHECK (length(network) BETWEEN 1 AND 128),
    CONSTRAINT payout_cursor_netuid_nonnegative
        CHECK (netuid >= 0),
    CONSTRAINT payout_cursor_start_block_positive
        CHECK (start_block > 0),
    CONSTRAINT payout_cursor_never_reads_before_start
        CHECK (last_scanned_block >= start_block - 1),
    CONSTRAINT payout_cursor_start_at_or_after_watch
        CHECK (start_block_timestamp >= watch_from)
);
