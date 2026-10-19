-- What a finalized payout was worth when it was paid.
--
-- The public results surface now publishes the payout behind a rewarded result, and "what was
-- paid" in dollars cannot be derived after the fact: `bounty_amount_usd` is today's rate, and
-- nothing recorded the rate at payout. Two values are kept, both written once when the watcher
-- records the row and never moved, like the other chain columns.
--
-- `tao_equivalent_rao` is the payout event's own `amount`: what the chain valued the moved Alpha
-- at in TAO, in that block. It is a chain fact and is always available going forward; it is NOT
-- comparable with `amount_rao` (Alpha) and must never be used for matching. Null on rows recorded
-- before this migration, which discarded it.
--
-- `alpha_usd` is TaoStats' Subnet Alpha/TAO times TAO/USD, observed by the watcher while it
-- recorded the row, and only when the payout block was recent enough for "now" to be the payout
-- price (see `payout_watcher.watcher.PRICE_FRESHNESS`). Null when no price was taken: a backfill
-- rescan, a watcher without a TaoStats key, or a failed lookup. A guessed historical price would be
-- worse than none, so there is no backfill.

ALTER TABLE treasury_payouts
    ADD COLUMN tao_equivalent_rao BIGINT,
    ADD COLUMN alpha_usd NUMERIC,
    ADD COLUMN alpha_usd_observed_at TIMESTAMPTZ,
    ADD CONSTRAINT treasury_payout_tao_equivalent_positive
        CHECK (tao_equivalent_rao IS NULL OR tao_equivalent_rao > 0),
    ADD CONSTRAINT treasury_payout_alpha_usd_positive
        CHECK (alpha_usd IS NULL
               OR (alpha_usd > 0 AND alpha_usd::TEXT NOT IN ('NaN', 'Infinity', '-Infinity'))),
    ADD CONSTRAINT treasury_payout_alpha_usd_observed
        CHECK ((alpha_usd IS NULL) = (alpha_usd_observed_at IS NULL));
