"""Entry point for the read-only registration watcher.

    python -m registration_watcher [--once] [--log-level INFO]

`--once` runs a single pass and exits: on an empty table that is the whole initial load,
which is also the quickest way to see a deployment read the chain and write the database.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from collections.abc import Sequence

from conjectures_subnet.axiom import configure_logging, get_axiom
from conjectures_subnet.competition import connect
from registration_watcher.settings import RegistrationWatcherSettings, SettingsError
from registration_watcher.source import BittensorRegistrationSource
from registration_watcher.watcher import SOURCE, RegistrationWatcher

logger = logging.getLogger("registration_watcher")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m registration_watcher")
    parser.add_argument("--once", action="store_true", help="run one pass and exit")
    parser.add_argument("--log-level", default="INFO")
    return parser


async def _run(args: argparse.Namespace) -> int:
    settings = RegistrationWatcherSettings.from_env()
    store = connect(settings.database_url)
    source = BittensorRegistrationSource(
        settings.network, archive_network=settings.archive_network
    )
    watcher = RegistrationWatcher(
        source=source,
        registrations=store.registrations,
        netuid=settings.netuid,
        poll_seconds=settings.poll_seconds,
        heartbeat=settings.heartbeat,
        watcher_id=settings.watcher_id,
    )
    try:
        # Before the chain: a database that is not there is a configuration problem, and
        # finding it after a minute of reading blocks would make it look like a chain one.
        if not await asyncio.to_thread(store.ping):
            raise SettingsError("the competition database is not reachable")
        logger.info(
            "registration watcher %s network=%s archive=%s netuid=%d poll=%.0fs",
            settings.watcher_id,
            settings.network,
            settings.archive_network,
            settings.netuid,
            settings.poll_seconds,
        )
        get_axiom().info(
            source=SOURCE,
            event_type="service_started",
            watcher_id=settings.watcher_id,
            network=settings.network,
            netuid=settings.netuid,
            mode="once" if args.once else "poll",
        )
        if args.once:
            written = await watcher.step()
            logger.info("one pass at block %s: %d row(s) recorded", watcher.last_block, written)
            return 0

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signal_name in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signal_name, stop.set)
        await watcher.run_forever(stop=stop)
        return 0
    finally:
        get_axiom().info(source=SOURCE, event_type="service_stopped", watcher_id=settings.watcher_id)
        await source.close()
        store.close()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    configure_logging(source=SOURCE, level=args.log_level)
    try:
        return asyncio.run(_run(args))
    except SettingsError as exc:
        logger.error("%s", exc)
        get_axiom().error(
            source=SOURCE,
            event_type="service_misconfigured",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return 2
    except KeyboardInterrupt:  # pragma: no cover
        return 0
    finally:
        get_axiom().close()


if __name__ == "__main__":
    sys.exit(main())
