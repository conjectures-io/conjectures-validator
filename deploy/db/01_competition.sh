#!/usr/bin/env bash
# =============================================================================
# 01_competition.sh
# Creates the competition database. Runs as the superuser, either from the
# postgres entrypoint on first cluster initialization (mounted into
# /docker-entrypoint-initdb.d/, which is why it is numbered after 00_init.sh —
# it grants to the monitor role that script creates) or by hand through
# `just db-create-competition` on a cluster that predates it.
#
# It is one file rather than a block in 00_init.sh precisely because of that
# second caller: the entrypoint runs only on an empty data directory, so an
# existing volume would otherwise never get this database, and nothing would
# say so — Alembic connects to a named database and reports it missing, it does
# not create one. Every statement here is idempotent, so both callers are safe
# to repeat.
#
# A second database in this same cluster, not a second cluster and not a second
# schema. Its schema is owned by Alembic while the main one is owned by Flyway,
# and two migration tools in one database each read the other's objects as
# drift; separate databases give each tool a history table it alone writes. The
# separation is also what lets the two key models coexist: V035 retired the
# miner hotkey in the main database and enforces it with triggers, while a
# competition entitlement is inherently per-hotkey, and a trigger does not reach
# across a database boundary.
#
# Application DDL does NOT belong here, for the same reason it does not belong
# in 00_init.sh. Tables, enums and indexes live in deploy/migrate/competition/
# so they are versioned by Alembic. What is here is only what needs superuser
# rights and cannot run inside a transaction: CREATE DATABASE itself, the
# database-level GUCs, and the extension.
# =============================================================================
set -euo pipefail

: "${COMPETITION_POSTGRES_DB:=conjectures_competition}"

# Connected to the main database, because you cannot create a database from a
# connection to the one being created.
psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" \
     -v competition_db="$COMPETITION_POSTGRES_DB" \
     -v owner="$POSTGRES_USER" <<-'EOSQL'

	-- CREATE DATABASE has no IF NOT EXISTS, and ON_ERROR_STOP=1 would abort on a
	-- re-run, so build the statement conditionally and \gexec it — the same
	-- idempotent shape 00_init.sh uses for its roles.
	SELECT format('CREATE DATABASE %I OWNER %I', :'competition_db', :'owner')
	WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'competition_db')
	\gexec

	-- The same per-connection defaults the main database gets, for the same
	-- reasons. Alembic overrides statement_timeout for itself, as Flyway does.
	ALTER DATABASE :"competition_db" SET timezone TO 'UTC';
	ALTER DATABASE :"competition_db" SET statement_timeout TO '60s';
	ALTER DATABASE :"competition_db" SET idle_in_transaction_session_timeout TO '60s';
	ALTER DATABASE :"competition_db" SET lock_timeout TO '10s';

	GRANT CONNECT ON DATABASE :"competition_db" TO monitor;

EOSQL

# An extension is installed into a database, not a cluster, so this half needs a
# connection to the database the block above just created.
psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$COMPETITION_POSTGRES_DB" <<-'EOSQL'

	CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

	GRANT USAGE ON SCHEMA public TO monitor;

	-- As in the main database, deliberately NO blanket SELECT on application
	-- tables: a competition submission is miner-authored source awaiting a
	-- verdict. Grant a specific view in a migration if a dashboard needs data.

EOSQL

echo "01_competition.sh: competition database '${COMPETITION_POSTGRES_DB}' is present."
