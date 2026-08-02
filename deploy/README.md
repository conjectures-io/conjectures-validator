# Database deployment

```
deploy/
  db/
    00_init.sh          # superuser, runs once on an empty data dir: extension, GUCs, monitor role
    postgresql.conf     # tuned server config, mounted over the stock one
  migrate/
    sql/
      V001__initial_schema.sql
      R__service_grants.sql # least-privilege desired-state grants
```

## Running it

```bash
cp .env.example .env                              # then edit the passwords
docker compose -f docker-compose.db.yml up -d
```

Compose starts Postgres, waits for `pg_isready`, then runs the one-shot `migrate`
service, which applies every pending migration and exits. Running it again
applies nothing:

```
Successfully validated 1 migration
Schema "public" is up to date. No migration necessary.
```

Useful commands:

```bash
docker compose -f docker-compose.db.yml run --rm migrate info      # applied vs pending
docker compose -f docker-compose.db.yml run --rm migrate validate  # checksums only, no writes
docker compose -f docker-compose.db.yml logs migrate
```

`POSTGRES_PORT` in `.env` sets the host port. Only loopback is published — the
database is never on a routable interface, and other compose services reach it
as `db:5432` regardless.

On first initialization, `00_init.sh` creates four non-superuser login roles:
`conjectures_api`, `conjectures_verifier`, `conjectures_reviewer`, and
`conjectures_reward`. Their passwords are separate `.env` values and
`R__service_grants.sql` gives each only the tables and state-transition columns it needs. The
Flyway owner credential is for migrations only; do not use it as a runtime `DATABASE_URL`.

## Writing a migration

Add a file. That is the whole workflow.

```
deploy/migrate/sql/V002__transactional_outbox.sql
```

`V<number>__<description>.sql`, double underscore, numbers strictly increasing.
Flyway records each applied file in `flyway_schema_history` with a checksum, so:

- **Never edit an applied migration.** The next run fails with
  `Migration checksum mismatch` rather than letting two environments diverge
  silently. To change something already deployed, add another migration.
- Each file runs in one transaction. Postgres has transactional DDL, so a
  migration that fails halfway leaves the database exactly as it was.
- `CREATE INDEX CONCURRENTLY` cannot run inside a transaction. For those, add a
  sibling `V00N__name.sql.conf` containing `executeInTransaction=false`, and
  accept that a failure there needs manual cleanup.
- Repeatable migrations (`R__name.sql`) re-run whenever their checksum changes.
  That is the right home for grants and views — desired state rather than
  one-shot facts — not for anything that mutates data.

`00_init.sh` is not versioned and runs only on a fresh volume. Nothing that the
schema depends on may live there.

## Keeping the ORM in sync

`conjectures_subnet/db/models.py` mirrors the migrations for runtime queries and
typing. It is not the source of truth and nothing creates the production schema
from it. Because no tool diffs plain SQL against ORM metadata, the mirror has to
be updated by hand and checked by comparison.

`scripts/check_schema_drift.py` does that comparison: it builds one scratch
database from `deploy/migrate/sql` and another from `Base.metadata.create_all()`,
compares `pg_catalog` — columns, constraints, indexes, domains, enum labels,
triggers and trigger-function bodies — then drops both. It exits non-zero on any
difference, so it works as a gate.

```bash
python3 scripts/check_schema_drift.py \
  --dsn postgresql://conjectures:<password>@127.0.0.1:5432/postgres
```

Run it after editing either side; a mirror that has silently drifted is worse than no mirror,
because tests built from the metadata would pass against a schema production never has.
