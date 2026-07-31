"""Entry point: `python -m verification_worker`.

Everything that can be wrong with the deployment is established before the first claim — the
settings, the task pool, and the identity of the verifier image — so a misconfigured worker
exits instead of spending an attempt per submission discovering the same problem.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from collections.abc import Sequence

from conjectures_subnet.db.engine import async_session_factory, create_async_db_engine
from verification_worker.runner import RunnerFailure, build_runner
from verification_worker.settings import SettingsError, WorkerSettings
from verification_worker.tasks import PoolTaskResolver, TaskNotAllowed
from verification_worker.worker import VerificationWorker

logger = logging.getLogger("verification_worker")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m verification_worker")
    parser.add_argument(
        "--once",
        action="store_true",
        help="drain the queue and exit, instead of polling",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="with --once, stop after this many submissions",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser


async def _run(args: argparse.Namespace) -> int:
    settings = WorkerSettings.from_env()
    # Loaded before any work: it validates every task id, repository commit and bundle digest
    # against the audited allowlist, and a pool that fails that is not one to verify against.
    tasks = PoolTaskResolver.load(
        allowlist_path=settings.task_allowlist_path,
        pool_root=settings.task_pool_root,
    )
    runner = build_runner(settings)
    engine = create_async_db_engine(settings.database_url or None)
    worker = VerificationWorker(
        settings=settings,
        sessions=async_session_factory(engine),
        runner=runner,
        tasks=tasks,
    )
    logger.info(
        "verification worker owner=%s runner=%s tasks=%d",
        settings.owner,
        settings.runner,
        len(tasks.tasks),
    )
    try:
        if args.once:
            processed = await worker.drain(limit=args.limit)
            logger.info("drained %d submissions", len(processed))
            return 0
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signal_name in (signal.SIGINT, signal.SIGTERM):
            # A submission in flight keeps its lease until it expires, so a stopped worker
            # never leaves a row claimed forever — but finishing the current job first means
            # the attempt is not wasted.
            loop.add_signal_handler(signal_name, stop.set)
        await worker.run_forever(stop=stop)
        return 0
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        return asyncio.run(_run(args))
    except (SettingsError, RunnerFailure, TaskNotAllowed) as exc:
        # Configuration, not a submission: say so plainly and do not start.
        logger.error("%s", exc)
        return 2
    except KeyboardInterrupt:  # pragma: no cover - signal handlers cover the normal path
        return 0


if __name__ == "__main__":
    sys.exit(main())
