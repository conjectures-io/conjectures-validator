#!/usr/bin/env python3
"""Cancel/restore an unsigned instruction with audit evidence; no wallet operations.

Provide a JSON request (see docs/PAYOUT_CANCELLATION.md). Default is a rollback-only
preflight; --apply commits. Run on the database host with its normal DB environment.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from conjectures_subnet.db.engine import create_async_db_engine, async_session_factory
from conjectures_subnet.db.payout_instructions import cancel_and_withdraw, change_instruction


async def run(request: dict, *, apply: bool):
    engine = create_async_db_engine()
    try:
        async with async_session_factory(engine)() as session:
            request = dict(request)
            command = request.pop("command")
            request["submission_id"] = uuid.UUID(request["submission_id"])
            if command == "cancel-and-withdraw":
                action, decision = await cancel_and_withdraw(session, **request)
            elif command == "restore":
                action = await change_instruction(session, action="RESTORE", **request)
                decision = None
            else:
                raise ValueError("command must be cancel-and-withdraw or restore")
            receipt = {"applied":apply, "reward_event_id":action.reward_event_id,
                       "action":action.action, "action_id":action.id,
                       "review_decision_id":decision.id if decision else None,
                       "supersedes_id":decision.supersedes_id if decision else None}
            if apply:
                await session.commit()
            else:
                await session.rollback()
            print(json.dumps(receipt, indent=2))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path)
    parser.add_argument("--apply", action="store_true")
    args=parser.parse_args()
    asyncio.run(run(json.loads(args.request.read_text()), apply=args.apply))
