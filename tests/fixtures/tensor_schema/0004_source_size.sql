-- Frozen schema ceiling, independent of the API's current admission policy.
ALTER TABLE sources
    DROP CONSTRAINT ck_sources_size,
    ADD CONSTRAINT ck_sources_size
        CHECK (octet_length(content) BETWEEN 1 AND 4194304);
