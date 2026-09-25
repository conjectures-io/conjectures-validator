"""The slice of the lz77 competition's schema this API reads and writes.

The schema is not ours. It lives, and migrates, in conjectures-optimisation-lz77
(`validator/db/models.py`, Alembic under `deploy/migrate/alembic/`), and that repository's
gate worker, chain watcher and weight setter write most of it. These are SQLAlchemy Core
tables naming only the columns this adapter touches -- not models, and never used to create
anything in production.

Two things keep this slice honest:

* `tests/test_competition_contract.py` reflects a database migrated by the competition's own
  Alembic head and checks every table and column here exists with a compatible type, and that
  every NOT NULL column without a default in a table this adapter inserts into is one it
  names. Run it with `FC_LZ77_SCHEMA_DSN` pointing at such a database.
* The API's test suite creates exactly these tables in its own throwaway database, so a column
  used by a query but missing here fails the suite rather than production.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    MetaData,
    PrimaryKeyConstraint,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()

registrations = Table(
    "registrations",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("uid", Integer, nullable=False),
    Column("ss58_hot", Text, nullable=False),
    Column("ss58_cold", Text, nullable=False),
    Column("block", BigInteger, nullable=False),
    Column("block_date", DateTime(timezone=True), nullable=False),
    UniqueConstraint("uid", "block"),
)

submissions = Table(
    "submissions",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    # NULL for the operator's baseline reference points, which are not competitors.
    Column("hotkey", Text, nullable=True),
    Column("baseline_key", Text, nullable=True),
    Column("digest", Text, nullable=False),
    Column("submitted_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("state", Text, nullable=False, server_default="queued"),
    Column("exit_code", Integer),
    Column("report", Text),
    Column("raw_bytes", BigInteger),
    Column("incumbent_bytes", BigInteger),
    Column("bytes", BigInteger),
    Column("parse_seconds", Float),
    Column("compression_seconds", Float),
    Column("time_ratio", Float),
    Column("verification_attempt", Text),
    Column("worker_id", Text),
    Column("claimed_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    UniqueConstraint("hotkey", "digest"),
)

entitlement_claims = Table(
    "entitlement_claims",
    metadata,
    Column(
        "registration_id",
        BigInteger,
        ForeignKey("registrations.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "submission_id",
        BigInteger,
        ForeignKey("submissions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    ),
)

# The uploaded files, so the gate host needs no filesystem shared with the API: the gate
# worker writes them into its own submission directory before it runs verify.py.
submission_files = Table(
    "submission_files",
    metadata,
    Column(
        "submission_id",
        BigInteger,
        ForeignKey("submissions.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("name", Text, nullable=False),
    Column("content", LargeBinary, nullable=False),
    PrimaryKeyConstraint("submission_id", "name"),
)

weight_sets = Table(
    "weight_sets",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("netuid", Integer, nullable=False),
    Column("block", BigInteger, nullable=False),
    Column("uids", JSONB, nullable=False),
    Column("weights", JSONB, nullable=False),
    Column("summary", Text),
    Column("accepted", Boolean, nullable=False),
    Column("dry_run", Boolean, nullable=False, server_default="false"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

score_snapshots = Table(
    "score_snapshots",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column(
        "weight_set_id",
        BigInteger,
        ForeignKey("weight_sets.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("hotkey", Text),
    Column("baseline_key", Text),
    Column("submission_id", BigInteger),
    Column("burn_reason", Text),
    Column("payable_weight", Float, nullable=False, server_default="0"),
    Column("time_s", Float),
    Column("ratio_pct", Float),
    Column("on_frontier", Boolean, nullable=False, server_default="false"),
    Column("pareto_weight", Float, nullable=False, server_default="0"),
    Column("improvement_weight", Float, nullable=False, server_default="0"),
    Column("combined_weight", Float, nullable=False, server_default="0"),
)

# Tables this adapter INSERTs into. The contract test requires every NOT NULL column without
# a default in these to be one named above, or an insert that works against this slice would
# fail against the real schema.
WRITTEN = (submissions, submission_files)

__all__ = [
    "WRITTEN",
    "entitlement_claims",
    "metadata",
    "registrations",
    "score_snapshots",
    "submission_files",
    "submissions",
    "weight_sets",
]
