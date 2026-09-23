"""The loop: follow the finalized head, record registration changes, never die on one bad pass.

Async on the chain side, like the other watchers, because that is what the platform's
Subtensor client is. The store underneath is the competition database's synchronous
`RegistrationsDb`, shared with the gate worker, so its calls run in a thread rather than
growing an async twin of the one method that decides who may submit.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from pathlib import Path
from typing import cast

from conjectures_subnet.axiom import get_axiom
from conjectures_subnet.competition.registrations import RegistrationsDb, changed_rows
from registration_watcher.source import RegistrationSource

logger = logging.getLogger("registration_watcher")

SOURCE = "registration-watcher"


class RegistrationWatcher:
    def __init__(
        self,
        *,
        source: RegistrationSource,
        registrations: RegistrationsDb,
        netuid: int,
        poll_seconds: float,
        heartbeat: Path | None = None,
        watcher_id: str = "registration-watcher",
    ) -> None:
        self._source = source
        self._registrations = registrations
        self._netuid = netuid
        self._poll_seconds = poll_seconds
        self._heartbeat = heartbeat
        self._watcher_id = watcher_id
        # Registration height -> on-chain time. An archive read each, and a height's time
        # never changes, so each is asked for once per process. Bounded in practice by the
        # number of registration events the subnet has ever had.
        self._block_times: dict[int, dt.datetime] = {}
        self.last_block: int | None = None

    async def step(self) -> int:
        """One pass. Returns how many registration rows it wrote.

        `last_block` advances only after the rows are committed, so a pass that fails part
        way -- an archive read that times out, a database that refuses -- is retried on the
        next pass rather than skipped. Skipping it would lose a registration for good, and a
        lost registration is a miner who paid and cannot submit.
        """
        head = await self._source.finalized_head()
        if self.last_block is not None and head <= self.last_block:
            self._beat()
            return 0

        snapshot = await self._source.snapshot(self._netuid, block=head)

        # Resolve the timestamps of exactly the rows this snapshot will change, before the
        # write rather than inside it: the archive read is async and `publish_snapshot` asks
        # for times synchronously, inside its transaction. That transaction re-diffs against
        # the table, so this read only decides which heights to fetch -- if the table moved
        # in between and a height is missing, the lookup raises, the transaction rolls back
        # and the next pass starts over.
        latest = await asyncio.to_thread(self._registrations.latest_keys)
        wanted = {cast(int, row["block"]) for row in changed_rows(latest, snapshot.neurons)}
        for height in sorted(wanted - self._block_times.keys()):
            self._block_times[height] = await self._source.block_time(height)

        written = await asyncio.to_thread(
            self._registrations.publish_snapshot,
            head,
            snapshot,
            self._block_times.__getitem__,
        )
        self.last_block = head
        if written:
            get_axiom().info(
                source=SOURCE,
                event_type="registrations_recorded",
                watcher_id=self._watcher_id,
                netuid=self._netuid,
                block=head,
                recorded=written,
                # The first pass on an empty table records the whole subnet; after that a
                # non-zero count is a real registration event.
                initial_load=not latest,
            )
        self._beat()
        return written

    async def run_forever(self, *, stop: asyncio.Event) -> None:
        """Pass after pass until `stop` is set. One failed pass never ends the loop."""
        while not stop.is_set():
            try:
                await self.step()
            except Exception as exc:  # noqa: BLE001 - one bad pass must not kill the watcher
                logger.exception("registration pass failed")
                get_axiom().error(
                    source=SOURCE,
                    event_type="unexpected_error",
                    watcher_id=self._watcher_id,
                    last_block=self.last_block,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._poll_seconds)
            except TimeoutError:
                pass

    def _beat(self) -> None:
        """Record that a pass completed, for the health probe.

        A live process is not evidence of a working one: a watcher whose every pass fails
        stays up forever. The probe reads this file's age, so it goes stale exactly when the
        passes stop succeeding.
        """
        if self._heartbeat is None:
            return
        try:
            self._heartbeat.touch()
        except OSError:
            logger.warning("could not write the heartbeat at %s", self._heartbeat, exc_info=True)


__all__ = ["SOURCE", "RegistrationWatcher"]
