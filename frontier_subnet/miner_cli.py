from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Sequence

import bittensor as bt
import uvicorn
from bittensor.keyfiles import KeyfileError

from frontier_subnet.auth import (
    AuthenticatedCallerPolicy,
    HotkeyAllowlistPolicy,
    MetagraphValidatorPolicy,
)
from frontier_subnet.chain import BittensorChainView, publish_axon
from frontier_subnet.config import MinerSettings
from frontier_subnet.miner import create_miner_app
from frontier_subnet.store import SubmissionStore
from verifier.errors import VerifierError


def default_database_path() -> Path:
    state = os.environ.get("XDG_STATE_HOME")
    root = Path(state).expanduser() if state else Path.home() / ".local" / "state"
    return root / "frontier-math" / "miner.sqlite3"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="frontier-miner",
        description="Serve locally imported Lean submissions; no solver is included.",
    )
    commands = result.add_subparsers(dest="command", required=True)

    load = commands.add_parser("load", help="import one immutable Lean submission")
    load.add_argument("--database", type=Path, default=default_database_path())
    load.add_argument("--task-dir", type=Path, required=True)
    load.add_argument("--submission", type=Path, required=True)
    load.add_argument("--allowlist", type=Path, default=Path("gold/allowlist.json"))

    serve = commands.add_parser("serve", help="serve commitments and reveals")
    serve.add_argument("--database", type=Path, default=default_database_path())
    serve.add_argument("--network", default="finney")
    serve.add_argument("--netuid", type=int, required=True)
    serve.add_argument("--wallet-name", default="default")
    serve.add_argument("--hotkey", default="default")
    serve.add_argument("--wallet-path", default=str(Path.home() / ".bittensor" / "wallets"))
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8091)
    serve.add_argument("--public-ip")
    serve.add_argument("--public-port", type=int)
    serve.add_argument("--publish-axon", action="store_true")
    serve.add_argument("--allow-hotkey", action="append", default=[])
    serve.add_argument("--allow-any-authenticated", action="store_true")
    serve.add_argument("--min-validator-tao", type=float, default=0.0)
    serve.add_argument("--metagraph-refresh-seconds", type=float, default=30.0)
    serve.add_argument("--round-blocks", type=int, default=360)
    serve.add_argument("--commit-blocks", type=int, default=120)
    serve.add_argument("--reveal-blocks", type=int, default=360)
    serve.add_argument("--requests-per-minute", type=int, default=60)
    serve.add_argument("--max-concurrent-requests", type=int, default=16)
    serve.add_argument(
        "--log-level",
        choices=("critical", "error", "warning", "info"),
        default="info",
    )
    return result


def _load(args: argparse.Namespace) -> int:
    store = SubmissionStore(args.database)
    imported = store.import_gold_submission(
        task_dir=args.task_dir,
        submission_path=args.submission,
        allowlist_path=args.allowlist,
    )
    print(
        json.dumps(
            {
                "loaded": True,
                "submission_bytes": imported.submission_bytes,
                "submission_sha256": imported.submission_sha256,
                "task_bundle_sha256": imported.task.task_bundle_sha256,
                "task_id": imported.task.task_id,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _serve(args: argparse.Namespace) -> int:
    if args.allow_any_authenticated and args.allow_hotkey:
        raise ValueError("--allow-any-authenticated and --allow-hotkey cannot be combined")
    settings = MinerSettings(
        network=args.network,
        netuid=args.netuid,
        database_path=args.database,
        host=args.host,
        port=args.port,
        public_ip=args.public_ip,
        public_port=args.public_port,
        min_validator_tao=args.min_validator_tao,
        metagraph_refresh_seconds=args.metagraph_refresh_seconds,
        round_blocks=args.round_blocks,
        commit_blocks=args.commit_blocks,
        reveal_blocks=args.reveal_blocks,
        requests_per_minute=args.requests_per_minute,
        max_concurrent_requests=args.max_concurrent_requests,
    )
    wallet = bt.Wallet(name=args.wallet_name, hotkey=args.hotkey, path=args.wallet_path)
    # Unlock before accepting traffic so a missing/encrypted key cannot fail in a request.
    hotkey = wallet.get_hotkey()
    if args.allow_any_authenticated:
        policy = AuthenticatedCallerPolicy()
    elif args.allow_hotkey:
        policy = HotkeyAllowlistPolicy(frozenset(args.allow_hotkey))
    else:
        policy = MetagraphValidatorPolicy(
            network=settings.network,
            netuid=settings.netuid,
            refresh_seconds=settings.metagraph_refresh_seconds,
            min_validator_tao=settings.min_validator_tao,
        )
    if args.publish_axon:
        if not settings.public_ip:
            raise ValueError("--publish-axon requires --public-ip")
        asyncio.run(
            publish_axon(
                network=settings.network,
                netuid=settings.netuid,
                public_ip=settings.public_ip,
                public_port=settings.advertised_port,
                wallet=hotkey,
            )
        )
    app = create_miner_app(
        settings=settings,
        wallet=hotkey,
        store=SubmissionStore(settings.database_path),
        chain_view=BittensorChainView(settings.network),
        caller_policy=policy,
    )
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        workers=1,
        log_level=args.log_level,
        access_log=False,
        server_header=False,
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "load":
            return _load(args)
        return _serve(args)
    except (
        bt.BittensorError,
        KeyfileError,
        OSError,
        RuntimeError,
        ValueError,
        VerifierError,
    ) as exc:
        raise SystemExit(f"frontier-miner: {exc}") from exc


if __name__ == "__main__":
    main()
