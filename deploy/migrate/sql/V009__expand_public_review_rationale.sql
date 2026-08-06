-- Public rationales may need to include detailed mathematical reasoning and citations. Preserve
-- the already-applied V008 migration and widen its bound with a new versioned migration.
ALTER TABLE review_decisions
    DROP CONSTRAINT review_notes_public_length,
    ADD CONSTRAINT review_notes_public_length
        CHECK (notes_public IS NULL OR length(notes_public) BETWEEN 1 AND 100000);
