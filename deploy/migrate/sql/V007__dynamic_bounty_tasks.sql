-- The first time a stable reward target enters the API catalog is its age origin.  Pricing uses
-- this table rather than process uptime or submission history, so restarts and source repins do
-- not reset an old bounty to age zero.
CREATE TABLE bounty_tasks (
    reward_target_id TEXT PRIMARY KEY
        CONSTRAINT bounty_tasks_reward_target_id_nonempty
        CHECK (length(reward_target_id) BETWEEN 1 AND 255),
    opened_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX bounty_tasks_opened_idx
    ON bounty_tasks (opened_at, reward_target_id);

-- Preserve the oldest observable age for targets that already received submissions before this
-- migration.  Catalog targets with no submission yet are inserted by the API on its first pricing
-- read and keep that timestamp thereafter.
INSERT INTO bounty_tasks (reward_target_id, opened_at)
SELECT reward_target_id, min(created_at)
FROM submissions
GROUP BY reward_target_id
ON CONFLICT (reward_target_id) DO NOTHING;

COMMENT ON COLUMN submissions.bounty_amount_rao IS
    'Indicative estimate shown at intake; not a locked payout amount';
COMMENT ON COLUMN submissions.bounty_policy_version IS
    'Pricing policy used for the intake estimate; payout pricing may be newer';
COMMENT ON COLUMN submissions.bounty_inputs IS
    'Inputs behind the intake estimate; retained for audit, never a payout promise';

-- The payout event, not the submission estimate, is the amount-of-record. Existing rows predate
-- dynamic pricing, so their submission snapshot is the only truthful backfill available.
ALTER TABLE reward_events
    ADD COLUMN pricing_policy_version TEXT,
    ADD COLUMN pricing_inputs JSONB;

UPDATE reward_events AS reward
SET pricing_policy_version = submission.bounty_policy_version,
    pricing_inputs = submission.bounty_inputs
FROM submissions AS submission
WHERE submission.id = reward.submission_id;

ALTER TABLE reward_events
    ALTER COLUMN pricing_policy_version SET NOT NULL,
    ADD CONSTRAINT reward_pricing_policy_version_nonempty
        CHECK (length(pricing_policy_version) BETWEEN 1 AND 64);
