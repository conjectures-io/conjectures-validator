from __future__ import annotations

import argparse
import functools
import logging
import signal
import threading

from conjectures_subnet.db.engine import create_db_engine, session_factory
from payout_notifier.settings import NotifierSettings, SettingsError
from payout_notifier.pricing import quote_formalization_defect_award
from payout_notifier.worker import PayoutNotifier, Processed

logger = logging.getLogger("payout_notifier")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="watch pending reward events and notify their Discord payout signers"
    )
    parser.add_argument("--once", action="store_true", help="process current DB state and exit")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        settings = NotifierSettings.from_env()
    except SettingsError as exc:
        logger.error("%s", exc)
        return 2

    engine = create_db_engine(settings.database_url)
    notifier = PayoutNotifier(
        sessions=session_factory(engine),
        webhook_url=settings.webhook_url,
        worker_id=settings.worker_id,
        retry_seconds=settings.retry_seconds,
        lease_seconds=settings.lease_seconds,
        defect_award_quoter=functools.partial(
            quote_formalization_defect_award,
            api_key=settings.taostats_api_key,
            netuid=settings.bounty_netuid,
            timeout_seconds=settings.taostats_timeout_seconds,
        ),
    )
    stop = threading.Event()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signal_name, lambda *_args: stop.set())

    logger.info(
        "payout notifier %s started poll=%ss retry=%ss",
        settings.worker_id,
        settings.poll_seconds,
        settings.retry_seconds,
    )
    try:
        result = Processed()
        while not stop.is_set():
            try:
                result = notifier.process_once()
                if (
                    result.payouts_seeded
                    or result.seeded
                    or result.delivered
                    or result.failed
                ):
                    logger.info(
                        "payout notification pass payouts=%d outbox=%d delivered=%d failed=%d",
                        result.payouts_seeded,
                        result.seeded,
                        result.delivered,
                        result.failed,
                    )
            except Exception:
                logger.exception("payout notification pass failed")
                if args.once:
                    return 1
            if args.once:
                return 0 if result.failed == 0 else 1
            stop.wait(settings.poll_seconds)
        return 0
    finally:
        engine.dispose()
        logger.info("payout notifier stopped")


if __name__ == "__main__":
    raise SystemExit(main())
