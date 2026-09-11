"""Exercise V038 and V039 against existing rows, including the V035 failure it replaces."""
from pathlib import Path
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg import sql
from conftest import DATABASE_SKIP_REASON, postgres_dsn

pytestmark = pytest.mark.skipif(postgres_dsn() is None, reason=DATABASE_SKIP_REASON)
MIGRATIONS = Path(__file__).resolve().parents[1] / "deploy/migrate/sql"


def test_migration_preserves_history_and_closes_hotkey_writes():
    # All DDL and fixtures are rolled back, in a private schema on the test server.
    with psycopg.connect(postgres_dsn().replace("postgresql+psycopg://", "postgresql://", 1)) as conn:
        try:
            schema = "legacy_test_" + uuid.uuid4().hex
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            conn.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema)))
            conn.execute("CREATE TABLE submissions (id int PRIMARY KEY, hotkey text, hotkey_signature bytea, manual_review_status text)")
            conn.execute("INSERT INTO submissions VALUES (1, 'historical', %s, 'UNREVIEWED')", (b"signature",))
            conn.execute("ALTER TABLE submissions ADD CONSTRAINT submission_names_no_hotkey CHECK (hotkey IS NULL AND hotkey_signature IS NULL) NOT VALID")
            with pytest.raises(psycopg.errors.CheckViolation):
                with conn.transaction():
                    conn.execute("UPDATE submissions SET manual_review_status = 'APPROVED' WHERE id = 1")

            # Apply V038's submission intake DDL before the V039 update guard. The full
            # migration chain and ORM mirror are also checked by check_schema_drift.py.
            intake = (MIGRATIONS / "V038__hotkey_retirement_binds_inserts.sql").read_text()
            intake = intake[intake.index("CREATE FUNCTION submissions_reject_hotkey()"):]
            intake = intake[:intake.index("CREATE FUNCTION submission_intents_reject_hotkey()")]
            conn.execute(intake)
            conn.execute((MIGRATIONS / "V039__preserve_legacy_submission_updates.sql").read_text())
            for status in ("APPROVED", "REJECTED"):
                conn.execute("UPDATE submissions SET manual_review_status = %s WHERE id = 1", (status,))
                assert conn.execute("SELECT hotkey, hotkey_signature, manual_review_status FROM submissions WHERE id = 1").fetchone() == ("historical", b"signature", status)
            conn.execute("UPDATE submissions SET hotkey = hotkey, hotkey_signature = hotkey_signature WHERE id = 1")
            conn.execute("INSERT INTO submissions VALUES (2, NULL, NULL, 'UNREVIEWED')")
            conn.execute("UPDATE submissions SET manual_review_status = 'APPROVED' WHERE id = 2")

            for hotkey, signature in (("new", None), (None, b"new"), ("new", b"new")):
                with pytest.raises(psycopg.errors.CheckViolation) as error:
                    with conn.transaction():
                        conn.execute("INSERT INTO submissions VALUES (3, %s, %s, 'UNREVIEWED')", (hotkey, signature))
                assert error.value.diag.constraint_name == "submission_names_no_hotkey"

            for row_id, hotkey, signature in (
                (1, None, None), (1, "changed", b"signature"),
                (1, "historical", b"changed"), (1, None, b"signature"),
                (1, "historical", None), (2, "added", None), (2, None, b"added"),
            ):
                with pytest.raises(psycopg.errors.CheckViolation) as error:
                    with conn.transaction():
                        conn.execute("UPDATE submissions SET hotkey = %s, hotkey_signature = %s WHERE id = %s", (hotkey, signature, row_id))
                assert error.value.diag.constraint_name == "submission_legacy_hotkey_immutable"
        finally:
            conn.rollback()
