-- Public conjecture pages group both attack directions by the durable reward target rather than
-- by a per-revision task id.  This index supports their attempt counts and newest-first activity
-- stream without a table scan, and keeps the migration source of truth aligned with the ORM.

CREATE INDEX submissions_reward_target_idx
    ON submissions (reward_target_id, created_at DESC);
