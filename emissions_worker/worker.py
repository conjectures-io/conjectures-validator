from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

import bittensor as bt

from conjectures_subnet.axiom import Severity, get_axiom
from emissions_worker import vector
from emissions_worker.source import NoVectorSource, VectorSource


logger = logging.getLogger("emissions_worker")

# Intentional policy constants. Changing where emissions go requires a reviewed code change,
# not an environment edit on a running validator. The split between the treasury and the
# competitions is the same kind of constant and lives in `allocation.py`.
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
    # Where the competition scores come from. The default preserves this worker's original
    # behaviour exactly: no source means no competition weights means 100% treasury, which
    # is the vector it set before competitions existed.
    source: VectorSource = field(default_factory=NoVectorSource)
    # Reads the metagraph's hotkeys, so a competition's per-hotkey scores can become
    # per-uid weights. Optional: without it there is nothing to map scores onto, and the
    # worker falls back to the treasury rather than guessing at uids.
    hotkeys: Callable[[], Sequence[str]] | None = None

    def plan(self) -> tuple[list[int], list[float]]:
        """The vector to submit this epoch.

        Every failure resolves to the treasury rather than to a skipped epoch: emissions
        cannot be set retroactively, so declining to submit is a decision to pay nobody.
        """
        if self.hotkeys is None:
            return vector.treasury_only(TREASURY_UID)
        try:
            scores = self.source.scores()
            if not scores:
                return vector.treasury_only(TREASURY_UID)
            return vector.combine(
                metagraph_hotkeys=self.hotkeys(),
                treasury_uid=TREASURY_UID,
                competition_scores=scores,
            )
        except Exception:
            # Reading the metagraph or the vector must not cost an epoch. Logged loudly and
            # paid to the treasury, which is where an unallocated share goes anyway.
            logger.exception("could not build the competition vector; paying the treasury")
            get_axiom().exception(
                source="emissions-worker",
                event_type="competition_vector_unavailable",
                severity=Severity.ERROR,
                netuid=NETUID,
            )
            return vector.treasury_only(TREASURY_UID)

    def submit(self, plan: tuple[list[int], list[float]] | None = None) -> ExtrinsicResult:
        """Submit this epoch's weight vector and require chain success."""
        uids, weights = plan if plan is not None else self.plan()
        result = self.client.execute(
            bt.SetWeights(
                netuid=NETUID,
                uids=uids,
                weights=weights,
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
        # Built once, before the retry loop: a vector that could not be fetched is a
        # decision about this epoch, not something to re-attempt against a chain that is
        # perfectly willing to accept it. Retrying here would spend the budget meant for
        # rejected extrinsics on a fetch that will fail the same way each time.
        uids, weights = self.plan()
        treasury_weight = next(
            (w for uid, w in zip(uids, weights, strict=True) if uid == TREASURY_UID), 0.0
        )
        competitors = sum(1 for uid in uids if uid != TREASURY_UID)
        attempt = 0
        while True:
            attempt += 1
            try:
                result = self.submit((uids, weights))
                logger.info(
                    "Subnet %s weights set: treasury UID %s = %.1f%%, %d competitor(s)",
                    NETUID,
                    TREASURY_UID,
                    treasury_weight * 100,
                    competitors,
                )
                get_axiom().info(
                    source="emissions-worker",
                    event_type="weights_set",
                    netuid=NETUID,
                    block=block,
                    treasury_uid=TREASURY_UID,
                    attempt=attempt,
                    treasury_weight=treasury_weight,
                    competitors_paid=competitors,
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
