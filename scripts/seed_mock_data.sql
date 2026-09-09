-- =====================================================================================
-- Mock data for a development / demo conjectures-validator database.
--
-- Covers every table created by deploy/migrate/sql/V001..V007 and, deliberately, every
-- state of every enum in them: all five `deposit_state`s, all five `intent_state`s, all
-- four `payout_state`s, both funding paths on `submissions`, a superseded review chain,
-- unattributed chain transfers, and enough low-value rows for the keyset-paginated public
-- feeds to actually page.
--
-- USAGE
--   docker exec -i conjectures_db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
--       < scripts/seed_mock_data.sql
--
--   `just psql` opens an interactive shell and does not forward arguments, so the redirect
--   above is the non-interactive form. From a host psql, `\i scripts/seed_mock_data.sql`.
--
-- PRECONDITIONS
--   * Flyway has run through V007 (`just migrate`).
--   * The target database is empty of seed data. This script is NOT idempotent: it uses
--     fixed UUIDs and fixed extrinsic references, so a second run fails on the unique
--     indexes rather than silently doubling the fixtures. Re-seed by re-running the
--     TRUNCATE block below (commented out — it destroys data, so opt in explicitly).
--   * NEVER run this against production. Every signature, digest and extrinsic reference
--     here is fabricated.
--
-- WHY THE PLAN TABLE
--   `proofs` enforces `digest = pg_catalog.sha256(content)` and `submissions.proof_digest`
--   is a UNIQUE foreign key into it, so proof bytes and their digests cannot be written
--   independently by hand without computing 80+ SHA-256 values offline. The temporary
--   `seed_plan` table below holds one declarative row per crafted submission; `proofs`,
--   `submissions`, `submission_intents`, `credit_ledger`, `verification_runs`,
--   `submission_events` and `review_decisions` are all derived from it, so the digests are
--   correct by construction and a fixture is edited in exactly one place.
--
-- IDENTIFIERS
--   `task_id`, `problem_id` and `reward_target_id` are the real values produced by
--   verifier/task_generator.py and verifier/task_pool.py for repository commit
--   e923379e609b9d5987011a1d1f06ec22ea25cd20 — the `problem_id`s here match the backfill
--   table in V004__stable_reward_targets.sql byte for byte, so the catalog, the API and
--   this seed agree on what a task is called.
--
--   SS58 addresses satisfy the `ss58` domain regex (48 base58 characters). They are
--   format-valid, not checksum-valid: nothing in the schema verifies the checksum, but do
--   not expect `bittensor` to decode them.
-- =====================================================================================

\set ON_ERROR_STOP on

BEGIN;

-- --- Optional reset -----------------------------------------------------------------
--
-- DESTRUCTIVE. Uncomment only on a database whose contents you are willing to lose.
-- Order is irrelevant under CASCADE, but the tables are listed leaf-first anyway so the
-- statement also documents the dependency graph.
--
-- TRUNCATE
--     api_rejection_log,
--     submission_events,
--     review_decisions,
--     reward_events,
--     verification_runs,
--     submissions,
--     submission_intents,
--     chain_transfers,
--     deposits,
--     credit_ledger,
--     proofs,
--     account_sessions,
--     login_challenges,
--     account_wallets,
--     accounts,
--     chain_watch_cursor,
--     bounty_tasks
-- RESTART IDENTITY CASCADE;


-- =====================================================================================
-- 0. Helpers and constants
-- =====================================================================================

-- A 32-byte digest from a label. Used wherever the schema wants a `sha256` that is not
-- constrained to hash any particular payload (request digests, bundle digests, session
-- and challenge secrets, container digests).
CREATE FUNCTION pg_temp.d32(label text) RETURNS bytea
    LANGUAGE sql IMMUTABLE AS $$ SELECT pg_catalog.sha256(convert_to(label, 'UTF8')) $$;

-- A 64-byte stand-in for an sr25519 signature. `octet_length(...) = 64` is checked on
-- submissions.signer_signature and account_wallets.signature.
CREATE FUNCTION pg_temp.sig64(label text) RETURNS bytea
    LANGUAGE sql IMMUTABLE AS $$
    SELECT decode(md5(label || ':0') || md5(label || ':1')
               || md5(label || ':2') || md5(label || ':3'), 'hex')
$$;

-- The bundle a task was generated into. Both `submissions` and `verification_runs` record
-- it; the run re-derives it from live Lean, and agreeing here is the normal case.
CREATE FUNCTION pg_temp.bundle(task_id text) RETURNS bytea
    LANGUAGE sql IMMUTABLE AS $$ SELECT pg_temp.d32('task-bundle:' || task_id) $$;

-- One credit is one verification attempt. `credit_ledger` stores rao and the credit count
-- is derived by division, so this price has to be the same number the deposits, the holds
-- and the spends were all quoted at.
CREATE FUNCTION pg_temp.credit_price() RETURNS bigint
    LANGUAGE sql IMMUTABLE AS $$ SELECT 500000000::bigint $$;   -- 0.5 TAO

CREATE FUNCTION pg_temp.treasury() RETURNS text
    LANGUAGE sql IMMUTABLE AS $$ SELECT '5TaADYQdRqPBUBFQcPH8XitrEuJP548XhfD6s1Np4GRgyjxB'::text $$;

-- There was a `validator_hotkey()` here, used as the hotkey side of every reward_events
-- destination. V035 removed the destination hotkey: `transfer_stake` leaves the stake on the
-- validator's own key, so a payout names a coldkey and nothing else, and
-- `reward_events.destination_hotkey` is NULL on every row written from here on.

CREATE FUNCTION pg_temp.verifier_version() RETURNS text
    LANGUAGE sql IMMUTABLE AS $$ SELECT '1.4.2'::text $$;

CREATE FUNCTION pg_temp.container() RETURNS bytea
    LANGUAGE sql IMMUTABLE AS $$
    SELECT pg_temp.d32('ghcr.io/conjectures-io/conjectures-verifier:1.4.2')
$$;

-- The 14 gate booleans of a passing run, as data/sample-report-accepted.json shapes them.
-- `checks` on verification_runs is a projection of the report, so the two are built from
-- the same place here.
CREATE FUNCTION pg_temp.checks_pass() RETURNS jsonb
    LANGUAGE sql IMMUTABLE AS $$
    SELECT jsonb_build_object(
        'axioms_permitted', true, 'challenge_built', true, 'lean_kernel_passed', true,
        'manifest_valid', true, 'nanoda_enabled', true, 'nanoda_passed', true,
        'production_sandbox', true, 'production_task', true, 'same_statement', true,
        'solution_built', true, 'source_type_hash_valid', true,
        'submission_policy_valid', true, 'task_commitment_valid', true,
        'trusted_hashes_valid', true)
$$;

-- The same 14 gates with the one that tripped set false. `failed` must be a key above or
-- the report describes a gate the verifier does not have.
CREATE FUNCTION pg_temp.checks_fail(failed text) RETURNS jsonb
    LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN failed IS NULL THEN pg_temp.checks_pass()
        ELSE jsonb_set(pg_temp.checks_pass(), ARRAY[failed], 'false'::jsonb)
    END
$$;

-- Report bytes, kept verbatim so `report_digest = sha256(report)` stays recomputable.
CREATE FUNCTION pg_temp.report(
    accepted boolean, reason text, stage text, task_id text, problem_id text,
    task_mode text, checks jsonb, duration_ms integer
) RETURNS bytea LANGUAGE sql IMMUTABLE AS $$
    SELECT convert_to(jsonb_pretty(jsonb_build_object(
        'schema_version', 2,
        'accepted', accepted,
        'reason_code', reason,
        'stage', stage,
        'task_id', task_id,
        'problem_id', problem_id,
        'task_mode', task_mode,
        'checks', checks,
        'duration_ms', duration_ms,
        'repository_commit', 'e923379e609b9d5987011a1d1f06ec22ea25cd20',
        'sandbox_mode', 'landrun',
        'permitted_axioms', jsonb_build_array('propext', 'Quot.sound', 'Classical.choice'),
        'theorem_names', jsonb_build_array('Bounty.target'),
        'comparator_exit_code', CASE WHEN accepted THEN 0 ELSE 1 END,
        'workspace_retained', false
    )), 'UTF8')
$$;


-- =====================================================================================
-- 1. Accounts
--
-- Ten accounts spanning the shapes the account API has to cope with: the ordinary
-- verified miner, a reviewer, an admin, a wallet-only account with no email at all, an
-- unverified mailbox, an account with no payout destination set, and an operator account
-- that is not a miner.
-- =====================================================================================

INSERT INTO accounts (id, email, email_verified, display_name, roles, payout_coldkey, created_at, updated_at) VALUES
    ('a0000000-0000-4000-8000-000000000001', 'alice@example.test',  true,  'alice-proves',   ARRAY['MINER'],                        '5C4hPGqPnDPP9jgWmBQfBAuxwiuFhWM6ttHwUvBAoMxLLJRD',  now() - interval '96 days', now() - interval '3 days'),
    ('a0000000-0000-4000-8000-000000000002', 'bob@example.test',    true,  'bob-formalizes', ARRAY['MINER'],                        '5C4hrfjw9DjXZTzV3MwzrrAr9P1MJhSrvWGWqi1eSuyUpnhM',  now() - interval '88 days', now() - interval '9 days'),
    ('a0000000-0000-4000-8000-000000000003', 'carol@example.test',  true,  'carol',          ARRAY['MINER', 'REVIEWER'],            '5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy',  now() - interval '81 days', now() - interval '1 day'),
    ('a0000000-0000-4000-8000-000000000004', 'dave@example.test',   true,  'dave-ops',       ARRAY['MINER', 'REVIEWER', 'ADMIN'],   '5EZotmLfrufXYvD6CCGsRRELEFdg9SnjaEzTmaemiBPNofBP',  now() - interval '80 days', now() - interval '2 hours'),
    -- Wallet-only: signed in with a coldkey and never gave an email. `email_verified`
    -- stays false because there is nothing to verify.
    ('a0000000-0000-4000-8000-000000000005', NULL,                  false, 'anon-miner-5',   ARRAY['MINER'],                        '5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty',  now() - interval '64 days', now() - interval '11 days'),
    -- Signed up, never clicked the magic link.
    ('a0000000-0000-4000-8000-000000000006', 'erin@example.test',   false, 'erin',           ARRAY['MINER'],                        NULL,                                               now() - interval '40 days', now() - interval '40 days'),
    -- Verified, but has not chosen where rewards should go. One nullable column since V035,
    -- where it used to be an all-or-nothing coldkey/hotkey pair.
    ('a0000000-0000-4000-8000-000000000007', 'frank@example.test',  true,  NULL,             ARRAY['MINER'],                        NULL,                                               now() - interval '33 days', now() - interval '4 days'),
    -- Bought credits and has not spent them: exercises a positive balance with no spends.
    ('a0000000-0000-4000-8000-000000000008', 'grace@example.test',  true,  'grace',          ARRAY['MINER'],                        '5HMqFHmvUpzuAjEnse3hzMKS5LsFL428hffCfenF2smuGNhs',  now() - interval '27 days', now() - interval '6 days'),
    ('a0000000-0000-4000-8000-000000000009', 'heidi@example.test',  true,  'heidi',          ARRAY['MINER'],                        '5vpw71uYWM51ST1Z2xtTE1u9FpqtkYGfJN6EFaiNC6NXP9Qj',  now() - interval '21 days', now() - interval '2 days'),
    -- Operator: reviews and pays, never submits. No MINER role.
    ('a0000000-0000-4000-8000-00000000000a', 'ops@example.test',    true,  'validator-ops',  ARRAY['ADMIN'],                        NULL,                                               now() - interval '100 days', now() - interval '5 hours');


-- --- Sign-in coldkeys ---------------------------------------------------------------
-- `coldkey` is the primary key: one coldkey signs in to exactly one account.

INSERT INTO account_wallets (account_id, coldkey, signature, linked_at) VALUES
    ('a0000000-0000-4000-8000-000000000001', '5C4hPGqPnDPP9jgWmBQfBAuxwiuFhWM6ttHwUvBAoMxLLJRD', pg_temp.sig64('wallet:alice'),  now() - interval '96 days'),
    ('a0000000-0000-4000-8000-000000000002', '5C4hrfjw9DjXZTzV3MwzrrAr9P1MJhSrvWGWqi1eSuyUpnhM', pg_temp.sig64('wallet:bob'),    now() - interval '88 days'),
    ('a0000000-0000-4000-8000-000000000003', '5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy', pg_temp.sig64('wallet:carol'),  now() - interval '81 days'),
    ('a0000000-0000-4000-8000-000000000004', '5EZotmLfrufXYvD6CCGsRRELEFdg9SnjaEzTmaemiBPNofBP', pg_temp.sig64('wallet:dave'),   now() - interval '80 days'),
    ('a0000000-0000-4000-8000-000000000005', '5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty', pg_temp.sig64('wallet:anon5'),  now() - interval '64 days'),
    -- Erin linked a coldkey and funded credits with it, but never verified her mailbox.
    -- An unverified email gates the email sign-in, not the chain, so her deposit below is
    -- still attributable to this account.
    ('a0000000-0000-4000-8000-000000000006', '5Gn2SyG6PmBstAjiPD93CTuxADqYaYqf6fKeFuezKsX7Chf9', pg_temp.sig64('wallet:erin'),   now() - interval '58 days'),
    ('a0000000-0000-4000-8000-000000000008', '5HMqFHmvUpzuAjEnse3hzMKS5LsFL428hffCfenF2smuGNhs', pg_temp.sig64('wallet:grace'),  now() - interval '27 days'),
    ('a0000000-0000-4000-8000-000000000009', '5vpw71uYWM51ST1Z2xtTE1u9FpqtkYGfJN6EFaiNC6NXP9Qj', pg_temp.sig64('wallet:heidi'),  now() - interval '21 days'),
    ('a0000000-0000-4000-8000-00000000000a', '5KCxFMXyRjerPoeMy1Ns52HCTgYqYgZoEi22hAng5hzvAFCQ', pg_temp.sig64('wallet:ops'),    now() - interval '100 days');
-- Account 7 signed up by email and never linked a wallet, which is also why it has no
-- payout destination and no credited deposit.


-- --- Miner coldkeys -----------------------------------------------------------------
-- V035 folded `linked_hotkeys` into `account_wallets`: miners sign with a coldkey, so the
-- addresses that used to be a separate table of mining identities are simply more linked
-- coldkeys. Each rig having its own coldkey is the shape this exercises, and one of them is
-- designated `accounts.submission_coldkey` further down.
--
-- `coldkey` is the primary key and therefore globally unique: two accounts claiming one
-- address would make submission attribution ambiguous. Account 1 runs three miners;
-- account 5 runs two.

INSERT INTO account_wallets (account_id, coldkey, signature, linked_at) VALUES
    ('a0000000-0000-4000-8000-000000000001', '5YXMcmddggxUSQsQsJyuAhtB3umCDmjcacdbDQvX9VWWm1x4', pg_temp.sig64('rig:alice-a'), now() - interval '96 days'),
    ('a0000000-0000-4000-8000-000000000001', '5JUCqYXJcJ2SPpugAfZN8ftLxhw2qEqs4AVgusS9wDfS52Wi', pg_temp.sig64('rig:alice-b'), now() - interval '70 days'),
    ('a0000000-0000-4000-8000-000000000001', '59AngoGg42QM5PtEUuYxbn6Ts5UA4EksUJMYsk65oEtnG3w4', pg_temp.sig64('rig:alice-c'), now() - interval '38 days'),
    ('a0000000-0000-4000-8000-000000000002', '5Pyp1nhoPAAEuxPPaqU8egQ9Lx96GwJpxDn2bJ7rHDEoKBwG', pg_temp.sig64('rig:bob-a'),   now() - interval '88 days'),
    ('a0000000-0000-4000-8000-000000000002', '5uo9Jo18H1UCroFX4iUA4V88XgFdn2wejmQQMQLB58uJmeG8', pg_temp.sig64('rig:bob-b'),   now() - interval '52 days'),
    ('a0000000-0000-4000-8000-000000000003', '5SiwiEjcqjpuf6stGQjNbdMPuGQ1Uhm2kYGwTjLBt7DKwDJc', pg_temp.sig64('rig:carol-a'), now() - interval '81 days'),
    ('a0000000-0000-4000-8000-000000000004', '5pni659U8ysLcyxkMZUDruW4eBdHAxst1iH9G53pNmyPBSyK', pg_temp.sig64('rig:dave-a'),  now() - interval '80 days'),
    ('a0000000-0000-4000-8000-000000000005', '5NQfa2nJhT3SHUfrJf8tgrsrwMtjun8SkH1hNCd9m6zjnzMx', pg_temp.sig64('rig:anon5-a'), now() - interval '64 days'),
    ('a0000000-0000-4000-8000-000000000005', '5dg49PZ9i5m5FWkJqBuhuLUeBYMBY3bz92Dpdd2J2EhkF1XK', pg_temp.sig64('rig:anon5-b'), now() - interval '30 days'),
    ('a0000000-0000-4000-8000-000000000006', '5voN6u6yQAm7r9ju3A2AfK5TJjUmB3ewQo52RGaRCFosVS7D', pg_temp.sig64('rig:erin-a'),  now() - interval '40 days'),
    ('a0000000-0000-4000-8000-000000000007', '5z9jFvWVD1EefYKRjte3Nx3rEE8Y3zV2JWgxfpzh85tXkcj5', pg_temp.sig64('rig:frank-a'), now() - interval '33 days'),
    ('a0000000-0000-4000-8000-000000000008', '5AHhzFJMzH9RPJP4oeAQfzWH6C7dFoDxWK5qV3MfcoeHveci', pg_temp.sig64('rig:grace-a'), now() - interval '27 days'),
    ('a0000000-0000-4000-8000-000000000009', '5dujVUYoSwss5ApFDBgEwdM29T6wHGMVzFLTo4xfVnAoHBSV', pg_temp.sig64('rig:heidi-a'), now() - interval '21 days');
-- Coldkeys 5NErXg3…, 53ZZjEw6…, 5Q2Kx1xq… are deliberately absent: they submit over the
-- extrinsic-funded path, which authenticates a coldkey signature against a confirmed
-- transfer and never touches an account. Their submissions below therefore have
-- account_id IS NULL.


-- --- The designated submission coldkey ----------------------------------------------
-- Which linked coldkey each account submits and spends credits under. `open_intent` signs
-- as this and refuses with NO_SUBMISSION_COLDKEY when it is null, so an account with
-- credits and no designation could not spend them.
--
-- The oldest linked key per account, which is the one the submissions below were written
-- under. `account_submission_coldkey_is_linked` is a composite foreign key against
-- (account_id, coldkey), so this can only ever name a key the account actually proved.
UPDATE accounts a
SET submission_coldkey = (
    SELECT w.coldkey
    FROM account_wallets w
    WHERE w.account_id = a.id
    ORDER BY w.linked_at, w.coldkey
    LIMIT 1
)
WHERE EXISTS (SELECT 1 FROM account_wallets w WHERE w.account_id = a.id);


-- =====================================================================================
-- 2. Sessions and login challenges
--
-- Only digests are stored, never the token or the nonce, so nothing here is replayable.
-- The three lifecycle states a session can be in are all present, because the live-session
-- lookup is a partial index on `revoked_at IS NULL` and wants both sides to exist.
-- =====================================================================================

-- No `csrf_sha256`: V021 stopped using it and V029 dropped it. A cookie session now proves
-- where a write was initiated from the browser's own Origin and Sec-Fetch-Site headers.
INSERT INTO account_sessions (id, account_id, token_sha256, issued_at, last_seen_at, expires_at, revoked_at, user_agent, source_ip) VALUES
    -- Live and recently used.
    ('c0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-000000000001', pg_temp.d32('session-token:alice-1'), now() - interval '3 days',  now() - interval '11 minutes', now() + interval '11 days', NULL, 'Mozilla/5.0 (X11; Linux x86_64) Firefox/141.0',        '203.0.113.24'),
    ('c0000000-0000-4000-8000-000000000002', 'a0000000-0000-4000-8000-000000000003', pg_temp.d32('session-token:carol-1'), now() - interval '1 day',   now() - interval '2 hours',    now() + interval '13 days', NULL, 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) Safari/18', '198.51.100.7'),
    ('c0000000-0000-4000-8000-000000000003', 'a0000000-0000-4000-8000-000000000004', pg_temp.d32('session-token:dave-1'),   now() - interval '5 hours', now() - interval '4 minutes',  now() + interval '13 days', NULL, 'conjectures-cli/0.9.1',                                 '203.0.113.201'),
    ('c0000000-0000-4000-8000-000000000004', 'a0000000-0000-4000-8000-000000000008', pg_temp.d32('session-token:grace-1'), now() - interval '6 days',  now() - interval '6 days',     now() + interval '8 days',  NULL, 'Mozilla/5.0 (Windows NT 10.0) Chrome/139.0',            '192.0.2.88'),
    -- Expired: the window closed and nobody extended it. Still readable for audit.
    ('c0000000-0000-4000-8000-000000000005', 'a0000000-0000-4000-8000-000000000002', pg_temp.d32('session-token:bob-old'), now() - interval '45 days', now() - interval '31 days',    now() - interval '17 days', NULL, 'Mozilla/5.0 (X11; Linux x86_64) Chrome/136.0',          '198.51.100.44'),
    ('c0000000-0000-4000-8000-000000000006', 'a0000000-0000-4000-8000-000000000005', pg_temp.d32('session-token:anon-old'), now() - interval '60 days', now() - interval '46 days',  now() - interval '32 days', NULL, 'python-httpx/0.27.0',                                   '203.0.113.99'),
    -- Revoked: the user signed out, or an operator cut it short.
    ('c0000000-0000-4000-8000-000000000007', 'a0000000-0000-4000-8000-000000000001', pg_temp.d32('session-token:alice-2'), now() - interval '20 days', now() - interval '19 days',    now() - interval '6 days',  now() - interval '19 days', 'Mozilla/5.0 (Android 15; Mobile) Firefox/141.0',    '192.0.2.31'),
    ('c0000000-0000-4000-8000-000000000008', 'a0000000-0000-4000-8000-000000000009', pg_temp.d32('session-token:heidi-1'), now() - interval '9 days',  now() - interval '8 days',     now() + interval '5 days',  now() - interval '8 days',  'Mozilla/5.0 (iPhone; CPU iPhone OS 18_1) Safari/18', '198.51.100.150'),
    ('c0000000-0000-4000-8000-000000000009', 'a0000000-0000-4000-8000-00000000000a', pg_temp.d32('session-token:ops-1'),     now() - interval '5 hours', now() - interval '3 minutes',  now() + interval '13 days', NULL, 'conjectures-admin/0.4.0',                               '10.4.2.17');


-- One table for all three challenge kinds. EMAIL needs a mailbox; WALLET and COLDKEY_LINK
-- need an address and the exact bytes the client is asked to sign; COLDKEY_LINK also needs
-- the account it attaches to.
INSERT INTO login_challenges (id, kind, account_id, email, ss58, secret_sha256, message, expires_at, consumed_at, created_at) VALUES
    -- EMAIL: consumed (the link was clicked), live, and expired.
    ('b0000000-0000-4000-8000-000000000001', 'EMAIL', NULL, 'alice@example.test', NULL, pg_temp.d32('challenge:email:alice-1'), NULL, now() - interval '96 days' + interval '15 minutes', now() - interval '96 days' + interval '4 minutes', now() - interval '96 days'),
    ('b0000000-0000-4000-8000-000000000002', 'EMAIL', NULL, 'grace@example.test', NULL, pg_temp.d32('challenge:email:grace-1'), NULL, now() - interval '27 days' + interval '15 minutes', now() - interval '27 days' + interval '2 minutes', now() - interval '27 days'),
    ('b0000000-0000-4000-8000-000000000003', 'EMAIL', NULL, 'frank@example.test', NULL, pg_temp.d32('challenge:email:frank-2'), NULL, now() + interval '12 minutes', NULL, now() - interval '3 minutes'),
    -- Erin's link expired unclicked, which is why accounts.email_verified is still false.
    ('b0000000-0000-4000-8000-000000000004', 'EMAIL', NULL, 'erin@example.test',  NULL, pg_temp.d32('challenge:email:erin-1'),  NULL, now() - interval '40 days' + interval '15 minutes', NULL, now() - interval '40 days'),
    -- WALLET: a coldkey sign-in nonce. The message is stored verbatim so verification
    -- never reconstructs it, and cannot reconstruct it differently.
    ('b0000000-0000-4000-8000-000000000005', 'WALLET', NULL, NULL, '5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy', pg_temp.d32('challenge:wallet:carol-1'), 'conjectures.io sign-in' || chr(10) || 'address: 5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy' || chr(10) || 'nonce: 4f1c9e2a8b7d6053', now() - interval '1 day' + interval '5 minutes',  now() - interval '1 day' + interval '38 seconds', now() - interval '1 day'),
    ('b0000000-0000-4000-8000-000000000006', 'WALLET', NULL, NULL, '5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty', pg_temp.d32('challenge:wallet:anon5-1'), 'conjectures.io sign-in' || chr(10) || 'address: 5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty' || chr(10) || 'nonce: bb70d41c5e9a3128', now() - interval '64 days' + interval '5 minutes', now() - interval '64 days' + interval '51 seconds', now() - interval '64 days'),
    -- Issued, never signed. The expiry sweeper's queue.
    ('b0000000-0000-4000-8000-000000000007', 'WALLET', NULL, NULL, '5GF78jpkNBTDFr4n21Bj9o3YHNo9Ac7L4Xp82RJkLJHpxbzK', pg_temp.d32('challenge:wallet:stranger'), 'conjectures.io sign-in' || chr(10) || 'address: 5GF78jpkNBTDFr4n21Bj9o3YHNo9Ac7L4Xp82RJkLJHpxbzK' || chr(10) || 'nonce: 0c3ea75f19b2d846', now() + interval '4 minutes', NULL, now() - interval '1 minute'),
    -- COLDKEY_LINK: attaches another coldkey to an account that already exists.
    ('b0000000-0000-4000-8000-000000000008', 'COLDKEY_LINK', 'a0000000-0000-4000-8000-000000000001', NULL, '59AngoGg42QM5PtEUuYxbn6Ts5UA4EksUJMYsk65oEtnG3w4', pg_temp.d32('challenge:link:alice-c'), 'conjectures.io link coldkey' || chr(10) || 'account: a0000000-0000-4000-8000-000000000001' || chr(10) || 'coldkey: 59AngoGg42QM5PtEUuYxbn6Ts5UA4EksUJMYsk65oEtnG3w4' || chr(10) || 'nonce: 71ad4c08f6e2b593', now() - interval '38 days' + interval '10 minutes', now() - interval '38 days' + interval '20 seconds', now() - interval '38 days'),
    ('b0000000-0000-4000-8000-000000000009', 'COLDKEY_LINK', 'a0000000-0000-4000-8000-000000000005', NULL, '5dg49PZ9i5m5FWkJqBuhuLUeBYMBY3bz92Dpdd2J2EhkF1XK', pg_temp.d32('challenge:link:anon5-b'), 'conjectures.io link coldkey' || chr(10) || 'account: a0000000-0000-4000-8000-000000000005' || chr(10) || 'coldkey: 5dg49PZ9i5m5FWkJqBuhuLUeBYMBY3bz92Dpdd2J2EhkF1XK' || chr(10) || 'nonce: 2d8fb6019c4ae735', now() - interval '30 days' + interval '10 minutes', now() - interval '30 days' + interval '15 seconds', now() - interval '30 days'),
    ('b0000000-0000-4000-8000-00000000000a', 'COLDKEY_LINK', 'a0000000-0000-4000-8000-000000000002', NULL, '5NErXg3it6UXbKDNHUhvzbgoixU44tT1ApNHFoza8hKeUAPp', pg_temp.d32('challenge:link:bob-c'),   'conjectures.io link coldkey' || chr(10) || 'account: a0000000-0000-4000-8000-000000000002' || chr(10) || 'coldkey: 5NErXg3it6UXbKDNHUhvzbgoixU44tT1ApNHFoza8hKeUAPp' || chr(10) || 'nonce: 9e5107cbd3a6f284', now() + interval '9 minutes', NULL, now() - interval '1 minute');


-- =====================================================================================
-- 3. The chain watcher's cursor
--
-- One row. `recipient`, `netuid`, `uid` and `watch_from` are recorded rather than left to
-- configuration, because the worker compares its environment against them at startup and
-- refuses to run when they disagree. Match these to your .env or the watcher will exit.
-- =====================================================================================

INSERT INTO chain_watch_cursor (watcher, recipient, netuid, uid, watch_from, start_block, start_block_timestamp, last_scanned_block, last_scanned_at, created_at) VALUES
    ('deposit-watcher', pg_temp.treasury(), 66, 12,
     now() - interval '100 days', 5120000, now() - interval '100 days' + interval '7 seconds',
     5408330, now() - interval '40 seconds', now() - interval '100 days');


-- =====================================================================================
-- 4. Deposits, the credit ledger, and observed transfers
--
-- Three tables that reference each other in a cycle: a deposit names the ledger entry it
-- produced, the entry names the deposit that caused it, and a transfer names the deposit
-- it funded. All three links are NULLable, so the cycle is broken by writing in stages:
--
--   4a. deposits, none of them CREDITED yet
--   4b. credit_ledger DEPOSIT entries, pointing back at those deposits
--   4c. UPDATE the deposits to CREDITED, now that there is a ledger id to name
--   4d. chain_transfers, which can now name both
--
-- Stage 4c is why it is done this way rather than inserting CREDITED rows directly:
-- deposit_credited_needs_ledger_and_finality would reject a CREDITED row whose
-- credited_ledger_id is still NULL.
-- =====================================================================================

-- --- 4a. Deposits, all five states --------------------------------------------------

INSERT INTO deposits (id, account_id, amount_rao, treasury_address, credit_price_rao, status,
                      extrinsic_reference, sender_coldkey, observed_amount_rao, block, failure_reason,
                      expires_at, created_at, updated_at) VALUES
    -- Will become CREDITED in 4c. Inserted as SEEN_UNFINALIZED, which only requires a
    -- reference, so the chain facts can be written once and never touched again.
    ('d0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-000000000001', 5000000000,  pg_temp.treasury(), pg_temp.credit_price(), 'SEEN_UNFINALIZED', '5310482-2-4', '5C4hPGqPnDPP9jgWmBQfBAuxwiuFhWM6ttHwUvBAoMxLLJRD', 5000000000,  5310482, NULL, now() - interval '95 days' + interval '2 hours', now() - interval '95 days', now() - interval '95 days'),
    ('d0000000-0000-4000-8000-000000000002', 'a0000000-0000-4000-8000-000000000002', 4000000000,  pg_temp.treasury(), pg_temp.credit_price(), 'SEEN_UNFINALIZED', '5317905-1-2', '5C4hrfjw9DjXZTzV3MwzrrAr9P1MJhSrvWGWqi1eSuyUpnhM', 4000000000,  5317905, NULL, now() - interval '87 days' + interval '2 hours', now() - interval '87 days', now() - interval '87 days'),
    -- 0.7 TAO at 0.5 buys one credit and leaves 0.2 towards the next: the remainder case
    -- the comment on chain_transfers.credit_price_rao warns not to recompute away.
    ('d0000000-0000-4000-8000-000000000003', 'a0000000-0000-4000-8000-000000000003',  700000000,  pg_temp.treasury(), pg_temp.credit_price(), 'SEEN_UNFINALIZED', '5325118-3-1', '5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy',  700000000,  5325118, NULL, now() - interval '80 days' + interval '2 hours', now() - interval '80 days', now() - interval '80 days'),
    ('d0000000-0000-4000-8000-000000000004', 'a0000000-0000-4000-8000-000000000005', 3000000000,  pg_temp.treasury(), pg_temp.credit_price(), 'SEEN_UNFINALIZED', '5348770-0-3', '5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty', 3000000000,  5348770, NULL, now() - interval '63 days' + interval '2 hours', now() - interval '63 days', now() - interval '63 days'),
    ('d0000000-0000-4000-8000-000000000005', 'a0000000-0000-4000-8000-000000000008', 10000000000, pg_temp.treasury(), pg_temp.credit_price(), 'SEEN_UNFINALIZED', '5389233-4-6', '5HMqFHmvUpzuAjEnse3hzMKS5LsFL428hffCfenF2smuGNhs', 10000000000, 5389233, NULL, now() - interval '26 days' + interval '2 hours', now() - interval '26 days', now() - interval '26 days'),
    -- Stays SEEN_UNFINALIZED: the transfer is visible but not final, so no credits yet.
    -- This is the reconciliation worker's live queue.
    ('d0000000-0000-4000-8000-000000000006', 'a0000000-0000-4000-8000-000000000001', 2500000000,  pg_temp.treasury(), pg_temp.credit_price(), 'SEEN_UNFINALIZED', '5408331-1-1', '5C4hPGqPnDPP9jgWmBQfBAuxwiuFhWM6ttHwUvBAoMxLLJRD', 2500000000,  5408331, NULL, now() + interval '110 minutes', now() - interval '10 minutes', now() - interval '4 minutes'),
    ('d0000000-0000-4000-8000-000000000007', 'a0000000-0000-4000-8000-000000000009', 1500000000,  pg_temp.treasury(), pg_temp.credit_price(), 'SEEN_UNFINALIZED', '5408329-0-2', '5vpw71uYWM51ST1Z2xtTE1u9FpqtkYGfJN6EFaiNC6NXP9Qj', 1500000000,  5408329, NULL, now() + interval '95 minutes', now() - interval '25 minutes', now() - interval '9 minutes'),
    -- AWAITING_TRANSFER: created, nothing seen on chain. No chain columns at all.
    ('d0000000-0000-4000-8000-000000000008', 'a0000000-0000-4000-8000-000000000004', 5000000000,  pg_temp.treasury(), pg_temp.credit_price(), 'AWAITING_TRANSFER', NULL, NULL, NULL, NULL, NULL, now() + interval '118 minutes', now() - interval '2 minutes', now() - interval '2 minutes'),
    ('d0000000-0000-4000-8000-000000000009', 'a0000000-0000-4000-8000-000000000007',  500000000,  pg_temp.treasury(), pg_temp.credit_price(), 'AWAITING_TRANSFER', NULL, NULL, NULL, NULL, NULL, now() + interval '105 minutes', now() - interval '15 minutes', now() - interval '15 minutes'),
    -- EXPIRED: the window closed with no transfer.
    ('d0000000-0000-4000-8000-00000000000a', 'a0000000-0000-4000-8000-000000000006', 1000000000,  pg_temp.treasury(), pg_temp.credit_price(), 'EXPIRED', NULL, NULL, NULL, NULL, NULL, now() - interval '39 days' + interval '2 hours', now() - interval '39 days', now() - interval '39 days' + interval '2 hours'),
    ('d0000000-0000-4000-8000-00000000000b', 'a0000000-0000-4000-8000-000000000002',  500000000,  pg_temp.treasury(), pg_temp.credit_price(), 'EXPIRED', NULL, NULL, NULL, NULL, NULL, now() - interval '12 days' + interval '2 hours', now() - interval '12 days', now() - interval '12 days' + interval '2 hours'),
    -- FAILED: seen, but it does not match what was declared. The reason is mandatory.
    ('d0000000-0000-4000-8000-00000000000c', 'a0000000-0000-4000-8000-000000000003', 5000000000,  pg_temp.treasury(), pg_temp.credit_price(), 'FAILED', '5371004-5-0', '5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy', 50000000, 5371004, 'AMOUNT_BELOW_DECLARED: declared 5000000000 rao, observed 50000000 rao', now() - interval '44 days' + interval '2 hours', now() - interval '44 days', now() - interval '44 days' + interval '9 minutes'),
    -- Two more that become CREDITED in 4c. They exist because accounts 4 and 6 each spend
    -- a credit further down, and a ledger that lets a balance go negative would mean the
    -- fixtures charged for attempts nobody paid for. The invariant check at the bottom of
    -- this file fails if either of these is removed.
    ('d0000000-0000-4000-8000-00000000000d', 'a0000000-0000-4000-8000-000000000004', 3000000000,  pg_temp.treasury(), pg_temp.credit_price(), 'SEEN_UNFINALIZED', '5330500-1-2', '5EZotmLfrufXYvD6CCGsRRELEFdg9SnjaEzTmaemiBPNofBP', 3000000000, 5330500, NULL, now() - interval '76 days' + interval '2 hours', now() - interval '76 days', now() - interval '76 days'),
    -- Erin never verified her mailbox but her coldkey still sent money: an unverified email
    -- gates the email sign-in, not the chain.
    ('d0000000-0000-4000-8000-00000000000e', 'a0000000-0000-4000-8000-000000000006', 1000000000,  pg_temp.treasury(), pg_temp.credit_price(), 'SEEN_UNFINALIZED', '5354200-0-0', '5Gn2SyG6PmBstAjiPD93CTuxADqYaYqf6fKeFuezKsX7Chf9', 1000000000, 5354200, NULL, now() - interval '58 days' + interval '2 hours', now() - interval '58 days', now() - interval '58 days');


-- --- 4b. Credit ledger --------------------------------------------------------------
--
-- Append-only. Amounts are signed rao, and the sign is fixed per kind so a debit cannot
-- be recorded as a credit by passing the wrong sign. Credits are DERIVED: the balance is
-- the sum of this table, and a credit count is that balance divided by the price in force.
-- SPEND entries are written later (section 6), once the intents they name exist.

INSERT INTO credit_ledger (account_id, kind, amount_rao, credit_price_rao, deposit_id, intent_id, reason, created_by, created_at) VALUES
    -- One DEPOSIT per finalized deposit. `credit_price_rao` is recorded so a later
    -- reprice does not restate what this purchase bought.
    ('a0000000-0000-4000-8000-000000000001', 'DEPOSIT',  5000000000,  pg_temp.credit_price(), 'd0000000-0000-4000-8000-000000000001', NULL, NULL, 'system', now() - interval '95 days' + interval '3 minutes'),
    ('a0000000-0000-4000-8000-000000000002', 'DEPOSIT',  4000000000,  pg_temp.credit_price(), 'd0000000-0000-4000-8000-000000000002', NULL, NULL, 'system', now() - interval '87 days' + interval '2 minutes'),
    ('a0000000-0000-4000-8000-000000000003', 'DEPOSIT',   700000000,  pg_temp.credit_price(), 'd0000000-0000-4000-8000-000000000003', NULL, NULL, 'system', now() - interval '80 days' + interval '4 minutes'),
    ('a0000000-0000-4000-8000-000000000005', 'DEPOSIT',  3000000000,  pg_temp.credit_price(), 'd0000000-0000-4000-8000-000000000004', NULL, NULL, 'system', now() - interval '63 days' + interval '2 minutes'),
    ('a0000000-0000-4000-8000-000000000008', 'DEPOSIT', 10000000000,  pg_temp.credit_price(), 'd0000000-0000-4000-8000-000000000005', NULL, NULL, 'system', now() - interval '26 days' + interval '5 minutes'),
    ('a0000000-0000-4000-8000-000000000004', 'DEPOSIT',  3000000000,  pg_temp.credit_price(), 'd0000000-0000-4000-8000-00000000000d', NULL, NULL, 'system', now() - interval '76 days' + interval '3 minutes'),
    ('a0000000-0000-4000-8000-000000000006', 'DEPOSIT',  1000000000,  pg_temp.credit_price(), 'd0000000-0000-4000-8000-00000000000e', NULL, NULL, 'system', now() - interval '58 days' + interval '2 minutes'),
    -- BONUS: a package bonus. No row behind it, so `reason` carries the explanation.
    ('a0000000-0000-4000-8000-000000000001', 'BONUS',    1000000000,  pg_temp.credit_price(), NULL, NULL, 'Launch package: 20 percent bonus on the first deposit', 'system', now() - interval '95 days' + interval '4 minutes'),
    ('a0000000-0000-4000-8000-000000000008', 'BONUS',    2000000000,  pg_temp.credit_price(), NULL, NULL, 'Launch package: 20 percent bonus on the first deposit', 'system', now() - interval '26 days' + interval '6 minutes'),
    -- ADJUSTMENT is the only kind allowed either sign, and it must say why.
    ('a0000000-0000-4000-8000-000000000003', 'ADJUSTMENT',  4300000000, pg_temp.credit_price(), NULL, NULL, 'Goodwill top-up after deposit d…000c was under-sent; see support thread OPS-118', 'operator:dave@example.test', now() - interval '43 days'),
    ('a0000000-0000-4000-8000-000000000002', 'ADJUSTMENT',  -500000000, pg_temp.credit_price(), NULL, NULL, 'Clawback of a duplicated promotional grant, ticket OPS-091',                     'operator:dave@example.test', now() - interval '50 days'),
    -- REFUND: an attempt that was never made, or was the validator's fault.
    ('a0000000-0000-4000-8000-000000000001', 'REFUND',     500000000,  pg_temp.credit_price(), NULL, NULL, 'Verification worker crashed before the sandbox started; attempt not consumed', 'system', now() - interval '7 days'),
    ('a0000000-0000-4000-8000-000000000005', 'REFUND',     500000000,  pg_temp.credit_price(), NULL, NULL, 'Intent expired before a bundle was attached; hold released and credit returned', 'system', now() - interval '18 days');


-- --- 4c. Promote the finalized deposits to CREDITED ----------------------------------
--
-- Now that each deposit has a ledger entry to name, the status and the link can be set
-- together. Matching on (deposit_id, kind) is unambiguous: one deposit credits at most
-- once, which `deposits.credited_ledger_id UNIQUE` also states from the other side.

UPDATE deposits AS d
SET status = 'CREDITED',
    credited_ledger_id = l.id
FROM credit_ledger AS l
WHERE l.deposit_id = d.id
  AND l.kind = 'DEPOSIT';


-- --- 4d. Observed transfers ---------------------------------------------------------
--
-- Every Balances.Transfer into the watched address. Separate from `deposits` because a
-- deposit is something an account declared it would send and a transfer is something that
-- arrived — and most arrivals have no declaration behind them. The reference is
-- `block-extrinsic-event`, keyed on the event index too, because one utility.batch emits
-- several Transfer events under a single extrinsic.

INSERT INTO chain_transfers (extrinsic_reference, block, block_timestamp, extrinsic_index, event_index,
                             sender_coldkey, recipient, amount_rao, status,
                             account_id, deposit_id, credit_price_rao, credits_granted, note,
                             observed_at, updated_at) VALUES
    -- CREDITED: attributed to an account, funding a deposit, with the price recorded.
    -- `credits_granted` is what this transfer bought on its own; the ledger keeps the rao
    -- and the remainder, so recomputing a balance from this column would lose money.
    ('5310482-2-4', 5310482, now() - interval '95 days', 2, 4, '5C4hPGqPnDPP9jgWmBQfBAuxwiuFhWM6ttHwUvBAoMxLLJRD', pg_temp.treasury(), 5000000000,  'CREDITED', 'a0000000-0000-4000-8000-000000000001', 'd0000000-0000-4000-8000-000000000001', pg_temp.credit_price(), 10, NULL, now() - interval '95 days' + interval '3 minutes', now() - interval '95 days' + interval '3 minutes'),
    ('5317905-1-2', 5317905, now() - interval '87 days', 1, 2, '5C4hrfjw9DjXZTzV3MwzrrAr9P1MJhSrvWGWqi1eSuyUpnhM', pg_temp.treasury(), 4000000000,  'CREDITED', 'a0000000-0000-4000-8000-000000000002', 'd0000000-0000-4000-8000-000000000002', pg_temp.credit_price(),  8, NULL, now() - interval '87 days' + interval '2 minutes', now() - interval '87 days' + interval '2 minutes'),
    ('5325118-3-1', 5325118, now() - interval '80 days', 3, 1, '5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy', pg_temp.treasury(),  700000000,  'CREDITED', 'a0000000-0000-4000-8000-000000000003', 'd0000000-0000-4000-8000-000000000003', pg_temp.credit_price(),  1, '0.2 TAO remainder carried in the ledger towards the next credit', now() - interval '80 days' + interval '4 minutes', now() - interval '80 days' + interval '4 minutes'),
    ('5348770-0-3', 5348770, now() - interval '63 days', 0, 3, '5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty', pg_temp.treasury(), 3000000000,  'CREDITED', 'a0000000-0000-4000-8000-000000000005', 'd0000000-0000-4000-8000-000000000004', pg_temp.credit_price(),  6, NULL, now() - interval '63 days' + interval '2 minutes', now() - interval '63 days' + interval '2 minutes'),
    ('5389233-4-6', 5389233, now() - interval '26 days', 4, 6, '5HMqFHmvUpzuAjEnse3hzMKS5LsFL428hffCfenF2smuGNhs', pg_temp.treasury(), 10000000000, 'CREDITED', 'a0000000-0000-4000-8000-000000000008', 'd0000000-0000-4000-8000-000000000005', pg_temp.credit_price(), 20, NULL, now() - interval '26 days' + interval '5 minutes', now() - interval '26 days' + interval '5 minutes'),
    ('5330500-1-2', 5330500, now() - interval '76 days', 1, 2, '5EZotmLfrufXYvD6CCGsRRELEFdg9SnjaEzTmaemiBPNofBP', pg_temp.treasury(), 3000000000,  'CREDITED', 'a0000000-0000-4000-8000-000000000004', 'd0000000-0000-4000-8000-00000000000d', pg_temp.credit_price(),  6, NULL, now() - interval '76 days' + interval '3 minutes', now() - interval '76 days' + interval '3 minutes'),
    ('5354200-0-0', 5354200, now() - interval '58 days', 0, 0, '5Gn2SyG6PmBstAjiPD93CTuxADqYaYqf6fKeFuezKsX7Chf9', pg_temp.treasury(), 1000000000,  'CREDITED', 'a0000000-0000-4000-8000-000000000006', 'd0000000-0000-4000-8000-00000000000e', pg_temp.credit_price(),  2, NULL, now() - interval '58 days' + interval '2 minutes', now() - interval '58 days' + interval '2 minutes'),
    -- UNATTRIBUTED: money arrived and belongs to nobody yet. The operator's queue.
    -- `credits_granted` must be 0 (transfer_uncredited_grants_nothing).
    ('5352011-1-0', 5352011, now() - interval '60 days', 1, 0, '5sELUj8auRHX96tjjGwfVvLurdixEk7GxhD7scG285ndfCdj', pg_temp.treasury(), 1000000000, 'UNATTRIBUTED', NULL, NULL, NULL, 0, 'No account owns the sending coldkey', now() - interval '60 days', now() - interval '60 days'),
    ('5361200-0-1', 5361200, now() - interval '54 days', 0, 1, '5GF78jpkNBTDFr4n21Bj9o3YHNo9Ac7L4Xp82RJkLJHpxbzK', pg_temp.treasury(),  250000000, 'UNATTRIBUTED', NULL, NULL, NULL, 0, 'No account owns the sending coldkey; below one credit at the current price', now() - interval '54 days', now() - interval '54 days'),
    ('5377418-6-2', 5377418, now() - interval '38 days', 6, 2, '5sELUj8auRHX96tjjGwfVvLurdixEk7GxhD7scG285ndfCdj', pg_temp.treasury(), 2000000000, 'UNATTRIBUTED', NULL, NULL, NULL, 0, 'Second arrival from the same unclaimed coldkey', now() - interval '38 days', now() - interval '38 days'),
    ('5401992-2-2', 5401992, now() - interval '5 days',  2, 2, '54GnKREdXkwD16ghTySY9E5AJo232KyHB3QJRHc44Y5RT57s', pg_temp.treasury(),  900000000, 'UNATTRIBUTED', NULL, NULL, NULL, 0, 'Sender has not completed the wallet link challenge', now() - interval '5 days', now() - interval '5 days'),
    -- Two events under one extrinsic: distinct event_index values, so they stay two rows.
    ('5404881-3-0', 5404881, now() - interval '3 days',  3, 0, '5eJjufukbD3ZmHZnGULa6KMdZ2FzXWaBKP9s8SQ2u6s1LEt6', pg_temp.treasury(),  600000000, 'UNATTRIBUTED', NULL, NULL, NULL, 0, 'utility.batch leg 1 of 2; sending coldkey unknown', now() - interval '3 days', now() - interval '3 days'),
    ('5404881-3-1', 5404881, now() - interval '3 days',  3, 1, '5eJjufukbD3ZmHZnGULa6KMdZ2FzXWaBKP9s8SQ2u6s1LEt6', pg_temp.treasury(),  400000000, 'UNATTRIBUTED', NULL, NULL, NULL, 0, 'utility.batch leg 2 of 2; sending coldkey unknown', now() - interval '3 days', now() - interval '3 days'),
    -- IGNORED: deliberately not credited, and it says why.
    ('5340155-0-0', 5340155, now() - interval '70 days', 0, 0, '5BQmG6PsQYgFueBcrUL45y3kAuRLQQeaLUWxjCP88iANhDjA', pg_temp.treasury(),     100000, 'IGNORED', NULL, NULL, NULL, 0, 'Dust: 0.0001 TAO, below the minimum crediting threshold', now() - interval '70 days', now() - interval '70 days'),
    ('5371004-5-0', 5371004, now() - interval '44 days', 5, 0, '5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy', pg_temp.treasury(),   50000000, 'IGNORED', NULL, NULL, NULL, 0, 'Under-sent against deposit d…000c; settled by ADJUSTMENT instead, ticket OPS-118', now() - interval '44 days', now() - interval '44 days' + interval '9 minutes'),
    ('5395640-1-3', 5395640, now() - interval '19 days', 1, 3, '5XkfrrouYxUVviUXGdeqQf6hY6UiPm6JGrvNBYrCheGK6aJR', pg_temp.treasury(),  750000000, 'IGNORED', NULL, NULL, NULL, 0, 'Refunded off-ledger at the sender request before crediting, ticket OPS-134', now() - interval '19 days', now() - interval '19 days' + interval '1 day');


-- =====================================================================================
-- 5. Bounty task ages
--
-- The first time a stable reward target enters the catalog is its age origin. Pricing
-- reads this table rather than process uptime, so restarts and source repins do not reset
-- an old bounty to age zero. All 29 audited Erdős targets, staggered so a dynamic-age
-- pricing policy produces a spread of quotes instead of one number.
-- =====================================================================================

INSERT INTO bounty_tasks (reward_target_id, opened_at)
SELECT target, now() - (age || ' days')::interval
FROM (VALUES
    ('fc-target:Erdos11.erdos_11',            97),
    ('fc-target:Erdos28.erdos_28',            97),
    ('fc-target:Erdos41.erdos_41',            97),
    ('fc-target:Erdos82.erdos_82',            97),
    ('fc-target:Erdos89.erdos_89',            97),
    ('fc-target:Erdos184.erdos_184',          92),
    ('fc-target:Erdos233.erdos_233',          92),
    ('fc-target:Erdos236.erdos_236',          92),
    ('fc-target:Erdos242.erdos_242',          92),
    ('fc-target:Erdos243.erdos_243',          85),
    ('fc-target:Erdos274.herzog_schonheim',   85),
    ('fc-target:Erdos282.erdos_282',          85),
    ('fc-target:Erdos340.erdos_340',          71),
    ('fc-target:Erdos364.erdos_364',          71),
    ('fc-target:Erdos371.erdos_371',          71),
    ('fc-target:Erdos373.erdos_373',          58),
    ('fc-target:Erdos535.erdos_535',          58),
    ('fc-target:Erdos617.erdos_617',          58),
    ('fc-target:Erdos624.erdos_624',          44),
    ('fc-target:Erdos677.erdos_677',          44),
    ('fc-target:Erdos779.erdos_779',          44),
    ('fc-target:Erdos859.erdos_859',          30),
    ('fc-target:Erdos889.erdos_889',          30),
    ('fc-target:Erdos912.erdos_912',          30),
    ('fc-target:Erdos932.erdos_932',          16),
    ('fc-target:Erdos952.erdos_952',          16),
    ('fc-target:Erdos982.erdos_982',          16),
    ('fc-target:Erdos1094.erdos_1094',         9),
    ('fc-target:Erdos1107.erdos_1107',         2)
) AS v(target, age)
ON CONFLICT (reward_target_id) DO NOTHING;


-- =====================================================================================
-- 6. The crafted submissions
--
-- One declarative row per submission in `seed_plan`; everything downstream is derived.
-- The `task_id`, `problem_id` and `reward_target_id` values are the real outputs of
-- verifier/task_generator.py for repository commit e923379e…, and the `problem_id`s match
-- V004's backfill table exactly.
--
-- STATUS MATRIX. `submissions_reward_target_reward_unique` allows at most one submission
-- per reward target with `reward_status <> 'INELIGIBLE'`, so exactly the rows below that
-- reach ELIGIBLE / REWARDED / FAILED hold a target of their own; every competing attempt
-- on an already-claimed target stays INELIGIBLE. Deliberately included:
--
--   * both funding paths (`funding` = 'credit' or 'extrinsic')
--   * an extrinsic-funded submission that still carries an account_id (s17)
--   * a claimed verification lease, and an unclaimed row with attempts already spent
--   * VERIFIED + REJECTED review: a human refusing a Lean-valid proof (s06)
--   * a rejection-then-correction review chain (s20)
--   * three attempts on one reward target, one of which was paid (s01, s18, s19)
-- =====================================================================================

CREATE TEMP TABLE seed_plan (
    handle              uuid PRIMARY KEY,
    label               text NOT NULL,            -- for the sanity report at the bottom
    funding             text NOT NULL,            -- 'credit' | 'extrinsic'
    account_id          uuid,
    intent_id           uuid,
    signer_coldkey      text NOT NULL,
    theorem             text NOT NULL,
    task_mode           text NOT NULL,
    task_id             text NOT NULL,
    problem_id          text NOT NULL,
    reward_target_id    text NOT NULL,
    verification        text NOT NULL,
    review              text NOT NULL,
    reward              text NOT NULL,
    failure_reason      text,
    failed_check        text,                     -- the gate that tripped, for the report
    review_required     boolean NOT NULL,
    bounty_rao          bigint NOT NULL,
    payment_reference   text,
    payment_sender      text,
    payment_rao         bigint,
    payment_block       bigint,
    lease_owner         text,
    lease_until         timestamptz,
    attempts            integer NOT NULL,
    proof_tactic        text NOT NULL,            -- makes each proof body distinct
    created_at          timestamptz NOT NULL
);

INSERT INTO seed_plan VALUES
-- ---- credit-funded (account path) --------------------------------------------------
('50000000-0000-4000-8000-000000000001', 's01 paid out',             'credit',    'a0000000-0000-4000-8000-000000000001', 'e0000000-0000-4000-8000-000000000001', '5YXMcmddggxUSQsQsJyuAhtB3umCDmjcacdbDQvX9VWWm1x4', 'Erdos11.erdos_11',          'formalized',     'fc-e923379e-erdos11-erdos-11-7c0303029e-formalized-v1',                'fc-e923379e-erdos11-erdos-11-ef45c19b49e06b8d799e891bbecc78bd-problem',                'fc-target:Erdos11.erdos_11',          'VERIFIED',   'APPROVED',   'REWARDED',   NULL,                       NULL,                      true,  180000000000, NULL, NULL, NULL, NULL, NULL, NULL, 1, E'  have key := Erdos11.density_bound\n  exact key.mp (by norm_num)', now() - interval '61 days'),
('50000000-0000-4000-8000-000000000002', 's02 awaiting payout',      'credit',    'a0000000-0000-4000-8000-000000000001', 'e0000000-0000-4000-8000-000000000002', '5JUCqYXJcJ2SPpugAfZN8ftLxhw2qEqs4AVgusS9wDfS52Wi', 'Erdos28.erdos_28',          'counterexample', 'fc-e923379e-erdos28-erdos-28-83c24f2cdd-counterexample-v1',            'fc-e923379e-erdos28-erdos-28-a2f028f341752d03f89481b9ef384e9f-problem',                'fc-target:Erdos28.erdos_28',          'VERIFIED',   'APPROVED',   'ELIGIBLE',   NULL,                       NULL,                      true,  210000000000, NULL, NULL, NULL, NULL, NULL, NULL, 2, E'  intro claim\n  obtain ⟨n, hn⟩ := Erdos28.witness\n  exact absurd (claim n) hn', now() - interval '4 days'),
('50000000-0000-4000-8000-000000000003', 's03 in review',            'credit',    'a0000000-0000-4000-8000-000000000002', 'e0000000-0000-4000-8000-000000000003', '5Pyp1nhoPAAEuxPPaqU8egQ9Lx96GwJpxDn2bJ7rHDEoKBwG', 'Erdos233.erdos_233',        'formalized',     'fc-e923379e-erdos233-erdos-233-f97637ffd1-formalized-v1',              'fc-e923379e-erdos233-erdos-233-ea125d7bba4f238810a030ca91216967-problem',               'fc-target:Erdos233.erdos_233',        'VERIFIED',   'UNREVIEWED', 'INELIGIBLE', NULL,                       NULL,                      true,   95000000000, NULL, NULL, NULL, NULL, NULL, NULL, 1, E'  refine Erdos233.of_partial ?_ ?_\n  · exact Erdos233.base_case\n  · intro k hk\n    simpa using Erdos233.step k hk', now() - interval '2 days'),
('50000000-0000-4000-8000-000000000004', 's04 kernel rejected',      'credit',    'a0000000-0000-4000-8000-000000000002', 'e0000000-0000-4000-8000-000000000004', '5uo9Jo18H1UCroFX4iUA4V88XgFdn2wejmQQMQLB58uJmeG8', 'Erdos242.erdos_242',        'formalized',     'fc-e923379e-erdos242-erdos-242-030fce5e67-formalized-v1',              'fc-e923379e-erdos242-erdos-242-939241f3b34c22446920c261f7410f4e-problem',               'fc-target:Erdos242.erdos_242',        'REJECTED',   'UNREVIEWED', 'INELIGIBLE', 'LEAN_KERNEL_REJECTED',     'lean_kernel_passed',      true,   88000000000, NULL, NULL, NULL, NULL, NULL, NULL, 1, E'  apply Erdos242.main\n  · exact Nat.le_refl _\n  · sorry', now() - interval '17 days'),
('50000000-0000-4000-8000-000000000005', 's05 lease held',           'credit',    'a0000000-0000-4000-8000-000000000003', 'e0000000-0000-4000-8000-000000000005', '5SiwiEjcqjpuf6stGQjNbdMPuGQ1Uhm2kYGwTjLBt7DKwDJc', 'Erdos243.erdos_243',        'formalized',     'fc-e923379e-erdos243-erdos-243-70efa0eb38-formalized-v1',              'fc-e923379e-erdos243-erdos-243-d1951cb199108aefec3236766b9fa28a-problem',               'fc-target:Erdos243.erdos_243',        'UNVERIFIED', 'UNREVIEWED', 'INELIGIBLE', NULL,                       NULL,                      true,  102000000000, NULL, NULL, NULL, NULL, 'verification-worker-2@fra1', now() + interval '9 minutes', 1, E'  classical\n  by_contra h\n  push_neg at h\n  exact Erdos243.contradiction h', now() - interval '14 minutes'),
('50000000-0000-4000-8000-000000000006', 's06 review rejected',      'credit',    'a0000000-0000-4000-8000-000000000005', 'e0000000-0000-4000-8000-000000000006', '5NQfa2nJhT3SHUfrJf8tgrsrwMtjun8SkH1hNCd9m6zjnzMx', 'Erdos274.herzog_schonheim', 'formalized',     'fc-e923379e-erdos274-herzog-schonheim-f8e2742c92-formalized-v1',       'fc-e923379e-erdos274-herzog-schonheim-256974bd7a1e545b734ee6a199582e6f-problem',        'fc-target:Erdos274.herzog_schonheim', 'VERIFIED',   'REJECTED',   'INELIGIBLE', 'REVIEW_STATEMENT_TOO_WEAK', NULL,                     true,  240000000000, NULL, NULL, NULL, NULL, NULL, NULL, 1, E'  simpa [Erdos274.trivialCover] using Erdos274.degenerate_case', now() - interval '25 days'),
('50000000-0000-4000-8000-000000000007', 's07 retries spent',        'credit',    'a0000000-0000-4000-8000-000000000001', 'e0000000-0000-4000-8000-000000000007', '59AngoGg42QM5PtEUuYxbn6Ts5UA4EksUJMYsk65oEtnG3w4', 'Erdos282.erdos_282',        'counterexample', 'fc-e923379e-erdos282-erdos-282-1c2039087d-counterexample-v1',          'fc-e923379e-erdos282-erdos-282-bcb9ad4be562494c8e075ce0f9b36e72-problem',               'fc-target:Erdos282.erdos_282',        'UNVERIFIED', 'UNREVIEWED', 'INELIGIBLE', NULL,                       NULL,                      true,  115000000000, NULL, NULL, NULL, NULL, NULL, NULL, 3, E'  intro claim\n  have big := claim (2 ^ 64)\n  interval_cases <;> omega', now() - interval '6 hours'),
('50000000-0000-4000-8000-000000000008', 's08 payout failed',        'credit',    'a0000000-0000-4000-8000-000000000004', 'e0000000-0000-4000-8000-000000000008', '5pni659U8ysLcyxkMZUDruW4eBdHAxst1iH9G53pNmyPBSyK', 'Erdos340.erdos_340',        'formalized',     'fc-e923379e-erdos340-erdos-340-a94c373747-formalized-v1',              'fc-e923379e-erdos340-erdos-340-b695c52ae5157320107e8626daa73103-problem',               'fc-target:Erdos340.erdos_340',        'VERIFIED',   'APPROVED',   'FAILED',     'PAYOUT_EXHAUSTED_RETRIES', NULL,                      true,  160000000000, NULL, NULL, NULL, NULL, NULL, NULL, 1, E'  refine Erdos340.equiv.mpr ?_\n  exact Erdos340.constructive_bound', now() - interval '33 days'),
('50000000-0000-4000-8000-000000000009', 's09 paid out',             'credit',    'a0000000-0000-4000-8000-000000000005', 'e0000000-0000-4000-8000-000000000009', '5dg49PZ9i5m5FWkJqBuhuLUeBYMBY3bz92Dpdd2J2EhkF1XK', 'Erdos364.erdos_364',        'formalized',     'fc-e923379e-erdos364-erdos-364-5b256fef06-formalized-v1',              'fc-e923379e-erdos364-erdos-364-0efeb470025a056f57889953efa4ff78-problem',               'fc-target:Erdos364.erdos_364',        'VERIFIED',   'APPROVED',   'REWARDED',   NULL,                       NULL,                      false, 133000000000, NULL, NULL, NULL, NULL, NULL, NULL, 1, E'  have h := Erdos364.finite_support\n  simpa [Erdos364.normalize] using h', now() - interval '29 days'),
('50000000-0000-4000-8000-00000000000a', 's10 axiom rejected',       'credit',    'a0000000-0000-4000-8000-000000000006', 'e0000000-0000-4000-8000-00000000000a', '5voN6u6yQAm7r9ju3A2AfK5TJjUmB3ewQo52RGaRCFosVS7D', 'Erdos371.erdos_371',        'formalized',     'fc-e923379e-erdos371-erdos-371-0dcceb322a-formalized-v1',              'fc-e923379e-erdos371-erdos-371-4a325f182fc63bb8e3dc8e7464c34382-problem',               'fc-target:Erdos371.erdos_371',        'REJECTED',   'UNREVIEWED', 'INELIGIBLE', 'UNPERMITTED_AXIOM',        'axioms_permitted',        true,   91000000000, NULL, NULL, NULL, NULL, NULL, NULL, 1, E'  exact Erdos371.viaChoiceHack (Classical.choice _)', now() - interval '21 days'),
('50000000-0000-4000-8000-000000000014', 's20 review corrected',     'credit',    'a0000000-0000-4000-8000-000000000003', 'e0000000-0000-4000-8000-000000000014', '5SiwiEjcqjpuf6stGQjNbdMPuGQ1Uhm2kYGwTjLBt7DKwDJc', 'Erdos617.erdos_617',        'formalized',     'fc-e923379e-erdos617-erdos-617-fc0d1c02e7-formalized-v1',              'fc-e923379e-erdos617-erdos-617-a34acf2481f541fc79980c8976555f9e-problem',               'fc-target:Erdos617.erdos_617',        'VERIFIED',   'APPROVED',   'ELIGIBLE',   NULL,                       NULL,                      true,  175000000000, NULL, NULL, NULL, NULL, NULL, NULL, 1, E'  refine Erdos617.of_two_cases ?_ ?_\n  · simpa using Erdos617.small\n  · exact Erdos617.large', now() - interval '11 days'),

-- ---- extrinsic-funded (one finalized transfer per submission) ----------------------
('50000000-0000-4000-8000-00000000000b', 's11 paid out',             'extrinsic', NULL,                                   NULL,                                   '5NErXg3it6UXbKDNHUhvzbgoixU44tT1ApNHFoza8hKeUAPp', 'Erdos1094.erdos_1094',      'formalized',     'fc-e923379e-erdos1094-erdos-1094-e88b987211-formalized-v1',            'fc-e923379e-erdos1094-erdos-1094-01b666aa74175f864ea98b577071421a-problem',             'fc-target:Erdos1094.erdos_1094',      'VERIFIED',   'APPROVED',   'REWARDED',   NULL,                       NULL,                      true,  145000000000, 'sub-5382410-3-1', '5BQmG6PsQYgFueBcrUL45y3kAuRLQQeaLUWxjCP88iANhDjA', 500000000, 5382410, NULL, NULL, 1, E'  have h := Erdos1094.reduction\n  exact h ▸ Erdos1094.core_argument', now() - interval '8 days'),
('50000000-0000-4000-8000-00000000000c', 's12 in review',            'extrinsic', NULL,                                   NULL,                                   '5NErXg3it6UXbKDNHUhvzbgoixU44tT1ApNHFoza8hKeUAPp', 'Erdos1107.erdos_1107',      'counterexample', 'fc-e923379e-erdos1107-erdos-1107-3475890cd6-counterexample-v1',        'fc-e923379e-erdos1107-erdos-1107-c72e16fe82aa85ac006b1eba5cd1ad3f-problem',             'fc-target:Erdos1107.erdos_1107',      'VERIFIED',   'UNREVIEWED', 'INELIGIBLE', NULL,                       NULL,                      true,   62000000000, 'sub-5407880-1-0', '5BQmG6PsQYgFueBcrUL45y3kAuRLQQeaLUWxjCP88iANhDjA', 500000000, 5407880, NULL, NULL, 1, E'  intro claim\n  exact Erdos1107.explicit_counterexample.elim (claim _)', now() - interval '31 hours'),
('50000000-0000-4000-8000-00000000000d', 's13 statement mismatch',   'extrinsic', NULL,                                   NULL,                                   '53ZZjEw6qRgqxctd65PWAjK7p8qvtkBZ7skZoni83izNGLbD', 'Erdos184.erdos_184',        'formalized',     'fc-e923379e-erdos184-erdos-184-b12a3988e6-formalized-v1',              'fc-e923379e-erdos184-erdos-184-c764216d3519f4f368618eb908156d41-problem',               'fc-target:Erdos184.erdos_184',        'REJECTED',   'UNREVIEWED', 'INELIGIBLE', 'STATEMENT_MISMATCH',       'same_statement',          true,   77000000000, 'sub-5390221-0-2', '5XkfrrouYxUVviUXGdeqQf6hY6UiPm6JGrvNBYrCheGK6aJR', 500000000, 5390221, NULL, NULL, 1, E'  trivial', now() - interval '24 days'),
('50000000-0000-4000-8000-00000000000e', 's14 queued',               'extrinsic', NULL,                                   NULL,                                   '53ZZjEw6qRgqxctd65PWAjK7p8qvtkBZ7skZoni83izNGLbD', 'Erdos236.erdos_236',        'formalized',     'fc-e923379e-erdos236-erdos-236-bb09909b42-formalized-v1',              'fc-e923379e-erdos236-erdos-236-a803bfc34440e3776176d5b2508923c9-problem',               'fc-target:Erdos236.erdos_236',        'UNVERIFIED', 'UNREVIEWED', 'INELIGIBLE', NULL,                       NULL,                      true,   84000000000, 'sub-5408320-2-1', '5XkfrrouYxUVviUXGdeqQf6hY6UiPm6JGrvNBYrCheGK6aJR', 500000000, 5408320, NULL, NULL, 0, E'  induction n with\n  | zero => simp\n  | succ k ih => simpa [Erdos236.recurrence] using ih', now() - interval '3 minutes'),
('50000000-0000-4000-8000-00000000000f', 's15 payout submitted',     'extrinsic', NULL,                                   NULL,                                   '5Q2Kx1xqsWrueQQrW4PKCTEw953LW3mPLiavywuqrBCPQBWT', 'Erdos373.erdos_373',        'formalized',     'fc-e923379e-erdos373-erdos-373-2fb7dd4b5c-formalized-v1',              'fc-e923379e-erdos373-erdos-373-4a45af22a7c17af177772028d2ff368c-problem',               'fc-target:Erdos373.erdos_373',        'VERIFIED',   'APPROVED',   'ELIGIBLE',   NULL,                       NULL,                      true,  198000000000, 'sub-5406115-4-0', '5maD1dVunEnfnm96uuNUL2q2qbuW5wPDdAjSAHB9WV5DwDoi', 500000000, 5406115, NULL, NULL, 1, E'  refine Erdos373.characterization.mp ?_\n  exact Erdos373.explicit_family', now() - interval '2 days'),
('50000000-0000-4000-8000-000000000010', 's16 timed out',            'extrinsic', NULL,                                   NULL,                                   '5Q2Kx1xqsWrueQQrW4PKCTEw953LW3mPLiavywuqrBCPQBWT', 'Erdos41.erdos_41',          'counterexample', 'fc-e923379e-erdos41-erdos-41-1c9c2c7f57-counterexample-v1',            'fc-e923379e-erdos41-erdos-41-d167be12aae45174188bb169a7113151-problem',                'fc-target:Erdos41.erdos_41',          'REJECTED',   'UNREVIEWED', 'INELIGIBLE', 'TIMEOUT',                  'solution_built',          true,   69000000000, 'sub-5398004-1-5', '5maD1dVunEnfnm96uuNUL2q2qbuW5wPDdAjSAHB9WV5DwDoi', 500000000, 5398004, NULL, NULL, 2, E'  intro claim\n  decide', now() - interval '13 days'),
-- The extrinsic path does not need an account, but it is allowed to have one: this row
-- was paid by a coldkey whose account also signed in, so the money is named by an
-- extrinsic while the submission is still attributed.
('50000000-0000-4000-8000-000000000011', 's17 paid out, attributed', 'extrinsic', 'a0000000-0000-4000-8000-000000000009', NULL,                                   '5dujVUYoSwss5ApFDBgEwdM29T6wHGMVzFLTo4xfVnAoHBSV', 'Erdos535.erdos_535',        'formalized',     'fc-e923379e-erdos535-erdos-535-6f7f7b52b4-formalized-v1',              'fc-e923379e-erdos535-erdos-535-27232d2b7e4601fb587cacbbf7a487b2-problem',               'fc-target:Erdos535.erdos_535',        'VERIFIED',   'APPROVED',   'REWARDED',   NULL,                       NULL,                      false, 156000000000, 'sub-5392887-0-1', '5vpw71uYWM51ST1Z2xtTE1u9FpqtkYGfJN6EFaiNC6NXP9Qj', 500000000, 5392887, NULL, NULL, 1, E'  simpa [Erdos535.rescale] using Erdos535.sharp_bound', now() - interval '18 days'),

-- ---- competing attempts on an already-claimed target -------------------------------
-- Both INELIGIBLE, so submissions_reward_target_reward_unique still holds against s01.
('50000000-0000-4000-8000-000000000012', 's18 lost the race',        'extrinsic', NULL,                                   NULL,                                   '5NErXg3it6UXbKDNHUhvzbgoixU44tT1ApNHFoza8hKeUAPp', 'Erdos11.erdos_11',          'counterexample', 'fc-e923379e-erdos11-erdos-11-e9b733866c-counterexample-v1',            'fc-e923379e-erdos11-erdos-11-ef45c19b49e06b8d799e891bbecc78bd-problem',                'fc-target:Erdos11.erdos_11',          'REJECTED',   'UNREVIEWED', 'INELIGIBLE', 'COMPARATOR_REJECTED',      'task_commitment_valid',   true,  180000000000, 'sub-5386540-2-0', '5BQmG6PsQYgFueBcrUL45y3kAuRLQQeaLUWxjCP88iANhDjA', 500000000, 5386540, NULL, NULL, 1, E'  intro claim\n  exact Erdos11.bogus_refutation claim', now() - interval '52 days'),
('50000000-0000-4000-8000-000000000013', 's19 late duplicate',       'credit',    'a0000000-0000-4000-8000-000000000002', 'e0000000-0000-4000-8000-000000000013', '5Pyp1nhoPAAEuxPPaqU8egQ9Lx96GwJpxDn2bJ7rHDEoKBwG', 'Erdos11.erdos_11',          'formalized',     'fc-e923379e-erdos11-erdos-11-7c0303029e-formalized-v1',                'fc-e923379e-erdos11-erdos-11-ef45c19b49e06b8d799e891bbecc78bd-problem',                'fc-target:Erdos11.erdos_11',          'UNVERIFIED', 'UNREVIEWED', 'INELIGIBLE', NULL,                       NULL,                      true,  180000000000, NULL, NULL, NULL, NULL, NULL, NULL, 0, E'  have key := Erdos11.density_bound\n  exact key.mp (by positivity)', now() - interval '40 minutes');


-- --- 6a. Intents for the credit-funded rows ------------------------------------------
--
-- Inserted as BUNDLE_ATTACHED, which requires the admitted proof and the request digest
-- but not a submission. They are promoted to CONFIRMED in 6e, once the submissions they
-- name exist — intent_confirmed_has_a_submission forbids doing it any earlier.
--
-- `proof_content` lives on the intent between upload and confirmation because `proofs` is
-- the record of what was *verified*, and an unconfirmed intent has not paid for
-- verification yet.

INSERT INTO submission_intents (id, account_id, signer_coldkey, task_id, task_bundle_sha256, status,
                                credits_held, credit_price_rao,
                                proof_content, proof_sha256, request_digest,
                                submission_id, expires_at, created_at, updated_at)
SELECT p.intent_id,
       p.account_id,
       p.signer_coldkey,
       p.task_id,
       pg_temp.bundle(p.task_id),
       'BUNDLE_ATTACHED',
       1,
       pg_temp.credit_price(),
       convert_to(body, 'UTF8'),
       pg_catalog.sha256(convert_to(body, 'UTF8')),
       pg_temp.d32('request:' || p.handle::text),
       NULL,
       p.created_at + interval '30 minutes',
       p.created_at - interval '4 minutes',
       p.created_at - interval '1 minute'
FROM seed_plan AS p
CROSS JOIN LATERAL (
    SELECT format(E'-- submission %s\ntheorem target : %s := by\n%s\n',
                  p.handle,
                  CASE WHEN p.task_mode = 'counterexample'
                       THEN format('¬ (fcTypeOfName%% "%s")', p.theorem)
                       ELSE format('fcTypeOfName%% "%s"', p.theorem)
                  END,
                  p.proof_tactic) AS body
) AS proof
WHERE p.funding = 'credit';


-- --- 6b. Extra intents in the states that never became a submission -----------------
--
-- OPEN, EXPIRED and CANCELLED complete the `intent_state` enum. None of them is allowed
-- a submission, and the two live ones are what `credits_available` subtracts as holds.

INSERT INTO submission_intents (id, account_id, signer_coldkey, task_id, task_bundle_sha256, status,
                                credits_held, credit_price_rao,
                                proof_content, proof_sha256, request_digest,
                                submission_id, expires_at, created_at, updated_at) VALUES
    -- OPEN: credit held, no bundle uploaded yet.
    ('e0000000-0000-4000-8000-0000000000f1', 'a0000000-0000-4000-8000-000000000001', '5YXMcmddggxUSQsQsJyuAhtB3umCDmjcacdbDQvX9VWWm1x4', 'fc-e923379e-erdos624-erdos-624-52aaa15f04-formalized-v1', pg_temp.bundle('fc-e923379e-erdos624-erdos-624-52aaa15f04-formalized-v1'), 'OPEN', 1, pg_temp.credit_price(), NULL, NULL, NULL, NULL, now() + interval '24 minutes', now() - interval '6 minutes', now() - interval '6 minutes'),
    -- A multi-credit hold, to prove credits_held is not always 1.
    ('e0000000-0000-4000-8000-0000000000f2', 'a0000000-0000-4000-8000-000000000008', '5AHhzFJMzH9RPJP4oeAQfzWH6C7dFoDxWK5qV3MfcoeHveci', 'fc-e923379e-erdos677-erdos-677-9d7d4ba36d-formalized-v1', pg_temp.bundle('fc-e923379e-erdos677-erdos-677-9d7d4ba36d-formalized-v1'), 'OPEN', 3, pg_temp.credit_price(), NULL, NULL, NULL, NULL, now() + interval '27 minutes', now() - interval '3 minutes', now() - interval '3 minutes'),
    -- BUNDLE_ATTACHED and still live: the proof is admitted, the digest was returned, and
    -- the client has not signed it yet.
    ('e0000000-0000-4000-8000-0000000000f3', 'a0000000-0000-4000-8000-000000000003', '5SiwiEjcqjpuf6stGQjNbdMPuGQ1Uhm2kYGwTjLBt7DKwDJc', 'fc-e923379e-erdos779-erdos-779-71ea8bbf9c-formalized-v1', pg_temp.bundle('fc-e923379e-erdos779-erdos-779-71ea8bbf9c-formalized-v1'), 'BUNDLE_ATTACHED', 1, pg_temp.credit_price(),
     convert_to(E'-- intent e0000000-0000-4000-8000-0000000000f3\ntheorem target : fcTypeOfName% "Erdos779.erdos_779" := by\n  exact Erdos779.pending_argument\n', 'UTF8'),
     pg_catalog.sha256(convert_to(E'-- intent e0000000-0000-4000-8000-0000000000f3\ntheorem target : fcTypeOfName% "Erdos779.erdos_779" := by\n  exact Erdos779.pending_argument\n', 'UTF8')),
     pg_temp.d32('request:intent-f3'), NULL, now() + interval '21 minutes', now() - interval '9 minutes', now() - interval '8 minutes'),
    -- EXPIRED: the window closed. The held credit was released, and the proof bytes were
    -- discarded with it. This is the intent the REFUND in section 4b refers to.
    ('e0000000-0000-4000-8000-0000000000f4', 'a0000000-0000-4000-8000-000000000005', '5NQfa2nJhT3SHUfrJf8tgrsrwMtjun8SkH1hNCd9m6zjnzMx', 'fc-e923379e-erdos859-erdos-859-e2f1f9e0a4-formalized-v1', pg_temp.bundle('fc-e923379e-erdos859-erdos-859-e2f1f9e0a4-formalized-v1'), 'EXPIRED', 1, pg_temp.credit_price(), NULL, NULL, NULL, NULL, now() - interval '18 days' + interval '30 minutes', now() - interval '18 days', now() - interval '18 days' + interval '30 minutes'),
    ('e0000000-0000-4000-8000-0000000000f5', 'a0000000-0000-4000-8000-000000000002', '5uo9Jo18H1UCroFX4iUA4V88XgFdn2wejmQQMQLB58uJmeG8', 'fc-e923379e-erdos889-erdos-889-3f0a51c60b-formalized-v1', pg_temp.bundle('fc-e923379e-erdos889-erdos-889-3f0a51c60b-formalized-v1'), 'EXPIRED', 1, pg_temp.credit_price(), NULL, NULL, NULL, NULL, now() - interval '9 days' + interval '30 minutes', now() - interval '9 days', now() - interval '9 days' + interval '30 minutes'),
    -- CANCELLED: the miner gave up before confirming. The credit was never spent, so
    -- nothing is refunded — the hold simply evaporates.
    ('e0000000-0000-4000-8000-0000000000f6', 'a0000000-0000-4000-8000-000000000001', '5JUCqYXJcJ2SPpugAfZN8ftLxhw2qEqs4AVgusS9wDfS52Wi', 'fc-e923379e-erdos912-erdos-912-49dd0da12f-formalized-v1', pg_temp.bundle('fc-e923379e-erdos912-erdos-912-49dd0da12f-formalized-v1'), 'CANCELLED', 1, pg_temp.credit_price(), NULL, NULL, NULL, NULL, now() - interval '5 days' + interval '30 minutes', now() - interval '5 days', now() - interval '5 days' + interval '11 minutes');


-- --- 6c. SPEND entries --------------------------------------------------------------
--
-- A SPEND names its INTENT, never its submission: `submissions.credit_ledger_id` points
-- at this table, so pointing back would make the two rows mutually dependent with no
-- insert order that works — and an append-only ledger cannot be backfilled.

INSERT INTO credit_ledger (account_id, kind, amount_rao, credit_price_rao, deposit_id, intent_id, reason, created_by, created_at)
SELECT p.account_id, 'SPEND', -pg_temp.credit_price(), pg_temp.credit_price(), NULL, p.intent_id, NULL, 'system', p.created_at
FROM seed_plan AS p
WHERE p.funding = 'credit';


-- --- 6d. Proofs and submissions ------------------------------------------------------
--
-- `proofs` is write-once by design (REVOKE UPDATE, DELETE from the service role in
-- deployment), and its digest is checked against its content, so the bytes are written
-- here exactly once and everything else refers to the digest.

CREATE TEMP TABLE seed_proof_body AS
SELECT p.handle,
       convert_to(
           format(E'-- submission %s\ntheorem target : %s := by\n%s\n',
                  p.handle,
                  CASE WHEN p.task_mode = 'counterexample'
                       THEN format('¬ (fcTypeOfName%% "%s")', p.theorem)
                       ELSE format('fcTypeOfName%% "%s"', p.theorem)
                  END,
                  p.proof_tactic),
           'UTF8') AS body
FROM seed_plan AS p;

INSERT INTO proofs (digest, content, byte_length, created_at)
SELECT pg_catalog.sha256(b.body), b.body, octet_length(b.body), p.created_at
FROM seed_proof_body AS b
JOIN seed_plan AS p USING (handle);

INSERT INTO submissions (
    id, signer_coldkey, idempotency_key, request_digest, task_id, task_bundle_sha256,
    problem_id, task_mode, reward_target_id, proof_digest,
    payment_reference, payment_sender, payment_amount_rao, payment_block, signer_signature,
    verification_status, manual_review_status, reward_status, failure_reason,
    manual_review_required, review_policy_version,
    bounty_amount_rao, bounty_policy_version, bounty_inputs,
    verification_lease_until, verification_lease_owner, verification_attempts,
    account_id, credit_ledger_id, intent_id,
    created_at, updated_at)
SELECT
    p.handle,
    -- On the extrinsic path the signer IS the payer -- `submission_signer_coldkey_is_funded`
    -- requires it -- so the plan's key is used only where there is no payment to match.
    COALESCE(p.payment_sender, p.signer_coldkey),
    -- Deterministic, and unique per (signer_coldkey, idempotency_key) because the handle is.
    ('f0000000-0000-4000-8000-' || right(replace(p.handle::text, '-', ''), 12))::uuid,
    pg_temp.d32('request:' || p.handle::text),
    p.task_id,
    pg_temp.bundle(p.task_id),
    p.problem_id,
    p.task_mode::task_mode,
    p.reward_target_id,
    pg_catalog.sha256(b.body),
    p.payment_reference,
    p.payment_sender,
    p.payment_rao,
    p.payment_block,
    pg_temp.sig64('signer-signature:' || p.handle::text),
    p.verification::verification_state,
    p.review::manual_review_state,
    p.reward::reward_state,
    p.failure_reason,
    p.review_required,
    'review-v1',
    p.bounty_rao,
    'dynamic-age-v1',
    -- What the pricing rule read, so the quote stays reproducible rather than asserted.
    jsonb_build_object(
        'policy', 'dynamic-age-v1',
        'reward_target_id', p.reward_target_id,
        'target_age_days', GREATEST(0, (SELECT floor(extract(epoch FROM (p.created_at - bt.opened_at)) / 86400)::int
                                        FROM bounty_tasks AS bt
                                        WHERE bt.reward_target_id = p.reward_target_id)),
        'pool_balance_rao', 41000000000000,
        'base_rao', 60000000000,
        'age_multiplier_bp', 10000 + (p.bounty_rao / 20000000)::int,
        'quoted_rao', p.bounty_rao,
        'note', 'Indicative estimate shown at intake; not a locked payout amount'),
    p.lease_until,
    p.lease_owner,
    p.attempts,
    p.account_id,
    CASE WHEN p.funding = 'credit'
         THEN (SELECT l.id FROM credit_ledger AS l
               WHERE l.intent_id = p.intent_id AND l.kind = 'SPEND')
    END,
    p.intent_id,
    p.created_at,
    GREATEST(p.created_at, p.created_at + interval '11 minutes')
FROM seed_plan AS p
JOIN seed_proof_body AS b USING (handle);


-- --- 6e. Promote the intents to CONFIRMED -------------------------------------------
--
-- The proof bytes now live in `proofs`, which is the durable record; keeping a second copy
-- on the intent would mean a proof deleted from one place still existed in the other.
--
-- The BEFORE UPDATE trigger rewrites updated_at to now(), so these intents lose their
-- backdated updated_at. That is what production does too — the confirmation is the last
-- write — so it is left alone rather than worked around.

UPDATE submission_intents AS i
SET status = 'CONFIRMED',
    submission_id = p.handle,
    proof_content = NULL,
    proof_sha256 = NULL
FROM seed_plan AS p
WHERE p.funding = 'credit'
  AND i.id = p.intent_id;


-- =====================================================================================
-- 7. Verification runs
--
-- Claims, not verdicts: an infrastructure failure spends an attempt on purpose, so a row
-- with attempts = 2 and a VERIFIED outcome has one INTERNAL_ERROR run and one accepted
-- run. `report` keeps the exact bytes so `report_digest` stays recomputable, and NULL
-- report means the run died before writing one.
-- =====================================================================================

-- The failed claims that precede a final verdict, one per spent-but-inconclusive attempt.
INSERT INTO verification_runs (submission_id, task_bundle_sha256, proof_digest, verifier_version,
                               container_digest, sandbox_mode, accepted, reason_code, stage,
                               checks, report, report_digest, started_at, finished_at)
SELECT s.id,
       s.task_bundle_sha256,
       s.proof_digest,
       pg_temp.verifier_version(),
       pg_temp.container(),
       'landrun',
       false,
       'INTERNAL_ERROR',
       'WORKSPACE_PREPARED',
       NULL,        -- the run died before a report existed, so checks and report are both NULL
       NULL,
       NULL,
       s.created_at + (n || ' minutes')::interval,
       s.created_at + (n || ' minutes')::interval + interval '38 seconds'
FROM submissions AS s
JOIN seed_plan AS p ON p.handle = s.id
CROSS JOIN generate_series(1, GREATEST(p.attempts - 1, 0)) AS n
WHERE p.attempts > 1;

-- The final verdict, for everything that reached one.
INSERT INTO verification_runs (submission_id, task_bundle_sha256, proof_digest, verifier_version,
                               container_digest, sandbox_mode, accepted, reason_code, stage,
                               checks, report, report_digest, started_at, finished_at)
SELECT s.id,
       s.task_bundle_sha256,
       s.proof_digest,
       pg_temp.verifier_version(),
       pg_temp.container(),
       'landrun',
       p.verification = 'VERIFIED',
       CASE WHEN p.verification = 'VERIFIED' THEN 'VERIFIED' ELSE p.failure_reason END,
       CASE WHEN p.verification = 'VERIFIED' THEN 'COMPLETED' ELSE 'COMPARATOR' END,
       verdict.checks,
       verdict.report,
       pg_catalog.sha256(verdict.report),
       s.created_at + (p.attempts || ' minutes')::interval,
       s.created_at + (p.attempts || ' minutes')::interval + (120 + (p.bounty_rao % 400))::int * interval '1 second'
FROM submissions AS s
JOIN seed_plan AS p ON p.handle = s.id
CROSS JOIN LATERAL (
    SELECT c.checks,
           pg_temp.report(
               p.verification = 'VERIFIED',
               CASE WHEN p.verification = 'VERIFIED' THEN 'VERIFIED' ELSE p.failure_reason END,
               CASE WHEN p.verification = 'VERIFIED' THEN 'COMPLETED' ELSE 'COMPARATOR' END,
               p.task_id, p.problem_id, p.task_mode, c.checks,
               (120 + (p.bounty_rao % 400))::int * 1000) AS report
    FROM (SELECT CASE WHEN p.verification = 'VERIFIED'
                      THEN pg_temp.checks_pass()
                      ELSE pg_temp.checks_fail(p.failed_check)
                 END AS checks) AS c
) AS verdict
WHERE p.verification <> 'UNVERIFIED';


-- =====================================================================================
-- 8. Review decisions
--
-- HUMAN is the only kind that may reject a Lean-valid proof. AUTOMATIC is recorded when
-- manual review is disabled, so the policy decision still exists as a row. ADVISORY is an
-- LLM pre-check: evidence, never binding.
-- =====================================================================================

-- ADVISORY pre-checks, on the verified submissions that went to a human.
INSERT INTO review_decisions (submission_id, decision, kind, reviewer, policy_version, reason_code, notes, evidence, created_at)
SELECT s.id,
       CASE WHEN p.label = 's20 review corrected' THEN 'REJECTED' ELSE 'APPROVED' END::review_outcome,
       'ADVISORY',
       'claude-opus-5',
       'review-v1',
       CASE WHEN p.label = 's20 review corrected' THEN 'ADVISORY_SUSPECTS_WEAKENING' ELSE 'ADVISORY_NO_CONCERN' END,
       'Automated pre-check. Advisory only; a human decision is still required.',
       jsonb_build_object(
           'model', 'claude-opus-5',
           'prompt_version', 'review-advisory-v3',
           'verdict', CASE WHEN p.label = 's20 review corrected' THEN 'reject' ELSE 'approve' END,
           'confidence', 0.71,
           'question', 'Does the submitted theorem statement match the conjecture as published?'),
       s.created_at + interval '25 minutes'
FROM submissions AS s
JOIN seed_plan AS p ON p.handle = s.id
WHERE p.verification = 'VERIFIED' AND p.review_required AND p.review <> 'UNREVIEWED';

-- The binding decisions.
INSERT INTO review_decisions (submission_id, decision, kind, reviewer, policy_version, reason_code, notes, evidence, created_at)
SELECT s.id,
       -- s20 is rejected here and corrected below, so its first binding decision is a
       -- rejection even though the submission now reads APPROVED.
       CASE WHEN p.label = 's20 review corrected' THEN 'REJECTED' ELSE p.review END::review_outcome,
       CASE WHEN p.review_required THEN 'HUMAN' ELSE 'AUTOMATIC' END::reviewer_kind,
       CASE WHEN p.review_required THEN 'reviewer:carol@example.test' ELSE 'system' END,
       'review-v1',
       CASE
           WHEN NOT p.review_required            THEN 'AUTO_REVIEW_DISABLED'
           WHEN p.label = 's20 review corrected' THEN 'REVIEW_STATEMENT_TOO_WEAK'
           WHEN p.review = 'REJECTED'            THEN COALESCE(p.failure_reason, 'REVIEW_REJECTED')
           ELSE 'REVIEW_APPROVED'
       END,
       CASE
           WHEN NOT p.review_required            THEN 'Manual review is disabled by policy; recorded so the decision is not implicit.'
           WHEN p.label = 's20 review corrected' THEN 'The target looked like a weakened restatement on first read.'
           WHEN p.review = 'REJECTED'            THEN 'The proof is Lean-valid but discharges a degenerate case, not the conjecture.'
           ELSE 'Statement matches the published conjecture; dependencies are within the permitted set.'
       END,
       NULL,
       s.created_at + interval '2 hours'
FROM submissions AS s
JOIN seed_plan AS p ON p.handle = s.id
WHERE p.review <> 'UNREVIEWED';

-- The correction. Decisions chain rather than being overwritten, and the foreign key on
-- (submission_id, supersedes_id) is composite, so a decision can only supersede another
-- decision on the same submission.
INSERT INTO review_decisions (submission_id, decision, kind, reviewer, policy_version, reason_code, notes, evidence, supersedes_id, created_at)
SELECT d.submission_id,
       'APPROVED',
       'HUMAN',
       'reviewer:dave@example.test',
       'review-v1',
       'REVIEW_APPROVED',
       'Supersedes the earlier rejection. The statement is the published form; the '
       || 'apparent weakening was a mathlib notation difference, confirmed with the source.',
       NULL,
       d.id,
       d.created_at + interval '3 days'
FROM review_decisions AS d
WHERE d.submission_id = '50000000-0000-4000-8000-000000000014'
  AND d.kind = 'HUMAN'
  AND d.decision = 'REJECTED';


-- =====================================================================================
-- 9. Reward events
--
-- The payout, and the amount of record — the estimate on `submissions` was indicative.
-- All four payout states. A failed attempt is never retried in place: the next attempt is
-- a new row, which is why s08 has two.
-- =====================================================================================

INSERT INTO reward_events (submission_id, eligibility_reason, amount_rao,
                           destination_coldkey, status,
                           extrinsic_reference, submitted_block, finalized_block, failure_reason,
                           pricing_policy_version, pricing_inputs,
                           initiated_by, created_at, submitted_at, confirmed_at)
SELECT s.id,
       CASE WHEN p.review_required THEN 'REVIEW_APPROVED' ELSE 'AUTO_REVIEW_DISABLED' END,
       payout.amount_rao,
       -- Where the money actually went. Captured, not derived, and one coldkey since V035:
       -- the account's configured destination, else the key that signed and paid. No
       -- destination hotkey is recorded -- `transfer_stake` leaves the stake on ours.
       COALESCE(a.payout_coldkey, p.signer_coldkey, p.payment_sender),
       payout.status::payout_state,
       payout.reference,
       payout.submitted_block,
       payout.finalized_block,
       payout.failure_reason,
       'dynamic-age-v1',
       jsonb_build_object(
           'policy', 'dynamic-age-v1',
           'reward_target_id', p.reward_target_id,
           'intake_estimate_rao', p.bounty_rao,
           'pool_balance_rao', 41000000000000,
           'settled_rao', payout.amount_rao,
           'note', 'Payout pricing, evaluated at approval; may differ from the intake estimate'),
       payout.initiated_by,
       s.created_at + interval '3 hours',
       payout.submitted_at,
       payout.confirmed_at
FROM submissions AS s
JOIN seed_plan AS p ON p.handle = s.id
LEFT JOIN accounts AS a ON a.id = p.account_id
CROSS JOIN LATERAL (VALUES
    -- (status, amount, reference, submitted_block, finalized_block, failure_reason, submitted_at, confirmed_at, initiated_by)
    (CASE p.label
         WHEN 's01 paid out'             THEN 'CONFIRMED'
         WHEN 's09 paid out'             THEN 'CONFIRMED'
         WHEN 's11 paid out'             THEN 'CONFIRMED'
         WHEN 's17 paid out, attributed' THEN 'CONFIRMED'
         WHEN 's15 payout submitted'     THEN 'SUBMITTED'
         WHEN 's08 payout failed'        THEN 'FAILED'
         ELSE 'PENDING'
     END,
     -- Settled slightly off the intake estimate, which is the point of recording both.
     (p.bounty_rao * 1.04)::bigint,
     CASE p.label
         WHEN 's01 paid out'             THEN 'pay-5384102-1-0'
         WHEN 's09 paid out'             THEN 'pay-5391775-0-2'
         WHEN 's11 paid out'             THEN 'pay-5405980-2-1'
         WHEN 's17 paid out, attributed' THEN 'pay-5394330-3-0'
         WHEN 's15 payout submitted'     THEN 'pay-5408300-1-1'
         ELSE NULL
     END,
     CASE p.label
         WHEN 's01 paid out'             THEN 5384102::bigint
         WHEN 's09 paid out'             THEN 5391775::bigint
         WHEN 's11 paid out'             THEN 5405980::bigint
         WHEN 's17 paid out, attributed' THEN 5394330::bigint
         WHEN 's15 payout submitted'     THEN 5408300::bigint
         ELSE NULL
     END,
     CASE p.label
         WHEN 's01 paid out'             THEN 5384104::bigint
         WHEN 's09 paid out'             THEN 5391777::bigint
         WHEN 's11 paid out'             THEN 5405982::bigint
         WHEN 's17 paid out, attributed' THEN 5394332::bigint
         ELSE NULL
     END,
     CASE p.label
         WHEN 's08 payout failed' THEN 'Insufficient stake on the validator hotkey to cover the transfer; retry scheduled'
         ELSE NULL
     END,
     CASE p.label
         WHEN 's01 paid out'             THEN s.created_at + interval '4 hours'
         WHEN 's09 paid out'             THEN s.created_at + interval '5 hours'
         WHEN 's11 paid out'             THEN s.created_at + interval '4 hours'
         WHEN 's17 paid out, attributed' THEN s.created_at + interval '6 hours'
         WHEN 's15 payout submitted'     THEN s.created_at + interval '4 hours'
         ELSE NULL
     END,
     CASE p.label
         WHEN 's01 paid out'             THEN s.created_at + interval '4 hours 2 minutes'
         WHEN 's09 paid out'             THEN s.created_at + interval '5 hours 3 minutes'
         WHEN 's11 paid out'             THEN s.created_at + interval '4 hours 1 minute'
         WHEN 's17 paid out, attributed' THEN s.created_at + interval '6 hours 2 minutes'
         ELSE NULL
     END,
     CASE WHEN p.review_required THEN 'operator:dave@example.test' ELSE 'system' END
    )
) AS payout(status, amount_rao, reference, submitted_block, finalized_block, failure_reason, submitted_at, confirmed_at, initiated_by)
WHERE p.reward <> 'INELIGIBLE';

-- The retry of s08's failed payout. A new row, because deduplication is the payout CLI's
-- job and a second attempt is a second attempt.
INSERT INTO reward_events (submission_id, eligibility_reason, amount_rao,
                           destination_coldkey, status,
                           extrinsic_reference, submitted_block, finalized_block, failure_reason,
                           pricing_policy_version, pricing_inputs,
                           initiated_by, created_at, submitted_at, confirmed_at)
SELECT s.id, 'REVIEW_APPROVED', (p.bounty_rao * 1.04)::bigint,
       a.payout_coldkey, 'FAILED',
       NULL, NULL, NULL,
       'Second attempt aborted before broadcast: the multisig rejected the call',
       'dynamic-age-v1',
       jsonb_build_object('policy', 'dynamic-age-v1', 'attempt', 2,
                          'reward_target_id', p.reward_target_id,
                          'intake_estimate_rao', p.bounty_rao),
       'operator:dave@example.test',
       s.created_at + interval '2 days', NULL, NULL
FROM submissions AS s
JOIN seed_plan AS p ON p.handle = s.id
JOIN accounts AS a ON a.id = p.account_id
WHERE p.label = 's08 payout failed';


-- =====================================================================================
-- 10. Submission timeline
--
-- What the miner sees while waiting. The status columns say where a submission is now;
-- this says how it got there, which is the question asked when nothing appears to be
-- happening. Built as one UNION ALL per transition so every submission gets a coherent,
-- ordered timeline without hand-writing 90 rows.
-- =====================================================================================

INSERT INTO submission_events (submission_id, kind, detail, context, actor, occurred_at)
-- Intake.
SELECT s.id, 'SUBMISSION_ACCEPTED',
       CASE WHEN p.funding = 'credit'
            THEN 'Credit debited and submission recorded.'
            ELSE 'Finalized payment confirmed and submission recorded.' END,
       CASE WHEN p.funding = 'credit'
            THEN jsonb_build_object('intent_id', p.intent_id, 'credit_ledger_id', s.credit_ledger_id)
            ELSE jsonb_build_object('payment_reference', p.payment_reference, 'payment_block', p.payment_block) END,
       'system', s.created_at
FROM submissions AS s JOIN seed_plan AS p ON p.handle = s.id

UNION ALL
SELECT s.id, 'QUEUED_FOR_VERIFICATION',
       'Waiting for a verification worker to claim the proof.',
       jsonb_build_object('task_id', p.task_id, 'task_mode', p.task_mode),
       'system', s.created_at + interval '2 seconds'
FROM submissions AS s JOIN seed_plan AS p ON p.handle = s.id

-- One claim per spent attempt.
UNION ALL
SELECT s.id, 'VERIFICATION_CLAIMED',
       format('Claimed by a verification worker (attempt %s).', n),
       jsonb_build_object('attempt', n, 'worker', 'verification-worker-' || (1 + n % 3) || '@fra1'),
       'verification-worker-' || (1 + n % 3) || '@fra1',
       s.created_at + (n || ' minutes')::interval
FROM submissions AS s JOIN seed_plan AS p ON p.handle = s.id
CROSS JOIN generate_series(1, p.attempts) AS n
WHERE p.attempts > 0

-- The inconclusive attempts that were retried.
UNION ALL
SELECT s.id, 'VERIFICATION_RETRY_SCHEDULED',
       'The worker died before producing a verdict; the attempt was spent and the proof requeued.',
       jsonb_build_object('attempt', n, 'reason_code', 'INTERNAL_ERROR'),
       'system',
       s.created_at + (n || ' minutes')::interval + interval '40 seconds'
FROM submissions AS s JOIN seed_plan AS p ON p.handle = s.id
CROSS JOIN generate_series(1, GREATEST(p.attempts - 1, 0)) AS n
WHERE p.attempts > 1

UNION ALL
SELECT s.id, 'VERIFICATION_PASSED',
       'The Lean kernel accepted the proof and every gate passed.',
       jsonb_build_object('verifier_version', pg_temp.verifier_version(), 'sandbox_mode', 'landrun'),
       'verification-worker-1@fra1', s.created_at + (p.attempts || ' minutes')::interval + interval '5 minutes'
FROM submissions AS s JOIN seed_plan AS p ON p.handle = s.id
WHERE p.verification = 'VERIFIED'

UNION ALL
SELECT s.id, 'VERIFICATION_FAILED',
       'The proof was rejected. The reason code is the machine-readable part.',
       jsonb_build_object('reason_code', p.failure_reason, 'failed_check', p.failed_check),
       'verification-worker-1@fra1', s.created_at + (p.attempts || ' minutes')::interval + interval '5 minutes'
FROM submissions AS s JOIN seed_plan AS p ON p.handle = s.id
WHERE p.verification = 'REJECTED'

UNION ALL
SELECT s.id, 'REVIEW_QUEUED',
       'Verified. Waiting for a reviewer.',
       NULL, 'system', s.created_at + interval '1 hour'
FROM submissions AS s JOIN seed_plan AS p ON p.handle = s.id
WHERE p.verification = 'VERIFIED' AND p.review_required

UNION ALL
SELECT s.id, 'REVIEW_APPROVED',
       CASE WHEN p.review_required
            THEN 'A reviewer approved the submission.'
            ELSE 'Manual review is disabled; approval recorded automatically.' END,
       jsonb_build_object('policy_version', 'review-v1'),
       CASE WHEN p.review_required THEN 'reviewer:carol@example.test' ELSE 'system' END,
       s.created_at + interval '2 hours'
FROM submissions AS s JOIN seed_plan AS p ON p.handle = s.id
WHERE p.review = 'APPROVED' AND p.label <> 's20 review corrected'

UNION ALL
SELECT s.id, 'REVIEW_REJECTED',
       'A reviewer rejected the submission. The proof is Lean-valid; the statement is not the conjecture.',
       jsonb_build_object('policy_version', 'review-v1', 'reason_code', COALESCE(p.failure_reason, 'REVIEW_REJECTED')),
       'reviewer:carol@example.test', s.created_at + interval '2 hours'
FROM submissions AS s JOIN seed_plan AS p ON p.handle = s.id
WHERE p.review = 'REJECTED' OR p.label = 's20 review corrected'

UNION ALL
SELECT s.id, 'REVIEW_APPROVED',
       'An earlier rejection was superseded on appeal.',
       jsonb_build_object('policy_version', 'review-v1', 'supersedes', 'the rejection three days earlier'),
       'reviewer:dave@example.test', s.created_at + interval '3 days 2 hours'
FROM submissions AS s JOIN seed_plan AS p ON p.handle = s.id
WHERE p.label = 's20 review corrected'

UNION ALL
SELECT s.id, 'REWARD_ELIGIBLE',
       'The submission is eligible for payout.',
       jsonb_build_object('intake_estimate_rao', p.bounty_rao), 'system',
       -- s20 only became eligible when the appeal overturned its rejection, so its event
       -- has to sit after the correction rather than after the original decision.
       s.created_at + CASE WHEN p.label = 's20 review corrected'
                           THEN interval '3 days 3 hours'
                           ELSE interval '3 hours' END
FROM submissions AS s JOIN seed_plan AS p ON p.handle = s.id
WHERE p.reward <> 'INELIGIBLE'

UNION ALL
SELECT r.submission_id, 'PAYOUT_SUBMITTED',
       'A transfer was signed and broadcast.',
       jsonb_build_object('extrinsic_reference', r.extrinsic_reference, 'amount_rao', r.amount_rao),
       r.initiated_by, r.submitted_at
FROM reward_events AS r
WHERE r.submitted_at IS NOT NULL

UNION ALL
SELECT r.submission_id, 'PAYOUT_CONFIRMED',
       'The transfer was finalized.',
       jsonb_build_object('finalized_block', r.finalized_block, 'amount_rao', r.amount_rao),
       'system', r.confirmed_at
FROM reward_events AS r
WHERE r.confirmed_at IS NOT NULL

UNION ALL
SELECT r.submission_id, 'PAYOUT_FAILED',
       'The payout attempt failed. The next attempt is recorded as a new event.',
       jsonb_build_object('failure_reason', r.failure_reason),
       r.initiated_by, r.created_at + interval '1 minute'
FROM reward_events AS r
WHERE r.status = 'FAILED';


-- =====================================================================================
-- 11. Bulk low-value submissions
--
-- 72 more submissions so the keyset-paginated public feeds actually page, the rejection
-- histogram has a shape, and the in-review queue is not three rows deep. All INELIGIBLE,
-- which is what keeps them clear of submissions_reward_target_reward_unique while still
-- cycling over all 29 reward targets.
--
-- Every one is extrinsic-funded with its own unique payment reference, and every proof
-- body is distinct because it embeds n.
-- =====================================================================================

CREATE TEMP TABLE seed_bulk AS
WITH targets AS (
    SELECT reward_target_id,
           row_number() OVER (ORDER BY reward_target_id) - 1 AS idx,
           count(*) OVER () AS total
    FROM bounty_tasks
),
rows AS (
    SELECT n,
           -- Spread over 90 days, newest last, with sub-day jitter so created_at is not
           -- unique per day and the (created_at, id) keyset cursor is exercised properly.
           now() - ((90.0 * (72 - n) / 72.0) || ' days')::interval - ((n * 37 % 1440) || ' minutes')::interval AS created_at,
           (ARRAY['5k4xuP9DQas3ZDan1JHpHcZT89wJY7BFRrQP8YCY25gt4JFn',
                  '5oizPxGjyqn9qR7uz5NYKar7caPiEfiHJ8uZCUMYMyyHEjDU',
                  '5j37iQuxdgxv6S1Uc6ZiTwhB2JYW19nUCC4MTPvf2KtgnsGD',
                  '5nsN54dmfNYLTBQfdCDKgKdbvkUgrJJ1nBeKPuuTVMtPYQKV',
                  '515MLXeZEQgzTyxjrVJ4CG4MGVaktBUJWdZ22nhwGBg6k4ZU',
                  '5Hs4bcMyt99vB1g8ABKFDEMNGWGFyzcFfTCm2DADQBDT2tMN'])[1 + n % 6] AS unused_rig,
           (ARRAY['54GnKREdXkwD16ghTySY9E5AJo232KyHB3QJRHc44Y5RT57s',
                  '5eJjufukbD3ZmHZnGULa6KMdZ2FzXWaBKP9s8SQ2u6s1LEt6',
                  '5BQmG6PsQYgFueBcrUL45y3kAuRLQQeaLUWxjCP88iANhDjA'])[1 + n % 3] AS payment_sender,
           -- 1 in 3 verified-and-in-review, 1 in 3 rejected, the rest still queued.
           (ARRAY['VERIFIED', 'REJECTED', 'REJECTED', 'UNVERIFIED'])[1 + n % 4] AS verification,
           (ARRAY['LEAN_KERNEL_REJECTED', 'STATEMENT_MISMATCH', 'UNPERMITTED_AXIOM',
                  'TIMEOUT', 'COMPARATOR_REJECTED', 'SUBMISSION_POLICY_VIOLATION',
                  'BUNDLE_DIGEST_MISMATCH', 'RESOURCE_LIMIT', 'NANODA_REJECTED'])[1 + n % 9] AS failure_reason,
           (ARRAY['lean_kernel_passed', 'same_statement', 'axioms_permitted',
                  'solution_built', 'task_commitment_valid', 'submission_policy_valid',
                  'manifest_valid', 'production_sandbox', 'nanoda_passed'])[1 + n % 9] AS failed_check,
           (ARRAY['formalized', 'formalized', 'counterexample'])[1 + n % 3] AS task_mode
    FROM generate_series(1, 72) AS n
)
SELECT
    ('50000000-0000-4000-8001-' || lpad(r.n::text, 12, '0'))::uuid AS handle,
    r.unused_rig,
    r.payment_sender,
    r.verification,
    CASE WHEN r.verification = 'REJECTED' THEN r.failure_reason END AS failure_reason,
    CASE WHEN r.verification = 'REJECTED' THEN r.failed_check END AS failed_check,
    r.task_mode,
    t.reward_target_id,
    -- The catalog's own naming, minus the digest segments, which would have to be
    -- recomputed from the theorem to be real. Distinct per row, which is what the schema
    -- requires; not byte-identical to a generated task id, which it does not.
    format('fc-e923379e-%s-%s-%s-v1',
           lower(translate(replace(t.reward_target_id, 'fc-target:', ''), '._', '--')),
           substr(md5(t.reward_target_id || r.task_mode), 1, 10),
           r.task_mode) AS task_id,
    format('fc-e923379e-%s-%s-problem',
           lower(translate(replace(t.reward_target_id, 'fc-target:', ''), '._', '--')),
           substr(md5(t.reward_target_id || 'problem'), 1, 32)) AS problem_id,
    format('sub-bulk-%s-%s-0', 5300000 + r.n * 137, r.n % 8) AS payment_reference,
    5300000 + r.n * 137 AS payment_block,
    60000000000 + (r.n % 17) * 3000000000 AS bounty_rao,
    CASE WHEN r.verification = 'UNVERIFIED' THEN 0 ELSE 1 + r.n % 2 END AS attempts,
    r.created_at,
    convert_to(
        format(E'-- bulk submission %s\ntheorem target : %s := by\n%s\n',
               r.n,
               CASE WHEN r.task_mode = 'counterexample'
                    THEN format('¬ (fcTypeOfName%% "%s")', replace(t.reward_target_id, 'fc-target:', ''))
                    ELSE format('fcTypeOfName%% "%s"', replace(t.reward_target_id, 'fc-target:', ''))
               END,
               (ARRAY[E'  simp [*]', E'  omega', E'  norm_num', E'  decide',
                      E'  aesop', E'  exact?', E'  linarith', E'  nlinarith'])[1 + r.n % 8]),
        'UTF8') AS body
FROM rows AS r
JOIN targets AS t ON t.idx = r.n % (SELECT total FROM targets LIMIT 1);

INSERT INTO proofs (digest, content, byte_length, created_at)
SELECT pg_catalog.sha256(body), body, octet_length(body), created_at FROM seed_bulk;

INSERT INTO submissions (
    id, signer_coldkey, idempotency_key, request_digest, task_id, task_bundle_sha256,
    problem_id, task_mode, reward_target_id, proof_digest,
    payment_reference, payment_sender, payment_amount_rao, payment_block, signer_signature,
    verification_status, manual_review_status, reward_status, failure_reason,
    manual_review_required, review_policy_version,
    bounty_amount_rao, bounty_policy_version, bounty_inputs,
    verification_attempts, created_at, updated_at)
SELECT
    b.handle, b.payment_sender,
    ('f0000000-0000-4000-8001-' || right(replace(b.handle::text, '-', ''), 12))::uuid,
    pg_temp.d32('request:' || b.handle::text),
    b.task_id, pg_temp.bundle(b.task_id), b.problem_id, b.task_mode::task_mode,
    b.reward_target_id, pg_catalog.sha256(b.body),
    b.payment_reference, b.payment_sender, 500000000, b.payment_block,
    pg_temp.sig64('signer-signature:' || b.handle::text),
    b.verification::verification_state, 'UNREVIEWED', 'INELIGIBLE', b.failure_reason,
    true, 'review-v1',
    b.bounty_rao, 'dynamic-age-v1',
    jsonb_build_object('policy', 'dynamic-age-v1', 'reward_target_id', b.reward_target_id,
                       'quoted_rao', b.bounty_rao, 'bulk_fixture', true),
    b.attempts, b.created_at, b.created_at + interval '7 minutes'
FROM seed_bulk AS b;

-- One verdict run each, for the ones that got one.
INSERT INTO verification_runs (submission_id, task_bundle_sha256, proof_digest, verifier_version,
                               container_digest, sandbox_mode, accepted, reason_code, stage,
                               checks, report, report_digest, started_at, finished_at)
SELECT b.handle, pg_temp.bundle(b.task_id), pg_catalog.sha256(b.body),
       pg_temp.verifier_version(), pg_temp.container(), 'landrun',
       b.verification = 'VERIFIED',
       CASE WHEN b.verification = 'VERIFIED' THEN 'VERIFIED' ELSE b.failure_reason END,
       CASE WHEN b.verification = 'VERIFIED' THEN 'COMPLETED' ELSE 'COMPARATOR' END,
       c.checks,
       pg_temp.report(b.verification = 'VERIFIED',
                      CASE WHEN b.verification = 'VERIFIED' THEN 'VERIFIED' ELSE b.failure_reason END,
                      CASE WHEN b.verification = 'VERIFIED' THEN 'COMPLETED' ELSE 'COMPARATOR' END,
                      b.task_id, b.problem_id, b.task_mode, c.checks, 90000 + (b.bounty_rao % 120000)::int),
       pg_catalog.sha256(pg_temp.report(b.verification = 'VERIFIED',
                      CASE WHEN b.verification = 'VERIFIED' THEN 'VERIFIED' ELSE b.failure_reason END,
                      CASE WHEN b.verification = 'VERIFIED' THEN 'COMPLETED' ELSE 'COMPARATOR' END,
                      b.task_id, b.problem_id, b.task_mode, c.checks, 90000 + (b.bounty_rao % 120000)::int)),
       b.created_at + interval '2 minutes',
       b.created_at + interval '2 minutes' + interval '4 minutes'
FROM seed_bulk AS b
CROSS JOIN LATERAL (
    SELECT CASE WHEN b.verification = 'VERIFIED'
                THEN pg_temp.checks_pass()
                ELSE pg_temp.checks_fail(b.failed_check)
           END AS checks
) AS c
WHERE b.verification <> 'UNVERIFIED';

-- A three-event timeline each, so the bulk rows are not blank in the miner-facing view.
INSERT INTO submission_events (submission_id, kind, detail, context, actor, occurred_at)
SELECT b.handle, 'SUBMISSION_ACCEPTED', 'Finalized payment confirmed and submission recorded.',
       jsonb_build_object('payment_reference', b.payment_reference), 'system', b.created_at
FROM seed_bulk AS b
UNION ALL
SELECT b.handle, 'QUEUED_FOR_VERIFICATION', 'Waiting for a verification worker to claim the proof.',
       NULL, 'system', b.created_at + interval '2 seconds'
FROM seed_bulk AS b
UNION ALL
SELECT b.handle,
       CASE WHEN b.verification = 'VERIFIED' THEN 'VERIFICATION_PASSED' ELSE 'VERIFICATION_FAILED' END,
       CASE WHEN b.verification = 'VERIFIED'
            THEN 'The Lean kernel accepted the proof and every gate passed.'
            ELSE 'The proof was rejected.' END,
       jsonb_build_object('reason_code', COALESCE(b.failure_reason, 'VERIFIED')),
       'verification-worker-1@fra1', b.created_at + interval '6 minutes'
FROM seed_bulk AS b
WHERE b.verification <> 'UNVERIFIED';


-- =====================================================================================
-- 12. API rejection log
--
-- Requests that never became submissions. Everything here is CLAIMED, never verified — if
-- it had been verified there would be a submission instead. Digests are hex as sent or as
-- computed by us; rejected proof bytes are not kept, only their length.
--
-- source_ip is personal data, so the retention window on this table is not optional.
-- =====================================================================================

INSERT INTO api_rejection_log (occurred_at, reason_code, http_status, claimed_ss58, idempotency_key,
                               task_id, task_bundle_sha256, proof_digest, proof_byte_length,
                               request_digest, payment_reference, source_ip, user_agent, detail) VALUES
    (now() - interval '2 hours',  'PAYMENT_NOT_FINALIZED', 409, '5k4xuP9DQas3ZDan1JHpHcZT89wJY7BFRrQP8YCY25gt4JFn', '6f2b1c8e-4a55-4a1a-9d31-77a0c9b41e20', 'fc-e923379e-erdos982-erdos-982-1d7c4e9a02-formalized-v1', encode(pg_temp.bundle('fc-e923379e-erdos982-erdos-982-1d7c4e9a02-formalized-v1'), 'hex'), encode(pg_temp.d32('rejected-proof:1'), 'hex'),  812, encode(pg_temp.d32('rejected-request:1'), 'hex'), '5408334-1-0', '203.0.113.61',  'conjectures-cli/0.9.1',       jsonb_build_object('observed_state', 'in_block', 'required', 'finalized', 'block', 5408334, 'finalized_head', 5408330)),
    (now() - interval '5 hours',  'SIGNATURE_INVALID',     401, '5oizPxGjyqn9qR7uz5NYKar7caPiEfiHJ8uZCUMYMyyHEjDU', 'b9d4a071-1f6e-4c22-8d0f-1c5b23e9a874', 'fc-e923379e-erdos952-erdos-952-8b1029ce77-formalized-v1', encode(pg_temp.bundle('fc-e923379e-erdos952-erdos-952-8b1029ce77-formalized-v1'), 'hex'), encode(pg_temp.d32('rejected-proof:2'), 'hex'), 1043, encode(pg_temp.d32('rejected-request:2'), 'hex'), '5408210-0-1', '198.51.100.19', 'python-httpx/0.27.0',         jsonb_build_object('scheme', 'sr25519', 'signed_over', 'request_digest', 'hint', 'the signature verifies against a different digest')),
    (now() - interval '9 hours',  'DUPLICATE_PROOF',       409, '5NErXg3it6UXbKDNHUhvzbgoixU44tT1ApNHFoza8hKeUAPp', 'c1e07a6b-3d2f-4f8c-9e11-4a7d8c0b1f39', 'fc-e923379e-erdos11-erdos-11-7c0303029e-formalized-v1',   encode(pg_temp.bundle('fc-e923379e-erdos11-erdos-11-7c0303029e-formalized-v1'), 'hex'),   encode(pg_temp.d32('rejected-proof:3'), 'hex'),  356, encode(pg_temp.d32('rejected-request:3'), 'hex'), '5408140-2-2', '203.0.113.61',  'conjectures-cli/0.9.1',       jsonb_build_object('note', 'These exact proof bytes were already submitted', 'first_seen_days_ago', 61)),
    (now() - interval '1 day',    'IDEMPOTENCY_CONFLICT',  409, '5Pyp1nhoPAAEuxPPaqU8egQ9Lx96GwJpxDn2bJ7rHDEoKBwG', 'f0000000-0000-4000-8000-000000000003', 'fc-e923379e-erdos233-erdos-233-f97637ffd1-formalized-v1', encode(pg_temp.bundle('fc-e923379e-erdos233-erdos-233-f97637ffd1-formalized-v1'), 'hex'), encode(pg_temp.d32('rejected-proof:4'), 'hex'),  907, encode(pg_temp.d32('rejected-request:4'), 'hex'), NULL,         '198.51.100.7',  'conjectures-cli/0.9.1',       jsonb_build_object('note', 'Key reused with a different request digest', 'stored_digest_differs', true)),
    (now() - interval '2 days',   'INSUFFICIENT_CREDITS',  402, '5z9jFvWVD1EefYKRjte3Nx3rEE8Y3zV2JWgxfpzh85tXkcj5', '2a4c6e80-9b13-4d5f-a072-6e8f1b3d5a97', 'fc-e923379e-erdos932-erdos-932-4c8a0b71de-formalized-v1', encode(pg_temp.bundle('fc-e923379e-erdos932-erdos-932-4c8a0b71de-formalized-v1'), 'hex'), NULL, NULL, encode(pg_temp.d32('rejected-request:5'), 'hex'), NULL, '192.0.2.140', 'Mozilla/5.0 (X11; Linux x86_64) Firefox/141.0', jsonb_build_object('balance_rao', 0, 'credit_price_rao', 500000000, 'live_holds', 0)),
    (now() - interval '3 days',   'TASK_NOT_ALLOWLISTED',  422, '5j37iQuxdgxv6S1Uc6ZiTwhB2JYW19nUCC4MTPvf2KtgnsGD', 'de71f0a2-5c48-4b96-8e03-0d2a7f61c845', 'fc-deadbeef-nonexistent-task-0000000000-formalized-v1',  encode(pg_temp.d32('unknown-bundle'), 'hex'),                                                                                     encode(pg_temp.d32('rejected-proof:6'), 'hex'),  512, encode(pg_temp.d32('rejected-request:6'), 'hex'), NULL,         '203.0.113.7',   'curl/8.9.1',                  jsonb_build_object('note', 'task_id is not in the pinned task pool', 'pool_selection', 'audited-direct-propositions-v2')),
    (now() - interval '4 days',   'SUBMISSION_TOO_LARGE',  413, '5nsN54dmfNYLTBQfdCDKgKdbvkUgrJJ1nBeKPuuTVMtPYQKV', '8c05b3d9-7e21-4a68-b4f7-9a1c0e5d3b62', 'fc-e923379e-erdos889-erdos-889-3f0a51c60b-formalized-v1', encode(pg_temp.bundle('fc-e923379e-erdos889-erdos-889-3f0a51c60b-formalized-v1'), 'hex'), encode(pg_temp.d32('rejected-proof:7'), 'hex'), 4718592, encode(pg_temp.d32('rejected-request:7'), 'hex'), NULL,      '198.51.100.212','python-httpx/0.27.0',         jsonb_build_object('observed_bytes', 4718592, 'limit_bytes', 262144)),
    (now() - interval '6 days',   'PAYMENT_AMOUNT_MISMATCH', 402, '53ZZjEw6qRgqxctd65PWAjK7p8qvtkBZ7skZoni83izNGLbD', '4b8e2f10-6a37-4d59-9c82-3f0b7e1a6d54', 'fc-e923379e-erdos912-erdos-912-49dd0da12f-formalized-v1', encode(pg_temp.bundle('fc-e923379e-erdos912-erdos-912-49dd0da12f-formalized-v1'), 'hex'), encode(pg_temp.d32('rejected-proof:8'), 'hex'), 1290, encode(pg_temp.d32('rejected-request:8'), 'hex'), '5399881-3-1', '203.0.113.145', 'conjectures-cli/0.9.0',       jsonb_build_object('observed_amount_rao', 100000000, 'expected_amount_rao', 500000000)),
    (now() - interval '8 days',   'PAYMENT_REFERENCE_REUSED', 409, '5NErXg3it6UXbKDNHUhvzbgoixU44tT1ApNHFoza8hKeUAPp', '7e1a0c53-2b9d-4e17-a640-5c8f2d1b7a09', 'fc-e923379e-erdos1094-erdos-1094-e88b987211-formalized-v1', encode(pg_temp.bundle('fc-e923379e-erdos1094-erdos-1094-e88b987211-formalized-v1'), 'hex'), encode(pg_temp.d32('rejected-proof:9'), 'hex'), 640, encode(pg_temp.d32('rejected-request:9'), 'hex'), 'sub-5382410-3-1', '203.0.113.61', 'conjectures-cli/0.9.1',    jsonb_build_object('note', 'One transfer backs one submission', 'already_used_by_submission', '50000000-0000-4000-8000-00000000000b')),
    (now() - interval '11 days',  'COLDKEY_NOT_LINKED',    403, '5wb9riCkCFP8QeVauGpiEEy6RkhgQZLyZWXGU9aKmmGybb2J', 'a3f7c091-8d24-4b5e-9713-6e0a2c8f5d41', 'fc-e923379e-erdos859-erdos-859-e2f1f9e0a4-formalized-v1', encode(pg_temp.bundle('fc-e923379e-erdos859-erdos-859-e2f1f9e0a4-formalized-v1'), 'hex'), NULL, NULL, encode(pg_temp.d32('rejected-request:10'), 'hex'), NULL, '192.0.2.201', 'Mozilla/5.0 (Windows NT 10.0) Chrome/139.0', jsonb_build_object('note', 'The intent had no designated submission coldkey')),
    (now() - interval '14 days',  'RATE_LIMITED',          429, '5k4xuP9DQas3ZDan1JHpHcZT89wJY7BFRrQP8YCY25gt4JFn', '5d9b2e74-0c61-4a38-8f95-2b7d1e0a4c63', NULL, NULL, NULL, NULL, NULL, NULL, '203.0.113.61', 'conjectures-cli/0.9.1', jsonb_build_object('window_seconds', 60, 'limit', 10, 'observed', 43)),
    (now() - interval '17 days',  'BUNDLE_MALFORMED',      422, '515MLXeZEQgzTyxjrVJ4CG4MGVaktBUJWdZ22nhwGBg6k4ZU', '1f6d8b04-4e29-4c71-a5b3-8d0f2e7a1c96', 'fc-e923379e-erdos779-erdos-779-71ea8bbf9c-formalized-v1', encode(pg_temp.bundle('fc-e923379e-erdos779-erdos-779-71ea8bbf9c-formalized-v1'), 'hex'), NULL, 20480, encode(pg_temp.d32('rejected-request:12'), 'hex'), NULL, '198.51.100.88', 'curl/8.9.1', jsonb_build_object('note', 'Archive contains a path outside the bundle root', 'offending_entry', '../../etc/passwd')),
    (now() - interval '21 days',  'CSRF_INVALID',          403, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, '192.0.2.31',  'Mozilla/5.0 (Android 15; Mobile) Firefox/141.0', jsonb_build_object('note', 'Double-submit header did not match the session-bound CSRF digest')),
    (now() - interval '26 days',  'SESSION_EXPIRED',       401, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, '198.51.100.44', 'Mozilla/5.0 (X11; Linux x86_64) Chrome/136.0', jsonb_build_object('expired_days_ago', 9)),
    (now() - interval '34 days',  'TASK_COMMITMENT_MISMATCH', 422, '5uo9Jo18H1UCroFX4iUA4V88XgFdn2wejmQQMQLB58uJmeG8', '9a2c4f68-7b03-4e15-8d97-1c6a3e0b5f28', 'fc-e923379e-erdos242-erdos-242-030fce5e67-formalized-v1', encode(pg_temp.d32('stale-bundle'), 'hex'), encode(pg_temp.d32('rejected-proof:15'), 'hex'), 1502, encode(pg_temp.d32('rejected-request:15'), 'hex'), '5375210-1-3', '198.51.100.19', 'conjectures-cli/0.8.4', jsonb_build_object('note', 'The bundle digest is from a repin two commits ago', 'expected', encode(pg_temp.bundle('fc-e923379e-erdos242-erdos-242-030fce5e67-formalized-v1'), 'hex')));


-- =====================================================================================
-- 13. Sanity report
--
-- Not decoration: these are the invariants that are easy to break while editing the
-- fixtures above, and a seed that silently violates one produces confusing API behaviour
-- rather than an error. Every `problem` count below must be 0.
-- =====================================================================================

\echo ''
\echo '=== row counts ==='
SELECT 'accounts' AS table, count(*) FROM accounts
UNION ALL SELECT 'account_wallets',     count(*) FROM account_wallets
UNION ALL SELECT 'account_sessions',    count(*) FROM account_sessions
UNION ALL SELECT 'login_challenges',    count(*) FROM login_challenges
UNION ALL SELECT 'deposits',            count(*) FROM deposits
UNION ALL SELECT 'credit_ledger',       count(*) FROM credit_ledger
UNION ALL SELECT 'chain_transfers',     count(*) FROM chain_transfers
UNION ALL SELECT 'chain_watch_cursor',  count(*) FROM chain_watch_cursor
UNION ALL SELECT 'bounty_tasks',        count(*) FROM bounty_tasks
UNION ALL SELECT 'submission_intents',  count(*) FROM submission_intents
UNION ALL SELECT 'proofs',              count(*) FROM proofs
UNION ALL SELECT 'submissions',         count(*) FROM submissions
UNION ALL SELECT 'submission_events',   count(*) FROM submission_events
UNION ALL SELECT 'verification_runs',   count(*) FROM verification_runs
UNION ALL SELECT 'review_decisions',    count(*) FROM review_decisions
UNION ALL SELECT 'reward_events',       count(*) FROM reward_events
UNION ALL SELECT 'api_rejection_log',   count(*) FROM api_rejection_log
ORDER BY 1;

\echo ''
\echo '=== enum coverage: every state below should have at least one row ==='
SELECT 'deposits.status'         AS dimension, status::text AS value, count(*) FROM deposits GROUP BY 2
UNION ALL SELECT 'intents.status',            status::text, count(*) FROM submission_intents GROUP BY 2
UNION ALL SELECT 'transfers.status',          status::text, count(*) FROM chain_transfers GROUP BY 2
UNION ALL SELECT 'ledger.kind',               kind::text,   count(*) FROM credit_ledger GROUP BY 2
UNION ALL SELECT 'submissions.verification',  verification_status::text, count(*) FROM submissions GROUP BY 2
UNION ALL SELECT 'submissions.review',        manual_review_status::text, count(*) FROM submissions GROUP BY 2
UNION ALL SELECT 'submissions.reward',        reward_status::text, count(*) FROM submissions GROUP BY 2
UNION ALL SELECT 'reward_events.status',      status::text, count(*) FROM reward_events GROUP BY 2
UNION ALL SELECT 'review_decisions.kind',     kind::text,   count(*) FROM review_decisions GROUP BY 2
ORDER BY 1, 2;

\echo ''
\echo '=== public feeds ==='
SELECT 'certified' AS feed, count(*) FROM submissions
    WHERE reward_status = 'REWARDED' AND manual_review_status = 'APPROVED' AND verification_status = 'VERIFIED'
UNION ALL
SELECT 'in_review', count(*) FROM submissions
    WHERE verification_status = 'VERIFIED' AND manual_review_status = 'UNREVIEWED'
UNION ALL
SELECT 'accepted (proof published)', count(*) FROM submissions
    WHERE verification_status = 'VERIFIED' AND manual_review_status = 'APPROVED';

\echo ''
\echo '=== worker queues ==='
SELECT 'verification: claimable' AS queue, count(*) FROM submissions
    WHERE verification_status = 'UNVERIFIED' AND verification_lease_until IS NULL
UNION ALL SELECT 'verification: leased', count(*) FROM submissions
    WHERE verification_status = 'UNVERIFIED' AND verification_lease_until IS NOT NULL
UNION ALL SELECT 'review', count(*) FROM submissions
    WHERE verification_status = 'VERIFIED' AND manual_review_status = 'UNREVIEWED'
UNION ALL SELECT 'reward', count(*) FROM submissions WHERE reward_status = 'ELIGIBLE'
UNION ALL SELECT 'deposit reconciliation', count(*) FROM deposits
    WHERE status IN ('AWAITING_TRANSFER', 'SEEN_UNFINALIZED')
UNION ALL SELECT 'unattributed transfers', count(*) FROM chain_transfers WHERE status = 'UNATTRIBUTED'
UNION ALL SELECT 'payout: pending or submitted', count(*) FROM reward_events
    WHERE status IN ('PENDING', 'SUBMITTED');

\echo ''
\echo '=== credit balances (rao, and credits at 0.5 TAO each) ==='
SELECT a.id,
       COALESCE(a.email, a.display_name) AS account,
       COALESCE(sum(l.amount_rao), 0) AS balance_rao,
       COALESCE(sum(l.amount_rao), 0) / pg_temp.credit_price() AS credits_from_balance,
       COALESCE((SELECT sum(i.credits_held) FROM submission_intents AS i
                 WHERE i.account_id = a.id AND i.status IN ('OPEN', 'BUNDLE_ATTACHED')), 0) AS live_holds
FROM accounts AS a
LEFT JOIN credit_ledger AS l ON l.account_id = a.id
GROUP BY a.id, a.email, a.display_name
ORDER BY 3 DESC;

\echo ''
\echo '=== invariants: every count must be 0 ==='
-- A negative balance would mean the fixtures spent credits nobody bought.
SELECT 'accounts with a negative credit balance' AS problem, count(*) FROM (
    SELECT account_id FROM credit_ledger GROUP BY account_id HAVING sum(amount_rao) < 0
) AS q
UNION ALL
-- The invariant V003 weakened from "always has a payment" to: exactly one funding source.
SELECT 'submissions not funded exactly once', count(*) FROM submissions
    WHERE (payment_reference IS NOT NULL)::int + (credit_ledger_id IS NOT NULL)::int <> 1
UNION ALL
SELECT 'proofs whose digest does not match their bytes', count(*) FROM proofs
    WHERE digest <> pg_catalog.sha256(content)
UNION ALL
SELECT 'verification runs whose report digest does not match', count(*) FROM verification_runs
    WHERE report IS NOT NULL AND report_digest <> pg_catalog.sha256(report)
UNION ALL
-- One reward per reward target: the rule submissions_reward_target_reward_unique enforces.
SELECT 'reward targets claimed more than once', count(*) FROM (
    SELECT reward_target_id FROM submissions WHERE reward_status <> 'INELIGIBLE'
    GROUP BY reward_target_id HAVING count(*) > 1
) AS q
UNION ALL
SELECT 'submissions with a REWARDED status and no confirmed payout', count(*) FROM submissions AS s
    WHERE s.reward_status = 'REWARDED'
      AND NOT EXISTS (SELECT 1 FROM reward_events AS r
                      WHERE r.submission_id = s.id AND r.status = 'CONFIRMED')
UNION ALL
SELECT 'credited deposits with no ledger entry', count(*) FROM deposits
    WHERE status = 'CREDITED' AND credited_ledger_id IS NULL
UNION ALL
SELECT 'credit-funded submissions whose intent is not CONFIRMED', count(*) FROM submissions AS s
    JOIN submission_intents AS i ON i.id = s.intent_id
    WHERE s.credit_ledger_id IS NOT NULL AND i.status <> 'CONFIRMED'
UNION ALL
SELECT 'submissions with no timeline', count(*) FROM submissions AS s
    WHERE NOT EXISTS (SELECT 1 FROM submission_events AS e WHERE e.submission_id = s.id)
UNION ALL
SELECT 'verified or rejected submissions with no verification run', count(*) FROM submissions AS s
    WHERE s.verification_status <> 'UNVERIFIED'
      AND NOT EXISTS (SELECT 1 FROM verification_runs AS v WHERE v.submission_id = s.id)
ORDER BY 1;

COMMIT;

\echo ''
\echo 'Seed committed. Nothing here is real: every signature, digest, extrinsic reference'
\echo 'and SS58 address is fabricated. Do not point a live chain watcher at this data'
\echo 'without also matching chain_watch_cursor to your own .env.'
