from __future__ import annotations

import asyncio
from dataclasses import dataclass

import bittensor as bt


@dataclass(frozen=True)
class ChainSnapshot:
    genesis_hash: str
    block: int


class BittensorChainView:
    """Read finalized Subtensor state without coupling it to a submission protocol."""

    def __init__(self, network: str):
        self.network = network
        self._genesis_hash: str | None = None
        self._lock = asyncio.Lock()

    async def snapshot(self) -> ChainSnapshot:
        async with self._lock, bt.Subtensor(self.network) as client:
            finalized_blocks = client.blocks(finalized=True)
            try:
                block = (await anext(finalized_blocks)).number
            finally:
                await finalized_blocks.aclose()
            genesis_hash = self._genesis_hash
            if genesis_hash is None:
                genesis = await client.block_info(0)
                if genesis is None or not genesis.hash:
                    raise RuntimeError("chain did not return a genesis block hash")
                genesis_hash = str(genesis.hash).lower()
                self._genesis_hash = genesis_hash
        return ChainSnapshot(genesis_hash=genesis_hash, block=int(block))
