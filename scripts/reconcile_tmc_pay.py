#!/usr/bin/env python3
"""Reconcile open TMC PAY credit orders against the processor.

TMC PAY dispatches one webhook per invoice transition and **never retries automatically** — a
failed delivery is marked failed in its dashboard and waits for a human to press retry. So a
deploy, a restart, or thirty seconds of network trouble at the wrong moment is a buyer who paid
and got nothing. That is not an acceptable failure mode for money, and this is the sweep that
makes it a delay instead of a loss.

Run it on a schedule — every minute or two is ample, given the invoice TTL is 30 minutes:

    python3 scripts/reconcile_tmc_pay.py                 # one pass, then exit
    python3 scripts/reconcile_tmc_pay.py --loop          # keep going
    python3 scripts/reconcile_tmc_pay.py --dry-run       # report, write nothing

It reads the same `.env` the API does, resolves the same database through
`conjectures_subnet.db.database_url()`, and applies exactly the same decision the webhook path
applies — `submission_api.routers.tmc_pay.apply_invoice`, which is shared rather than
reimplemented, because two answers to "does this status mean credits" is one too many.

Crediting is idempotent, so a pass that races a webhook is harmless: whichever gets there first
writes the ledger entry and the other sees a conflict and stops. It is therefore safe to run this
alongside a live API, and safe to run two of them by mistake.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import logging
import signal
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from conjectures_subnet.axiom import configure_logging, get_axiom
from conjectures_subnet.db import tmc_pay as order_store
from conjectures_subnet.db.engine import (
    async_session_factory,
    create_async_db_engine,
    database_url,
)
from submission_api import tmc_pay
from submission_api.routers import tmc_pay as tmc_pay_router
from submission_api.settings import Settings, SettingsError
from submission_api.rates import UnavailableTaoUsdPriceReader  # noqa: E402

logger = logging.getLogger("reconcile_tmc_pay")

DEFAULT_BATCH = 50
DEFAULT_INTERVAL_SECONDS = 60.0

# How long an order must have gone unpolled before this sweep reads it again. Comfortably longer
# than the read endpoint's own interval, so a buyer sitting on the payment page — who is polling
# every few seconds and is the fastest path to their own credits — is not competing with a
# background job for the same rate limit.
DEFAULT_MIN_AGE_SECONDS = 30.0


class _Gateway:
    """Just enough of `Services` for `apply_invoice`, without building the whole app.

    `apply_invoice` takes settings and a session; `refresh_order` also wants a `Services` for its
    gateway. Constructing the real one would load the task catalog, the pin set and the terms
    document, none of which reconciliation touches — so this passes a minimal stand-in and keeps
    the script's failure modes to "cannot reach TMC PAY" and "cannot reach the database".
    """

    def __init__(self, client: tmc_pay.InvoiceGateway) -> None:
        self.tmc_pay = client
        self.tao_usd = UnavailableTaoUsdPriceReader()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 scripts/reconcile_tmc_pay.py")
    parser.add_argument(
        "--loop",
        action="store_true",
        help="keep reconciling on an interval instead of exiting after one pass",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL_SECONDS,
        help=f"seconds between passes with --loop (default {DEFAULT_INTERVAL_SECONDS:.0f})",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=DEFAULT_BATCH,
        help=f"how many orders one pass reads (default {DEFAULT_BATCH})",
    )
    parser.add_argument(
        "--min-age",
        type=float,
        default=DEFAULT_MIN_AGE_SECONDS,
        help="only re-read orders not polled for this many seconds",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be read and applied, without writing anything",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


async def _pass(
    *,
    sessions,
    services: _Gateway,
    settings: Settings,
    batch: int,
    min_age: float,
    dry_run: bool,
) -> tuple[int, int, int]:
    """One sweep. Returns (read, credited, failed).

    Each order is committed on its own, in its own session, so a processor error on one does not
    roll back the ones already settled and a long backlog makes visible progress.
    """
    async with sessions() as session:
        candidates = await order_store.open_orders(
            session,
            limit=batch,
            before=_now() - dt.timedelta(seconds=min_age),
        )
        # Materialised before the per-order sessions open, so the queue is read once rather than
        # re-queried while it is being drained.
        pending = [
            (order.id, order.account_id, order.invoice_id, str(order.status))
            for order in candidates
            if order.invoice_id is not None
        ]
        orphans = len(candidates) - len(pending)

    if orphans:
        # An order with no invoice id is either mid-creation or the wrong side of a lost
        # create-response. Neither is readable back from TMC PAY — there is no lookup by
        # `external_id` — so it is reported and left for the webhook that echoes it.
        logger.info(
            "%d order(s) have no invoice id and cannot be polled; a webhook is what will "
            "resolve them",
            orphans,
        )

    credited = failed = 0
    for order_id, account_id, invoice_id, before_status in pending:
        if dry_run:
            logger.info(
                "would read invoice %s for order %s (currently %s)",
                invoice_id,
                order_id,
                before_status,
            )
            continue
        async with sessions() as session:
            order = await order_store.get_order(session, order_id, account_id)
            was_credited = order.credited_ledger_id is not None
            try:
                order = await tmc_pay_router.refresh_order(
                    session,
                    order,
                    services=services,  # type: ignore[arg-type]  - see _Gateway
                    settings=settings,
                    now=_now(),
                )
            except tmc_pay.TmcPayError as exc:
                # Retryable or not, the next pass will try again; nothing here is lost by
                # continuing to the next order.
                failed += 1
                logger.warning("could not reconcile order %s: %s", order_id, exc)
                await session.rollback()
                continue
            if not was_credited and order.credited_ledger_id is not None:
                credited += 1
                logger.info(
                    "credited order %s from a %s invoice that no webhook had applied",
                    order_id,
                    order.status,
                )
            elif str(order.status) != before_status:
                logger.info(
                    "order %s moved %s to %s", order_id, before_status, order.status
                )

    if not dry_run:
        async with sessions() as session:
            # Housekeeping, once per pass and after the reads: close the orders whose invoice TTL
            # elapsed with nothing seen. Only rows that cannot hold money are touched — see
            # `db.tmc_pay.expire_lapsed`. After, not before, so an invoice that was paid in its
            # last seconds is credited by the read above rather than expired out from under it.
            closed = await order_store.expire_lapsed(session, now=_now())
            if closed:
                await session.commit()
                logger.info("closed %d order(s) whose invoice expired unpaid", closed)

    return len(pending), credited, failed


async def _run(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    if not settings.tmc_pay_enabled:
        logger.error(
            "TMC PAY is not configured; set TMC_PAY_API_BASE_URL, TMC_PAY_API_KEY and "
            "TMC_PAY_WEBHOOK_SECRET"
        )
        return 2

    client = tmc_pay.TmcPayClient(
        base_url=settings.tmc_pay_base_url,
        api_key=settings.tmc_pay_api_key,
        timeout_seconds=settings.tmc_pay_timeout_seconds,
    )
    engine = create_async_db_engine(settings.database_url or database_url())
    sessions = async_session_factory(engine)
    services = _Gateway(client)

    stop = asyncio.Event()
    if args.loop:
        loop = asyncio.get_running_loop()
        for name in (signal.SIGINT, signal.SIGTERM):
            # Each order commits on its own, so stopping between them loses nothing.
            loop.add_signal_handler(name, stop.set)

    try:
        while True:
            read, credited, failed = await _pass(
                sessions=sessions,
                services=services,
                settings=settings,
                batch=args.batch,
                min_age=args.min_age,
                dry_run=args.dry_run,
            )
            logger.info(
                "pass complete: %d order(s) read, %d credited, %d could not be read",
                read,
                credited,
                failed,
            )
            if credited or failed:
                get_axiom().info(
                    source="tmc-pay-reconciler",
                    event_type="tmc_pay_reconciled",
                    orders_read=read,
                    orders_credited=credited,
                    orders_failed=failed,
                    dry_run=args.dry_run,
                )
            if not args.loop:
                return 0
            try:
                await asyncio.wait_for(stop.wait(), timeout=args.interval)
                return 0
            except TimeoutError:
                continue
    finally:
        await client.aclose()
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    configure_logging(source="tmc-pay-reconciler", level=args.log_level)
    try:
        return asyncio.run(_run(args))
    except SettingsError as exc:
        # Configuration, not the processor. Exit 2 so a compose service leaves it dead and
        # visible rather than crash-looping the message out of the scrollback — the contract the
        # deposit watcher and the verification worker already have.
        logger.error("%s", exc)
        return 2
    except KeyboardInterrupt:  # pragma: no cover - the signal handlers cover --loop
        return 0
    finally:
        get_axiom().close()


if __name__ == "__main__":
    sys.exit(main())
