# Migration runner for the COMPETITION database. Carries Alembic plus the SQLAlchemy
# models the revisions are generated from, and nothing else -- no API, no verifier, no
# Lean toolchain. BUILD FROM THE REPO ROOT (compose sets `context: .`), so both
# conjectures_subnet/ and the alembic tree are in scope.
#
# There is no equivalent image for the proofs database and there should not be: those
# migrations are plain SQL, so the stock flyway/flyway image plus a read-only bind mount
# is the whole runner. This one exists because these revisions import the models.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

WORKDIR /app

# Dependency layer, cached across source edits. Only what alembic/env.py imports:
# alembic, SQLAlchemy (the models) and the driver. Pinned to the same versions
# pyproject.toml resolves, so the image and a developer's venv migrate identically.
RUN pip install --no-cache-dir \
      "alembic==1.20.0" "SQLAlchemy==2.0.51" "psycopg[binary]==3.3.4"

# The models are the schema's source of truth. The whole package comes rather than just
# competition/ + db/engine.py: env.py resolves the URL through competition_database_url(),
# which lives in conjectures_subnet.db.engine, and importing that submodule executes
# conjectures_subnet/db/__init__.py, which pulls in the proofs models and data-access
# modules. Sharing the resolver is what stops a migration and a running worker disagreeing
# about which database they mean, and this is what sharing it costs.
COPY conjectures_subnet ./conjectures_subnet

# Alembic config and revisions are ops artifacts, not part of any package.
COPY deploy/migrate/competition/alembic.ini ./alembic.ini
COPY deploy/migrate/competition/alembic ./alembic

CMD ["alembic", "-c", "alembic.ini", "upgrade", "head"]
