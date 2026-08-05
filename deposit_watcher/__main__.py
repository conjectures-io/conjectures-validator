"""Entry point: `python -m deposit_watcher`.

Everything that can be wrong with the deployment is established before the first block is read —
the settings, the subnet registration of the address being watched, and the cursor — so a
misconfigured watcher exits instead of quietly crediting nothing for a week.

    python -m deposit_watcher            # poll until stopped
    python -m deposit_watcher --once     # catch up to the finalized head and exit
    python -m deposit_watcher --dry-run  # resolve and print what it would watch, write nothing
"""

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
from deposit_watcher.settings import SettingsError, WatcherSettings
from deposit_watcher.watcher import DepositWatcher

logger = logging.getLogger("deposit_watcher")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m deposit_watcher")
    parser.add_argument(
        "--once",
        action="store_true",
        help="catch up to the finalized head and exit, instead of polling",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="with --once, stop after this many batches",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="check the configuration and report what would be watched, without scanning",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser


async def _run(args: argparse.Namespace) -> int:
    settings = WatcherSettings.from_env()
    source = chain.BittensorTransferSource(
        settings.network, archive_network=settings.archive_network
    )
    engine = create_async_db_engine(settings.database_url or None)
    watcher = DepositWatcher(
        settings=settings,
        sessions=async_session_factory(engine),
        source=source,
    )
    try:
        if settings.verify_registration:
            await watcher.verify_identity()
            logger.info(
                "confirmed %s is the hotkey at netuid %d uid %d",
                settings.recipient,
                settings.netuid,
                settings.uid,
            )
        else:
            # Loud, because it is the only check that the money being watched is ours.
            logger.warning(
                "DEPOSIT_WATCH_VERIFY_REGISTRATION is off: watching %s without confirming it "
                "is the hotkey at netuid %d uid %d",
                settings.recipient,
                settings.netuid,
                settings.uid,
            )
            get_axiom().warn(
                source="deposit-watcher",
                event_type="service_misconfigured",
                error="DEPOSIT_WATCH_VERIFY_REGISTRATION is off; the watched address is not "
                "confirmed to be this validator's own hotkey",
                recipient=settings.recipient,
                netuid=settings.netuid,
                uid=settings.uid,
            )

        cursor = await watcher.resolve_cursor()
        logger.info(
            "deposit watcher %s network=%s recipient=%s watch_from=%s start_block=%d "
            "last_scanned=%d price=%d rao/credit",
            settings.watcher_id,
            settings.network,
            settings.recipient,
            settings.watch_from.isoformat(),
            cursor.start_block,
            cursor.last_scanned_block,
            settings.credit_price_rao,
        )
        get_axiom().info(
            source="deposit-watcher",
            event_type="service_started",
            watcher_id=settings.watcher_id,
            network=settings.network,
            recipient=settings.recipient,
            netuid=settings.netuid,
            uid=settings.uid,
            watch_from=settings.watch_from.isoformat(),
            start_block=cursor.start_block,
            last_scanned_block=cursor.last_scanned_block,
            credit_price_rao=settings.credit_price_rao,
            batch_blocks=settings.batch_blocks,
            verify_registration=settings.verify_registration,
            mode="dry-run" if args.dry_run else ("once" if args.once else "poll"),
        )
        if args.dry_run:
            head = await source.finalized_head()
            logger.info(
                "dry run: finalized head is %d, so %d block(s) are unread. Writing nothing.",
                head,
                max(0, head - cursor.last_scanned_block),
            )
            return 0

        if args.once:
            passes = await watcher.catch_up(max_passes=args.limit)
            logger.info(
                "caught up in %d pass(es), %d transfer(s) credited",
                len(passes),
                sum(item.credited for item in passes),
            )
            return 0

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signal_name in (signal.SIGINT, signal.SIGTERM):
            # The cursor advances per block inside its own transaction, so stopping mid-batch
            # loses nothing: the next start re-reads from the last block it recorded.
            loop.add_signal_handler(signal_name, stop.set)
        await watcher.run_forever(stop=stop)
        return 0
    finally:
        get_axiom().info(
            source="deposit-watcher",
            event_type="service_stopped",
            watcher_id=settings.watcher_id,
        )
        # The source holds a websocket open across the whole run — see BittensorTransferSource on
        # why it is not opened per call — so it is closed here rather than left to collection.
        await source.close()
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    # In place of `logging.basicConfig`: same stderr output, plus the Axiom bridge. See
    # `conjectures_subnet.axiom.handler`.
    configure_logging(source="deposit-watcher", level=args.log_level)
    try:
        return asyncio.run(_run(args))
    except SettingsError as exc:
        # Configuration, not the chain: say so plainly and do not start. Exit 2 rather than 1, so
        # the compose service can leave it dead and visible instead of crash-looping the message
        # out of the scrollback — the same contract the verification worker has.
        logger.error("%s", exc)
        get_axiom().error(
            source="deposit-watcher",
            event_type="service_misconfigured",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return 2
    except KeyboardInterrupt:  # pragma: no cover - signal handlers cover the normal path
        return 0
    finally:
        # Flush before exit; the transport batches on a background thread. See the same call in
        # verification_worker/__main__.py.
        get_axiom().close()


if __name__ == "__main__":
    sys.exit(main())
