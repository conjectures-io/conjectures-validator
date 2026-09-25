"""The validator schema: submissions, registrations, entitlements, weights.

Six tables. The one that carries the competition's rule is `entitlement_claims`:
registration_id is its primary key and submission_id is unique, so one registration
can buy exactly one accepted submission and no amount of concurrency can make it buy two.

Revision ID: 0001
Revises:
Create Date: 2026-09-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rate_limit_windows",
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hits", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("subject", "window_start"),
    )
    op.create_index(
        "ix_rate_limit_windows_window_start", "rate_limit_windows", ["window_start"], unique=False
    )
    op.create_table(
        "registrations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("uid", sa.Integer(), nullable=False),
        sa.Column("ss58_hot", sa.Text(), nullable=False),
        sa.Column("ss58_cold", sa.Text(), nullable=False),
        sa.Column("block", sa.BigInteger(), nullable=False),
        sa.Column("block_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "inserted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uid", "block", name="uq_registrations_uid_block"),
    )
    op.create_index("ix_registrations_ss58_hot", "registrations", ["ss58_hot"], unique=False)
    op.create_index("ix_registrations_uid", "registrations", ["uid"], unique=False)
    op.create_table(
        "submissions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("hotkey", sa.Text(), nullable=False),
        sa.Column("digest", sa.Text(), nullable=False),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("state", sa.Text(), server_default="queued", nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("report", sa.Text(), nullable=True),
        sa.Column("raw_bytes", sa.BigInteger(), nullable=True),
        sa.Column("incumbent_bytes", sa.BigInteger(), nullable=True),
        sa.Column("bytes", sa.BigInteger(), nullable=True),
        sa.Column("incumbent_seconds", sa.Float(), nullable=True),
        sa.Column("parse_seconds", sa.Float(), nullable=True),
        sa.Column("time_ratio", sa.Float(), nullable=True),
        sa.Column("worker_id", sa.Text(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('queued', 'verifying', 'accepted', 'rejected', 'error')",
            name="ck_submissions_state",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("hotkey", "digest", name="uq_submissions_hotkey_digest"),
    )
    op.create_index("ix_submissions_hotkey", "submissions", ["hotkey"], unique=False)
    op.create_index(
        "ix_submissions_state_submitted_at",
        "submissions",
        ["state", "submitted_at", "id"],
        unique=False,
    )
    op.create_table(
        "weight_sets",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("netuid", sa.Integer(), nullable=False),
        sa.Column("block", sa.BigInteger(), nullable=False),
        sa.Column("uids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("weights", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("dry_run", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_weight_sets_created_at", "weight_sets", ["created_at"], unique=False)
    op.create_table(
        "entitlement_claims",
        sa.Column("registration_id", sa.BigInteger(), nullable=False),
        sa.Column("submission_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "claimed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["registration_id"], ["registrations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("registration_id"),
        sa.UniqueConstraint("submission_id"),
    )
    op.create_table(
        "score_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("weight_set_id", sa.BigInteger(), nullable=False),
        sa.Column("hotkey", sa.Text(), nullable=False),
        sa.Column("submission_id", sa.BigInteger(), nullable=True),
        sa.Column("time_s", sa.Float(), nullable=True),
        sa.Column("ratio_pct", sa.Float(), nullable=True),
        sa.Column("on_frontier", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("pareto_weight", sa.Float(), server_default="0", nullable=False),
        sa.Column("improvement_weight", sa.Float(), server_default="0", nullable=False),
        sa.Column("combined_weight", sa.Float(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["weight_set_id"], ["weight_sets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_score_snapshots_hotkey", "score_snapshots", ["hotkey"], unique=False)
    op.create_index(
        "ix_score_snapshots_weight_set_id", "score_snapshots", ["weight_set_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_score_snapshots_weight_set_id", table_name="score_snapshots")
    op.drop_index("ix_score_snapshots_hotkey", table_name="score_snapshots")
    op.drop_table("score_snapshots")
    op.drop_table("entitlement_claims")
    op.drop_index("ix_weight_sets_created_at", table_name="weight_sets")
    op.drop_table("weight_sets")
    op.drop_index("ix_submissions_state_submitted_at", table_name="submissions")
    op.drop_index("ix_submissions_hotkey", table_name="submissions")
    op.drop_table("submissions")
    op.drop_index("ix_registrations_uid", table_name="registrations")
    op.drop_index("ix_registrations_ss58_hot", table_name="registrations")
    op.drop_table("registrations")
    op.drop_index("ix_rate_limit_windows_window_start", table_name="rate_limit_windows")
    op.drop_table("rate_limit_windows")
