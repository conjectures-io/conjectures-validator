# Competitions

The API serves proof-gated competitions under `/v1/competitions/{slug}` beside the proofs
platform, on the same origin. Each competition:

- **owns its database**, including its schema and migrations, in its own repository. The API
  opens one engine per competition and never uses the proofs database for one;
- **owns its processes**: the gate worker that runs its verifier, the chain watcher that records
  registrations, and the weight setter. They run from the competition's repository and write its
  database;
- **plugs into the API through an adapter**: a class in
  [`submission_api/competitions/`](../submission_api/competitions/) that answers a fixed set of
  questions against that database.

The router, the response schemas, authentication, paging and the proofs database never change
when a competition is added, swapped or removed.

```
                     ┌──────────────────────── conjectures-validator API ───────────────────────┐
browser / miner ───► │ /v1/...               proofs routers ───► proofs engine ───► proofs DB    │
                     │ /v1/competitions/{slug} one router ─► registry ─► adapter ─► its engine ─┼─► competition DB
                     └──────────────────────────────────────────────────────────────────────────┘      ▲
                                                               competition repo: gate worker, chain ───┘
                                                               watcher, weight setter, migrations
```

## Configuration

```bash
COMPETITIONS=lz77
COMPETITION_LZ77_DATABASE_URL=postgresql+psycopg://api:…@conjectures_lz77_db:5432/conjectures
```

`COMPETITIONS` lists the served slugs, and each slug's URL is `COMPETITION_<SLUG>_DATABASE_URL`,
with the slug upper-cased and dashes turned into underscores. There is no fallback. Startup
refuses in any of these cases:

- a listed slug has no URL;
- a slug has no adapter;
- a URL is the proofs database's;
- two competitions share a URL.

The role in the URL needs `SELECT` on the tables the adapter maps, and `INSERT` on the tables it
queues into. The operator requeue additionally needs `UPDATE` on the submissions table.

A competition whose database is down answers `503 COMPETITION_UNAVAILABLE` on its own routes and
drops out of `GET /v1/competitions`. `/readyz` reports it under `competitions` but never fails
because of it. Every replica reads the same competition database, so failing readiness would
take the proofs platform out of rotation along with it.

## The surface

The same routes serve every competition:

| Method | Path | Auth |
|---|---|---|
| GET | `/v1/competitions` | none |
| GET | `/v1/competitions/{slug}` | none |
| GET | `/v1/competitions/{slug}/leaderboard` | none |
| GET | `/v1/competitions/{slug}/stats` | none |
| GET | `/v1/competitions/{slug}/scores` | none |
| GET | `/v1/competitions/{slug}/submissions` | none |
| GET | `/v1/competitions/{slug}/submissions/{id}` | none |
| GET | `/v1/competitions/{slug}/submissions/{id}/report` | none |
| GET | `/v1/competitions/{slug}/submissions/{id}/source` | none, accepted only |
| GET | `/v1/competitions/{slug}/competitors/{hotkey}` | none |
| POST | `/v1/competitions/{slug}/submissions` | hotkey signature (headers) |
| POST | `/v1/competitions/{slug}/submissions/session` | browser session |
| GET | `/v1/competitions/{slug}/me/submissions` | session or CLI bearer |
| GET | `/v1/competitions/{slug}/admin/queue` | ADMIN |
| GET | `/v1/competitions/{slug}/admin/submissions/{id}` | ADMIN |
| POST | `/v1/competitions/{slug}/admin/submissions/{id}/requeue` | ADMIN |

A client renders any competition from `GET /v1/competitions/{slug}`, which returns:

- the files a submission carries, each with a size cap;
- the published metrics, each with a label, a unit and which direction is better;
- which metric the board is ranked by;
- the competition's headline numbers.

Every submission and standing then carries its measurements as `metrics`, keyed by those metric
keys.

### Submitting

The signed submit is a multipart body with one part per declared file, plus three headers:

```
X-Conjectures-Hotkey:    <ss58>
X-Conjectures-Timestamp: <unix seconds>
X-Conjectures-Signature: <hex sr25519 signature over the message below>

conjectures-competition-submit-v1
competition: <slug>
digest: <adapter digest of the files; sha256 over them in declared order by default>
hotkey: <ss58>
timestamp: <unix seconds>
```

The scalars travel as headers because `CORS_REQUEST_HEADERS` allowlists no `X-Conjectures-*`
header, and that is what keeps this endpoint unreachable from a browser. The message names the
competition, so a signature cannot be replayed against another one.

The browser uses `/submissions/session` instead. That path needs no signature: the account's
proved submission coldkey must be the coldkey that registered the named hotkey, according to the
competition's registration records.

## Adding a competition

1. **Write the adapter.** Create `submission_api/competitions/<name>/` with a subclass of
   `CompetitionAdapter` (see [`base.py`](../submission_api/competitions/base.py)):
   - `info`: the slug, name, files, metrics and ranking metric;
   - the abstract methods: public reads, eligibility, `queue`;
   - optionally `files`, `scores`, `stuck`, `operator_view` and `requeue`. A capability left out
     answers `404 NOT_SUPPORTED`.

   Map only the columns you use, as SQLAlchemy Core tables in a `tables.py`. The schema stays
   the competition's.
2. **Register it.** Add one line to
   [`catalog.py`](../submission_api/competitions/catalog.py).
3. **Test it.** Build it on
   [`tests/test_api_competitions.py`](../tests/test_api_competitions.py), which runs the real
   lz77 adapter against a throwaway database holding its table slice, plus an in-memory `Toy`
   adapter. Add a contract check like
   [`tests/test_competition_contract.py`](../tests/test_competition_contract.py) against a
   database migrated by the competition's own migrations.
4. **Configure it.** Add the slug to `COMPETITIONS` and set its database URL.

To **remove** a competition, drop it from `COMPETITIONS`. To delete it for good, also remove its
catalog line and adapter package. To **swap** a competition's database, change its URL; nothing
else holds a reference to it.

## The lz77 adapter

[`lz77/`](../submission_api/competitions/lz77/) reads the schema owned by
conjectures-optimisation-lz77 (`validator/db/models.py`, Alembic under
`deploy/migrate/alembic/`).

The API writes only two things:

- a queued `submissions` row;
- its files in `submission_files`, which the competition's gate worker writes into its own
  submission directory before it runs `verify.py`. That is why the gate host needs no filesystem
  shared with the API.

The adapter restates the competition's entitlement rules:

- a registration row is required to submit;
- a hotkey may have no more queued than it has unspent registrations;
- the gate spends a registration only on acceptance.

Submits for one hotkey are serialised with a transaction-scoped advisory lock.

`tests/test_competition_contract.py` checks the adapter's table slice against a database migrated
to the competition's Alembic head. It needs `FC_LZ77_SCHEMA_DSN`, so CI skips it. Run it when
either side's schema changes:

```bash
# in conjectures-optimisation-lz77, against an empty database:
DATABASE_URL=postgresql+psycopg://…/lz77_contract just db-migrate
# here:
FC_LZ77_SCHEMA_DSN=postgresql+psycopg://…/lz77_contract pytest -q tests/test_competition_contract.py
```
