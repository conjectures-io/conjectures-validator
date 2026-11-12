-- Reopening a deadline-only rejection preserves the append-only decision chain without
-- granting approval, publishing the proof, or permitting a payout. Existing rows are unchanged.
ALTER TYPE review_outcome ADD VALUE IF NOT EXISTS 'UNREVIEWED';
