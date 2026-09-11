"""The health probe checks durable scan progress using a read-only transaction."""

import pytest

from deposit_watcher import healthcheck


@pytest.mark.parametrize("row,healthy", [(None, False), ((100, 301), False), ((100, 12), True)])
def test_health_depends_on_cursor_age(monkeypatch, capsys, row, healthy):
    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, query, params):
            assert "last_scanned_at" in query
            assert params == ("deposits",)
            return self

        def fetchone(self):
            return row

    def connect(dsn, **kwargs):
        assert "default_transaction_read_only=on" in kwargs["options"]
        assert kwargs["connect_timeout"] == 3
        return Connection()

    monkeypatch.setattr(healthcheck.psycopg, "connect", connect)
    assert healthcheck.check("postgresql://unused") is healthy
    assert capsys.readouterr().out


def test_database_failure_is_unhealthy_without_leaking_credentials(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise OSError("secret connection details")

    monkeypatch.setattr(healthcheck, "check", fail)
    assert healthcheck.main() == 1
    assert "secret" not in capsys.readouterr().out
