#!/usr/bin/env python3
"""Open chat-log intake: stamp the cutoff for the earlier-proof bonus, or correct it.

Run once at go-live, after the staging run passes and immediately before setting
TRAJECTORY_BONUS_ENABLED=true and NEXT_PUBLIC_TRAJECTORY_BONUS=1. Proofs submitted before
this moment qualify for the $500/$250 bonus for one year. The terms take effect at the same
moment. A second run changes nothing.

    DATABASE_URL=... python scripts/open_chat_log_intake.py --opened-by "Name" --confirm

If something goes wrong, the moment can be corrected. Each correction is recorded permanently
with the previous moment, who made it and why. Claims already made keep the cutoff they were
made under.

    DATABASE_URL=... python scripts/open_chat_log_intake.py --correct-to 2026-10-13T20:00:00Z \
        --corrected-by "Name" --reason "Relaunched after rollback" --confirm
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
from conjectures_subnet.db.trajectory_models import ChatLogProgram, ChatLogProgramCorrection
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


async def correct_opening(session, *, opened_at, corrected_by, reason, now=None):
    """Move the opening moment with a permanent correction record; return the previous moment."""
    if opened_at.tzinfo is None:
        raise SystemExit("--correct-to needs a timezone, for example 2026-10-13T20:00:00Z.")
    if opened_at > (now or dt.datetime.now(dt.UTC)):
        raise SystemExit("--correct-to cannot be in the future.")
    program = await session.scalar(select(ChatLogProgram).where(ChatLogProgram.id == 1).with_for_update())
    if program is None:
        raise SystemExit("Intake has not been opened; there is nothing to correct.")
    previous = program.opened_at
    # The database accepts the change only alongside this record, in the same transaction.
    session.add(ChatLogProgramCorrection(previous_opened_at=previous, opened_at=opened_at,
                                         corrected_by=corrected_by, reason=reason))
    await session.flush()
    program.opened_at = opened_at
    await session.commit()
    return previous


async def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--opened-by", help="who is opening intake, for the record")
    parser.add_argument("--correct-to", type=dt.datetime.fromisoformat,
                        help="correct the opening moment to this ISO 8601 time")
    parser.add_argument("--corrected-by", help="who is correcting it, for the record")
    parser.add_argument("--reason", help="why the opening moment is being corrected")
    parser.add_argument("--confirm", action="store_true", help="required: the record is permanent")
    args = parser.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL must be explicitly configured")
    if args.correct_to is not None:
        by, reason = (args.corrected_by or "").strip(), (args.reason or "").strip()
        if not args.confirm or not 1 <= len(by) <= 255 or not 1 <= len(reason) <= 2000:
            raise SystemExit("Pass --confirm, --corrected-by and --reason: corrections are permanent.")
        engine = create_async_db_engine(url)
        try:
            async with async_session_factory(engine)() as session:
                previous = await correct_opening(session, opened_at=args.correct_to,
                                                 corrected_by=by, reason=reason)
        finally:
            await engine.dispose()
        closes = args.correct_to + dt.timedelta(days=CLAIM_WINDOW_DAYS)
        print(f"Corrected the opening from {previous.isoformat()} to {args.correct_to.isoformat()}."
              f" Earlier proofs submitted before then may claim the bonus until {closes.isoformat()};"
              " claims already made keep their cutoff.")
        return
    if not args.confirm or not 1 <= len((args.opened_by or "").strip()) <= 255:
        raise SystemExit("Pass --confirm and a non-empty --opened-by.")
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
