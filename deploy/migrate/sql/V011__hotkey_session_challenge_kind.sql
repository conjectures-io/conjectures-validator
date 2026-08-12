-- A fourth login challenge kind: a hotkey proving itself to open a CLI session.
--
-- This migration adds the enum label and nothing else, and that separation is required rather
-- than tidy. Flyway runs each migration in its own transaction, and PostgreSQL will not let a
-- newly added enum label be *used* in the transaction that added it — the CHECK constraint in
-- V012 references 'HOTKEY_SESSION', so it has to run after this file has committed. Putting both
-- in one file fails with "unsafe use of new value of enum type".
--
-- Keep colon-prefixed words out of these comments. scripts/check_schema_drift.py feeds each
-- migration through SQLAlchemy's text() construct, which treats a colon followed by an identifier
-- as a bind parameter even inside a SQL comment.
--
-- The label goes last. Enum ordinality is the declaration order, and nothing orders by this
-- column, but appending keeps the on-disk order matching the Python enum's member order so the
-- schema-drift check compares like with like.

ALTER TYPE login_challenge_kind ADD VALUE 'HOTKEY_SESSION';
