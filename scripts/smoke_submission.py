#!/usr/bin/env python3
"""Insert one submission straight into the queue, to prove a deployed worker claims and verifies.

This writes to whatever database DATABASE_URL names. On a production host that is the production
database, and the row is a real row: it appears in the public feeds like any other submission. It
is left INELIGIBLE for reward and flagged for manual review — the two defaults that keep it out of
the payout path — and `--delete` takes it back out.

    set -a; . /etc/conjectures/verification-worker.env; set +a
    scripts/smoke_submission.py --dry-run          # what it would insert
    scripts/smoke_submission.py                    # insert it
    scripts/smoke_submission.py --status <id>      # the verdict, once the worker has run
    scripts/smoke_submission.py --delete <id>      # remove it again

The proof defaults to a stub that cannot pass, because the point is to exercise claim -> container
-> verdict rather than to earn anything. Pass --proof to send real Lean.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conjectures_subnet.db.models import (  # noqa: E402
    Proof,
    Submission,
    TaskMode,
    VerificationRun,
)

# A well-known Substrate test address. Valid SS58 so the domain accepts it, and recognisably not a
# real miner. `payment_reference` carries the marker that makes these rows greppable.
SMOKE_HOTKEY = "5C4hrfjw9DjXZTzV3MwzrrAr9P1MJhSrvWGWqi1eSuyUpnhM"
SMOKE_POLICY = "smoke-test"
STUB_PROOF = """\
-- Smoke test submission. The expected verdict is REJECTED.
-- It deliberately proves nothing the task asked for: this exists to exercise
-- claim -> container -> verdict on a deployed worker, not to earn a reward.
-- nonce {nonce}
theorem smoke_test_placeholder : True := trivial
"""


def stub_proof() -> bytes:
    """A fresh stub each call, because `submissions.proof_digest` is UNIQUE.

    Without the nonce the second smoke test on any database fails on the unique constraint rather
    than on anything to do with the worker.
    """
    return STUB_PROOF.format(nonce=uuid.uuid4()).encode("utf-8")


def digest_bytes(value: str) -> bytes:
    """The allowlist writes digests as `sha256:<hex>`; the SHA256 domain stores 32 raw bytes."""
    return bytes.fromhex(value.removeprefix("sha256:"))


def pick_bundle(allowlist: Path, task_id: str | None) -> dict:
    bundles = json.loads(allowlist.read_text(encoding="utf-8"))["allowed_task_bundles"]
    if task_id is None:
        return bundles[0]
    for bundle in bundles:
        if bundle["task_id"] == task_id:
            return bundle
    raise SystemExit(f"task_id {task_id!r} is not in {allowlist}")


def build(bundle: dict, proof: bytes) -> tuple[Proof, Submission]:
    proof_digest = hashlib.sha256(proof).digest()
    return (
        Proof(digest=proof_digest, content=proof, byte_length=len(proof)),
        Submission(
            id=uuid.uuid4(),
            hotkey=SMOKE_HOTKEY,
            idempotency_key=uuid.uuid4(),
            request_digest=hashlib.sha256(b"smoke-test-request").digest(),
            task_id=bundle["task_id"],
            task_bundle_sha256=digest_bytes(bundle["task_bundle_sha256"]),
            problem_id=bundle["problem_id"],
            reward_target_id=bundle["reward_target_id"],
            task_mode=TaskMode(bundle["mode"]),
            proof_digest=proof_digest,
            # Exactly one funding source is required. The extrinsic path needs all four fields,
            # and payment_reference is UNIQUE, so it carries a fresh marker each run.
            payment_reference=f"{SMOKE_POLICY}-{uuid.uuid4()}",
            payment_sender=SMOKE_HOTKEY,
            payment_amount_rao=1,
            payment_block=1,
            hotkey_signature=bytes(64),
            review_policy_version=SMOKE_POLICY,
            bounty_amount_rao=1,  # bounty_amount_positive requires > 0
            bounty_policy_version=SMOKE_POLICY,
        ),
    )


def report(session: Session, submission_id: uuid.UUID) -> None:
    submission = session.get(Submission, submission_id)
    if submission is None:
        raise SystemExit(f"no submission {submission_id}")
    print(f"task_id             {submission.task_id}")
    print(f"verification_status {submission.verification_status}")
    print(f"attempts            {submission.verification_attempts}")
    print(f"lease_owner         {submission.verification_lease_owner}")
    print(f"failure_reason      {submission.failure_reason}")
    runs = session.scalars(
        select(VerificationRun)
        .where(VerificationRun.submission_id == submission_id)
        .order_by(VerificationRun.id)
    ).all()
    print(f"verification_runs   {len(runs)}")
    for run in runs:
        print(
            f"  accepted={run.accepted} reason={run.reason_code} stage={run.stage} "
            f"sandbox={run.sandbox_mode} version={run.verifier_version}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", help="default: the first bundle in the allowlist")
    parser.add_argument("--proof", type=Path, help="Lean file to submit; default is a failing stub")
    parser.add_argument(
        "--allowlist",
        type=Path,
        default=Path(os.environ.get("TASK_ALLOWLIST_PATH", "../conjectures-tasks/allowlist.json")),
    )
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    parser.add_argument("--status", metavar="UUID", help="report on an existing submission")
    parser.add_argument("--delete", metavar="UUID", help="remove a submission and its runs")
    parser.add_argument("--dry-run", action="store_true", help="print the row, touch no database")
    args = parser.parse_args()

    if args.dry_run:
        bundle = pick_bundle(args.allowlist, args.task_id)
        proof = args.proof.read_bytes() if args.proof else stub_proof()
        _, submission = build(bundle, proof)
        for column in Submission.__table__.columns:
            value = getattr(submission, column.name, None)
            if value is not None:
                shown = value.hex() if isinstance(value, bytes) else value
                print(f"{column.name:24} {shown}")
        print(f"\nproof: {len(proof)} bytes, sha256 {hashlib.sha256(proof).hexdigest()}")
        return 0

    if not args.database_url:
        raise SystemExit(
            "no DATABASE_URL. On the worker host:\n"
            "  set -a; . /etc/conjectures/verification-worker.env; set +a"
        )
    # The worker's URL is async-flavoured; psycopg drives both, so it is reused verbatim.
    engine = create_engine(args.database_url, future=True)

    with Session(engine) as session, session.begin():
        if args.status:
            report(session, uuid.UUID(args.status))
            return 0
        if args.delete:
            submission_id = uuid.UUID(args.delete)
            if session.get(Submission, submission_id) is None:
                raise SystemExit(f"no submission {submission_id}")
            runs = session.execute(
                delete(VerificationRun).where(VerificationRun.submission_id == submission_id)
            ).rowcount
            session.execute(delete(Submission).where(Submission.id == submission_id))
            # The proof row is deliberately left: `proofs` is meant to be write-once, and on a
            # host where that has been enforced a DELETE here would abort the whole transaction.
            print(f"deleted submission {submission_id} and {runs} verification run(s)")
            print("the proofs row was left in place; it is content-addressed and unreferenced")
            return 0

        bundle = pick_bundle(args.allowlist, args.task_id)
        proof = args.proof.read_bytes() if args.proof else stub_proof()
        proof_row, submission = build(bundle, proof)
        # Held in a plain local: committing expires the instance's attributes, and the session is
        # closed by the time this is printed.
        submission_id = submission.id
        if session.get(Proof, proof_row.digest) is None:
            session.add(proof_row)
        session.add(submission)

    print(f"submission {submission_id}")
    print(f"task_id    {bundle['task_id']}")
    print(f"mode       {bundle['mode']}")
    print("\nwatch the worker pick it up:")
    print("  sudo journalctl -u conjectures-verification-worker -f")
    print(f"\nthen: scripts/smoke_submission.py --status {submission_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
