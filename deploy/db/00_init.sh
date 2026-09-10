#!/usr/bin/env bash
# =============================================================================
# 00_init.sh
# Runs exactly once, on first cluster initialization, as the superuser. Use it
# only for things that must exist BEFORE migrations and that need superuser
# rights: extensions, roles, database-level GUCs.
#
# Application DDL does NOT belong here. Tables, domains, enums, indexes and the
# updated_at trigger live in deploy/migrate/sql/, so they are versioned and
# checksummed by Flyway. Anything created here is invisible to that history and
# will silently differ between a cluster built today and one rebuilt later.
#
# The official postgres entrypoint exports POSTGRES_USER / POSTGRES_DB and a
# working libpq environment, plus any vars from .env. We forward the ones we
# need to psql with -v so :'NAME' substitution works.
# =============================================================================
set -euo pipefail

: "${MONITOR_PASSWORD:=monitor}"   # fallback if not provided via .env
: "${REVIEWER_PASSWORD:=}"        # empty means create the role NOLOGIN

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" \
     -v db_name="$POSTGRES_DB" \
     -v monitor_password="$MONITOR_PASSWORD" \
     -v reviewer_password="$REVIEWER_PASSWORD" <<-'EOSQL'

    -- The schema needs no extension for UUIDs: gen_random_uuid() has been in
    -- core since PostgreSQL 13, so pgcrypto is deliberately not installed.

    -- Backing view for query-bottleneck analysis. The library itself is
    -- preloaded via shared_preload_libraries in postgresql.conf.
    CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

    -- Per-database defaults applied to every new connection to this database.
    -- Migrations override statement_timeout for themselves via FLYWAY_INIT_SQL,
    -- because a large CREATE INDEX legitimately runs longer than any query should.
    ALTER DATABASE :"db_name" SET timezone TO 'UTC';
    ALTER DATABASE :"db_name" SET statement_timeout TO '60s';
    ALTER DATABASE :"db_name" SET idle_in_transaction_session_timeout TO '60s';
    ALTER DATABASE :"db_name" SET lock_timeout TO '10s';

    -- Read-only monitoring role for dashboards and on-call: pg_monitor exposes
    -- pg_stat_statements and the pg_stat_* views with no access to table data.
    -- (psql does not substitute :'vars' inside DO $$..$$ blocks, so build the
    --  statement in plain SQL and run it with \gexec — idempotent.)
    SELECT format('CREATE ROLE monitor LOGIN PASSWORD %L', :'monitor_password')
    WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'monitor')
    \gexec

    GRANT pg_monitor TO monitor;
    GRANT CONNECT ON DATABASE :"db_name" TO monitor;
    GRANT USAGE ON SCHEMA public TO monitor;

    -- Intentionally NO blanket SELECT on application tables, unlike a typical
    -- monitoring setup. proofs.content is hostile miner-submitted source and
    -- submissions holds payment identities; SUBNET.md:113-116 keeps those inside
    -- the validator's security context. Grant specific views if a dashboard
    -- needs data, and do it in a versioned migration so the grant is reviewable.

    -- Review role. It exists chiefly so that a pg_dump taken from an
    -- environment that has it restores here without aborting: roles are
    -- cluster-level, so a database dump carries the GRANTs that name them but
    -- never the roles themselves, and pg_restore --single-transaction rolls the
    -- whole restore back on the first missing grantee.
    --
    -- Created NOLOGIN unless REVIEWER_PASSWORD is set, so a fresh cluster does
    -- not stand up a login role with a default password. Set REVIEWER_PASSWORD
    -- in .env only where something actually authenticates as this role.
    SELECT format(
               'CREATE ROLE conjectures_reviewer %s',
               CASE WHEN :'reviewer_password' = '' THEN 'NOLOGIN'
                    ELSE format('LOGIN PASSWORD %L', :'reviewer_password') END)
    WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'conjectures_reviewer')
    \gexec

    GRANT CONNECT ON DATABASE :"db_name" TO conjectures_reviewer;
    GRANT USAGE ON SCHEMA public TO conjectures_reviewer;

    -- As with monitor: no blanket SELECT. Whatever this role is meant to read
    -- should be granted explicitly in a migration.

EOSQL

echo "00_init.sh: extensions, database settings and the monitor and conjectures_reviewer roles configured."
