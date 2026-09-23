"""    python -m competition_worker [--once] [--limit N] [--check]

`--check` is the deployment preflight: it verifies every gate this host serves and probes
the database, then exits. It is what an operator runs after installing the unit, and what
answers "is this host actually able to take work" without taking any.
"""

from __future__ import annotations

import argparse
import logging
import sys

from competition_worker.registry import GateUnavailable
from competition_worker.settings import Settings, SettingsError
from competition_worker.worker import CompetitionWorker, gates_for
from conjectures_subnet.competition import connect

logger = logging.getLogger("competition_worker")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").strip())
    parser.add_argument(
        "--check", action="store_true", help="verify the gates and the database, then exit"
    )
    parser.add_argument(
        "--once", action="store_true", help="drain the queue once, then exit"
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="stop after this many submissions"
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    try:
        settings = Settings.from_env()
    except SettingsError as exc:
        # Configuration, not a crash: print the reason and stop, so a unit that will never
        # work fails at install rather than forty-five minutes into a claimed submission.
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        gates = gates_for(settings)
    except GateUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    store = connect(settings.database_url or None)
    try:
        if not store.ping():
            print("error: the competition database is not reachable", file=sys.stderr)
            return 2
        if args.check:
            print(
                f"ok: {len(gates)} gate(s) verified ({', '.join(sorted(gates))}), "
                "database reachable"
            )
            return 0
        worker = CompetitionWorker(store, settings, gates)
        if args.once:
            result = worker.drain(limit=args.limit)
            logger.info(
                "verified=%d errored=%d abandoned=%d skipped=%s",
                result.verified,
                result.errored,
                result.abandoned,
                result.skipped or "none",
            )
            return 0
        worker.run_forever()
    except KeyboardInterrupt:
        logger.info("interrupted")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
