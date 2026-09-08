-- =====================================================================================
-- Grant test credits to a coldkey, and hand back a usable session, so the credit-funded
-- submission flow can be driven end to end without a chain transfer.
--
-- Re-runnable: run it again to top up. Each run appends one ADJUSTMENT to the ledger
-- (which is append-only, so a top-up is a new row, never an edit) and refreshes the
-- session's expiry.
--
-- ----------------------------------------------------------------------------------
-- EDIT THESE, THEN RUN
-- ----------------------------------------------------------------------------------
-- Only `coldkey` is mandatory. Everything else has a working default.
--
-- One key, not two. V035 retired the miner hotkey: the coldkey below is linked, designated
-- as the account's submission coldkey, and set as its payout destination.

\set coldkey '5C4hPGqPnDPP9jgWmBQfBAuxwiuFhWM6ttHwUvBAoMxLLJRD'
\set email   'dev@example.test'

-- How many credits to add on this run. One credit is one verification attempt.
\set credits 50

-- MUST equal the API's PAYMENT_AMOUNT_RAO. `POST /v1/submissions/intents` quotes the hold
-- at `settings.payment_amount_rao` (submission_api/routers/intents.py), and the balance is
-- divided by that same number to get a credit count — so a mismatch here silently buys
-- fewer credits than the ledger paid for. 500000000 rao = 0.5 TAO is the default.
\set credit_price_rao 500000000

-- The session secret. The database stores only its SHA-256, so this is the plaintext value
-- you send; nothing else can recover it. Replace it for anything reachable from outside
-- your machine.
--
-- No CSRF token: V021 stopped using `account_sessions.csrf_sha256` and V029 dropped it. A
-- cookie session now proves where a write was initiated from the browser's own `Origin` and
-- `Sec-Fetch-Site` headers instead.
\set session_token 'dev-session-6f2b1c8e4a554a1a9d3177a0c9b41e20'

-- Session lifetime, in days.
\set session_days 30

-- ----------------------------------------------------------------------------------
-- USAGE
--   docker exec -i conjectures_db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
--       < scripts/grant_test_credits.sql
--
-- WHAT IT DOES
--   1. Finds the account that owns `coldkey`. Failing that, the one with `email`. Failing
--      both, creates one.
--   2. Links `coldkey` to it, designates it as the submission coldkey, and sets it as the
--      payout destination if there is none.
--   3. Appends an ADJUSTMENT of `credits * credit_price_rao` to the credit ledger.
--   4. Upserts a session row whose digest matches `session_token`.
--   5. Prints the balance and a ready-to-paste curl invocation.
--
-- WHAT IT DOES NOT DO
--   * It does not verify a signature. `account_wallets.signature` is a 64-byte placeholder,
--     because writing the row directly is the point — it skips the challenge-and-sign round
--     trip the API would otherwise require. Nothing downstream re-checks it.
--   * It does not touch `deposits` or `chain_transfers`. There is no transfer behind this
--     credit, which is exactly why the entry is an ADJUSTMENT and not a DEPOSIT: DEPOSIT
--     requires a deposit_id, and inventing one would claim money arrived when it did not.
--   * It does not unpause submissions. If `SUBMISSIONS_PAUSED` is set, the intent endpoint
--     returns 503 regardless of your balance — check `GET /v1/system/status`.
--
-- LOCAL DEVELOPMENT ONLY. This mints spendable credit with no payment behind it.
-- =====================================================================================

\set ON_ERROR_STOP on

BEGIN;

-- psql does not interpolate `:variables` inside dollar-quoted strings, so the values are
-- handed to the PL/pgSQL block below through session settings instead.
SELECT set_config('seed.coldkey',          :'coldkey',                true),
       set_config('seed.email',            :'email',                  true),
       set_config('seed.credits',          (:credits)::text,          true),
       set_config('seed.price',            (:credit_price_rao)::text, true),
       set_config('seed.session_token',    :'session_token',          true),
       set_config('seed.session_days',     (:session_days)::text,     true)
\gset _discard_

DO $grant$
DECLARE
    v_coldkey       text    := current_setting('seed.coldkey');
    v_email         text    := nullif(current_setting('seed.email'), '');
    v_credits       bigint  := current_setting('seed.credits')::bigint;
    v_price         bigint  := current_setting('seed.price')::bigint;
    v_token         text    := current_setting('seed.session_token');
    v_days          int     := current_setting('seed.session_days')::int;

    v_account       uuid;
    v_amount        bigint;
    v_owner         uuid;
    v_created       boolean := false;
    -- 64 bytes, the length `wallet_signature_len` demands.
    v_signature     bytea;
BEGIN
    -- --- Validate the inputs before writing anything -------------------------------
    --
    -- The `ss58` domain would reject a malformed address anyway, but its error names the
    -- domain and not the variable you mistyped, which is a poor thing to debug.
    IF v_coldkey IS NULL OR v_coldkey !~ '^[1-9A-HJ-NP-Za-km-z]{48}$' THEN
        RAISE EXCEPTION 'coldkey % is not a 48-character SS58 address', coalesce(v_coldkey, '<null>');
    END IF;
    IF v_credits <= 0 THEN
        RAISE EXCEPTION 'credits must be positive, got %', v_credits;
    END IF;
    IF v_price <= 0 THEN
        RAISE EXCEPTION 'credit_price_rao must be positive, got %', v_price;
    END IF;
    v_amount := v_credits * v_price;
    v_signature := decode(md5(v_coldkey || ':0') || md5(v_coldkey || ':1')
                       || md5(v_coldkey || ':2') || md5(v_coldkey || ':3'), 'hex');

    -- --- Find the account, in the order that respects what already exists ----------
    --
    -- The coldkey first: it is globally unique and it is what the request will be made
    -- under, so if it is already linked, that account is the only correct answer. Crediting
    -- a different one would leave the balance somewhere the submission cannot spend it.
    SELECT account_id INTO v_account FROM account_wallets WHERE coldkey = v_coldkey;

    IF v_account IS NULL AND v_email IS NOT NULL THEN
        SELECT id INTO v_account FROM accounts WHERE lower(email) = lower(v_email);
    END IF;

    IF v_account IS NULL THEN
        INSERT INTO accounts (email, email_verified, display_name, roles, payout_coldkey)
        VALUES (v_email,
                v_email IS NOT NULL,
                'dev-' || substr(v_coldkey, 2, 6),
                ARRAY['MINER']::text[],
                -- One column since V035, and it needs no proof of control: it is only a
                -- destination. Rewards have nowhere to go without it.
                v_coldkey)
        RETURNING id INTO v_account;
        -- `submission_coldkey` is deliberately NOT set here. The wallet row does not exist
        -- yet, and `account_submission_coldkey_is_linked` is a composite foreign key
        -- against (account_id, coldkey) — so the designation has to wait until after the
        -- link below.
        v_created := true;
        RAISE NOTICE 'created account % (%)', v_account, coalesce(v_email, 'no email');
    ELSE
        RAISE NOTICE 'using existing account %', v_account;
    END IF;

    -- MINER is the role the submission endpoints assume. An account that lost it — or an
    -- ADMIN-only operator account reused for testing — would still authenticate and then
    -- fail further in, which reads as a credit problem and is not one.
    UPDATE accounts
    SET roles = array_append(roles, 'MINER')
    WHERE id = v_account AND NOT ('MINER' = ANY(roles));

    -- --- Link the coldkey ----------------------------------------------------------
    --
    -- `coldkey` is the primary key of account_wallets: one coldkey signs in to exactly one
    -- account. If it belongs to someone else that is a genuine conflict and silently
    -- ignoring it would leave you crediting an account you cannot sign in to.
    SELECT account_id INTO v_owner FROM account_wallets WHERE coldkey = v_coldkey;
    IF v_owner IS NULL THEN
        INSERT INTO account_wallets (account_id, coldkey, signature)
        VALUES (v_account, v_coldkey, v_signature);
        RAISE NOTICE 'linked coldkey %', v_coldkey;
    ELSIF v_owner <> v_account THEN
        RAISE EXCEPTION 'coldkey % is already the sign-in key for account %, not %',
            v_coldkey, v_owner, v_account;
    END IF;

    -- --- Designate it as the submission coldkey ------------------------------------
    --
    -- `open_intent` signs as `Account.submission_coldkey` and refuses with
    -- NO_SUBMISSION_COLDKEY when none is set, so an account credited but not designated
    -- would have a balance it cannot spend — which is exactly the failure this script
    -- exists to avoid. Ordered after the link above because the composite foreign key
    -- requires the wallet row to exist.
    UPDATE accounts
    SET submission_coldkey = v_coldkey
    WHERE id = v_account AND submission_coldkey IS DISTINCT FROM v_coldkey;

    -- Give the account somewhere to be paid, if it has nowhere yet. Not required to submit,
    -- but a submission that reaches ELIGIBLE with no destination cannot be paid out, and
    -- that failure surfaces a long way from here.
    UPDATE accounts
    SET payout_coldkey = v_coldkey
    WHERE id = v_account AND payout_coldkey IS NULL;

    -- --- The credit ----------------------------------------------------------------
    --
    -- ADJUSTMENT, not DEPOSIT or BONUS. DEPOSIT is constrained to name a `deposits` row
    -- (ledger_deposit_names_its_deposit) and there is no transfer behind this. ADJUSTMENT
    -- is the kind that means "an operator put this here", and the schema requires it to
    -- say why — which is the right shape for credit that was never bought.
    INSERT INTO credit_ledger (account_id, kind, amount_rao, credit_price_rao, reason, created_by)
    VALUES (v_account, 'ADJUSTMENT', v_amount, v_price,
            format('Local test credit: %s credits at %s rao, granted by scripts/grant_test_credits.sql',
                   v_credits, v_price),
            'operator:grant_test_credits.sql');

    RAISE NOTICE 'granted % credits (% rao)', v_credits, v_amount;

    -- --- The session ---------------------------------------------------------------
    --
    -- Only the digests are stored, so the plaintext tokens are printed below and exist
    -- nowhere else. `token_sha256` is UNIQUE, which is what makes a re-run extend the same
    -- session instead of accumulating one per run.
    INSERT INTO account_sessions (account_id, token_sha256,
                                  expires_at, user_agent, source_ip)
    VALUES (v_account,
            pg_catalog.sha256(convert_to(v_token, 'UTF8')),
            now() + (v_days || ' days')::interval,
            'grant_test_credits.sql',
            '127.0.0.1')
    ON CONFLICT (token_sha256) DO UPDATE
        SET account_id   = EXCLUDED.account_id,
            expires_at   = EXCLUDED.expires_at,
            last_seen_at = now(),
            -- A previously revoked session has to come back, or a second run of this
            -- script would hand back a credential that no longer authenticates.
            revoked_at   = NULL;

    RAISE NOTICE 'session ready for account %, valid % days', v_account, v_days;

    -- `false`, not `true`: a transaction-local setting is discarded at COMMIT, and every
    -- reporting query below runs after it. With `true` they all fail on an empty string
    -- cast to uuid, after the grant has already been committed.
    PERFORM set_config('seed.account_id', v_account::text, false);
END
$grant$;

COMMIT;


-- =====================================================================================
-- What you now have
-- =====================================================================================

\echo ''
\echo '=== account ==='
SELECT a.id,
       a.email,
       a.roles,
       a.payout_coldkey IS NOT NULL AS payout_set,
       a.submission_coldkey IS NOT NULL AS submission_key_set,
       (SELECT count(*) FROM account_wallets w WHERE w.account_id = a.id) AS wallets
FROM accounts AS a
WHERE a.id = current_setting('seed.account_id')::uuid;

\echo ''
\echo '=== balance (the numbers GET /v1/me/credits will report) ==='
-- Mirrors conjectures_subnet/db/credits.credit_balance: the ledger sum, minus live holds,
-- floored to whole credits and clamped at zero. Holds are excluded by their expiry rather
-- than by their status, so a lapsed intent stops counting the moment it lapses.
WITH ledger AS (
    SELECT coalesce(sum(amount_rao), 0) AS balance_rao
    FROM credit_ledger
    WHERE account_id = current_setting('seed.account_id')::uuid
),
holds AS (
    SELECT coalesce(sum(credits_held * credit_price_rao), 0) AS held_rao
    FROM submission_intents
    WHERE account_id = current_setting('seed.account_id')::uuid
      AND status IN ('OPEN', 'BUNDLE_ATTACHED')
      AND expires_at > now()
)
SELECT l.balance_rao,
       h.held_rao,
       greatest(l.balance_rao - h.held_rao, 0) / (:credit_price_rao)::bigint AS credits_available,
       greatest(l.balance_rao - h.held_rao, 0) % (:credit_price_rao)::bigint AS remainder_rao,
       (:credit_price_rao)::bigint AS credit_price_rao
FROM ledger AS l CROSS JOIN holds AS h;

\echo ''
\echo '=== ledger, newest first ==='
SELECT kind, amount_rao, created_by, left(coalesce(reason, ''), 60) AS reason, created_at
FROM credit_ledger
WHERE account_id = current_setting('seed.account_id')::uuid
ORDER BY id DESC
LIMIT 10;

\echo ''
\echo '=== how to use the session ==='
\echo 'Cookie: conjectures_session=<session_token>    (HttpOnly in the browser; just a header here)'
\echo ''
\echo 'No CSRF header: the session CSRF token was retired by V021 and dropped by V029. Writes'
\echo 'are guarded by the Origin and Sec-Fetch-Site checks in CsrfMiddleware, which fail open'
\echo 'when both headers are absent — so curl needs neither.'
\echo ''
\echo '  API=http://localhost:8000'
\echo '  TOKEN=<session_token>'
\echo ''
\echo '  # 1. confirm the credit landed'
\echo '  curl -s $API/v1/me/credits -b "conjectures_session=$TOKEN"'
\echo ''
\echo '  # 2. pick a task'
\echo '  curl -s "$API/v1/tasks?limit=1"'
\echo ''
\echo '  # 3. open an intent, holding one credit'
\echo '  curl -s -X POST $API/v1/submissions/intents \'
\echo '       -b "conjectures_session=$TOKEN" \'
\echo '       -H "Content-Type: application/json" \'
\echo '       -d "{\"task_id\":\"<task_id>\",\"task_bundle_sha256\":\"<bundle_digest>\"}"'
\echo '  #    No key in the body: the signer is the account'"'"'s designated submission coldkey,'
\echo '  #    which this script has already set.'
\echo ''
\echo '  # 4. upload the bundle; the response carries the request digest to sign'
\echo '  curl -s -X PUT $API/v1/submissions/intents/<intent_id>/bundle \'
\echo '       -b "conjectures_session=$TOKEN" \'
\echo '       -H "Content-Type: application/octet-stream" --data-binary @bundle.tar.gz'
\echo ''
\echo '  # 5. sign that digest with the submission coldkey, then debit the credit and submit'
\echo '  curl -s -X POST $API/v1/submissions/intents/<intent_id>/confirm \'
\echo '       -b "conjectures_session=$TOKEN" \'
\echo '       -H "Content-Type: application/json" \'
\echo '       -d "{\"signature\":\"0x<sr25519 over the request digest>\"}"'
\echo ''
\echo 'Step 5 is the one thing this script cannot shortcut: the confirm signature is checked'
\echo 'against the submission coldkey for real, so sign it with the key you named. POST'
\echo '/v1/submissions/preflight is free and spends no credit, so use it to shake out bundle'
\echo 'policy errors before step 3.'
\echo ''
\echo 'Local development only: this credit has no payment behind it.'
