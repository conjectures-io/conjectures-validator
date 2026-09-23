"""Carry the submitted files in the row, and name the account behind a web submission.

Two changes that the move onto the platform forces.

`parse_source` / `proof_source`: the competition service used to write the two uploaded
files next to the database and let the gate worker read them back off the same disk. That
worked while both were `python -m ...` on one box. On the platform the API runs in a
container and the gate runs on bare metal -- it needs bubblewrap, Lean and cargo on the
host and cannot be containerized -- so there is no shared filesystem left to rely on, and
a writable mount from the API host to the gate host would be a channel pointing the wrong
way. The files travel in the row instead. Each is capped at 512 KiB by the API, and the
pair is what `digest` is the sha256 of, so a verdict stays recomputable from one row.

`account_id`: a session submission from the website is made by a signed-in account, and
accounts live in the proofs database. PostgreSQL has no cross-database foreign key, so
this is a bare UUID the API is responsible for. That is the price of the two-database
split and it is paid deliberately -- the alternative was one database with two migration
tools in it.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "submissions",
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("submissions", sa.Column("parse_source", sa.LargeBinary(), nullable=True))
    op.add_column("submissions", sa.Column("proof_source", sa.LargeBinary(), nullable=True))

    # Both files or neither. Existing rows have neither, so this holds on arrival.
    op.create_check_constraint(
        "ck_submissions_sources_paired",
        "submissions",
        "(parse_source IS NULL) = (proof_source IS NULL)",
    )

    # Partial, because most rows have no account and this index only serves the
    # "my submissions" query. A full index here would be mostly NULLs.
    op.create_index(
        "ix_submissions_account_id",
        "submissions",
        ["account_id"],
        postgresql_where=sa.text("account_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_submissions_account_id", table_name="submissions")
    op.drop_constraint("ck_submissions_sources_paired", "submissions", type_="check")
    op.drop_column("submissions", "proof_source")
    op.drop_column("submissions", "parse_source")
    op.drop_column("submissions", "account_id")
