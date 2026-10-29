# Migration runner for the COMPETITION database. Carries Alembic plus the SQLAlchemy
# models the revisions are generated from, and nothing else -- no API, no verifier, no
# Lean toolchain. BUILD FROM THE REPO ROOT (compose sets `context: .`), so both
# conjectures_subnet/ and the alembic tree are in scope.
#
# There is no equivalent image for the proofs database and there should not be: those
# migrations are plain SQL, so the stock flyway/flyway image plus a read-only bind mount
# is the whole runner. This one exists because these revisions import the models.
# The same digest as the other python-slim images, so Dependabot moves them together and the
# nightly image scan reads one base rather than whatever the tag pointed at on build day.
FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

WORKDIR /app

# The base is pinned by digest, so the distribution's security fixes published since that digest
# was cut reach the image only here. The nightly image scan reads the result.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Dependency layer, cached across source edits. Only what alembic/env.py imports:
# alembic, SQLAlchemy (the models) and the driver, at the versions the lock records -- the
# same ones a developer's venv resolves, so the two migrate identically. `--require-hashes`
# extends to Mako and MarkupSafe, which Alembic pulls in and nothing here names.
COPY requirements-service.hashes ./
RUN python -m pip install --no-cache-dir --upgrade pip==26.2.2 \
    && pip install --no-cache-dir --require-hashes --constraint requirements-service.hashes \
      alembic SQLAlchemy "psycopg[binary]" \
    && rm -f requirements-service.hashes \
    && rm -f /usr/local/lib/python3.12/ensurepip/_bundled/pip-*.whl

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
