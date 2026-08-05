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

from conjectures_subnet.axiom import configure_logging, get_axiom
from conjectures_subnet.db.engine import async_session_factory, create_async_db_engine
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from verification_worker.runner import RunnerFailure, build_runner
from verification_worker.settings import SettingsError, WorkerSettings
from verification_worker.tasks import PoolTaskResolver, TaskNotAllowed
from verification_worker.worker import VerificationWorker

logger = logging.getLogger("verification_worker")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m verification_worker")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--once",
        action="store_true",
        help="drain the queue and exit, instead of polling",
    )
    mode.add_argument(
        "--check",
        action="store_true",
        help="validate production config, task pool, verifier image and database, then exit",
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
    if args.check and not settings.production:
        raise SettingsError("--check is a production preflight and requires APP_MODE=PROD")
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
    get_axiom().info(
        source="verification-worker",
        event_type="service_started",
        owner=settings.owner,
        runner=settings.runner,
        tasks=len(tasks.tasks),
        app_mode=settings.app_mode,
        container_digest=runner.container_digest,
        max_attempts=settings.max_attempts,
        # Loud in the dataset rather than only in the container's logs. An operator asking "was
        # this deployment ever able to produce a trustworthy accept" should not have to infer it.
        allow_insecure_sandbox=settings.allow_insecure_sandbox,
        mode="check" if args.check else ("once" if args.once else "poll"),
    )
    try:
        if args.check:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            logger.info(
                "production preflight passed owner=%s image=%s tasks=%d",
                settings.owner,
                runner.container_digest,
                len(tasks.tasks),
            )
            return 0
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
        get_axiom().info(source="verification-worker", event_type="service_stopped")
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    # In place of `logging.basicConfig`. Same stderr format as before, plus the Axiom bridge when
    # AXIOM_TOKEN and AXIOM_DATASET are set — so the existing `logger.*` calls in this worker
    # arrive as events with a severity and this worker's source, without being rewritten.
    configure_logging(source="verification-worker", level=args.log_level)
    try:
        return asyncio.run(_run(args))
    except (SettingsError, RunnerFailure, TaskNotAllowed, SQLAlchemyError) as exc:
        # Configuration, not a submission: say so plainly and do not start.
        logger.error("%s", exc)
        get_axiom().error(
            source="verification-worker",
            event_type="service_misconfigured",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return 2
    except KeyboardInterrupt:  # pragma: no cover - signal handlers cover the normal path
        return 0
    finally:
        # The transport batches on a background thread, so an exit that does not flush loses the
        # last few seconds — which for a worker that just refused to start is the only interesting
        # part. `atexit` would catch it too; this makes the ordering explicit.
        get_axiom().close()


if __name__ == "__main__":
    sys.exit(main())
