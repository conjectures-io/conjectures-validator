-- Durable outbox state for automatic payout-signer notifications.
--
-- One row is one signer being told about one PENDING reward event. The notifier seeds these
-- rows from reward_events, leases due work with SKIP LOCKED, posts the generated btcli command,
-- and marks the row SENT. Keeping this in PostgreSQL means a container restart does not ping a
-- signer again merely because the process forgot what it already delivered.
--
-- Delivery is at least once across a crash exactly between Discord accepting a POST and this
-- row being marked SENT. Discord webhooks offer no idempotency key, so eliminating that narrow
-- duplicate window would require weakening the opposite guarantee and sometimes losing a payout
-- notification entirely.

CREATE TABLE payout_discord_deliveries (
    reward_event_id    BIGINT NOT NULL REFERENCES reward_events (id),
    signer_wallet      TEXT NOT NULL,
    discord_user_id    TEXT NOT NULL,

    status             TEXT NOT NULL DEFAULT 'PENDING',
    attempt_count      INTEGER NOT NULL DEFAULT 0,
    next_attempt_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_owner        TEXT,
    lease_until        TIMESTAMPTZ,
    last_error         TEXT,

    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at       TIMESTAMPTZ,

    PRIMARY KEY (reward_event_id, signer_wallet),

    CONSTRAINT payout_discord_wallet_nonempty
        CHECK (length(signer_wallet) BETWEEN 1 AND 1024),
    CONSTRAINT payout_discord_user_id_shape
        CHECK (discord_user_id ~ '^[0-9]{1,32}$'),
    CONSTRAINT payout_discord_status_known
        CHECK (status IN ('PENDING', 'SENDING', 'SENT', 'FAILED')),
    CONSTRAINT payout_discord_attempt_nonnegative
        CHECK (attempt_count >= 0),
    CONSTRAINT payout_discord_lease_paired
        CHECK ((status = 'SENDING') = (lease_owner IS NOT NULL AND lease_until IS NOT NULL)),
    CONSTRAINT payout_discord_sent_paired
        CHECK ((status = 'SENT') = (delivered_at IS NOT NULL)),
    CONSTRAINT payout_discord_updated_after_created
        CHECK (updated_at >= created_at),
    CONSTRAINT payout_discord_delivered_after_created
        CHECK (delivered_at IS NULL OR delivered_at >= created_at)
);

CREATE INDEX payout_discord_due_idx
    ON payout_discord_deliveries (next_attempt_at, reward_event_id, signer_wallet)
    WHERE status IN ('PENDING', 'FAILED', 'SENDING');
