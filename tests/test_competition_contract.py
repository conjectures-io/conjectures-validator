"""Each competition adapter's table slice, against the competition's real schema.

A competition's schema lives and migrates in the competition's own repository; an adapter here
maps only the columns it reads or writes. That slice can fall out of step silently -- a column
renamed there, or a new NOT NULL one an adapter's insert does not set -- and the API's own suite
cannot see it, because it builds its database from the slice. So this reflects a database
migrated by the competition's own Alembic head and checks the slice against it:

* every table and column the adapter names exists, with a compatible type and nullability;
* every NOT NULL column without a default, in a table the adapter inserts into, is one it names.

It needs such a database, so it is skipped unless `FC_MINIZ_SCHEMA_DSN` points at one. See
docs/COMPETITIONS.md for how to make one. Run it whenever either side's schema changes.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("sqlalchemy", reason="the contract check needs the db extra")
pytest.importorskip("psycopg", reason="the contract check needs the db extra")

from sqlalchemy import create_engine, inspect
from sqlalchemy.types import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Integer,
    LargeBinary,
    Text,
)

from submission_api.competitions.miniz_oxide import tables as miniz

DSN = os.environ.get("FC_MINIZ_SCHEMA_DSN", "").strip()
pytestmark = pytest.mark.skipif(
    not DSN, reason="set FC_MINIZ_SCHEMA_DSN to a database migrated by the miniz Alembic head"
)

# Which reflected Postgres type satisfies which declared one. Broad families, not exact names:
# the adapter needs "an integer that fits", not "INTEGER spelled this way".
FAMILIES = (
    (BigInteger, ("BIGINT",)),
    (Integer, ("INTEGER", "BIGINT", "SMALLINT")),
    (Float, ("DOUBLE PRECISION", "REAL", "FLOAT", "NUMERIC")),
    (Boolean, ("BOOLEAN",)),
    (DateTime, ("TIMESTAMP",)),
    (LargeBinary, ("BYTEA",)),
    (Text, ("TEXT", "VARCHAR")),
)


def _compatible(declared, reflected) -> bool:
    rendered = str(reflected.compile()).upper()
    for family, names in FAMILIES:
        if isinstance(declared, family):
            return rendered.startswith(names)
    # JSONB and anything else: the reflected type's name must match.
    return type(declared).__name__.upper() in rendered


@pytest.fixture(scope="module")
def schema():
    engine = create_engine(DSN.replace("+asyncpg", "+psycopg"))
    try:
        inspector = inspect(engine)
        yield {
            name: {column["name"]: column for column in inspector.get_columns(name)}
            for name in inspector.get_table_names()
        }
    finally:
        engine.dispose()


@pytest.mark.parametrize("table", miniz.metadata.sorted_tables, ids=lambda t: t.name)
def test_every_mapped_column_exists_with_a_compatible_type(schema, table):
    assert table.name in schema, f"{table.name} is not in the competition's schema"
    real = schema[table.name]
    for column in table.columns:
        assert column.name in real, f"{table.name}.{column.name} is not in the competition's schema"
        assert _compatible(column.type, real[column.name]["type"]), (
            f"{table.name}.{column.name}: mapped as {column.type}, "
            f"the schema has {real[column.name]['type']}"
        )
        if not column.nullable:
            assert not real[column.name]["nullable"], (
                f"{table.name}.{column.name} is NOT NULL here but nullable there, so the "
                "adapter would misread a NULL"
            )


@pytest.mark.parametrize("table", miniz.WRITTEN, ids=lambda t: t.name)
def test_an_insert_sets_every_column_the_schema_requires(schema, table):
    required = {
        name
        for name, column in schema[table.name].items()
        if not column["nullable"] and column["default"] is None and not column.get("autoincrement")
    }
    missing = required - {column.name for column in table.columns}
    assert not missing, f"inserts into {table.name} would leave {sorted(missing)} unset"
