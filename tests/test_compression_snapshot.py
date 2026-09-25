"""Which weight_sets row the competition reads treat as the current scoring pass.

A skipped epoch is written with `api_snapshot` as JSON `null` rather than SQL NULL, and a
query that only asks `IS NOT NULL` picks it: every competition read then failed with
`'NoneType' object has no attribute 'get'`. Found on production, where the first passes
were all skips. Needs a real Postgres, because the distinction is Postgres's own.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from conftest import postgres_dsn
from submission_api import compression_store as store

pytestmark = pytest.mark.skipif(postgres_dsn() is None, reason="no database")

TABLES = """
CREATE TABLE weight_sets (
    id integer PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(),
    dry_run boolean NOT NULL, accepted boolean NOT NULL, api_snapshot jsonb);
CREATE TABLE score_snapshots (
    weight_set_id integer, submission_id integer, aggregation_id integer,
    admission_check_id integer, hotkey text, baseline_key text, on_frontier boolean,
    pareto_weight float, improvement_weight float, combined_weight float,
    payable_weight float, burn_reason text);
CREATE TABLE submissions (
    id integer, state text, aggregation_id integer, admission_check_id integer,
    source_sha256 text, verifier_fingerprint text, baseline_active boolean, hotkey text);
"""

PUBLISHED = '{"policy": {"version": "v", "competition_share": 0.2}, "items": []}'


def _run(rows: list[tuple[int, str | None]], sid: int | None):
    """store.snapshot over weight_sets holding `rows` (id, api_snapshot SQL literal)."""
    dsn = postgres_dsn()
    assert dsn is not None
    schema = f"snap_{uuid.uuid4().hex[:10]}"

    async def scenario():
        engine = create_async_engine(dsn, connect_args={"options": f"-csearch_path={schema}"})
        try:
            async with engine.begin() as conn:
                await conn.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
                for statement in filter(str.strip, TABLES.split(";")):
                    await conn.execute(sa.text(statement))
                for row_id, literal in rows:
                    value = "NULL" if literal is None else f"'{literal}'::jsonb"
                    await conn.execute(
                        sa.text(
                            "INSERT INTO weight_sets (id, dry_run, accepted, api_snapshot) "
                            f"VALUES ({row_id}, true, false, {value})"
                        )
                    )
            async with AsyncSession(engine) as session:
                return await store.snapshot(session, sid)
        finally:
            async with engine.begin() as conn:
                await conn.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await engine.dispose()

    return asyncio.run(scenario())


def test_a_later_skipped_pass_does_not_hide_the_last_published_one():
    snap = _run([(1, PUBLISHED), (2, "null"), (3, None)], None)
    assert snap is not None and snap["id"] == 1
    assert snap["api_snapshot"]["policy"]["version"] == "v"


def test_only_skipped_passes_means_no_snapshot_rather_than_a_crash():
    assert _run([(1, "null"), (2, None)], None) is None


def test_a_skipped_pass_cannot_be_selected_by_id():
    assert _run([(1, PUBLISHED), (2, "null")], 2) is None
    assert _run([(1, PUBLISHED), (2, "null")], 1)["id"] == 1
