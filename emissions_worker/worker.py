from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

import bittensor as bt

from conjectures_subnet.axiom import Severity, get_axiom


logger = logging.getLogger("emissions_worker")

# Intentional policy constants. Changing where emissions go requires a reviewed code change,
# not an environment edit on a running validator.
NETUID = 66
TREASURY_UID = 121


class ExtrinsicResult(Protocol):
    def raise_for_failure(self) -> Any: ...


class WeightClient(Protocol):
    def wait_for_epoch(self, netuid: int, *, timeout: float | None = None) -> Any: ...

    def execute(
        self, intent: Any, wallet: Any, *, retries: int = 2
    ) -> ExtrinsicResult: ...


@dataclass
class TreasuryWeightWorker:
    client: WeightClient
    wallet: Any
    retry_seconds: float = 30
    sleep: Callable[[float], None] = time.sleep

    def submit(self) -> ExtrinsicResult:
        """Submit the one-weight treasury vector and require chain success."""
        result = self.client.execute(
            bt.SetWeights(
                netuid=NETUID,
                uids=[TREASURY_UID],
                weights=[1.0],
            ),
            self.wallet,
            retries=2,
        )
        result.raise_for_failure()
        return result

    def run_epoch(self) -> tuple[Any, ExtrinsicResult]:
        """Wait for the next observed epoch, then keep trying until its weight is set."""
        epoch = self.client.wait_for_epoch(NETUID)
        block = getattr(epoch, "block", None)
        logger.info(
            "Subnet %s epoch observed at block %s; setting treasury UID %s to 100%%",
            NETUID,
            block if block is not None else "unknown",
            TREASURY_UID,
        )
        get_axiom().info(
            source="emissions-worker",
            event_type="epoch_observed",
            netuid=NETUID,
            block=block,
            treasury_uid=TREASURY_UID,
        )
        attempt = 0
        while True:
            attempt += 1
            try:
                result = self.submit()
                logger.info(
                    "Subnet %s weights set: treasury UID %s = 100%%",
                    NETUID,
                    TREASURY_UID,
                )
                get_axiom().info(
                    source="emissions-worker",
                    event_type="weights_set",
                    netuid=NETUID,
                    block=block,
                    treasury_uid=TREASURY_UID,
                    attempt=attempt,
                )
                return epoch, result
            except KeyboardInterrupt:
                raise
            except Exception:
                logger.exception(
                    "Treasury weight submission failed; retrying in %s seconds",
                    self.retry_seconds,
                )
                # `warning` while it is still retrying: a rejected extrinsic on a busy block is
                # ordinary, and this loop does not give up. The condition worth an alert is a
                # streak of these covering a whole epoch, which is a rate over `attempt`.
                get_axiom().exception(
                    source="emissions-worker",
                    event_type="weights_failed",
                    severity=Severity.WARNING,
                    netuid=NETUID,
                    block=block,
                    treasury_uid=TREASURY_UID,
                    attempt=attempt,
                    retry_seconds=self.retry_seconds,
                )
                self.sleep(self.retry_seconds)

    def run_forever(self) -> None:
        """Set the treasury weight after every observed Subnet 66 epoch."""
        while True:
            try:
                self.run_epoch()
            except KeyboardInterrupt:
                return
            except Exception:
                logger.exception(
                    "Epoch watch failed; reconnecting in %s seconds",
                    self.retry_seconds,
                )
                # `error` here, unlike the retry above: the epoch watch failing means no weight
                # will be set for the epoch that just passed, and an epoch's emissions cannot be
                # set retroactively.
                get_axiom().exception(
                    source="emissions-worker",
                    event_type="weights_failed",
                    netuid=NETUID,
                    treasury_uid=TREASURY_UID,
                    stage="epoch_watch",
                    retry_seconds=self.retry_seconds,
                )
                self.sleep(self.retry_seconds)


__all__ = ["NETUID", "TREASURY_UID", "TreasuryWeightWorker"]
