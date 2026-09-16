#!/usr/bin/env python3
"""List and dispose of treasury payouts that settled no obligation.

The payout watcher records every finalized outbound payout into `treasury_payouts` and then tries
to match it. Most match immediately. The ones that do not sit `UNCLAIMED`, and this is how a human
empties that queue.

    python3 scripts/reconcile_treasury_payouts.py list
    python3 scripts/reconcile_treasury_payouts.py bind --payout 3 --reward-event 12 \\
        --note "paid by hand on 2026-09-14 before the notifier was repaired"
    python3 scripts/reconcile_treasury_payouts.py disregard --payout 4 \\
        --note "treasury rebalance, not a bounty"

**Why a human is required, and why this is not a bug to be automated away.** The watcher will not
let an obligation claim a payout that predates it -- `reward_events.created_at <= block_timestamp +
CHAIN_CLOCK_TOLERANCE`. That rule is what stops a newly created obligation from silently eating an
old, lookalike transfer, and relaxing it would be a far more expensive mistake than the one it
prevents. So when somebody pays a solver before the system knew to expect it, the fingerprint alone
cannot say which submission that money was for. Only a person can, and `bind` is where they say so
on the record.

`bind` refuses a pairing whose amount or destination coldkey disagree, so the operator's authority
extends to *which obligation*, never to *how much* or *to whom*.

A script rather than an admin route, matching `reconcile_tmc_pay.py`: this runs a handful of times
a year, on a host that already has the database, and adding an authenticated write endpoint for it
would be a larger attack surface than the task justifies.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from conjectures_subnet.db import async_session_scope  # noqa: E402
from conjectures_subnet.db import payouts as store  # noqa: E402
from conjectures_subnet.db.engine import (  # noqa: E402
    async_session_factory,
    create_async_db_engine,
    database_url,
)
from conjectures_subnet.db.errors import RecordNotFound  # noqa: E402
from conjectures_subnet.db.models import RewardEvent  # noqa: E402

logger = logging.getLogger("reconcile_treasury_payouts")

# Wide enough for an ss58 address, which is the column that decides the layout.
_ROW = "{id:>5}  {block:>10}  {when:<20}  {amount:>18}  {destination}"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reconcile_treasury_payouts")
    parser.add_argument("--database-url", default="")
    parser.add_argument("--log-level", default="INFO")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="show every payout that settles nothing")

    bind = sub.add_parser(
        "bind", help="settle one obligation with one already-observed payout"
    )
    bind.add_argument("--payout", type=int, required=True, help="treasury_payouts.id")
    bind.add_argument(
        "--reward-event", type=int, required=True, help="reward_events.id"
    )
    bind.add_argument(
        "--note",
        required=True,
        help="why this payout pays this obligation; recorded on the row",
    )

    disregard = sub.add_parser(
        "disregard", help="rule one payout out of reconciliation, with a reason"
    )
    disregard.add_argument("--payout", type=int, required=True)
    disregard.add_argument("--note", required=True)
    return parser


async def _list(sessions) -> int:
    async with async_session_scope(sessions) as session:
        pending = await store.unclaimed_payouts(session, limit=500)
        if not pending:
            print("no unclaimed treasury payouts")
            return 0
        print(
            _ROW.format(
                id="id",
                block="block",
                when="paid at (UTC)",
                amount="alpha rao",
                destination="destination coldkey",
            )
        )
        for payout in pending:
            print(
                _ROW.format(
                    id=payout.id,
                    block=payout.block,
                    when=payout.block_timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    amount=payout.amount_rao,
                    destination=payout.destination_coldkey,
                )
            )
        # The obligations these could plausibly belong to, so the operator does not have to go
        # and write the join by hand.  Amount and coldkey only: the clock is exactly what is in
        # question here, so filtering on it would hide the candidates worth seeing.
        print("\nunresolved obligations with a matching fingerprint:")
        found = False
        for payout in pending:
            candidates = await session.scalars(
                select(RewardEvent)
                .where(
                    RewardEvent.amount_rao == payout.amount_rao,
                    RewardEvent.destination_coldkey == payout.destination_coldkey,
                    RewardEvent.chain_observed.is_(False),
                )
                .order_by(RewardEvent.created_at)
            )
            for row in candidates:
                found = True
                print(
                    f"  payout {payout.id} <- reward event {row.id} "
                    f"(submission {row.submission_id}, created {row.created_at:%Y-%m-%d %H:%M:%S})"
                )
        if not found:
            print("  none")
    return 0


async def _bind(sessions, *, payout_id: int, reward_event_id: int, note: str) -> int:
    async with async_session_scope(sessions) as session:
        update = await store.bind_payout(
            session,
            payout_id=payout_id,
            reward_event_id=reward_event_id,
            note=note,
        )
    verb = "settled" if update.changed else "was already settled;"
    print(
        f"payout {payout_id} {verb} reward event {update.reward_event_id} "
        f"(submission {update.submission_id})"
    )
    return 0


async def _disregard(sessions, *, payout_id: int, note: str) -> int:
    async with async_session_scope(sessions) as session:
        payout = await store.disregard_payout(
            session, payout_id=payout_id, note=note
        )
        print(f"payout {payout_id} ({payout.extrinsic_reference}) disregarded: {note}")
    return 0


async def _run(args: argparse.Namespace) -> int:
    engine = create_async_db_engine(args.database_url.strip() or database_url())
    sessions = async_session_factory(engine)
    try:
        if args.command == "list":
            return await _list(sessions)
        if args.command == "bind":
            return await _bind(
                sessions,
                payout_id=args.payout,
                reward_event_id=args.reward_event,
                note=args.note,
            )
        return await _disregard(sessions, payout_id=args.payout, note=args.note)
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        return asyncio.run(_run(args))
    except (RecordNotFound, store.PayoutConflict) as exc:
        # Both mean the operator's assertion disagrees with stored state, which is a refusal
        # rather than a crash: print it plainly and exit non-zero.
        logger.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
