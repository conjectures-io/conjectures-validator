"""Confirm a manually executed treasury-multisig payout from finalized chain state.

This command is read-only with respect to Subtensor. An operator supplies the
canonical extrinsic reference after the multisig has executed the transfer. The
command checks finality, treasury sender, winner destination, and exact frozen
amount before atomically marking the payout confirmed.
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conjectures_subnet.chain import BittensorMultisigTransferReader, EXTRINSIC_REFERENCE
from conjectures_subnet.db import async_session_factory, create_async_db_engine, database_url
from conjectures_subnet.db import submissions as store
from conjectures_subnet.db.models import PayoutState, RewardEvent
from submission_api.payments import FinalizedTransfer


class TransferReader(Protocol):
    async def finalized_transfer(self, *, reference: str) -> FinalizedTransfer | None: ...


def _validate_operator(operator: str) -> None:
    if not operator or len(operator) > 230 or "\x00" in operator:
        raise RuntimeError("operator id must contain 1 to 230 non-NUL characters")


def validate_payout_transfer(
    *,
    treasury_account: str,
    destination_coldkey: str,
    amount_rao: int,
    transfer: FinalizedTransfer,
) -> None:
    """Require chain evidence to match every frozen payout instruction field."""

    if (
        transfer.sender != treasury_account
        or transfer.recipient != destination_coldkey
        or transfer.amount_rao != amount_rao
    ):
        raise RuntimeError("finalized transfer does not match the payout instruction")


async def confirm_payout(
    *,
    sessions: async_sessionmaker[AsyncSession],
    reader: TransferReader,
    reward_event_id: int,
    extrinsic_reference: str,
    operator: str,
) -> RewardEvent:
    """Validate one finalized transfer and attach it to its payout instruction."""

    _validate_operator(operator)
    if EXTRINSIC_REFERENCE.fullmatch(extrinsic_reference) is None:
        raise RuntimeError("extrinsic reference is not canonical")

    async with sessions() as session:
        snapshot = await session.get(RewardEvent, reward_event_id)
        if snapshot is None:
            raise RuntimeError("reward event not found")
        if PayoutState(snapshot.status) is PayoutState.CONFIRMED:
            if snapshot.extrinsic_reference != extrinsic_reference:
                raise RuntimeError("reward event is confirmed with a different transfer")
            return snapshot
        if PayoutState(snapshot.status) is not PayoutState.AWAITING_MULTISIG:
            raise RuntimeError("reward event is not awaiting multisig execution")
        expected_treasury = snapshot.treasury_account
        expected_destination = snapshot.destination_coldkey
        expected_amount = snapshot.bounty_amount_rao

    transfer = await reader.finalized_transfer(reference=extrinsic_reference)
    if transfer is None:
        raise RuntimeError("reference is not a successful finalized multisig TAO transfer")
    validate_payout_transfer(
        treasury_account=expected_treasury,
        destination_coldkey=expected_destination,
        amount_rao=expected_amount,
        transfer=transfer,
    )

    async with sessions() as session:
        payout = (
            await session.execute(
                select(RewardEvent)
                .where(RewardEvent.id == reward_event_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if payout is None:
            raise RuntimeError("reward event not found")
        try:
            validate_payout_transfer(
                treasury_account=payout.treasury_account,
                destination_coldkey=payout.destination_coldkey,
                amount_rao=payout.bounty_amount_rao,
                transfer=transfer,
            )
        except RuntimeError as exc:
            raise RuntimeError("payout instruction changed during confirmation") from exc
        payout = await store.confirm_manual_payout(
            session,
            payout.id,
            extrinsic_reference=extrinsic_reference,
            finalized_block=transfer.block,
            actor=f"payout-confirm:{operator}",
        )
        await session.commit()
        return payout


async def _run(args: argparse.Namespace) -> None:
    engine = create_async_db_engine(args.database_url)
    try:
        payout = await confirm_payout(
            sessions=async_session_factory(engine),
            reader=BittensorMultisigTransferReader(args.network),
            reward_event_id=args.reward_event_id,
            extrinsic_reference=args.extrinsic_reference,
            operator=args.operator,
        )
        print(
            f"reward event {payout.id} confirmed as {payout.extrinsic_reference} "
            f"at finalized block {payout.finalized_block}"
        )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="confirm one manually executed treasury-multisig payout"
    )
    parser.add_argument("--database-url", default=database_url())
    parser.add_argument("--network", default="finney")
    parser.add_argument("--reward-event-id", type=int, required=True)
    parser.add_argument("--extrinsic-reference", required=True)
    parser.add_argument("--operator", required=True)
    asyncio.run(_run(parser.parse_args()))
