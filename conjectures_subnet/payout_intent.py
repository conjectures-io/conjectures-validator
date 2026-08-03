"""Record a payout instruction for manual treasury-multisig execution.

This command has database access only. It never loads a wallet, constructs an
extrinsic, signs, or broadcasts. The resulting row freezes the treasury source,
winner destination, and intake-quoted amount for operators to execute manually.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import uuid

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conjectures_subnet.db import async_session_factory, create_async_db_engine, database_url
from conjectures_subnet.db import submissions as store
from conjectures_subnet.db.models import RewardEvent, RewardState, Submission
from verifier.bundle import SS58_ADDRESS


COMMIT = re.compile(r"^[0-9a-f]{7,40}$")


def _validate_operator(operator: str) -> None:
    if not operator or len(operator) > 230 or "\x00" in operator:
        raise RuntimeError("operator id must contain 1 to 230 non-NUL characters")


async def record_payout_intent(
    *,
    sessions: async_sessionmaker[AsyncSession],
    treasury_account: str,
    payout_policy_commit: str,
    operator: str,
    submission_id: uuid.UUID | None = None,
) -> RewardEvent:
    """Freeze one eligible winner's manual payout instruction."""

    _validate_operator(operator)
    if SS58_ADDRESS.fullmatch(treasury_account) is None:
        raise RuntimeError("treasury account is not a valid SS58 address")
    if COMMIT.fullmatch(payout_policy_commit) is None:
        raise RuntimeError("payout policy commit must be a 7 to 40 character lowercase git hash")

    async with sessions() as session:
        if submission_id is None:
            submission_id = (
                await session.execute(
                    select(Submission.id)
                    .where(
                        Submission.reward_status == RewardState.ELIGIBLE,
                        ~exists().where(RewardEvent.submission_id == Submission.id),
                    )
                    .order_by(Submission.created_at)
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if submission_id is None:
                raise RuntimeError("no reward-eligible winner needs a payout instruction")

        payout = await store.create_reward_event(
            session,
            submission_id,
            treasury_account=treasury_account,
            payout_policy_commit=payout_policy_commit,
            initiated_by=f"payout-intent:{operator}",
        )
        await session.commit()
        return payout


async def _run(args: argparse.Namespace) -> None:
    engine = create_async_db_engine(args.database_url)
    try:
        payout = await record_payout_intent(
            sessions=async_session_factory(engine),
            treasury_account=args.treasury_account,
            payout_policy_commit=args.payout_policy_commit,
            operator=args.operator,
            submission_id=args.submission_id,
        )
        print(
            json.dumps(
                {
                    "amount_rao": payout.bounty_amount_rao,
                    "destination_coldkey": payout.destination_coldkey,
                    "destination_hotkey": payout.destination_hotkey,
                    "payout_policy_commit": payout.payout_policy_commit,
                    "reward_event_id": payout.id,
                    "status": str(payout.status),
                    "submission_id": str(payout.submission_id),
                    "treasury_account": payout.treasury_account,
                },
                sort_keys=True,
            )
        )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="record one payout instruction for manual treasury-multisig execution"
    )
    parser.add_argument("--database-url", default=database_url())
    parser.add_argument("--submission-id", type=uuid.UUID)
    parser.add_argument("--treasury-account", required=True)
    parser.add_argument("--payout-policy-commit", required=True)
    parser.add_argument("--operator", required=True)
    asyncio.run(_run(parser.parse_args()))
