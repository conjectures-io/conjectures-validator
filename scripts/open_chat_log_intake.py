#!/usr/bin/env python3
"""Open chat-log intake: stamp the permanent cutoff for the earlier-proof bonus.

Run once at go-live, after the staging run passes and immediately before setting
TRAJECTORY_BONUS_ENABLED=true and NEXT_PUBLIC_TRAJECTORY_BONUS=1. Proofs submitted before
this moment qualify for the $500/$250 bonus for one year. The terms take effect at the same
moment. The record cannot be changed or deleted afterwards; a second run changes nothing.

    DATABASE_URL=... python scripts/open_chat_log_intake.py --opened-by "Name" --confirm
"""
import argparse
import asyncio
import datetime as dt
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from conjectures_subnet.db import async_session_factory, create_async_db_engine
from conjectures_subnet.db.trajectory_models import ChatLogProgram
from submission_api.trajectory_terms import TERMS_SHA256, TERMS_VERSION
from submission_api.trajectories import CLAIM_WINDOW_DAYS


async def open_intake(session, *, opened_by):
    """Return (opened_at, newly_opened). Never moves an existing opening."""
    if "draft" in TERMS_VERSION:
        raise SystemExit("Refusing to open with draft terms.")
    created = await session.scalar(insert(ChatLogProgram).values(
        id=1, opened_at=func.now(), terms_version=TERMS_VERSION,
        terms_sha256=bytes.fromhex(TERMS_SHA256.removeprefix("sha256:")), opened_by=opened_by,
    ).on_conflict_do_nothing().returning(ChatLogProgram.opened_at))
    await session.commit()
    if created is not None:
        return created, True
    return await session.scalar(select(ChatLogProgram.opened_at).where(ChatLogProgram.id == 1)), False


async def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--opened-by", required=True, help="who is opening intake, for the record")
    parser.add_argument("--confirm", action="store_true", help="required: this cannot be undone")
    args = parser.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL must be explicitly configured")
    if not args.confirm or not 1 <= len(args.opened_by.strip()) <= 255:
        raise SystemExit("Pass --confirm and a non-empty --opened-by: the opening is permanent.")
    engine = create_async_db_engine(url)
    try:
        async with async_session_factory(engine)() as session:
            opened_at, new = await open_intake(session, opened_by=args.opened_by.strip())
    finally:
        await engine.dispose()
    closes = opened_at + dt.timedelta(days=CLAIM_WINDOW_DAYS)
    print(("Opened" if new else "Already opened; nothing changed. Opened") +
          f" at {opened_at.isoformat()} under {TERMS_VERSION}. Earlier proofs submitted before then"
          f" may claim the bonus until {closes.isoformat()}.")


if __name__ == "__main__":
    asyncio.run(main())
