"""Winner payout worker with fail-safe deduplication.

The worker commits a unique PENDING reward row before touching the wallet. It
then submits one exact-amount TAO transfer and waits for finality. If the
process loses the result after signing, the PENDING row remains unresolved and
blocks every retry; an operator must reconcile it rather than risk paying twice.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import bittensor as bt
from sqlalchemy import exists, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conjectures_subnet.chain import BittensorTransferReader, EXTRINSIC_REFERENCE
from conjectures_subnet.db import async_session_factory, create_async_db_engine, database_url
from conjectures_subnet.db import submissions as store
from conjectures_subnet.db.models import PayoutState, RewardEvent, RewardState, Submission


LOG = logging.getLogger(__name__)
COMMIT = re.compile(r"^[0-9a-f]{7,40}$")
# One database-wide signer at a time. Multiple processes sharing a coldkey can
# otherwise race its account nonce even though each individual submission is
# protected by a unique payout row.
REWARD_SIGNER_LOCK = 0x434F4E4A454354


@dataclass(frozen=True)
class SubmittedPayout:
    reference: str
    included_block: int


class BittensorTaoGateway:
    """The only component that loads a wallet or signs a payout."""

    def __init__(
        self,
        *,
        network: str,
        wallet_name: str,
        wallet_path: Path,
        max_payout_rao: int,
    ):
        if max_payout_rao <= 0 or max_payout_rao > (1 << 63) - 1:
            raise ValueError("maximum payout must fit a positive PostgreSQL BIGINT")
        self.network = network
        self.wallet = bt.Wallet(name=wallet_name, path=str(wallet_path))
        self.reader = BittensorTransferReader(network)
        self.max_payout_rao = max_payout_rao

    async def submit(self, payout: RewardEvent) -> SubmittedPayout:
        if (
            payout.bounty_amount_rao <= 0
            or payout.bounty_amount_rao > self.max_payout_rao
        ):
            raise RuntimeError("frozen payout amount exceeds the wallet spend policy")
        if payout.source_coldkey != self.wallet.coldkeypub.ss58_address:
            raise RuntimeError("reserved payout source does not match the loaded reward wallet")
        intent = bt.Transfer(
            dest_ss58=payout.destination_coldkey,
            amount_tao=bt.Balance.from_rao(payout.bounty_amount_rao),
        )
        policy = bt.Policy(max_spend_tao=bt.Balance.from_rao(self.max_payout_rao))
        async with bt.Subtensor(self.network, policy=policy) as client:
            result = await client.execute(
                intent,
                self.wallet,
                wait_for_inclusion=True,
                wait_for_finalization=False,
            )
        result.raise_for_failure()
        if result.extrinsic_id is None:
            raise RuntimeError("finalized payout did not return an extrinsic reference")
        match = EXTRINSIC_REFERENCE.fullmatch(result.extrinsic_id)
        if match is None:
            raise RuntimeError("payout returned a noncanonical extrinsic reference")
        block = int(match.group("block"))
        return SubmittedPayout(
            reference=result.extrinsic_id,
            included_block=block,
        )

    async def finalized(self, payout: RewardEvent) -> int | None:
        if payout.extrinsic_reference is None:
            return None
        transfer = await self.reader.finalized_transfer(reference=payout.extrinsic_reference)
        if transfer is None:
            return None
        if (
            transfer.sender != payout.source_coldkey
            or transfer.recipient != payout.destination_coldkey
            or transfer.amount_rao != payout.bounty_amount_rao
        ):
            raise RuntimeError("recorded payout reference does not match the finalized transfer")
        return transfer.block


class RewardWorker:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        gateway: BittensorTaoGateway,
        bounty_commit: str,
        worker_id: str = "reward-worker",
    ):
        if COMMIT.fullmatch(bounty_commit) is None:
            raise ValueError("bounty commit must be a 7 to 40 character lowercase git hash")
        if not worker_id or len(worker_id) > 255 or "\x00" in worker_id:
            raise ValueError("worker id must contain 1 to 255 non-NUL characters")
        self.sessions = sessions
        self.gateway = gateway
        self.bounty_commit = bounty_commit
        self.worker_id = worker_id

    async def _reconcile_one(self) -> bool:
        async with self.sessions() as session:
            payout = (
                await session.execute(
                    select(RewardEvent)
                    .where(RewardEvent.status == PayoutState.SUBMITTED)
                    .order_by(RewardEvent.created_at)
                    .limit(1)
                )
            ).scalar_one_or_none()
        if payout is None:
            return False
        finalized_block = await self.gateway.finalized(payout)
        if finalized_block is None:
            return False
        async with self.sessions() as session:
            await store.mark_reward_confirmed(
                session,
                payout.id,
                finalized_block=finalized_block,
                actor=self.worker_id,
            )
            await session.commit()
        return True

    async def _reserve_one(self) -> RewardEvent | None:
        async with self.sessions() as session:
            submission = (
                await session.execute(
                    select(Submission)
                    .where(
                        Submission.reward_status == RewardState.ELIGIBLE,
                        ~exists().where(RewardEvent.submission_id == Submission.id),
                    )
                    .order_by(Submission.created_at)
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if submission is None:
                return None
            if submission.bounty_amount_rao > self.gateway.max_payout_rao:
                raise RuntimeError(
                    "eligible submission bounty exceeds the configured wallet safety cap"
                )
            payout = await store.create_reward_event(
                session,
                submission.id,
                source_coldkey=self.gateway.wallet.coldkeypub.ss58_address,
                bounty_commit=self.bounty_commit,
                initiated_by=self.worker_id,
            )
            await session.commit()  # must precede signing
            return payout

    async def run_once(self) -> bool:
        if await self._reconcile_one():
            return True
        payout = await self._reserve_one()
        if payout is None:
            return False
        try:
            submitted = await self.gateway.submit(payout)
        except Exception:
            # Ambiguous by design: an RPC error may happen after broadcast. Do
            # not mark FAILED and never construct a second attempt.
            LOG.exception(
                "payout %s is unresolved PENDING; reconcile on chain before any action",
                payout.id,
            )
            return True

        # If the database write below fails, this is the operator's durable log
        # handle for reconciling the intentionally unresolved PENDING row.
        LOG.info(
            "payout %s included as %s for %s rao",
            payout.id,
            submitted.reference,
            payout.bounty_amount_rao,
        )

        async with self.sessions() as session:
            await store.mark_reward_submitted(
                session,
                payout.id,
                extrinsic_reference=submitted.reference,
                submitted_block=submitted.included_block,
            )
            await session.commit()
        return True


async def _serve(args: argparse.Namespace) -> None:
    engine = create_async_db_engine(args.database_url)
    gateway = BittensorTaoGateway(
        network=args.network,
        wallet_name=args.wallet_name,
        wallet_path=args.wallet_path,
        max_payout_rao=args.max_payout_rao,
    )
    worker = RewardWorker(
        sessions=async_session_factory(engine),
        gateway=gateway,
        bounty_commit=args.bounty_commit,
        worker_id=args.worker_id,
    )
    try:
        # Session-level advisory lock: released automatically if this process or
        # its database connection dies. Keep the owning connection checked out
        # and heartbeat it before every signing/reconciliation iteration.
        async with engine.connect() as signer_lock:
            acquired = await signer_lock.scalar(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": REWARD_SIGNER_LOCK}
            )
            await signer_lock.commit()
            if acquired is not True:
                raise RuntimeError("another reward signer already holds the database lock")
            try:
                while True:
                    await signer_lock.execute(text("SELECT 1"))
                    await signer_lock.commit()
                    if not await worker.run_once():
                        await asyncio.sleep(args.poll_seconds)
            finally:
                try:
                    await signer_lock.execute(
                        text("SELECT pg_advisory_unlock(:key)"),
                        {"key": REWARD_SIGNER_LOCK},
                    )
                    await signer_lock.commit()
                except Exception:  # connection loss already releases the server-side lock
                    LOG.exception("could not explicitly release the reward signer lock")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="pay finalized TAO bounties to problem winners")
    parser.add_argument("--database-url", default=database_url())
    parser.add_argument("--network", default="finney")
    parser.add_argument("--wallet-name", required=True)
    parser.add_argument("--wallet-path", type=Path, required=True)
    parser.add_argument(
        "--max-payout-rao",
        type=int,
        required=True,
        help="wallet safety cap; each submission is paid its lower frozen bounty quote",
    )
    parser.add_argument("--bounty-commit", required=True)
    parser.add_argument("--worker-id", default="reward-worker")
    parser.add_argument("--poll-seconds", type=float, default=6.0)
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_serve(parser.parse_args()))
