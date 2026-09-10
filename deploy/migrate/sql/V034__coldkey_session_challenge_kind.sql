-- A fifth login challenge kind: a coldkey proving itself to open a CLI session.
--
-- The CLI used to mint its bearer token from a HOTKEY_SESSION challenge, because a hotkey is what
-- lived on a mining box. Miners now sign everything with a coldkey, so the same flow needs the
-- same kind spelled for the key that actually signs. Distinct from WALLET, which opens a browser
-- cookie session, and from COLDKEY_LINK, which attaches a key to an account that is already
-- signed in: this one mints a durable bearer credential for a caller with no session at all.
--
-- Alone in its own migration for the reason V014 records: PostgreSQL will not let a value added
-- to an enum by `ALTER TYPE` be *used* in the same transaction, and Flyway wraps each migration
-- in one. V035 puts this value inside CHECK constraints, so it has to run after this file has
-- committed. Putting both in one file fails with "unsafe use of new value of enum type".

ALTER TYPE login_challenge_kind ADD VALUE 'COLDKEY_SESSION';
