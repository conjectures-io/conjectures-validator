-- A binding review has two different audiences. `notes` remains the internal audit trail;
-- `notes_public` is the deliberately redacted explanation that API responses may publish.
-- Keeping separate columns makes disclosure allowlisted instead of trusting every future writer
-- of internal notes to remember that those bytes might become public.
ALTER TABLE review_decisions
    ADD COLUMN notes_public TEXT,
    ADD CONSTRAINT review_notes_public_length
        CHECK (notes_public IS NULL OR length(notes_public) BETWEEN 1 AND 10000);

COMMENT ON COLUMN review_decisions.notes IS
    'Internal reviewer notes; never publish through miner-facing or public APIs';
COMMENT ON COLUMN review_decisions.notes_public IS
    'Reviewed, redacted decision rationale safe for miner-facing and public APIs';
