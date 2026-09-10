-- A dev ADMIN account with a live browser session, or ADMIN added to an account you already have.
--
--     psql "$DATABASE_URL" -v allow_dev_seed=1 -f scripts/seed_dev_admin.sql
--
--     docker exec -i conjectures_db psql -U conjectures -d conjectures \
--       -v allow_dev_seed=1 -f - < scripts/seed_dev_admin.sql
--
-- Defaults to creating `dev-admin@example.test`. Point it at an account that already exists --
-- one registered through `conjectures auth register`, say -- by naming its address:
--
--     ... -v allow_dev_seed=1 -v email=you@example.com -f scripts/seed_dev_admin.sql
--
-- Either way it adds ADMIN (keeping whatever roles were already there) and prints a fresh cookie
-- session for it. Same trick as `seed_dev_accounts.sql`: a session token is an opaque string
-- stored as a SHA-256 digest, so a row written here authenticates exactly as a minted one does.
--
-- ONLY A COOKIE, AND THAT IS THE POINT.
--
-- There is no bearer token here, unlike `seed_dev_accounts.sql`, because a bearer token could not
-- use the role: `dependencies.BEARER_ROLES` is `{MINER}`, so every `/v1/admin` route answers a
-- bearer caller with `403 ROLE_REQUIRES_BROWSER_SESSION` however genuine the ADMIN grant is. A
-- long-lived token sitting in a file on a mining box must not be a route to the surface that
-- decides whether a proof earns money. Minting one anyway would mean linking a fake coldkey to an
-- admin account to produce a credential that cannot do admin work -- liability with no use.
--
-- If this account should also mine, link and designate a coldkey through the normal flow, or run
-- `seed_dev_accounts.sql`, which does issue bearer tokens for its two accounts.
--
-- USING IT
--
--     curl -H "Cookie: conjectures_session=..." localhost:8000/v1/admin/accounts/<uuid>
--
--     curl -X PUT localhost:8000/v1/admin/accounts/<uuid>/roles \
--          -H "Cookie: conjectures_session=..." \
--          -H "X-Conjectures-CSRF: ..." \
--          -H 'Content-Type: application/json' -d '{"roles":["MINER","REVIEWER"]}'
--
-- The CSRF header is needed on writes only. There is no listing endpoint by design, so reads are
-- by account id -- take one from `seed_dev_accounts.sql`, or `SELECT id, email FROM accounts`.
--
-- Note that an admin cannot remove their own ADMIN through the API (`CANNOT_REMOVE_OWN_ADMIN`),
-- so re-running this is the way back if you demote yourself in a test.

\if :{?allow_dev_seed}
\else
\echo '                                                                    '
\echo 'refusing: this seeds a LIVE admin credential and prints it to your terminal.'
\echo 'Re-run with -v allow_dev_seed=1 if this is a development database.'
\echo '                                                                    '
\quit
\endif

\if :{?email}
\else
\set email 'dev-admin@example.test'
\endif

\set ON_ERROR_STOP on

BEGIN;

WITH secrets AS (
    SELECT
        lower(:'email')::TEXT AS email,
        -- 256 bits from the same CSPRNG `gen_random_uuid` uses, with no extension to install.
        replace(gen_random_uuid()::TEXT || gen_random_uuid()::TEXT, '-', '') AS cookie_token
),
-- Create it, or add ADMIN to what is already there. `accounts_email_idx` is a partial expression
-- index, so the inference clause has to name the expression *and* the predicate to match it.
granted AS (
    INSERT INTO accounts (email, email_verified, display_name, roles)
    -- Sorted, because `accounts.set_roles` stores them sorted and an array that differs only in
    -- order would make a diff of two role sets an exercise in set comparison. The update branch
    -- below sorts too, so both paths leave the same value.
    SELECT email, true, 'Dev Admin', ARRAY['ADMIN', 'MINER']::TEXT[] FROM secrets
    ON CONFLICT (lower(email)) WHERE email IS NOT NULL
    DO UPDATE SET
        -- Union, never replacement: promoting an account must not quietly strip a REVIEWER grant
        -- it already had. MINER rides along because `accounts.set_roles` re-adds it on every
        -- change, so an account without it is a state no code path can produce.
        roles = (
            SELECT array_agg(DISTINCT role ORDER BY role)
            FROM unnest(accounts.roles || ARRAY['MINER', 'ADMIN']::TEXT[]) AS role
        ),
        updated_at = now()
    RETURNING id, email, roles
),
-- Only the ones this script issued before. A re-run should not sign you out of a browser session
-- you opened for real, but leaving every previous dev cookie live means an admin credential you
-- printed once and forgot stays valid for ninety days.
retired AS (
    UPDATE account_sessions s
    SET revoked_at = now()
    FROM granted g
    WHERE s.account_id = g.id
      AND s.user_agent = 'seed_dev_admin.sql'
      AND s.revoked_at IS NULL
    RETURNING s.id
),
issued AS (
    -- No `csrf_sha256`: V021 stopped using it and V029 dropped it. A cookie session proves
    -- where a write was initiated from the browser's own Origin and Sec-Fetch-Site headers.
    INSERT INTO account_sessions
        (account_id, token_sha256, kind, expires_at, user_agent)
    SELECT
        g.id,
        sha256(convert_to(s.cookie_token, 'UTF8')),
        'COOKIE',
        now() + INTERVAL '90 days',
        'seed_dev_admin.sql'
    FROM granted g CROSS JOIN secrets s
    RETURNING account_id
)
SELECT
    g.email,
    g.id AS account_id,
    array_to_string(g.roles, ',') AS roles,
    s.cookie_token,
    (SELECT count(*) FROM retired) AS older_sessions_revoked
FROM granted g CROSS JOIN secrets s;

COMMIT;
