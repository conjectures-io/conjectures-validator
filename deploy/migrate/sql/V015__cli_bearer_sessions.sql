-- Two kinds of session in one table: the browser's cookie, and the CLI's bearer token.
--
-- V003 modelled one kind — an HttpOnly cookie with a CSRF token bound to the row. The miner CLI
-- needs a credential it can put in an `Authorization` header, minted by a hotkey signature rather
-- than by a coldkey or a mailbox. That is the same object in every respect that matters here: an
-- opaque 256-bit secret, stored only as a digest, revocable in one UPDATE, with an expiry. So it
-- is the same table with a discriminator, not a second table that would duplicate the
-- authenticate/revoke/expire logic and then drift from it.
--
-- Keep colon-prefixed words out of these comments. scripts/check_schema_drift.py feeds each
-- migration through SQLAlchemy's text() construct, which treats a colon followed by an identifier
-- as a bind parameter even inside a SQL comment.
--
-- WHAT DIFFERS BETWEEN THE TWO KINDS, and is therefore enforced here rather than remembered:
--
--   * A COOKIE session has a CSRF token; a BEARER session must not. CSRF is a defence against a
--     browser attaching an *ambient* credential to a cross-site request. A bearer token is not
--     ambient — no browser sends it unless script put it there, and script that can do that is
--     already same-origin. Storing a CSRF digest for a bearer session would imply a check that
--     cannot be performed, since the CLI has nowhere to read the cookie half from.
--   * A BEARER session is scoped to the hotkey that minted it, and a COOKIE session is not. The
--     scope is what keeps a token minted by one linked hotkey from acting as another, and it is
--     recorded so the check reads durable state rather than trusting the request.
--
-- Both are expressed as biconditional CHECKs — `(kind = X) = (column IS NOT NULL)` — so neither a
-- missing value nor a value on the wrong kind can be written. A one-sided check would let a
-- cookie session carry a hotkey scope that nothing would ever read.

CREATE TYPE account_session_kind AS ENUM (
    'COOKIE',   -- the browser: HttpOnly cookie plus a row-bound CSRF token
    'BEARER'    -- the CLI: an Authorization header, scoped to one linked hotkey
);


ALTER TABLE account_sessions
    -- Every row that exists today was issued to a browser, so the default backfills truthfully.
    -- The default is kept afterwards rather than dropped: it matches how `roles` and
    -- `email_verified` carry theirs, and an insert that forgets `kind` while setting
    -- `hotkey_scope` now fails the biconditional below instead of silently becoming a cookie.
    ADD COLUMN kind account_session_kind NOT NULL DEFAULT 'COOKIE',
    -- Where the bearer token's authority stops. NULL for a cookie session, which is scoped to the
    -- account rather than to any one key.
    ADD COLUMN hotkey_scope ss58;

-- Was NOT NULL when a cookie was the only kind of session. A bearer row has no CSRF token to
-- store, and the CHECK below is strictly stronger than the NOT NULL it replaces: it still forbids
-- a cookie session without one, and additionally forbids a bearer session with one.
ALTER TABLE account_sessions
    ALTER COLUMN csrf_sha256 DROP NOT NULL;

ALTER TABLE account_sessions
    ADD CONSTRAINT session_csrf_belongs_to_cookie_sessions
        CHECK ((kind = 'COOKIE') = (csrf_sha256 IS NOT NULL)),
    ADD CONSTRAINT session_scope_belongs_to_bearer_sessions
        CHECK ((kind = 'BEARER') = (hotkey_scope IS NOT NULL));

-- Counting an account's live sessions by kind: the per-account ceiling on concurrent CLI tokens,
-- and the `/v1/me/sessions` listing. `account_sessions_account_idx` orders by issued_at for the
-- page; this one answers "how many live, of which kind" without reading the rows.
CREATE INDEX account_sessions_live_kind_idx ON account_sessions (account_id, kind)
    WHERE revoked_at IS NULL;


-- HOTKEY_SESSION is a signature flow, so it carries an address and a verbatim message exactly as
-- WALLET and HOTKEY_LINK do. Dropped and recreated rather than added alongside, because two
-- overlapping constraints on the same column would each have to be read to know what is required.
--
-- Deliberately NOT added to `challenge_link_has_account`: a HOTKEY_SESSION challenge is minted
-- before the account is known. The hotkey is looked up in `linked_hotkeys` at verify time, which
-- is what makes an unlinked hotkey a 403 rather than an unmintable challenge — and what keeps the
-- challenge endpoint from disclosing whether a hotkey is linked.
ALTER TABLE login_challenges
    DROP CONSTRAINT challenge_wallet_present;

ALTER TABLE login_challenges
    ADD CONSTRAINT challenge_wallet_present
        CHECK (kind NOT IN ('WALLET', 'HOTKEY_LINK', 'HOTKEY_SESSION')
               OR (ss58 IS NOT NULL AND message IS NOT NULL));


-- How many times a signature has been offered against this challenge and failed.
--
-- The signature flows verify BEFORE consuming, so that a wrong signature does not burn the nonce
-- and force the user to start over. The cost of that choice is that an open challenge accepts
-- unlimited signature attempts, and each one is an sr25519 verification on an unauthenticated
-- path. This bounds it: past a small ceiling the challenge is spent, exactly as if it had been
-- used. Cheaper than the alternatives — it needs no second table and no in-process state, so it
-- survives a restart and holds across replicas.
--
-- Griefing a victim's challenge with deliberate failures is not reachable: a challenge is looked
-- up by the digest of its own nonce, and only the client that requested it has the nonce.
ALTER TABLE login_challenges
    ADD COLUMN attempts SMALLINT NOT NULL DEFAULT 0
        CONSTRAINT challenge_attempts_not_negative CHECK (attempts >= 0);
