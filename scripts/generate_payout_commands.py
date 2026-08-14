#!/usr/bin/env python3
"""Print copy-pasteable btcli commands for pending payout instructions.

The database is read-only here: this script does not sign, submit, or update a payout.  It reads
the amount-of-record and destination from ``reward_events``, then prints the same call once for
each multisig signer wallet.

With the repository's payout wallet defaults, the common case is just::

    python3 scripts/generate_payout_commands.py

Select one instruction when the queue contains more than one::

    python3 scripts/generate_payout_commands.py --event-id 17

By default the query runs through the deployed ``conjectures_db`` container, so the host needs no
Python packages. ``--dsn`` wins over ``DATABASE_URL`` and the PostgreSQL values in the
repository's ``.env``. Every output line is valid shell input; headings begin with ``#``, so the
complete output can be pasted into a terminal. The automatic ``payout_notifier`` service uses the
same renderer when PostgreSQL exposes a new pending payout.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from payout_notifier.discord import (
    DEFAULT_DISCORD_MENTIONS,
    DEFAULT_MULTISIG,
    DEFAULT_NETUID,
    DEFAULT_NETWORK,
    DEFAULT_ORIGIN_HOTKEY,
    DEFAULT_PROXY_FOR,
    DEFAULT_WALLETS,
    discord_notifications,
    render_command,
    render_payouts,
    send_discord_notifications,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_DB_CONTAINER = "conjectures_db"


def read_dotenv(path: Path = PROJECT_ROOT / ".env") -> dict[str, str]:
    """Read the simple KEY=value form used by this repository's environment file."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def resolve_dsn(explicit: str | None) -> str:
    """Resolve a SQLAlchemy PostgreSQL URL without changing the process environment."""
    if explicit:
        return explicit
    env = {**read_dotenv(), **os.environ}
    if env.get("DATABASE_URL", "").strip():
        return env["DATABASE_URL"].strip()
    user = env.get("POSTGRES_USER", "conjectures")
    password = env.get("POSTGRES_PASSWORD", "conjectures")
    host = env.get("POSTGRES_HOST", "127.0.0.1")
    port = env.get("POSTGRES_PORT", "5432")
    database = env.get("POSTGRES_DB", "conjectures")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{database}"


def configured_database_url() -> str | None:
    """An explicit URL from the process or .env, if one was configured."""
    env = {**read_dotenv(), **os.environ}
    value = env.get("DATABASE_URL", "").strip()
    return value or None


def _query(event_ids: Sequence[int]) -> str:
    where_event = ""
    if event_ids:
        unique_ids = dict.fromkeys(event_ids)
        where_event = " AND id IN (" + ",".join(str(value) for value in unique_ids) + ")"
    return (
        "BEGIN READ ONLY;\n"
        "SELECT id, submission_id::text, destination_coldkey, "
        "destination_hotkey, amount_rao\n"
        "FROM reward_events\n"
        "WHERE status = 'PENDING'\n"
        "  AND extrinsic_reference IS NULL"
        + where_event
        + "\nORDER BY id;\n"
        "ROLLBACK;\n"
    )


def _container_is_running(container: str) -> bool:
    docker = shutil.which("docker")
    if docker is None:
        return False
    result = subprocess.run(
        [docker, "inspect", "--format", "{{.State.Running}}", container],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def _run_psql(query: str, *, dsn: str, db_container: str | None) -> str:
    """Run psql either inside the database container or from the host."""
    common = [
        "-X",
        "--quiet",
        "--csv",
        "--tuples-only",
        "-v",
        "ON_ERROR_STOP=1",
    ]
    if db_container is not None and _container_is_running(db_container):
        docker = shutil.which("docker")
        assert docker is not None
        command = [
            docker,
            "exec",
            "-i",
            db_container,
            "sh",
            "-c",
            'exec psql "$@" -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
            "payout-psql",
            *common,
        ]
        source = f"database container {db_container!r}"
        environment = None
    else:
        psql = shutil.which("psql")
        if psql is None:
            container_help = (
                f" and container {db_container!r} is not running"
                if db_container is not None
                else ""
            )
            raise RuntimeError(f"psql is not installed{container_help}")
        # libpq accepts a connection URI in PGDATABASE. Keeping it out of argv avoids exposing a
        # password-bearing DATABASE_URL in the process list while psql is running.
        normalized_dsn = dsn.replace("postgresql+psycopg://", "postgresql://", 1)
        environment = {**os.environ, "PGDATABASE": normalized_dsn}
        command = [psql, *common]
        source = "PostgreSQL"

    result = subprocess.run(
        command,
        input=query,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or f"psql exited {result.returncode}"
        raise RuntimeError(f"{source} query failed: {detail}")
    return result.stdout


def pending_payouts(
    dsn: str,
    event_ids: Sequence[int],
    *,
    db_container: str | None = None,
) -> list[tuple[int, str, str, str, int]]:
    """Load unpaid payout facts, oldest first, without opening a write transaction."""
    output = _run_psql(_query(event_ids), dsn=dsn, db_container=db_container)
    parsed = list(csv.reader(io.StringIO(output)))
    if any(len(row) != 5 for row in parsed):
        raise RuntimeError("psql returned an unexpected payout row shape")
    return [(int(row[0]), row[1], row[2], row[3], int(row[4])) for row in parsed]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--dsn",
        help="database URL; default: DATABASE_URL or POSTGRES_* from .env/the environment",
    )
    parser.add_argument(
        "--db-container",
        default=DEFAULT_DB_CONTAINER,
        help=(
            "PostgreSQL container used when DATABASE_URL/--dsn is absent "
            f"(default: {DEFAULT_DB_CONTAINER})"
        ),
    )
    parser.add_argument(
        "--event-id",
        type=int,
        action="append",
        default=[],
        help="render this PENDING reward event only; repeat to select several",
    )
    parser.add_argument("--origin-hotkey", default=DEFAULT_ORIGIN_HOTKEY)
    parser.add_argument("--origin-netuid", type=int, default=DEFAULT_NETUID)
    parser.add_argument("--destination-netuid", type=int, default=DEFAULT_NETUID)
    parser.add_argument("--proxy-for", default=DEFAULT_PROXY_FOR)
    parser.add_argument("--multisig", default=DEFAULT_MULTISIG)
    parser.add_argument(
        "--wallet",
        action="append",
        help="multisig signer wallet; repeat for each signer (default: the two team wallets)",
    )
    parser.add_argument("--network", default=DEFAULT_NETWORK)
    args = parser.parse_args(argv)

    if args.event_id and any(event_id <= 0 for event_id in args.event_id):
        parser.error("--event-id must be positive")
    if args.origin_netuid < 0 or args.destination_netuid < 0:
        parser.error("netuids must be non-negative")

    try:
        configured_dsn = args.dsn or configured_database_url()
        rows = pending_payouts(
            resolve_dsn(configured_dsn),
            args.event_id,
            db_container=args.db_container if configured_dsn is None else None,
        )
    except Exception as exc:
        raise SystemExit(f"could not read pending payouts: {exc}") from exc

    if not rows:
        selected = (
            " for event id(s) " + ", ".join(str(value) for value in args.event_id)
            if args.event_id
            else ""
        )
        print(f"no PENDING payout instructions{selected}", file=sys.stderr)
        return 1 if args.event_id else 0

    if args.event_id:
        found = {row[0] for row in rows}
        missing = [
            event_id for event_id in dict.fromkeys(args.event_id) if event_id not in found
        ]
        if missing:
            print(
                "not PENDING with no extrinsic reference: reward event(s) "
                + ", ".join(str(event_id) for event_id in missing),
                file=sys.stderr,
            )
            return 1

    wallets = args.wallet or DEFAULT_WALLETS
    output = render_payouts(
        rows,
        wallets=wallets,
        origin_hotkey=args.origin_hotkey,
        origin_netuid=args.origin_netuid,
        destination_netuid=args.destination_netuid,
        proxy_for=args.proxy_for,
        multisig=args.multisig,
        network=args.network,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
