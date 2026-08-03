-- Indexes for the public, unauthenticated result feeds.
--
-- V001 indexed these predicates for the workers: oldest-first, over `(created_at)`, for
-- FOR UPDATE SKIP LOCKED claiming. The public feeds read the same rows in the opposite
-- direction and page through them by keyset, so they need `(created_at DESC, id DESC)`.
--
-- Two reasons this is a second index rather than a reuse of the worker one:
--
--   * `id` has to be in the index. The page predicate compares the pair as one row value,
--     `(created_at, id) < (cursor_created_at, cursor_id)`, because `created_at` is not unique
--     — two submissions committed in one transaction share it, and a cursor on the timestamp
--     alone would either repeat a row or skip one. Without `id` in the index the comparison is
--     resolved by a heap fetch per candidate row.
--     (Keep colon-prefixed words out of these comments. scripts/check_schema_drift.py feeds
--     each migration through SQLAlchemy's text() construct, which treats a colon followed by
--     an identifier as a bind parameter even inside a SQL comment, and the file then fails to
--     execute with no value bound.)
--   * The worker index on `submissions_review_queue_idx` is deliberately ascending and small.
--     Leaving it to serve a public newest-first feed would couple the queue the validator
--     depends on to traffic from the internet.
--
-- Both stay partial, so they index only what is published and not the whole history.

-- Certified: paid out. The predicate is `reward_status` alone rather than the full three-way
-- condition the feed queries with, so the planner can still use it for any query that implies
-- it — the query adds `manual_review_status` and `verification_status`, which only narrows.
CREATE INDEX submissions_certified_feed_idx ON submissions (created_at DESC, id DESC)
    WHERE reward_status = 'REWARDED';

-- In review: Lean-verified, awaiting the reward decision. Same predicate as
-- submissions_review_queue_idx, different order and one more column.
CREATE INDEX submissions_in_review_feed_idx ON submissions (created_at DESC, id DESC)
    WHERE verification_status = 'VERIFIED' AND manual_review_status = 'UNREVIEWED';
