"""Which database the test suite may use, decided fail-closed.

Many database tests are destructive: they drop and recreate the whole schema, or create and drop
scratch databases. So the suite never discovers a server. It uses one only when both are given
explicitly for the run:

* the DSN, in `FC_POSTGRES_DSN` (proofs schema) or `FC_COMPETITION_POSTGRES_DSN` (competition);
* the identity of the fixture server it must be, in `FC_TEST_DATABASE_SYSTEM_IDENTIFIER`: the
  `system_identifier` from `pg_control_system()`, unique to one initialised cluster.

Before any test touches the server, a read-only transaction asks it for its identifier and the
DSN is refused unless they match. No DSN means the database tests skip. A DSN without the
identity, a mismatched identity, or a DSN naming a known shared or production endpoint
(TCP port 5440 or 5432) is an error that stops the run — it is never a silent fallback.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from functools import cache

IDENTITY_ENV = "FC_TEST_DATABASE_SYSTEM_IDENTIFIER"
# The shared pytest stack and the production database. Refused even with a matching identity.
REFUSED_TCP_PORTS = frozenset({5432, 5440})
IDENTITY_SQL = (
    "SELECT current_database(), (SELECT system_identifier::text FROM pg_control_system())"
)


class DatabaseGuardError(RuntimeError):
    """The configured database is not a verified private test fixture."""


def effective_endpoints(dsn: str, env: Mapping[str, str]) -> list[tuple[str, str, int]]:
    """Every endpoint libpq would try for `dsn` under `env`: (kind, host, port).

    Parsed by libpq itself (`conninfo_to_dict`), so URI query overrides (`?host=…&port=…`),
    `hostaddr`, and multi-host lists are seen exactly as a connection would see them, with the
    `PGHOST`/`PGHOSTADDR`/`PGPORT` fallbacks taken from `env`. A service indirection cannot be
    checked without reading a service file, so it is refused rather than followed.
    """
    from psycopg.conninfo import conninfo_to_dict

    try:
        params = conninfo_to_dict(dsn.replace("postgresql+psycopg://", "postgresql://", 1))
    except Exception as exc:  # noqa: BLE001 - an unparseable DSN is refused, not guessed at
        raise DatabaseGuardError(f"the DSN cannot be parsed: {exc}") from exc
    if params.get("service") or env.get("PGSERVICE") or env.get("PGSERVICEFILE"):
        raise DatabaseGuardError("a libpq service indirection cannot be verified before connecting")
    hosts = str(params.get("host", env.get("PGHOST", "")) or "")
    addresses = str(params.get("hostaddr", env.get("PGHOSTADDR", "")) or "")
    ports = str(params.get("port", env.get("PGPORT", "")) or "")
    host_list = hosts.split(",") if hosts else [""]
    address_list = addresses.split(",") if addresses else []
    count = max(len(host_list), len(address_list))
    port_list = ports.split(",") if ports else [""]
    if len(port_list) == 1:
        port_list = port_list * count
    if len(port_list) != count or (address_list and len(address_list) != count):
        raise DatabaseGuardError("the DSN's host, hostaddr and port lists do not line up")
    endpoints = []
    for index in range(count):
        host = host_list[index] if index < len(host_list) else ""
        address = address_list[index] if index < len(address_list) else ""
        try:
            port = int(port_list[index] or 5432)
        except ValueError as exc:
            raise DatabaseGuardError(f"the DSN names an invalid port: {port_list[index]!r}") from exc
        if address or (host and not host.startswith("/") and not host.startswith("@")):
            endpoints.append(("tcp", address or host, port))
        elif host:
            endpoints.append(("socket", host, port))
        else:
            endpoints.append(("default", "", port))
    return endpoints


def _endpoint_refusal(dsn: str, env: Mapping[str, str]) -> str | None:
    for kind, host, port in effective_endpoints(dsn, env):
        if kind == "default":
            return "the DSN does not name its server; libpq would fall back to a default socket"
        if kind == "tcp" and port in REFUSED_TCP_PORTS:
            return f"the DSN reaches TCP {host}:{port}, a shared or production endpoint"
    return None


def _probe(dsn: str) -> tuple[str, str]:
    import psycopg

    libpq = dsn.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(
        libpq, connect_timeout=5, options="-c default_transaction_read_only=on"
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(IDENTITY_SQL)
            database, identifier = cursor.fetchone()
    return str(database), str(identifier)


def verified_dsn(
    variable: str,
    *,
    environ: Mapping[str, str] | None = None,
    probe: Callable[[str], tuple[str, str]] = _probe,
) -> str | None:
    """The DSN in `variable` once its server is proven to be the declared fixture, else None."""
    env = os.environ if environ is None else environ
    dsn = env.get(variable, "").strip()
    if not dsn:
        return None
    expected = env.get(IDENTITY_ENV, "").strip()
    if not expected:
        raise DatabaseGuardError(
            f"{variable} is set but {IDENTITY_ENV} is not; database tests are destructive and "
            "run only against a fixture whose identity is declared for this run"
        )
    refusal = _endpoint_refusal(dsn, env)
    if refusal is not None:
        raise DatabaseGuardError(f"refusing {variable}: {refusal}")
    try:
        database, identifier = probe(dsn)
    except Exception as exc:  # noqa: BLE001 - any failure to prove identity refuses the DSN
        raise DatabaseGuardError(f"cannot verify the identity of {variable}: {exc}") from exc
    if identifier != expected:
        raise DatabaseGuardError(
            f"{variable} reaches server {identifier} ({database}), not the declared fixture {expected}"
        )
    return dsn


COMPETITION_ALEMBIC_INI = "deploy/migrate/competition/alembic.ini"


def worker_dsn(dsn: str, worker: str) -> str:
    """`dsn` with only the database name changed, to one private to the pytest-xdist `worker`.

    Every other parameter is kept as parsed, so a socket directory given as `?host=` still names
    the server that was verified; cutting the string at its last "/" would not.
    """
    from sqlalchemy.engine import make_url

    url = make_url(dsn)
    return url.set(database=f"{url.database}-{worker}").render_as_string(hide_password=False)


def isolate_worker(worker: str) -> None:
    """Give this pytest-xdist worker its own proofs and competition databases.

    The database tests drop and rebuild whole schemas, so workers sharing one database would
    destroy each other's state. Each worker instead gets `<database>-<worker>` on the same
    fixture cluster: created fresh here, the competition one migrated to head the way CI migrates
    the shared one, and the environment repointed at them before any test reads its DSN. Both
    the shared DSN and the worker's are proven by `verified_dsn` first, so this can only ever
    create databases on the declared fixture.
    """
    import psycopg
    from sqlalchemy.engine import make_url

    for variable in ("FC_POSTGRES_DSN", "FC_COMPETITION_POSTGRES_DSN"):
        shared = verified_dsn(variable)
        if shared is None:
            continue
        own = worker_dsn(shared, worker)
        name = make_url(own).database
        libpq = shared.replace("postgresql+psycopg://", "postgresql://", 1)
        with psycopg.connect(libpq, autocommit=True, connect_timeout=5) as connection:
            connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            connection.execute(f'CREATE DATABASE "{name}"')
        os.environ[variable] = own
        verified_dsn(variable)
        if variable == "FC_COMPETITION_POSTGRES_DSN":
            _migrate_competition(own)


def _migrate_competition(url: str) -> None:
    from alembic import command
    from alembic.config import Config

    config = Config(COMPETITION_ALEMBIC_INI)
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    # env.py reads the URL from the environment; restore it so no test inherits this one.
    previous = os.environ.get("COMPETITION_DATABASE_URL")
    os.environ["COMPETITION_DATABASE_URL"] = url
    try:
        command.upgrade(config, "head")
    finally:
        if previous is None:
            os.environ.pop("COMPETITION_DATABASE_URL", None)
        else:
            os.environ["COMPETITION_DATABASE_URL"] = previous


@cache
def postgres_dsn() -> str | None:
    """The proofs-schema test DSN, verified; None skips the database tests."""
    return verified_dsn("FC_POSTGRES_DSN")


@cache
def competition_dsn() -> str | None:
    """The competition-schema test DSN, verified; None skips those tests."""
    return verified_dsn("FC_COMPETITION_POSTGRES_DSN")


__all__ = [
    "IDENTITY_ENV",
    "DatabaseGuardError",
    "competition_dsn",
    "isolate_worker",
    "postgres_dsn",
    "verified_dsn",
]
