"""Adapters that present the store as the seams the workers already expect.

The chain watcher takes a `SnapshotSink` and writes through it, knowing nothing about
the database. This is the production sink: it satisfies the Protocol structurally and
forwards each snapshot to the store. Wiring is a one-liner at the entry point; the
watcher itself is untouched.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable

from .registrations import RegistrationsDb


class DatabaseSnapshotSink:
    """A `chain.sink.SnapshotSink` that persists each snapshot as registration rows.

    `block_time` resolves a registration block height to its on-chain time; it is wired
    from the chain source, so the store can stamp `block_date` without itself depending
    on the chain. It is invoked only for uids whose keys changed, so the archive lookup
    happens only when there is a real registration to record.
    """

    def __init__(
        self, registrations: RegistrationsDb, block_time: Callable[[int], dt.datetime]
    ) -> None:
        self._registrations = registrations
        self._block_time = block_time

    def publish(self, head, metagraph) -> None:
        self._registrations.publish_snapshot(head, metagraph, self._block_time)
