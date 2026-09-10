-- One correction per decision, so the append-only chain stays a chain.
--
-- `review_decisions.supersedes_id` has existed since V001 and nothing has ever written it: the
-- table was built for corrections and no route reached them, so `record_human_decision` refuses a
-- second decision outright and a misfiled one was permanent. `POST /v1/admin/reviews/{id}/correction`
-- is what writes it, and this index is what makes the shape it writes enforceable rather than
-- merely intended.
--
-- What was already guaranteed, and is why this is one index rather than several:
--
--   * a correction can only supersede a decision on the SAME submission --
--     `review_supersedes_same_submission` is a composite foreign key against
--     (submission_id, id), so a decision cannot reach across submissions;
--   * a decision cannot supersede itself -- `review_supersedes_not_self`.
--
-- What was not guaranteed: that a decision is superseded at most once. Two reviewers correcting
-- the same decision would each append a row naming it, and the history would fork -- two live
-- leaves, both claiming to be the current decision, with `manual_review_status` summarising
-- whichever landed last. The service serialises them on the submission row (`FOR UPDATE`, then a
-- check that the decision being corrected is still the latest one), and this is that rule written
-- where it cannot be forgotten: the index is the authority, the service check exists to give the
-- reviewer the reason instead of a constraint name.
--
-- Partial, because PostgreSQL treats NULLs as distinct in a unique index and a plain unique on
-- `supersedes_id` would therefore constrain nothing at all today -- every existing row has NULL
-- there. Scoping to the non-null rows says what is meant and keeps the index small: only
-- corrections are in it.
--
-- `submission_id` leads the index rather than being omitted. It is redundant for uniqueness --
-- `supersedes_id` alone already identifies one row, and the composite FK pins it to this
-- submission -- but it makes the index answer "does this submission have corrections" as well,
-- which is the query the reviewer panel runs when it opens a decided submission.
--
-- No backfill and nothing to clean first: no row in this table has a non-null `supersedes_id`,
-- so the index is built over zero rows and cannot fail on existing history.

CREATE UNIQUE INDEX review_decisions_supersedes_unique
    ON review_decisions (submission_id, supersedes_id)
    WHERE supersedes_id IS NOT NULL;

COMMENT ON INDEX review_decisions_supersedes_unique IS
    'A decision may be corrected at most once, so the append-only history is a linear chain '
    'rather than a fork with two live leaves.';
