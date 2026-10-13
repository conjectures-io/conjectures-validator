#!/usr/bin/env python3
"""Daily chat-log retention job. Does not inspect, print or transmit package data.

Accepted logs are licensed for sale and kept. Unsubmitted deposits are deleted after 24 hours;
other packages after 90 days; minimal records after seven years unless they back a payout.

Use the deployment's explicit DATABASE_URL and normal application DB credential. Scheduling,
backup expiry and at-rest encryption must be configured and tested before enabling intake.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import delete, exists, func, or_, select
from sqlalchemy.orm import defer
from submission_api import trajectories as store
from conjectures_subnet.db import async_session_factory, create_async_db_engine
from conjectures_subnet.db.trajectory_models import TrajectoryClaim
from conjectures_subnet.db.models import RewardEvent, Submission


async def purge(session):
    # Logs uploaded for a new bounty whose proof never followed are deleted outright.
    unsubmitted = (await session.execute(delete(TrajectoryClaim).where(
        TrajectoryClaim.status == "DEPOSITED", TrajectoryClaim.submission_id.is_(None),
        TrajectoryClaim.expires_at <= func.now()))).rowcount
    await session.commit()
    # Bounded batches; never load bytea contents merely to discard them. Accepted logs are
    # licensed for sale and kept; every other package expires on schedule.
    removed = unsubmitted
    expiring = (TrajectoryClaim.expires_at <= func.now(), TrajectoryClaim.package.is_not(None),
                TrajectoryClaim.submission_id.is_not(None), TrajectoryClaim.status.not_in(store.APPROVED))
    while True:
        candidate_ids = (await session.scalars(select(TrajectoryClaim.id).where(*expiring)
                                               .order_by(TrajectoryClaim.id).limit(100))).all()
        if not candidate_ids:
            break
        processed = 0
        for claim_id in candidate_ids:
            submission_id = await session.scalar(select(TrajectoryClaim.submission_id).where(TrajectoryClaim.id == claim_id))
            proof = await session.scalar(select(Submission.id).where(Submission.id == submission_id)
                                         .with_for_update(skip_locked=True))
            if proof is None:
                continue
            claim = await session.scalar(select(TrajectoryClaim).options(defer(TrajectoryClaim.package)).where(
                    TrajectoryClaim.id == claim_id, *expiring)
                .execution_options(populate_existing=True).with_for_update(skip_locked=True))
            if claim is None:
                continue
            claim.package = None
            if claim.status in ("UNDER_REVIEW", "NEEDS_CHANGES"):
                claim.status = "EXPIRED"
            claim.state_version += 1
            removed += 1
            processed += 1
        if not processed:
            await session.rollback()
            break  # Busy rows are retried by the next scheduled run.
        await session.commit()
    # Minimal records go after seven years, except accepted logs and records backing a payout.
    from conjectures_subnet.db.trajectory_fulfillment import TrajectoryPayment, TrajectoryDelivery
    audit_expired = (
        ~exists(select(TrajectoryPayment.id).where(TrajectoryPayment.claim_id == TrajectoryClaim.id)),
        ~exists(select(TrajectoryDelivery.id).where(TrajectoryDelivery.claim_id == TrajectoryClaim.id)),TrajectoryClaim.audit_expires_at <= func.now(), TrajectoryClaim.status.not_in(store.APPROVED),
                     or_(TrajectoryClaim.amount_usd_cents > 0,
                         ~exists(select(RewardEvent.id).where(RewardEvent.submission_id == TrajectoryClaim.submission_id))))
    audit_removed = 0
    while True:
        ids = (await session.scalars(select(TrajectoryClaim.id).where(*audit_expired)
                                     .order_by(TrajectoryClaim.id).limit(100))).all()
        processed = 0
        for claim_id in ids:
            submission_id = await session.scalar(select(TrajectoryClaim.submission_id).where(TrajectoryClaim.id == claim_id))
            proof = await session.scalar(select(Submission.id).where(Submission.id == submission_id)
                                         .with_for_update(skip_locked=True))
            if proof is None:
                continue
            result = await session.execute(delete(TrajectoryClaim).where(TrajectoryClaim.id == claim_id, *audit_expired))
            processed += result.rowcount
        await session.commit()
        audit_removed += processed
        if not processed:
            break
    return removed, audit_removed


async def main():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL must be explicitly configured")
    engine = create_async_db_engine(url)
    try:
        async with async_session_factory(engine)() as session:
            expired, audit = await purge(session)
            print(f"Expired packages removed: {expired}; expired audit records removed: {audit}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
