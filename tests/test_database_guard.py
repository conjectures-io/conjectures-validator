"""The test suite's database decision fails closed. No server is touched here."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from database_guard import IDENTITY_ENV, DatabaseGuardError, verified_dsn

DSN = "postgresql+psycopg://fixture@/fixture-db"
FIXTURE_ID = "7693752834671001646"
TESTS = Path(__file__).resolve().parent


def never_probe(dsn: str) -> tuple[str, str]:
    raise AssertionError(f"the guard must not connect to {dsn} here")


def test_without_a_dsn_database_tests_skip_and_nothing_is_contacted():
    assert verified_dsn("FC_POSTGRES_DSN", environ={}, probe=never_probe) is None
    assert verified_dsn("FC_POSTGRES_DSN", environ={IDENTITY_ENV: FIXTURE_ID}, probe=never_probe) is None


def test_a_dsn_without_a_declared_identity_stops_the_run_before_connecting():
    with pytest.raises(DatabaseGuardError, match=IDENTITY_ENV):
        verified_dsn("FC_POSTGRES_DSN", environ={"FC_POSTGRES_DSN": DSN}, probe=never_probe)


def test_a_server_with_another_identity_is_refused():
    with pytest.raises(DatabaseGuardError, match="not the declared fixture"):
        verified_dsn(
            "FC_POSTGRES_DSN",
            environ={"FC_POSTGRES_DSN": DSN, IDENTITY_ENV: FIXTURE_ID, "PGHOST": "/run/private-fixture"},
            probe=lambda dsn: ("fixture-db", "1111111111111111111"),
        )


def test_an_unreachable_or_unprovable_server_is_refused():
    def failing(dsn: str) -> tuple[str, str]:
        raise OSError("connection refused")

    with pytest.raises(DatabaseGuardError, match="cannot verify"):
        verified_dsn(
            "FC_POSTGRES_DSN",
            environ={"FC_POSTGRES_DSN": DSN, IDENTITY_ENV: FIXTURE_ID, "PGHOST": "/run/private-fixture"},
            probe=failing,
        )


SOCKET = {"PGHOST": "/run/private-fixture"}


@pytest.mark.parametrize(
    "dsn,extra",
    [
        ("postgresql+psycopg://conjectures-pytest:pw@127.0.0.1:5440/conjectures-pytest", {}),
        ("postgresql+psycopg://someone@localhost:5432/conjectures", {}),
        ("postgresql+psycopg://someone@db/conjectures", {}),  # default port 5432
        # libpq query overrides win over the URI authority.
        ("postgresql+psycopg://fixture@/fixture-db?host=127.0.0.1&port=5440", {}),
        ("postgresql+psycopg://fixture@%2Frun%2Fprivate-fixture/fixture-db?host=127.0.0.1&port=5440", {}),
        ("postgresql+psycopg://fixture@/fixture-db?hostaddr=127.0.0.1&port=5440", SOCKET),
        # One shared endpoint anywhere in a multi-host list is enough to refuse.
        ("postgresql+psycopg://fixture@%2Frun%2Fprivate-fixture,127.0.0.1:5440/fixture-db", {}),
        ("postgresql+psycopg://fixture@127.0.0.1:6000,127.0.0.1/fixture-db", {}),
        # Fallbacks come from the environment the run declares, not from the guard's own process.
        ("postgresql+psycopg://fixture@/fixture-db", {"PGHOST": "127.0.0.1", "PGPORT": "5440"}),
        ("postgresql+psycopg://fixture@/fixture-db", {"PGHOSTADDR": "10.0.0.5"}),
    ],
)
def test_shared_and_production_endpoints_are_refused_before_any_connection(dsn, extra):
    with pytest.raises(DatabaseGuardError, match="shared or production endpoint"):
        verified_dsn(
            "FC_POSTGRES_DSN",
            environ={"FC_POSTGRES_DSN": dsn, IDENTITY_ENV: FIXTURE_ID, **extra},
            probe=never_probe,
        )


@pytest.mark.parametrize(
    "dsn,extra,message",
    [
        ("postgresql+psycopg://fixture@/fixture-db", {}, "does not name its server"),
        ("postgresql+psycopg://fixture@/fixture-db?service=shared", SOCKET, "service"),
        ("postgresql+psycopg://fixture@/fixture-db", {**SOCKET, "PGSERVICE": "shared"}, "service"),
        ("postgresql+psycopg://fixture@/fixture-db?port=abc", SOCKET, "invalid port|cannot be parsed"),
    ],
)
def test_unverifiable_endpoints_are_refused_before_any_connection(dsn, extra, message):
    with pytest.raises(DatabaseGuardError, match=message):
        verified_dsn(
            "FC_POSTGRES_DSN",
            environ={"FC_POSTGRES_DSN": dsn, IDENTITY_ENV: FIXTURE_ID, **extra},
            probe=never_probe,
        )


def test_the_declared_fixture_is_accepted_over_its_socket():
    seen = []

    def probe(dsn: str) -> tuple[str, str]:
        seen.append(dsn)
        return "fixture-db", FIXTURE_ID

    assert (
        verified_dsn(
            "FC_POSTGRES_DSN",
            environ={"FC_POSTGRES_DSN": DSN, IDENTITY_ENV: FIXTURE_ID, **SOCKET},
            probe=probe,
        )
        == DSN
    )
    assert seen == [DSN]


def test_no_test_helper_discovers_or_defaults_to_a_shared_database():
    """The harness modules hold no default DSN and no reachability probe."""
    for name in ("conftest.py", "conftest_api.py", "database_guard.py"):
        tree = ast.parse((TESTS / name).read_text())
        defined = {
            node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.ClassDef))
        } | {
            target.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        assert not {"PYTEST_DSN", "PYTEST_COMPETITION_DSN", "_reachable"} & defined, name
        strings = [
            node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]
        assert not any("@127.0.0.1:5440" in value or "conjectures-pytest-pw" in value for value in strings), name


def test_no_test_module_falls_back_to_the_removed_shared_dsn():
    """A test cannot reintroduce the shared stack by importing a default the harness dropped.

    `test_api_public.py` once built a production-shaped app on `postgres_dsn() or PYTEST_DSN`,
    which reached the shared stack whenever the verified DSN was absent.
    """
    removed = {"PYTEST_DSN", "PYTEST_COMPETITION_DSN", "_reachable"}
    for path in sorted(TESTS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        assert not removed & imported, path.name
