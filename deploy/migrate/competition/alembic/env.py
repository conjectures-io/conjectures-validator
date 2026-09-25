"""Alembic environment for the competition database.

Two things here are load-bearing.

**The URL comes from `competition_database_url()`, never `DATABASE_URL`.** Flyway owns the
proofs database and Alembic owns this one; if this file honoured `DATABASE_URL` the way the
competition service used to, then any shell already configured to reach the proofs database
-- which is most of them -- would migrate the competition schema straight into it. The
resolver is shared with the API and the worker, so a migration and a running process can
never disagree about which database they mean.

**`target_metadata` names exactly one `Base`.** `conjectures_subnet.competition.models.Base`
and `conjectures_subnet.db.models.Base` are separate declarative bases that both define a
table called `submissions` -- different databases, different columns. Importing the wrong one,
or both, would make autogenerate propose dropping every table it could not see.

Unlike the competition repo this was extracted from, no `sys.path` manipulation is needed:
the models are an installed package here.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from conjectures_subnet.competition.models import Base
from conjectures_subnet.db.engine import competition_database_url

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=competition_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = competition_database_url()
    connectable = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
