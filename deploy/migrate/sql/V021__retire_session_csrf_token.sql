-- Retire the row-bound CSRF token.
--
-- V003 gave every cookie session a second secret: a CSRF token, stored as a digest on the
-- session row, handed to the browser in a script-readable `conjectures_csrf` cookie, and echoed
-- back in `X-Conjectures-CSRF` on every write. V015 made it NULL-able so that a bearer session
-- could exist without one, and added the biconditional CHECK dropped below.
--
-- It is gone because the two headers a browser sets on every state-changing request — `Origin`
-- and `Sec-Fetch-Site` — establish the same fact and cannot be forged by a page, while the token
-- could only ever be as strong as a cookie that page script had to be able to read. Anything
-- that could read the token could also make the request. See `submission_api/origin_policy.py`.
--
-- **The column is deliberately not dropped here.** Dropping it in the same migration that ships
-- the code which stops writing it makes a rolling deploy unsafe: an API instance from the
-- previous version, still running while this migration lands, would INSERT `csrf_sha256` into a
-- column that no longer exists and fail every sign-in until it was replaced. Expand now,
-- contract later — a follow-up migration drops the column once no old instance can be running,
-- and there is nothing sensitive about leaving it: the values are SHA-256 digests of tokens that
-- nothing accepts any more.
--
-- The CHECK, on the other hand, must go now. It requires every COOKIE row to carry a non-NULL
-- `csrf_sha256`, so leaving it in place would make the first sign-in on the new code an
-- IntegrityError.

ALTER TABLE account_sessions
    DROP CONSTRAINT session_csrf_belongs_to_cookie_sessions;

COMMENT ON COLUMN account_sessions.csrf_sha256 IS
    'Retired in V021. Digest of a CSRF token no longer issued or checked; unmapped by the ORM '
    'and dropped in a later contraction migration. Do not read, write, or reuse the name.';
