"""Database access for the conjectures.io validator.

The schema itself is owned by the plain-SQL migrations in
``deploy/migrate/sql/``, applied by Flyway. This package is the runtime view of
that schema, not its source of truth:

* ``models`` — the tables, mirroring the migrations by hand;
* ``autoreview_models`` — the ``autoreview`` schema: advisory LLM pre-review results,
  written by ``conjectures-autoreview`` and read by the review console. Nothing in this
  package touches them at runtime; they are here so ``create_all`` and the drift check
  see one ``MetaData``;
* ``engine`` — URL resolution, sync and async engines, sessions, unit-of-work scopes;
* ``submissions`` — the submission seam: funded intake, verdict recording, reward
  eligibility, the API rejection log, and an account's own submissions;
* ``accounts`` — accounts, sessions, login challenges, and linked keys. Every secret
  is stored only as a digest and every challenge is claimed atomically;
* ``credits`` — the append-only credit ledger, deposits, and the balance arithmetic.
  Integer rao only, and holds are not ledger entries;
* ``intents`` — hold a credit, attach a bundle, then spend and submit in one
  transaction. Also the submission timeline;
* ``public`` — the read-only queries behind the unauthenticated endpoints, whose
  row types carry no miner-identifying column at all;
* ``verification`` — claiming work off the verification queue under a lease, because
  the verifier runs far longer than a transaction may stay open;
* ``transfers`` — every transfer observed at the treasury and where the chain watcher
  has read to. Records an arrival before deciding what it is worth, and credits only
  through ``credits``;
* ``digests`` — conversion between ``sha256:<hex>`` and the raw 32 bytes stored;
* ``errors`` — domain failures, free of any transport vocabulary.

Verify the mirror against the migrations with ``scripts/check_schema_drift.py``
rather than assuming they still agree.
"""

from __future__ import annotations

from conjectures_subnet.db import (
    accounts,
    autoreview_models,
    credits,
    digests,
    engine,
    errors,
    intents,
    models,
    public,
    submissions,
    transfers,
)
from conjectures_subnet.db.engine import (
    async_session_factory,
    async_session_scope,
    create_async_db_engine,
    create_db_engine,
    database_url,
    session_factory,
    session_scope,
)
from conjectures_subnet.db.errors import (
    DatabaseError,
    DuplicatePayment,
    DuplicateProof,
    IdempotencyConflict,
    RecordConflict,
    RecordNotFound,
)
from conjectures_subnet.db.models import Base

__all__ = [
    "Base",
    "DatabaseError",
    "DuplicatePayment",
    "DuplicateProof",
    "IdempotencyConflict",
    "RecordConflict",
    "RecordNotFound",
    "accounts",
    "async_session_factory",
    "async_session_scope",
    "autoreview_models",
    "create_async_db_engine",
    "create_db_engine",
    "credits",
    "database_url",
    "digests",
    "engine",
    "errors",
    "intents",
    "models",
    "public",
    "session_factory",
    "session_scope",
    "submissions",
    "transfers",
]
