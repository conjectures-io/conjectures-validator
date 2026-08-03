"""Insert one paid submission straight into the database, so the worker has something to verify.

Development only. This is the submission API's intake path with the two gates that cost money
removed: no payment is confirmed on chain and no hotkey signature is checked. It refuses to run
against APP_MODE=PROD, and it is not a miner client — `scripts/submit_proof.py` is that, and it goes
through the real API.

Everything else is the real path: `create_submission` writes the row, so the digest, payment,
idempotency and duplicate-proof constraints all apply, and `problem_id` and `task_mode` are taken
from the audited allowlist rather than from anything passed in here.

    python3 scripts/seed_submission.py --list
    python3 scripts/seed_submission.py --proof my_attempt.lean --task-id fc-379fc029-...-formalized-v1

The proof is spliced between the task's SolutionHeader and SolutionFooter, inside `namespace
Bounty`, so it must define `theorem target`. The static policy check runs here too, before the row
is written, because finding out from the worker's log costs a round trip.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from conjectures_subnet.db import submissions as store
from conjectures_subnet.db.engine import (
    async_session_factory,
    create_async_db_engine,
)
from verification_worker.tasks import PoolTaskResolver
from verifier.hashing import sha256_bytes
from verifier.static_checks import check_submission
from verifier.task_loader import load_task_bundle
from verifier.task_registry import TaskPoolRegistry

# A well-formed SS58 pair. Nothing signs or pays here, so these only have to satisfy the column
# constraints and be recognisable in a query as not belonging to a real miner.
DEV_HOTKEY = "5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy"
DEV_COLDKEY = "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"


def read_dotenv() -> dict[str, str]:
    path = PROJECT_ROOT / ".env"
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, _, value = stripped.partition("=")
            values[key.strip()] = value.strip()
    return values


def resolve_dsn(explicit: str | None) -> str:
    if explicit:
        return explicit
    env = {**read_dotenv(), **os.environ}
    if env.get("APP_MODE", "DEV").strip().upper() == "PROD":
        raise SystemExit(
            "refusing to run against APP_MODE=PROD: this bypasses payment confirmation and "
            "signature verification, so a row it writes is a submission nobody paid for"
        )
    if env.get("DATABASE_URL"):
        return env["DATABASE_URL"]
    try:
        return (
            f"postgresql+psycopg://{env['POSTGRES_USER']}:{env['POSTGRES_PASSWORD']}"
            f"@127.0.0.1:{env.get('POSTGRES_PORT', '5432')}/{env['POSTGRES_DB']}"
        )
    except KeyError as exc:
        raise SystemExit(f"cannot build a DSN: {exc} is not set in .env, and --dsn was not given")


def load_pool(
    allowlist: Path | None, pool_root: Path | None
) -> tuple[TaskPoolRegistry, PoolTaskResolver]:
    """The allowlist and the pool on disk, cross-checked exactly as the worker does.

    Reads the same TASK_ALLOWLIST_PATH / TASK_POOL_ROOT the worker reads, so submitting against a
    development pool cannot be done by accident: whichever pool the worker was pointed at, point
    this at the same one or it will refuse the task id.
    """
    env = {**read_dotenv(), **os.environ}
    allowlist_path = (
        allowlist
        or (Path(env["TASK_ALLOWLIST_PATH"]) if env.get("TASK_ALLOWLIST_PATH") else None)
        or PROJECT_ROOT / "tasks" / "allowlist.json"
    )
    root = (
        pool_root
        or (Path(env["TASK_POOL_ROOT"]) if env.get("TASK_POOL_ROOT") else None)
        or PROJECT_ROOT / "tasks" / "pool"
    )
    registry = TaskPoolRegistry.load(allowlist_path)
    resolver = PoolTaskResolver.load(allowlist_path=allowlist_path, pool_root=root)
    return registry, resolver


def print_tasks(registry: TaskPoolRegistry, resolver: PoolTaskResolver, mode: str | None) -> None:
    rows = sorted(
        (allowed for allowed in registry.tasks.values() if mode is None or allowed.mode == mode),
        key=lambda allowed: (allowed.problem_id, allowed.mode),
    )
    print(f"{len(rows)} task(s)\n")
    for allowed in rows:
        directory = resolver.tasks[allowed.task_id].task_dir.name
        print(f"{allowed.mode:<14} {allowed.task_id}\n{'':<14} dir={directory}\n")


async def insert(dsn: str, *, allowed, task_dir: Path, proof: bytes, review: bool) -> None:
    engine = create_async_db_engine(dsn)
    sessions = async_session_factory(engine)
    digest = sha256_bytes(proof)
    try:
        async with sessions() as session:
            view = await store.create_submission(
                session,
                store.NewSubmission(
                    hotkey=DEV_HOTKEY,
                    idempotency_key=uuid.uuid4(),
                    request_digest=digest,
                    task_id=allowed.task_id,
                    task_bundle_sha256=allowed.task_bundle_sha256,
                    # From the allowlist, never from the command line: the whole point of these two
                    # columns is that the submitter does not choose which reward they compete for.
                    problem_id=allowed.problem_id,
                    reward_family_id=allowed.reward_family_id,
                    task_mode=store.TaskMode(allowed.mode),
                    proof_content=proof,
                    proof_sha256=digest,
                    payment_reference=f"dev-ref-{uuid.uuid4()}",
                    payment_sender=DEV_COLDKEY,
                    payment_amount_rao=500_000_000,
                    payment_block=1,
                    hotkey_signature=b"\x11" * 64,
                    manual_review_required=review,
                    review_policy_version="dev-v1",
                    bounty_amount_rao=1_000_000_000,
                    bounty_policy_version="flat-v1",
                ),
            )
            await session.commit()
            print(f"submission {view.submission.id}")
            print(f"  task     {allowed.task_id}")
            print(f"  mode     {allowed.mode}")
            print(f"  problem  {allowed.problem_id}")
            print(f"  bundle   {task_dir.name}")
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="seed_submission.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--proof", type=Path, help="the candidate Lean, spliced into namespace Bounty")
    parser.add_argument("--task-id", help="which task to submit against; see --list")
    parser.add_argument("--list", action="store_true", help="show the allowlisted tasks and exit")
    parser.add_argument("--mode", choices=("formalized", "counterexample"), help="filter --list")
    parser.add_argument("--dsn", help="override the DSN built from .env")
    parser.add_argument(
        "--allowlist", type=Path, help="allowlist to resolve against (default TASK_ALLOWLIST_PATH)"
    )
    parser.add_argument(
        "--pool-root", type=Path, help="task pool root (default TASK_POOL_ROOT)"
    )
    parser.add_argument(
        "--manual-review",
        action="store_true",
        help="require human review, so a verified proof is not automatically made reward-eligible",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="insert even if the static policy check rejects the proof, to exercise that path",
    )
    args = parser.parse_args(argv)

    registry, resolver = load_pool(args.allowlist, args.pool_root)

    if args.list:
        print_tasks(registry, resolver, args.mode)
        return 0
    if not args.proof or not args.task_id:
        parser.error("--proof and --task-id are both required unless --list is given")

    allowed = registry.tasks.get(args.task_id)
    if allowed is None:
        parser.error(f"task {args.task_id} is not on the allowlist; run --list")
    task_dir = resolver.tasks[args.task_id].task_dir
    manifest = load_task_bundle(task_dir).manifest

    proof = args.proof.read_bytes()
    if not proof.strip():
        parser.error(f"{args.proof} is empty")
    if len(proof) > manifest.max_submission_bytes:
        parser.error(
            f"{args.proof} is {len(proof)} bytes; this task admits {manifest.max_submission_bytes}"
        )

    # The same check the verifier runs first, so a proof that cannot get past it says so here
    # rather than one poll cycle later in the worker's log.
    static = check_submission(proof.decode("utf-8", errors="replace"), manifest)
    if not static.valid:
        print("static policy check rejects this proof:", file=sys.stderr)
        for violation in static.violations:
            print(f"  - {violation}", file=sys.stderr)
        if not args.force:
            print("\npass --force to insert it anyway and watch the worker reject it", file=sys.stderr)
            return 1
        print(file=sys.stderr)

    asyncio.run(
        insert(
            resolve_dsn(args.dsn),
            allowed=allowed,
            task_dir=task_dir,
            proof=proof,
            review=args.manual_review,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
