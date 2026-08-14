"""Entry point for the read-only payout-chain watcher."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from collections.abc import Sequence

from conjectures_subnet import transfers as chain
from conjectures_subnet.axiom import configure_logging, get_axiom
from conjectures_subnet.db.engine import async_session_factory, create_async_db_engine
from payout_watcher.settings import PayoutWatcherSettings, SettingsError
from payout_watcher.watcher import PayoutWatcher

logger = logging.getLogger("payout_watcher")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m payout_watcher")
    parser.add_argument("--once", action="store_true", help="run one scan pass and exit")
    parser.add_argument("--log-level", default="INFO")
    return parser


async def _run(args: argparse.Namespace) -> int:
    settings = PayoutWatcherSettings.from_env()
    source = chain.BittensorTransferSource(
        settings.network, archive_network=settings.archive_network
    )
    engine = create_async_db_engine(settings.database_url)
    watcher = PayoutWatcher(
        settings=settings,
        sessions=async_session_factory(engine),
        source=source,
    )
    try:
        cursor = await watcher.resolve_cursor()
        logger.info(
            "payout watcher %s network=%s origin=%s/%s netuid=%d cursor=%s",
            settings.watcher_id,
            settings.network,
            settings.origin_coldkey,
            settings.origin_hotkey,
            settings.netuid,
            "waiting-for-first-payout" if cursor is None else cursor.last_scanned_block,
        )
        get_axiom().info(
            source="payout-watcher",
            event_type="service_started",
            watcher_id=settings.watcher_id,
            network=settings.network,
            origin_coldkey=settings.origin_coldkey,
            origin_hotkey=settings.origin_hotkey,
            netuid=settings.netuid,
            last_scanned_block=None if cursor is None else cursor.last_scanned_block,
            mode="once" if args.once else "poll",
        )
        if args.once:
            await watcher.scan_once()
            return 0

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signal_name in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signal_name, stop.set)
        await watcher.run_forever(stop=stop)
        return 0
    finally:
        get_axiom().info(
            source="payout-watcher",
            event_type="service_stopped",
            watcher_id=settings.watcher_id,
        )
        await source.close()
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    configure_logging(source="payout-watcher", level=args.log_level)
    try:
        return asyncio.run(_run(args))
    except SettingsError as exc:
        logger.error("%s", exc)
        get_axiom().error(
            source="payout-watcher",
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
