#!/usr/bin/env python3
"""Daily private-content retention job. Does not inspect, print or transmit package data.

Use the deployment's explicit DATABASE_URL and normal application DB credential. Scheduling,
backup expiry and at-rest encryption must be configured and tested before enabling intake.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import delete, func, select
from sqlalchemy.orm import defer
from submission_api import trajectories as store
from conjectures_subnet.db import async_session_factory, create_async_db_engine
from conjectures_subnet.db.trajectory_models import TrajectoryClaim


async def purge(session):
    # Bounded batches; never load bytea contents merely to discard them.
    removed = 0
    while True:
        claims = (await session.scalars(select(TrajectoryClaim).options(
            defer(TrajectoryClaim.package), defer(TrajectoryClaim.commercial_election)).where(
            TrajectoryClaim.expires_at <= func.now(),
            (TrajectoryClaim.package.is_not(None)) | (TrajectoryClaim.commercial_election["provenance_expired"].as_boolean().is_(None))
        ).limit(100).with_for_update(skip_locked=True))).all()
        if not claims:
            break
        for claim in claims:
            claim.package = None
            await store.minimize_election(session, claim)
            if claim.status in ("UNDER_REVIEW", "NEEDS_CHANGES"):
                claim.status = "EXPIRED"
            claim.state_version += 1
            removed += 1
        await session.commit()
    audit = await session.execute(delete(TrajectoryClaim).where(TrajectoryClaim.audit_expires_at <= func.now()))
    await session.commit()
    return removed, audit.rowcount


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
