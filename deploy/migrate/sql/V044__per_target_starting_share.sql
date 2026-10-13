-- Each reward target keeps the starting share it opened with, so changing the share for new
-- targets never reprices targets that are already open. Every existing target opened at 1/20.
-- The API writes the configured share explicitly; afterwards the default is the new 1/40, so an
-- API that predates this migration also opens new targets at 1/40 during a deployment.
ALTER TABLE bounty_tasks
    ADD COLUMN start_share_numerator INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN start_share_denominator INTEGER NOT NULL DEFAULT 20,
    ADD CONSTRAINT bounty_tasks_start_share_positive
        CHECK (start_share_numerator > 0 AND start_share_denominator > 0);
ALTER TABLE bounty_tasks ALTER COLUMN start_share_denominator SET DEFAULT 40;
