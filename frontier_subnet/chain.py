from __future__ import annotations

import asyncio
import ipaddress
from dataclasses import dataclass
from typing import Any

import bittensor as bt


@dataclass(frozen=True)
class ChainSnapshot:
    genesis_hash: str
    block: int


class BittensorChainView:
    """Small chain seam used by the miner for finalized round timing."""

    def __init__(self, network: str):
        self.network = network
        self._genesis_hash: str | None = None
        self._lock = asyncio.Lock()

    async def snapshot(self) -> ChainSnapshot:
        async with self._lock:
            async with bt.Subtensor(self.network) as client:
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


async def publish_axon(
    *,
    network: str,
    netuid: int,
    public_ip: str,
    public_port: int,
    wallet: Any,
    protocol: int = 4,
    version: int = 1,
) -> Any:
    """Publish endpoint metadata only when it differs from the metagraph."""

    signer = bt.resolve_signer(wallet, role="hotkey")
    address = ipaddress.ip_address(public_ip)
    desired = (
        f"[{address}]:{public_port}"
        if address.version == 6
        else f"{address}:{public_port}"
    )
    async with bt.Subtensor(network) as client:
        metagraph = await client.subnets.metagraph(netuid, commitments=False)
        if metagraph is None:
            raise RuntimeError(f"subnet {netuid} does not exist on the selected network")
        neuron = metagraph.by_hotkey(signer.ss58_address)
        if neuron is None:
            raise RuntimeError("miner hotkey is not registered on the selected subnet")
        raw_axons = metagraph.raw.get("axons") or []
        if not isinstance(raw_axons, (list, tuple)):
            raw_axons = []
        raw_axon = raw_axons[neuron.uid] if neuron.uid < len(raw_axons) else None
        if (
            neuron.axon == desired
            and isinstance(raw_axon, dict)
            and int(raw_axon.get("ip") or 0) == int(address)
            and int(raw_axon.get("ip_type") or 0) == address.version
            and int(raw_axon.get("port") or 0) == public_port
            and int(raw_axon.get("protocol") or 0) == protocol
            and int(raw_axon.get("version") or 0) == version
        ):
            return None
        result = await client.execute(
            bt.ServeAxon(
                netuid=netuid,
                ip=str(address),
                port=public_port,
                protocol=protocol,
                version=version,
            ),
            wallet,
        )
        result.raise_for_failure()
        return result
