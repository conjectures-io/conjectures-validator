from __future__ import annotations

import argparse
import logging

import bittensor as bt

from conjectures_subnet.axiom import configure_logging, get_axiom
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
    # In place of `logging.basicConfig`. The format keeps this worker's trailing colon after the
    # logger name, which is what its existing log scraping matches on.
    configure_logging(
        source="emissions-worker",
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        log_format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        settings = EmissionsSettings.from_env()
    except SettingsError as exc:
        get_axiom().error(
            source="emissions-worker",
            event_type="service_misconfigured",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        get_axiom().close()
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
    get_axiom().info(
        source="emissions-worker",
        event_type="service_started",
        netuid=NETUID,
        treasury_uid=TREASURY_UID,
        network=settings.network,
        wallet_name=settings.wallet_name,
        wallet_hotkey=settings.wallet_hotkey,
        retry_seconds=settings.retry_seconds,
        mode="once" if args.once else "poll",
    )
    try:
        if args.once:
            worker.run_epoch()
        else:
            worker.run_forever()
    finally:
        get_axiom().info(
            source="emissions-worker", event_type="service_stopped", netuid=NETUID
        )
        client.close()
        # Flush before exit; the transport batches on a background thread.
        get_axiom().close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
