-- Complete the expand/contract removal begun in V021.
--
-- V021 stopped all reads and writes of `account_sessions.csrf_sha256` but retained the column so
-- API instances from the preceding release could finish a rolling deployment without failing
-- sign-ins. Every supported release now uses Origin and Sec-Fetch-Site for browser CSRF checks and
-- leaves this retired digest unmapped. Keeping the column would make the migration-built schema
-- permanently disagree with the ORM mirror that new databases use.
--
-- Do not use IF EXISTS: a missing column would mean the migration history or schema has drifted,
-- and Flyway should fail closed rather than record a contraction it did not perform.

ALTER TABLE account_sessions
    DROP COLUMN csrf_sha256;
