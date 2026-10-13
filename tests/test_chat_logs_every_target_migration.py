"""Exercise V050: every target moves onto the chat-log policy, and nothing else about it moves."""
from datetime import UTC, datetime
from pathlib import Path
import uuid

import pytest

from conftest import DATABASE_SKIP_REASON, postgres_dsn

psycopg = pytest.importorskip("psycopg")
sql = psycopg.sql

pytestmark = pytest.mark.skipif(postgres_dsn() is None, reason=DATABASE_SKIP_REASON)
MIGRATIONS = Path(__file__).resolve().parents[1] / "deploy/migrate/sql"
OPENED = datetime(2026, 9, 1, tzinfo=UTC)


def test_every_target_requires_logs_and_keeps_its_bounty_terms():
    # All DDL and fixtures are rolled back, in a private schema on the test server.
    with psycopg.connect(postgres_dsn().replace("postgresql+psycopg://", "postgresql://", 1)) as conn:
        try:
            schema = "chat_logs_test_" + uuid.uuid4().hex
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            conn.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema)))
            conn.execute("CREATE TABLE bounty_tasks (reward_target_id text PRIMARY KEY, opened_at timestamptz NOT NULL, "
                         "start_share_numerator integer NOT NULL, start_share_denominator integer NOT NULL)")
            conn.execute("INSERT INTO bounty_tasks VALUES ('old-a', %s, 1, 20), ('old-b', %s, 1, 20)", (OPENED, OPENED))

            # V046's policy column and guard, as deployed: existing rows become legacy.
            v046 = (MIGRATIONS / "V046__chat_log_bounty_policy.sql").read_text()
            v046 = v046[v046.index("ALTER TABLE bounty_tasks ADD COLUMN pricing_policy"):]
            conn.execute(v046[:v046.index("-- Shared by instruction creation")])
            conn.execute("INSERT INTO bounty_tasks VALUES ('new-p34', %s, 1, 40)", (OPENED,))
            before = conn.execute("SELECT reward_target_id, opened_at, start_share_numerator, start_share_denominator "
                                  "FROM bounty_tasks ORDER BY 1").fetchall()

            conn.execute((MIGRATIONS / "V050__chat_logs_for_every_target.sql").read_text())

            assert conn.execute("SELECT DISTINCT pricing_policy FROM bounty_tasks").fetchall() == [
                ("linear-age-v3-chat-logs",)]
            assert conn.execute("SELECT reward_target_id, opened_at, start_share_numerator, start_share_denominator "
                                "FROM bounty_tasks ORDER BY 1").fetchall() == before
            # The guard is back on: no target leaves the policy, and no starting share moves.
            for change in ("pricing_policy = 'legacy'", "start_share_denominator = 40"):
                with pytest.raises(psycopg.errors.CheckViolation, match="keeps its original pricing policy"):
                    with conn.transaction():
                        conn.execute(f"UPDATE bounty_tasks SET {change} WHERE reward_target_id = 'old-a'")
        finally:
            conn.rollback()
