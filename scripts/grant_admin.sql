-- Grant the first ADMIN role. Run by hand, by someone with database access.
--
--     docker compose -f docker-compose.db.yml exec -T db \
--         psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
--         -v account_email="'you@example.com'" -f - < scripts/grant_admin.sql
--
-- There is deliberately no endpoint for this. The API can grant ADMIN
-- (PUT /v1/admin/accounts/{id}/roles) but only to a caller who already holds it, so the first
-- one has to come from outside. An endpoint that could mint the first admin could mint the
-- second, and its own access control would then be some other secret needing its own rotation
-- story. Database access is a boundary that already exists and is already audited.
--
-- After this, use the API. It records who changed what in an Axiom `roles_changed` event;
-- `accounts.roles` is overwritten in place, so a change made here leaves no trace beyond the
-- row itself.
--
-- The account must already exist: sign in at the website first, by magic link or coldkey. This
-- script deliberately does not create one — an account with a role but no verified way to
-- reach it is not something anyone can sign in as.

\set ON_ERROR_STOP on

BEGIN;

-- Handed to the server rather than interpolated into the block below. psql substitutes `:name`
-- in the query buffer, but never inside a quoted literal or a dollar-quoted body -- so
-- `:account_email` between `$$` reaches the server verbatim and PL/pgSQL rejects it with
-- `syntax error at or near ":"`, taking the whole script with it under ON_ERROR_STOP. Local to
-- the transaction, so it is gone at COMMIT.
SELECT set_config('grant_admin.email', :account_email, true);

-- Refuse loudly rather than reporting "UPDATE 0". A typo'd address would otherwise look
-- exactly like success, and nobody re-reads the row to check.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM accounts
        WHERE lower(email) = lower(current_setting('grant_admin.email'))
    ) THEN
        RAISE EXCEPTION 'no account with email %; sign in at the website first',
            current_setting('grant_admin.email');
    END IF;
END
$$;

UPDATE accounts
SET roles = ARRAY(
        SELECT DISTINCT unnest(roles || ARRAY['ADMIN'])
        ORDER BY 1
    )
WHERE lower(email) = lower(:account_email);

-- Show the result, so the operator sees what they did rather than trusting it.
SELECT id, email, roles FROM accounts WHERE lower(email) = lower(:account_email);

COMMIT;

-- Revoking is the same shape, and is better done through the API so it is recorded:
--
--     UPDATE accounts
--     SET roles = array_remove(roles, 'ADMIN')
--     WHERE lower(email) = lower(:account_email);
--
-- Note that the API refuses to let an admin remove their own ADMIN role — with no other admin
-- it is unrecoverable without this file. That is the situation this script exists for.
