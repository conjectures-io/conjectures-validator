-- Two dev accounts with live credentials, no wallets and no signatures required.
--
--     psql "$DATABASE_URL" -v allow_dev_seed=1 -f scripts/seed_dev_accounts.sql
--
-- or, with the database in compose and no psql on the host:
--
--     docker exec -i conjectures_db psql -U conjectures -d conjectures \
--       -v allow_dev_seed=1 -f - < scripts/seed_dev_accounts.sql
--
-- A session token is an opaque random string whose SHA-256 digest is what the server stores, so
-- a row inserted here is indistinguishable from one minted by a real sign-in. Nothing about the
-- normal flow is skipped except the part that proves who you are -- which is the part that needs
-- keys. That is the whole trick, and it is also why this must never touch production: the tokens
-- it prints are live credentials for the accounts it creates.
--
-- WHAT YOU GET, per account: a BEARER token for the CLI/Authorization header, and a COOKIE
-- session with its CSRF token for the browser flows. You need both, and this is the part worth
-- reading before you file a bug:
--
--   * A **bearer token can only ever exercise the MINER role.** `dependencies.BEARER_ROLES` is
--     `{MINER}`, so a REVIEWER or ADMIN account presenting a bearer token is refused with
--     `ROLE_REQUIRES_BROWSER_SESSION` -- deliberately, because a bearer token is minted by a
--     coldkey, and the token it mints is a long-lived file on a mining box. Reviewer work needs
--     the cookie.
--   * Cookie sessions are also the only credential accepted for linking a coldkey, setting the
--     payout destination, editing the profile and claiming a deposit (`BROWSER_SESSION_REQUIRED`).
--
-- Reading anything -- `/v1/me`, submissions, credits, the ledger -- works with either, but a
-- bearer read is **redacted**: no email, no payout destination, and only the one coldkey the
-- token is scoped to. `GET /v1/me` returning `"email": null` under a bearer token is the contract
-- working, not the seed being incomplete. Read it with the cookie to see the whole account.
--
-- USING THEM
--
--     curl -H "Authorization: Bearer conj_cli_..." localhost:8000/v1/me
--
--     curl -H "Cookie: conjectures_session=..." localhost:8000/v1/me
--     curl -X PATCH localhost:8000/v1/me \
--          -H "Cookie: conjectures_session=..." \
--          -H "X-Conjectures-CSRF: ..." \
--          -H 'Content-Type: application/json' -d '{"display_name":"whatever"}'
--
-- The CSRF header is needed on writes only. `curl` sends no `Origin` and no `Sec-Fetch-Site`, so
-- the other two thirds of the CSRF guard pass; a browser hitting this from a page would also need
-- its origin in `CORS_ALLOWED_ORIGINS`.
--
-- Re-running replaces both accounts and issues fresh tokens. The old ones stop working, because
-- the rows are gone -- `ON DELETE CASCADE` from `accounts` takes the sessions and the wallet
-- links with them.

\if :{?allow_dev_seed}
\else
\echo '                                                                    '
\echo 'refusing: this seeds LIVE credentials and prints them to your terminal.'
\echo 'Re-run with -v allow_dev_seed=1 if this is a development database.'
\echo '                                                                    '
\quit
\endif

\set ON_ERROR_STOP on

BEGIN;

-- Both accounts, by the addresses this script owns. CASCADE removes their linked coldkeys and
-- every session, so a re-run cannot leave a stale token live.
DELETE FROM accounts WHERE email IN ('dev-miner@example.test', 'dev-reviewer@example.test');

-- One statement. The data-modifying CTEs run to completion whether or not the final SELECT reads
-- their output, and that SELECT is the only place the plaintext secrets exist -- psql prints them
-- as the statement executes, and nothing stores them.
WITH wanted (email, display_name, roles, coldkey) AS (
    VALUES
        -- Well-known development addresses (//Alice, //Bob). Nothing here verifies a signature,
        -- so any string matching the `ss58` domain would do -- but using the real ones means
        -- these accounts still work if you later sign in with `--uri //Alice` for real.
        (
            'dev-miner@example.test',
            'Dev Miner',
            ARRAY['MINER']::TEXT[],
            '5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY'
        ),
        (
            'dev-reviewer@example.test',
            'Dev Reviewer',
            -- MINER as well as REVIEWER, because that is what the API itself would store:
            -- `accounts.set_roles` re-adds MINER unconditionally, so a reviewer account that
            -- lacked it would be a state no code path can produce.
            ARRAY['MINER', 'REVIEWER']::TEXT[],
            '5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty'
        )
),
secrets AS (
    -- Freshly random per run rather than constants in a file: a token committed to git is a
    -- token in everyone's clone. Two UUIDs give 256 bits from the same CSPRNG `gen_random_uuid`
    -- uses, with no extension to install -- `pgcrypto` is not assumed here.
    SELECT
        w.*,
        'conj_cli_' || replace(gen_random_uuid()::TEXT || gen_random_uuid()::TEXT, '-', '')
            AS bearer_token,
        replace(gen_random_uuid()::TEXT || gen_random_uuid()::TEXT, '-', '') AS cookie_token
    FROM wanted w
),
created AS (
    INSERT INTO accounts (email, email_verified, display_name, roles)
    SELECT email, true, display_name, roles FROM secrets
    RETURNING id, email
),
-- Required, not decoration: a bearer session is scoped to a coldkey, and `coldkey_still_linked`
-- re-checks that link on *every* request the token authenticates. Without the row the token
-- authenticates once and then 401s.
--
-- V035 replaced `linked_hotkeys` with `account_wallets` here. One table now answers both
-- questions it used to take two for -- who may sign in, and who may submit -- because proving a
-- coldkey does both.
linked AS (
    INSERT INTO account_wallets (account_id, coldkey, signature)
    SELECT c.id, s.coldkey, decode(repeat('00', 64), 'hex')
    FROM created c JOIN secrets s ON s.email = c.email
    RETURNING account_id, coldkey
),
-- Only the digest is stored, exactly as `sessions.issue_bearer` would store it. `sha256()` is
-- built into PostgreSQL 11+, so this needs no extension either.
bearer AS (
    INSERT INTO account_sessions
        (account_id, token_sha256, kind, coldkey_scope, expires_at, user_agent)
    SELECT
        c.id,
        sha256(convert_to(s.bearer_token, 'UTF8')),
        'BEARER',
        s.coldkey,
        now() + INTERVAL '90 days',
        'seed_dev_accounts.sql'
    FROM created c JOIN secrets s ON s.email = c.email
    RETURNING account_id
),
cookie AS (
    INSERT INTO account_sessions
        (account_id, token_sha256, kind, expires_at, user_agent)
    SELECT
        c.id,
        sha256(convert_to(s.cookie_token, 'UTF8')),
        'COOKIE',
        now() + INTERVAL '90 days',
        'seed_dev_accounts.sql'
    FROM created c JOIN secrets s ON s.email = c.email
    RETURNING account_id
)
SELECT
    s.display_name,
    c.id AS account_id,
    array_to_string(s.roles, ',') AS roles,
    s.coldkey,
    s.bearer_token,
    s.cookie_token
FROM secrets s JOIN created c ON c.email = s.email;

-- The designation, and it has to be its own statement rather than another CTE above.
--
-- Every sub-statement in a WITH shares one snapshot and cannot see another's effects on a
-- target table, so an `UPDATE accounts` up there would match zero rows: the accounts it wants
-- were inserted by the `created` CTE in the same statement. It would fail silently -- the seed
-- would report success and hand back a token that then refuses every submission with
-- NO_SUBMISSION_COLDKEY.
--
-- Down here the rows are visible, and still inside the transaction, so a failure rolls the
-- whole seed back rather than leaving accounts with no designated key.
--
-- The intent flow reads this: `open_intent` signs as `Account.submission_coldkey`. Ordering
-- after the wallet insert also satisfies `account_submission_coldkey_is_linked`, the composite
-- foreign key against (account_id, coldkey).
UPDATE accounts a
SET submission_coldkey = w.coldkey
FROM account_wallets w
WHERE w.account_id = a.id
  AND a.email IN ('dev-miner@example.test', 'dev-reviewer@example.test');

COMMIT;
