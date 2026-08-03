"""Database access for the conjectures.io validator.

The schema itself is owned by the plain-SQL migrations in
``deploy/migrate/sql/``, applied by Flyway. This package is the runtime view of
that schema, not its source of truth:

* ``models`` — the tables, mirroring the migrations by hand;
* ``engine`` — URL resolution, sync and async engines, sessions, unit-of-work scopes;
* ``submissions`` — the submission seam: payment-gated intake, verdict recording,
  reward eligibility, and the API rejection log;
* ``verification`` — claiming work off the verification queue under a lease, because
  the verifier runs far longer than a transaction may stay open;
* ``digests`` — conversion between ``sha256:<hex>`` and the raw 32 bytes stored;
* ``errors`` — domain failures, free of any transport vocabulary.

Verify the mirror against the migrations with ``scripts/check_schema_drift.py``
rather than assuming they still agree.
"""

from __future__ import annotations

from conjectures_subnet.db import digests, engine, errors, models, submissions
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
    "async_session_factory",
    "async_session_scope",
    "create_async_db_engine",
    "create_db_engine",
    "database_url",
    "digests",
    "engine",
    "errors",
    "models",
    "session_factory",
    "session_scope",
    "submissions",
]
