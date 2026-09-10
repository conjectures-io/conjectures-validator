"""Read-only Docker health probe: a live PID is not evidence of scanned blocks."""

from __future__ import annotations

import sys

import psycopg

from conjectures_subnet.db.engine import database_url
from conjectures_subnet.db.transfers import DEPOSIT_WATCHER

MAX_SCAN_AGE_SECONDS = 300


def check(dsn: str, *, max_age: int = MAX_SCAN_AGE_SECONDS) -> bool:
    """Use database time and a read-only transaction; missing cursors fail closed."""
    with psycopg.connect(
        dsn.replace("postgresql+psycopg://", "postgresql://", 1),
        connect_timeout=3,
        options="-c default_transaction_read_only=on -c statement_timeout=3000",
    ) as connection:
        row = connection.execute(
            "SELECT last_scanned_block, "
            "EXTRACT(EPOCH FROM (now() - last_scanned_at)) "
            "FROM chain_watch_cursor WHERE watcher = %s",
            (DEPOSIT_WATCHER,),
        ).fetchone()
    if row is None:
        print("deposit scan cursor is missing")
        return False
    block, age = row
    print(f"deposit scan block={block} age_seconds={float(age):.0f}")
    return 0 <= age <= max_age


def main() -> int:
    try:
        return 0 if check(database_url()) else 1
    except Exception as exc:
        # Connection errors can contain credentials. Keep Docker's health output safe.
        print(f"deposit scan health check failed: {type(exc).__name__}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
