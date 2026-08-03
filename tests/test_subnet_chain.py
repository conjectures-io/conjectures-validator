from __future__ import annotations

import asyncio
from types import SimpleNamespace

import bittensor as bt

from conjectures_subnet.chain import BittensorChainView


class _FakeSubtensor:
    def __init__(self, *, block: int):
        self.block_number = block
        self.block_info_calls = 0
        self.finalized_stream_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def blocks(self, *, finalized=False):
        assert finalized is True
        self.finalized_stream_calls += 1
        yield SimpleNamespace(number=self.block_number)

    async def block_info(self, block):
        assert block == 0
        self.block_info_calls += 1
        return SimpleNamespace(hash="0x" + "12" * 32)


def test_chain_snapshot_uses_finalized_head_and_caches_genesis_hash(monkeypatch):
    clients = []

    def factory(_network):
        client = _FakeSubtensor(block=100 + len(clients))
        clients.append(client)
        return client

    monkeypatch.setattr(bt, "Subtensor", factory)
    view = BittensorChainView("local")
    first = asyncio.run(view.snapshot())
    second = asyncio.run(view.snapshot())

    assert first.genesis_hash == "0x" + "12" * 32
    assert first.block == 100
    assert second.block == 101
    assert sum(client.finalized_stream_calls for client in clients) == 2
    assert sum(client.block_info_calls for client in clients) == 1
