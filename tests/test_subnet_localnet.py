from __future__ import annotations

import asyncio
import os

import bittensor as bt
import pytest

from frontier_subnet.chain import BittensorChainView


pytestmark = [
    pytest.mark.subnet_integration,
    pytest.mark.skipif(
        os.environ.get("BT_SUBNET_INTEGRATION") != "1",
        reason="set BT_SUBNET_INTEGRATION=1 with the pinned localnet running",
    ),
]


def test_pinned_localnet_chain_and_default_subnet_are_reachable():
    endpoint = os.environ.get(
        "BT_LOCALNET_ENDPOINT",
        "ws://127.0.0.1:9944",
    )

    async def inspect():
        snapshot = await BittensorChainView(endpoint).snapshot()
        async with bt.Subtensor(endpoint) as client:
            metagraph = await client.subnets.metagraph(1, commitments=False)
        return snapshot, metagraph

    snapshot, metagraph = asyncio.run(inspect())
    assert snapshot.genesis_hash.startswith("0x")
    assert len(snapshot.genesis_hash) == 66
    assert snapshot.block >= 0
    assert metagraph is not None
    assert metagraph.netuid == 1
