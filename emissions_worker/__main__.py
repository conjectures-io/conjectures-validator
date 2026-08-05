from __future__ import annotations

import argparse
import logging

import bittensor as bt

from emissions_worker.settings import EmissionsSettings, SettingsError
from emissions_worker.worker import NETUID, TREASURY_UID, TreasuryWeightWorker


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            f"set 100% of Subnet {NETUID} validator weight to treasury UID {TREASURY_UID} "
            "after every epoch"
        )
    )
    parser.add_argument("--once", action="store_true", help="set one epoch, then exit")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        settings = EmissionsSettings.from_env()
    except SettingsError as exc:
        parser.error(str(exc))

    client = bt.Subtensor(network=settings.network)
    wallet = bt.Wallet(
        name=settings.wallet_name,
        hotkey=settings.wallet_hotkey,
        path=str(settings.wallet_path),
    )
    worker = TreasuryWeightWorker(
        client=client,
        wallet=wallet,
        retry_seconds=settings.retry_seconds,
    )
    try:
        if args.once:
            worker.run_epoch()
        else:
            worker.run_forever()
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
