"""The competition migrations and the competition models must describe one database.

This is the competition side's answer to `scripts/check_schema_drift.py`, and it is a
different check because the problem is different. The proofs schema is hand-written SQL with
a hand-maintained ORM mirror, so nothing but that script diffs the two sources. Here the
models ARE the source and Alembic generates from them -- so what drifts is a revision that
was edited by hand, or a model changed without a revision. Applying the revisions to an empty
database and asserting that autogenerate has nothing left to propose catches both.

Also asserted here: that this all happens in the competition database and nowhere near the
proofs one. That is the property the whole two-database split exists to provide, and it is
worth a test rather than a comment, because the failure mode -- competition tables appearing
in the proofs database -- is silent and expensive.
"""

from __future__ import annotations

import os
import uuid

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext

from conjectures_subnet.competition import models
from conjectures_subnet.competition.status import STATE_VALUES, SubmissionState
from conjectures_subnet.db.models import Base as ProofsBase
from conftest import COMPETITION_SKIP_REASON, competition_dsn

pytestmark = pytest.mark.skipif(competition_dsn() is None, reason=COMPETITION_SKIP_REASON)

ALEMBIC_INI = "deploy/migrate/competition/alembic.ini"


def _config(url: str) -> Config:
    config = Config(ALEMBIC_INI)
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture
def migrated():
    """A scratch database built by the revisions, not by `create_all`.

    It has to come from the revisions, or the test would be comparing the models against
    themselves and would pass no matter how far the deploy path had drifted.
    """
    dsn = competition_dsn()
    assert dsn is not None
    base, _ = dsn.rsplit("/", 1)
    name = f"conjectures_competition_scratch_{uuid.uuid4().hex[:8]}"
    admin = sa.create_engine(f"{base}/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    url = f"{base}/{name}"
    # env.py reads the URL from the environment, so set the one it reads -- and only that
    # one. Leaving DATABASE_URL alone is deliberate: if env.py ever honoured it again, the
    # tests below would still be pointing somewhere harmless and would not notice.
    previous = os.environ.get("COMPETITION_DATABASE_URL")
    os.environ["COMPETITION_DATABASE_URL"] = url
    try:
        command.upgrade(_config(url), "head")
        yield url
    finally:
        if previous is None:
            os.environ.pop("COMPETITION_DATABASE_URL", None)
        else:
            os.environ["COMPETITION_DATABASE_URL"] = previous
        with admin.connect() as conn:
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def test_the_migrations_build_exactly_what_the_models_describe(migrated):
    engine = sa.create_engine(migrated)
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(
                conn, opts={"compare_type": True, "compare_server_default": True}
            )
            diff = compare_metadata(context, models.Base.metadata)
    finally:
        engine.dispose()
    assert diff == [], f"models and migrations have drifted: {diff}"


def test_the_migrations_are_reversible(migrated):
    # A revision that cannot be undone is a revision nobody dares apply.
    command.downgrade(_config(migrated), "base")
    engine = sa.create_engine(migrated)
    try:
        remaining = sa.inspect(engine).get_table_names()
    finally:
        engine.dispose()
    assert set(remaining) <= {"alembic_version"}


def test_the_competition_base_is_not_the_proofs_base():
    """Two declarative bases, two catalogs -- and both define a table called `submissions`.

    Sharing one `MetaData` would merge them, and then `create_all` and every autogenerate run
    would see one schema made of two databases' tables. The name collision is harmless while
    they are separate and disastrous the moment they are not, which is what this asserts.
    """
    assert models.Base.metadata is not ProofsBase.metadata
    assert "submissions" in models.Base.metadata.tables
    assert "submissions" in ProofsBase.metadata.tables
    competition_only = set(models.Base.metadata.tables) - set(ProofsBase.metadata.tables)
    assert {"registrations", "entitlement_claims", "weight_sets"} <= competition_only


def test_the_state_check_matches_the_python_enum(migrated):
    # The CHECK constraint and the enum are two spellings of one list; if they drift, a state
    # the code can produce becomes a state the database refuses to store.
    assert STATE_VALUES == ("queued", "verifying", "accepted", "rejected", "error")
    engine = sa.create_engine(migrated)
    try:
        with engine.connect() as conn:
            rendered = conn.execute(
                sa.text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conname = 'ck_submissions_state'"
                )
            ).scalar_one()
    finally:
        engine.dispose()
    for state in SubmissionState:
        assert f"'{state.value}'" in rendered
