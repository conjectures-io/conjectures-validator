"""Count how many times the gate has claimed a submission.

A validator-side error requeues a submission uncharged -- the gate being broken is not the
miner's fault -- but with no count, a submission that breaks the gate every time cycles
forever and the queue behind it never moves. The service this came from avoided that by
stopping the whole worker on the first validator error, which trades one stuck submission
for a stopped queue and, once one worker serves several competitions, for a stopped
competition that had nothing to do with it.

Counting instead lets the worker give up on the individual row and keep going.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "submissions",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("submissions", "attempts")
