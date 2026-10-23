#!/usr/bin/env python3
"""Give one account a page-filling history of submissions, for testing the account panel.

Development and dev-server only. Each submission goes through the same store calls the website's
session path makes (`open_intent`, `attach_bundle`, `confirm`), so the credit ledger, the intent,
the events and every schema constraint are the real ones. What it skips is the upload, the
bundle admission and the worker: the proof bytes are a placeholder, and the verdicts are written
directly. The credits it spends are granted first as one ADJUSTMENT.

The rows cycle through the states named by `--states`:

    pending      UNVERIFIED; a running worker will claim it and reject the placeholder proof
    build        REJECTED by the verifier, SOLUTION_BUILD_FAILED
    axiom        REJECTED by the verifier, UNPERMITTED_AXIOM
    disqualified VERIFIED, then rejected by a human reviewer
    in_review    VERIFIED and awaiting the reward decision (opt-in, see below)

`in_review` is left out unless named, because it is public: the target's `display_status` turns
`in_review`, the row appears in the review queue and the public in-review feed, and its locked
bounty reserves that much of the treasury until someone rejects it. Nothing is ever approved: an
approval makes a reward payable and closes the target.

Run inside the API container, which has the packages, the task allowlist and DATABASE_URL. The
image does not ship `scripts/`, so copy it in first:

    docker cp scripts/seed_account_submissions.py conjectures_api:/tmp/
    docker exec conjectures_api python /tmp/seed_account_submissions.py \\
        --email someone@example.com --count 60 --confirm

Refuses APP_MODE=PROD unless `--dev-server` says this production-mode server is the dev one.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import itertools
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conjectures_subnet.db import accounts as account_store
from conjectures_subnet.db import credits as credit_store
from conjectures_subnet.db import intents as intent_store
from conjectures_subnet.db import submissions as submission_store
from conjectures_subnet.db.engine import async_session_factory, create_async_db_engine
from conjectures_subnet.db.models import CreditEntryKind, ReviewOutcome, TaskMode
from verifier.hashing import sha256_bytes
from verifier.task_registry import TaskPoolRegistry

STATES = ("pending", "build", "axiom", "disqualified", "in_review")
DEFAULT_STATES = "pending,build,axiom,disqualified"
# One credit at whatever price the server charges; the ledger records the price with the grant.
DEFAULT_CREDIT_PRICE_RAO = 500_000_000
SEED_BOUNTY_RAO = 10_000_000
SEED_POLICY = "seed-account-submissions"
CREATED_BY = "operator:seed_account_submissions.py"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


async def _verdict(session, submission, *, accepted: bool, reason_code: str) -> None:
    started = _now()
    await submission_store.record_verification_result(
        session,
        submission,
        accepted=accepted,
        reason_code=reason_code,
        stage="seed",
        verifier_version=SEED_POLICY,
        container_digest=sha256_bytes(SEED_POLICY.encode()),
        sandbox_mode="none",
        checks={"lean_kernel_passed": accepted},
        report=None,
        started_at=started,
        finished_at=started + dt.timedelta(seconds=42),
    )


async def seed(dsn: str, *, email: str, count: int, tasks, price: int, states) -> None:
    engine = create_async_db_engine(dsn)
    sessions = async_session_factory(engine)
    try:
        async with sessions() as session:
            account = await account_store.find_by_email(session, email)
            if account is None:
                raise SystemExit(
                    f"no account with email {email}; sign up on the site first, or create one "
                    "with scripts/seed_dev_accounts.sql"
                )
            await credit_store.record_entry(
                session,
                account_id=account.id,
                kind=CreditEntryKind.ADJUSTMENT,
                amount_rao=count * price,
                credit_price_rao=price,
                reason=f"{count} credits for seeded test submissions",
                created_by=CREATED_BY,
            )
            await session.commit()
            account_id = account.id

        tally = dict.fromkeys(states, 0)
        for n, (state, task) in enumerate(
            zip(itertools.cycle(states), itertools.cycle(tasks), strict=False)
        ):
            if n == count:
                break
            async with sessions() as session:
                now = _now()
                key = uuid.uuid4()
                # Unique bytes per row: `proofs` is keyed by digest and a proof is submitted once.
                proof = (
                    f"-- seeded by {CREATED_BY}, {key}\n"
                    "theorem target : True := trivial\n"
                ).encode()
                proof_sha256 = sha256_bytes(proof)
                intent, _ = await intent_store.open_intent(
                    session,
                    account_id=account_id,
                    task_id=task.task_id,
                    task_bundle_sha256=task.task_bundle_sha256,
                    credit_price_rao=price,
                    expires_at=now + dt.timedelta(minutes=10),
                    now=now,
                )
                await intent_store.attach_bundle(
                    session,
                    intent,
                    proof_content=proof,
                    proof_sha256=proof_sha256,
                    request_digest=submission_store.session_request_digest(
                        account_id=str(account_id),
                        task_id=task.task_id,
                        task_bundle_sha256=task.task_bundle_sha256,
                        proof_sha256=proof_sha256,
                        idempotency_key=str(key),
                        public_credit=None,
                    ),
                    now=now,
                )
                confirmed = await intent_store.confirm(
                    session,
                    intent.id,
                    account_id,
                    problem_id=task.problem_id,
                    reward_target_id=task.reward_target_id,
                    task_mode=TaskMode(task.mode),
                    signer_signature=None,
                    # Always: a Lean-valid proof without manual review is approved automatically.
                    manual_review_required=True,
                    review_policy_version=SEED_POLICY,
                    bounty_amount_rao=SEED_BOUNTY_RAO,
                    bounty_policy_version=SEED_POLICY,
                    bounty_inputs={"seeded": "true"},
                    now=now,
                    idempotency_key=key,
                )
                submission = confirmed.submission
                if state == "build":
                    await _verdict(session, submission, accepted=False,
                                   reason_code="SOLUTION_BUILD_FAILED")
                elif state == "axiom":
                    await _verdict(session, submission, accepted=False,
                                   reason_code="UNPERMITTED_AXIOM")
                elif state in ("in_review", "disqualified"):
                    await _verdict(session, submission, accepted=True, reason_code="VERIFIED")
                if state == "disqualified":
                    await submission_store.record_human_decision(
                        session,
                        submission.id,
                        decision=ReviewOutcome.REJECTED,
                        reason_code="DUPLICATE_OF_EARLIER_SUBMISSION",
                        reviewer=CREATED_BY,
                        notes_public="Seeded test rejection.",
                    )
                await session.commit()
                tally[state] += 1
        print(f"seeded {count} submissions for {email} ({account_id})")
        for state, number in tally.items():
            print(f"  {state:<13}{number}")
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--email", required=True, help="the account to seed; it must exist")
    parser.add_argument("--count", type=int, default=60, help="submissions to create (default 60)")
    parser.add_argument("--states", default=DEFAULT_STATES,
                        help=f"comma-separated, cycled in order; any of {', '.join(STATES)} "
                             f"(default {DEFAULT_STATES})")
    parser.add_argument("--dev-server", action="store_true",
                        help="allow APP_MODE=PROD: this production-mode server is the dev one")
    parser.add_argument("--confirm", action="store_true", help="required: the rows are permanent")
    args = parser.parse_args()

    if os.environ.get("APP_MODE", "DEV").strip().upper() == "PROD" and not args.dev_server:
        raise SystemExit("refusing APP_MODE=PROD; pass --dev-server if this is the dev server")
    if not 1 <= args.count <= 1000:
        raise SystemExit("--count must be between 1 and 1000")
    states = tuple(s.strip() for s in args.states.split(",") if s.strip())
    if not states or set(states) - set(STATES):
        raise SystemExit(f"--states takes any of {', '.join(STATES)}")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL must be set; run this inside the API container")
    allowlist = os.environ.get("TASK_ALLOWLIST_PATH")
    if not allowlist:
        raise SystemExit("TASK_ALLOWLIST_PATH must be set; run this inside the API container")
    tasks = sorted(TaskPoolRegistry.load(Path(allowlist)).tasks.values(), key=lambda t: t.task_id)
    if not tasks:
        raise SystemExit(f"{allowlist} lists no tasks")
    price = int(os.environ.get("PAYMENT_AMOUNT_RAO", DEFAULT_CREDIT_PRICE_RAO))
    if not args.confirm:
        print(f"would seed {args.count} submissions for {args.email} across {len(tasks)} tasks; "
              "pass --confirm")
        return 1
    asyncio.run(seed(dsn, email=args.email, count=args.count, tasks=tasks, price=price,
                     states=states))
    return 0


if __name__ == "__main__":
    sys.exit(main())
