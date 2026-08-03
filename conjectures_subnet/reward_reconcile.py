"""Explicit repair for an ambiguous reward payout.

This command never signs. An operator supplies a canonical reference found in
the reward wallet's chain history; the command requires it to be finalized and
to match sender, destination, and exact amount before advancing the durable row.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from conjectures_subnet.chain import BittensorTransferReader
from conjectures_subnet.db import async_session_factory, create_async_db_engine, database_url
from conjectures_subnet.db import submissions as store
from conjectures_subnet.db.models import PayoutState, RewardEvent


async def reconcile(args: argparse.Namespace) -> None:
    if not args.operator or len(args.operator) > 230 or "\x00" in args.operator:
        raise RuntimeError("operator id must contain 1 to 230 non-NUL characters")
    engine = create_async_db_engine(args.database_url)
    sessions = async_session_factory(engine)
    try:
        async with sessions() as session:
            snapshot = await session.get(RewardEvent, args.reward_event_id)
            if snapshot is None:
                raise RuntimeError("reward event not found")
            expected_destination = snapshot.destination_coldkey
            expected_amount = snapshot.bounty_amount_rao
            expected_sender = snapshot.source_coldkey

        transfer = await BittensorTransferReader(args.network).finalized_transfer(
            reference=args.extrinsic_reference
        )
        if transfer is None:
            raise RuntimeError("reference is not a successful finalized direct TAO transfer")
        if (
            transfer.sender != expected_sender
            or transfer.recipient != expected_destination
            or transfer.amount_rao != expected_amount
        ):
            raise RuntimeError("finalized transfer does not match the reserved payout")

        async with sessions() as session:
            payout = (
                await session.execute(
                    select(RewardEvent)
                    .where(RewardEvent.id == args.reward_event_id)
                    .with_for_update()
                )
            ).scalar_one()
            state = PayoutState(payout.status)
            if state is PayoutState.PENDING:
                await store.mark_reward_submitted(
                    session,
                    payout.id,
                    extrinsic_reference=args.extrinsic_reference,
                    submitted_block=transfer.block,
                )
            elif state in {PayoutState.SUBMITTED, PayoutState.CONFIRMED}:
                if payout.extrinsic_reference != args.extrinsic_reference:
                    raise RuntimeError("reward event already records a different reference")
            else:
                raise RuntimeError("failed reward event cannot be reconciled automatically")
            await store.mark_reward_confirmed(
                session,
                payout.id,
                finalized_block=transfer.block,
                actor=f"reward-reconcile:{args.operator}",
            )
            await session.commit()
        print(
            f"reward event {args.reward_event_id} confirmed as {args.extrinsic_reference} "
            f"at finalized block {transfer.block}"
        )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="reconcile one ambiguous reward payout")
    parser.add_argument("--database-url", default=database_url())
    parser.add_argument("--network", default="finney")
    parser.add_argument("--reward-event-id", type=int, required=True)
    parser.add_argument("--extrinsic-reference", required=True)
    parser.add_argument("--operator", required=True)
    asyncio.run(reconcile(parser.parse_args()))
