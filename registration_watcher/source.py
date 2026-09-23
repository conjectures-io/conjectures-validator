"""The chain side: who holds each uid on the subnet, as of one finalized block.

`RegistrationSource` is the seam the watcher is written against, so its tests run on a fake
with no node. `BittensorRegistrationSource` is the live one.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from conjectures_subnet.transfers import (
    SUBNET_KEYS,
    BittensorTransferSource,
    ChainUnavailable,
    decode_ss58,
)

# The three storage items one registration is made of. `Keys` is the one the transfer
# watchers already read; the other two are its siblings in the same pallet. Checked against
# Finney: all three answer for netuid 66 keyed by a plain integer uid, `Owner` by hotkey.
BLOCK_AT_REGISTRATION = ("SubtensorModule", "BlockAtRegistration")
OWNER = ("SubtensorModule", "Owner")


@dataclass(frozen=True, slots=True)
class Neuron:
    """One uid's registration: the four fields `RegistrationsDb.publish_snapshot` reads."""

    uid: int
    hotkey: str
    coldkey: str
    # The block this uid registered at, not the block it was observed at, so a row records
    # when the registration became true even if the watcher was down at the time.
    block_at_registration: int


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Every registered uid on one subnet, as of one block."""

    netuid: int
    block: int
    neurons: tuple[Neuron, ...]


class RegistrationSource(Protocol):
    async def finalized_head(self) -> int: ...

    async def snapshot(self, netuid: int, *, block: int) -> Snapshot: ...

    async def block_time(self, block: int) -> dt.datetime: ...

    async def close(self) -> None: ...


class BittensorRegistrationSource(BittensorTransferSource):
    """`RegistrationSource` over a live Subtensor node.

    A subclass rather than a second client, to reuse what the transfer source already gets
    right: one held connection rather than one per read (the public Finney endpoints answer
    429 to the latter), every read bounded in time, and a dropped connection re-established
    on the next call. `finalized_head` and the archive-backed `block` come with it unchanged;
    this adds the one read the transfer watchers never needed.
    """

    async def snapshot(self, netuid: int, *, block: int) -> Snapshot:
        """Every uid on `netuid` at `block`, in three reads rather than three per uid.

        Pinned to `block` on every read, so the three maps describe one instant: a uid that
        re-registers between two unpinned reads could otherwise pair its new hotkey with its
        old registration block, and that row would record a registration that never
        happened.
        """

        async def read(client: Any) -> tuple[Sequence[Any], Sequence[Any]]:
            keys = await client.query_map(SUBNET_KEYS, [netuid], block=block)
            registered = await client.query_map(BLOCK_AT_REGISTRATION, [netuid], block=block)
            return keys, registered

        keys, registered = await self._read(self.network, read)
        hotkeys = {_integer(uid): decode_ss58(_unwrap(value)) for uid, value in keys}
        heights = {_integer(uid): _integer(value) for uid, value in registered}
        uids = sorted(hotkeys)

        async def owners(client: Any) -> Sequence[Any]:
            return await client.query_batch(
                OWNER, [[hotkeys[uid]] for uid in uids], block=block
            )

        coldkeys = await self._read(self.network, owners) if uids else ()
        if len(coldkeys) != len(uids):
            raise ChainUnavailable(
                f"asked for {len(uids)} owners at block {block}, got {len(coldkeys)}"
            )

        neurons = []
        for uid, owner in zip(uids, coldkeys, strict=True):
            if uid not in heights or owner is None:
                # A uid whose sibling entries are missing at the same pinned block is a chain
                # answer this module does not understand. Refusing the whole snapshot keeps a
                # half-described uid out of a table that decides who may submit.
                raise ChainUnavailable(f"uid {uid} is incompletely described at block {block}")
            neurons.append(
                Neuron(
                    uid=uid,
                    hotkey=hotkeys[uid],
                    coldkey=decode_ss58(_unwrap(owner)),
                    block_at_registration=heights[uid],
                )
            )
        return Snapshot(netuid=netuid, block=block, neurons=tuple(neurons))

    async def block_time(self, block: int) -> dt.datetime:
        """When `block` was produced, from the archive connection.

        A registration height is usually far older than a lite node's pruned-state window,
        which is exactly what the inherited `block` already reads from the archive for.
        """
        return (await self.block(block)).timestamp


def _unwrap(value: Any) -> Any:
    # Some SDK paths hand back a wrapper with `.value`; Finney's answers here were bare.
    return getattr(value, "value", value)


def _integer(value: Any) -> int:
    return int(_unwrap(value))


__all__ = [
    "BLOCK_AT_REGISTRATION",
    "OWNER",
    "BittensorRegistrationSource",
    "Neuron",
    "RegistrationSource",
    "Snapshot",
]
