from __future__ import annotations

import asyncio
from types import SimpleNamespace

import bittensor as bt

from conjectures_subnet.chain import (
    BittensorChainView,
    BittensorMultisigTransferReader,
    BittensorTransferReader,
)


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


class _FakePaymentChain:
    def __init__(self, *, finalized=120, success=True):
        self.finalized = finalized
        self.success = success
        self.neurons = self
        self.owner_block = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def blocks(self, *, finalized=False):
        assert finalized is True
        yield SimpleNamespace(number=self.finalized)

    async def block_info(self, block):
        assert block == 100
        return SimpleNamespace(
            extrinsics=[
                None,
                {
                    "address": "5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy",
                    "call": {
                        "call_module": "Balances",
                        "call_function": "transfer_keep_alive",
                        "call_args": [
                            {
                                "name": "dest",
                                "value": {
                                    "Id": "5C4hrfjw9DjXZTzV3MwzrrAr9P1MJhSrvWGWqi1eSuyUpnhM"
                                },
                            },
                            {"name": "value", "value": 500_000_000},
                        ],
                    },
                },
            ]
        )

    async def query(self, descriptor, *, block):
        assert descriptor == ("System", "Events")
        assert block == 100
        outcome = "ExtrinsicSuccess" if self.success else "ExtrinsicFailed"
        return [
            {
                "extrinsic_idx": 1,
                "event": {"module_id": "System", "event_id": outcome, "attributes": {}},
            }
        ]

    async def hotkey_owner(self, hotkey, *, block):
        self.owner_block = block
        return "5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy"


def test_transfer_reader_requires_finality_and_success(monkeypatch):
    chain = _FakePaymentChain()
    monkeypatch.setattr(bt, "Subtensor", lambda _network: chain)
    reader = BittensorTransferReader("local")
    transfer = asyncio.run(reader.finalized_transfer(reference="100-0001"))
    assert transfer is not None
    assert transfer.amount_rao == 500_000_000
    assert transfer.block == 100

    chain.success = False
    assert asyncio.run(reader.finalized_transfer(reference="100-0001")) is None
    chain.success = True
    chain.finalized = 99
    assert asyncio.run(reader.finalized_transfer(reference="100-0001")) is None
    assert asyncio.run(reader.finalized_transfer(reference="0100-0001")) is None
    assert asyncio.run(reader.finalized_transfer(reference="100-1")) is None


def test_transfer_reader_checks_ownership_at_the_payment_block(monkeypatch):
    chain = _FakePaymentChain()
    monkeypatch.setattr(bt, "Subtensor", lambda _network: chain)
    reader = BittensorTransferReader("local")
    owns = asyncio.run(
        reader.coldkey_owns_hotkey(
            coldkey="5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy",
            hotkey="5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
            block=100,
        )
    )
    assert owns is True
    assert chain.owner_block == 100


class _FakeMultisigChain:
    multisig = "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"
    recipient = "5C4hrfjw9DjXZTzV3MwzrrAr9P1MJhSrvWGWqi1eSuyUpnhM"

    def __init__(self, *, finalized=120, outer_success=True, inner_success=True, executed=True):
        self.finalized = finalized
        self.outer_success = outer_success
        self.inner_success = inner_success
        self.executed = executed

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def blocks(self, *, finalized=False):
        assert finalized is True
        yield SimpleNamespace(number=self.finalized)

    async def block_info(self, block):
        assert block == 100
        inner = {
            "call_module": "Balances",
            "call_function": "transfer_keep_alive",
            "call_args": [
                {"name": "dest", "value": {"Id": self.recipient}},
                {"name": "value", "value": 1_000_000_000},
            ],
        }
        return SimpleNamespace(
            extrinsics=[
                None,
                {
                    "address": "5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy",
                    "call": {
                        "call_module": "Multisig",
                        "call_function": "as_multi",
                        "call_args": [
                            {"name": "threshold", "value": 2},
                            {"name": "call", "value": inner},
                        ],
                    },
                },
            ]
        )

    async def query(self, descriptor, *, block):
        assert descriptor == ("System", "Events")
        assert block == 100
        events = [
            {
                "extrinsic_idx": 1,
                "event": {
                    "module_id": "Balances",
                    "event_id": "Transfer",
                    "attributes": {
                        "from": self.multisig,
                        "to": self.recipient,
                        "amount": 1_000_000_000,
                    },
                },
            }
        ]
        if self.executed:
            events.append(
                {
                    "extrinsic_idx": 1,
                    "event": {
                        "module_id": "Multisig",
                        "event_id": "MultisigExecuted",
                        "attributes": {
                            "multisig": self.multisig,
                            "result": {"Ok": None} if self.inner_success else {"Err": "Failed"},
                        },
                    },
                }
            )
        events.append(
            {
                "extrinsic_idx": 1,
                "event": {
                    "module_id": "System",
                    "event_id": "ExtrinsicSuccess" if self.outer_success else "ExtrinsicFailed",
                    "attributes": {},
                },
            }
        )
        return events


def test_multisig_transfer_reader_requires_executed_exact_inner_transfer(monkeypatch):
    chain = _FakeMultisigChain()
    monkeypatch.setattr(bt, "Subtensor", lambda _network: chain)

    direct_reader = BittensorTransferReader("local")
    assert asyncio.run(direct_reader.finalized_transfer(reference="100-0001")) is None

    reader = BittensorMultisigTransferReader("local")
    transfer = asyncio.run(reader.finalized_transfer(reference="100-0001"))
    assert transfer is not None
    assert transfer.sender == chain.multisig
    assert transfer.recipient == chain.recipient
    assert transfer.amount_rao == 1_000_000_000

    chain.inner_success = False
    assert asyncio.run(reader.finalized_transfer(reference="100-0001")) is None
    chain.inner_success = True
    chain.executed = False
    assert asyncio.run(reader.finalized_transfer(reference="100-0001")) is None
