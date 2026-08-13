#!/usr/bin/env python3
"""Prove that the ORM mirror still matches the migrations.

`deploy/migrate/sql/` is the source of truth and `conjectures_subnet/db/models.py` is a
hand-maintained mirror of it. No tool diffs plain SQL against ORM metadata, so the only honest
check is to build both and compare the catalogs.

    python3 scripts/check_schema_drift.py --dsn postgresql://user:pass@host:5432/postgres

Creates two scratch databases on the target server, applies the migrations to one and
`Base.metadata.create_all()` to the other, compares `pg_catalog`, prints a summary, and drops
both. Exits non-zero on any difference, so it is usable as a gate.

Compares columns (name, type, nullability, default), constraints (check, unique, primary,
foreign key, with their definitions), indexes (with their definitions), domains, enum labels,
triggers, and the bodies of trigger functions.

Every comparison covers each schema in `SCHEMAS`, and the schema name is part of the compared
tuple rather than only a filter. Filtering alone would let a table created in the wrong schema
on one side read as a *missing* table rather than a misplaced one, and the printed diff has to
be legible enough to tell those apart.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MIGRATIONS = ROOT / "deploy" / "migrate" / "sql"
MIGRATION_NAME = re.compile(r"^V(\d+)__[A-Za-z0-9_]+\.sql$")

# Every schema the migrations create. `autoreview` holds the advisory projection from V016; it is
# a separate namespace precisely so it can be dropped and rebuilt without reaching a core table,
# and it has to be compared here or the mirror in `db/autoreview_models.py` is unverified. Bound as
# a query parameter rather than interpolated: a schema name is data, not SQL.
SCHEMAS = ("public", "autoreview")

QUERIES: dict[str, str] = {
    "columns": """
        SELECT table_schema, table_name, column_name, data_type, udt_name, is_nullable,
               coalesce(column_default, ''), coalesce(character_maximum_length, -1)
        FROM information_schema.columns
        WHERE table_schema = ANY(:schemas) AND table_name <> 'flyway_schema_history'
        ORDER BY table_schema, table_name, column_name
    """,
    "constraints": """
        SELECT nsp.nspname, rel.relname, con.conname, con.contype,
               pg_get_constraintdef(con.oid)
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
        WHERE nsp.nspname = ANY(:schemas) AND rel.relname <> 'flyway_schema_history'
        ORDER BY nsp.nspname, rel.relname, con.conname, pg_get_constraintdef(con.oid)
    """,
    "indexes": """
        SELECT schemaname, tablename, indexname, indexdef
        FROM pg_indexes
        WHERE schemaname = ANY(:schemas) AND tablename <> 'flyway_schema_history'
        ORDER BY schemaname, tablename, indexname
    """,
    "domains": """
        SELECT n.nspname, t.typname, pg_catalog.format_type(t.typbasetype, t.typtypmod),
               pg_get_constraintdef(c.oid)
        FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        LEFT JOIN pg_constraint c ON c.contypid = t.oid
        WHERE n.nspname = ANY(:schemas) AND t.typtype = 'd'
        ORDER BY n.nspname, t.typname, pg_get_constraintdef(c.oid)
    """,
    "enums": """
        SELECT n.nspname, t.typname, e.enumlabel, e.enumsortorder
        FROM pg_type t
        JOIN pg_enum e ON e.enumtypid = t.oid
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE n.nspname = ANY(:schemas) AND t.typtype = 'e'
        ORDER BY n.nspname, t.typname, e.enumsortorder
    """,
    "triggers": """
        SELECT n.nspname, c.relname, t.tgname, pg_get_triggerdef(t.oid)
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = ANY(:schemas) AND NOT t.tgisinternal
        ORDER BY n.nspname, c.relname, t.tgname
    """,
    "functions": """
        SELECT n.nspname, p.proname, pg_get_functiondef(p.oid)
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = ANY(:schemas)
        ORDER BY n.nspname, p.proname
    """,
}


def migration_files() -> list[Path]:
    files = sorted(
        (path for path in MIGRATIONS.glob("*.sql") if MIGRATION_NAME.fullmatch(path.name)),
        key=lambda path: int(MIGRATION_NAME.fullmatch(path.name).group(1)),
    )
    if not files:
        raise SystemExit(f"no migrations found in {MIGRATIONS}")
    return files


def snapshot(engine) -> dict[str, list[tuple]]:
    from sqlalchemy import text

    result: dict[str, list[tuple]] = {}
    with engine.connect() as connection:
        for name, query in QUERIES.items():
            rows = connection.execute(text(query), {"schemas": list(SCHEMAS)}).all()
            result[name] = [tuple("" if v is None else str(v) for v in row) for row in rows]
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dsn",
        required=True,
        help="libpq/SQLAlchemy URL of a server the script may create databases on",
    )
    parser.add_argument("--prefix", default="drift_check", help="scratch database name prefix")
    parser.add_argument("--keep", action="store_true", help="do not drop the scratch databases")
    args = parser.parse_args(argv)

    from sqlalchemy import create_engine, text

    from conjectures_subnet.db.models import Base

    base = args.dsn.replace("postgresql+psycopg://", "postgresql://", 1)
    admin = create_engine(
        base.replace("postgresql://", "postgresql+psycopg://", 1),
        isolation_level="AUTOCOMMIT",
    )
    sql_db = f"{args.prefix}_sql"
    orm_db = f"{args.prefix}_orm"

    def database_url(name: str) -> str:
        head, _, tail = base.replace("postgresql://", "", 1).partition("/")
        return f"postgresql+psycopg://{head}/{name}"

    # Held outside the try so the finally can always dispose them. A failure while applying a
    # migration or running create_all otherwise leaves a live connection on a scratch database,
    # and the DROP in the finally then raises ObjectInUse — replacing the error that actually
    # matters with one about cleanup. This script exists to say what is wrong, so it must not lose
    # the answer on the way out.
    sql_engine = None
    orm_engine = None
    try:
        with admin.connect() as connection:
            for name in (sql_db, orm_db):
                connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
                connection.execute(text(f'CREATE DATABASE "{name}"'))

        sql_engine = create_engine(database_url(sql_db))
        with sql_engine.begin() as connection:
            for path in migration_files():
                connection.execute(text(path.read_text()))
        applied = [path.name for path in migration_files()]

        orm_engine = create_engine(database_url(orm_db))
        Base.metadata.create_all(orm_engine)

        from_sql = snapshot(sql_engine)
        from_orm = snapshot(orm_engine)
    finally:
        for engine in (sql_engine, orm_engine):
            if engine is not None:
                engine.dispose()
        if not args.keep:
            with admin.connect() as connection:
                for name in (sql_db, orm_db):
                    connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()

    print(f"migrations applied: {', '.join(applied)}")
    print(f"schemas compared:   {', '.join(SCHEMAS)}")
    drifted = False
    total = 0
    for name in QUERIES:
        expected, actual = from_sql[name], from_orm[name]
        total += len(expected)
        only_sql = [row for row in expected if row not in actual]
        only_orm = [row for row in actual if row not in expected]
        if not only_sql and not only_orm:
            print(f"  {name:12} {len(expected):4} objects agree")
            continue
        drifted = True
        print(f"  {name:12} DRIFT")
        for row in only_sql:
            print(f"      only in migrations: {row}")
        for row in only_orm:
            print(f"      only in the ORM   : {row}")

    if drifted:
        print(
            "\nthe ORM mirror does not match deploy/migrate/sql "
            "(conjectures_subnet/db/models.py, autoreview_models.py)"
        )
        return 1
    print(f"\nno drift: the mirror matches the migrations on all {total} objects")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
