from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import bittensor as bt
import pytest

from conjectures_subnet.weights import TreasuryWeightGateway, U16_MAX


VALIDATOR_HOTKEY = "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"
VALIDATOR_COLDKEY = "5FLSigC9H8QvdvHXuE5mJktFan9Gi35GSQuT2C7B9F1vYue6"
TREASURY_HOTKEY = "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"
TREASURY_COLDKEY = "5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy"
OWNER_HOTKEY = "5C4hrfjw9DjXZTzV3MwzrrAr9P1MJhSrvWGWqi1eSuyUpnhM"
OWNER_COLDKEY = "5GoKvxxmhyJk7AwrTjkwSFHKd89peJeLFF9w1F5Eo2oYjG84"


class _Graph:
    def __init__(
        self,
        *,
        target_uid: int = 7,
        target_coldkey: str = TREASURY_COLDKEY,
        signer_uid: int = 3,
        signer_permit: bool = True,
        owner_hotkey: str = OWNER_HOTKEY,
        owner_coldkey: str = OWNER_COLDKEY,
    ):
        self.netuid = 66
        self.owner_hotkey = owner_hotkey
        self.owner_coldkey = owner_coldkey
        self._neurons = {
            TREASURY_HOTKEY: SimpleNamespace(
                uid=target_uid,
                hotkey=TREASURY_HOTKEY,
                coldkey=target_coldkey,
                validator_permit=False,
            ),
            VALIDATOR_HOTKEY: SimpleNamespace(
                uid=signer_uid,
                hotkey=VALIDATOR_HOTKEY,
                coldkey=VALIDATOR_COLDKEY,
                validator_permit=signer_permit,
            ),
        }

    def by_hotkey(self, hotkey):
        return self._neurons.get(hotkey)


class _Wallet:
    def __init__(self, *, name, hotkey, path):
        self.name = name
        self.hotkey_name = hotkey
        self.path = path
        self.hotkeypub = SimpleNamespace(ss58_address=VALIDATOR_HOTKEY)


class _Client:
    def __init__(self, graph=None):
        self.subnets = self
        self.graph = graph or _Graph()
        self.included_graph = self.graph
        self.minimum = 1
        self.maximum = U16_MAX
        self.required_version = 0
        self.commit_reveal = True
        self.plan_warnings = []
        self.result = bt.ExtrinsicResult(
            success=True,
            message="finalized",
            block_hash="0x" + "22" * 32,
            extrinsic_id="200-0001",
            data={"reveal_round": 987},
        )
        self.planned_intent = None
        self.executed_intent = None
        self.execute_kwargs = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def blocks(self, *, finalized=False):
        assert finalized is True
        yield SimpleNamespace(number=200)

    async def block_info(self, block):
        if block == 0:
            return SimpleNamespace(hash="0x" + "11" * 32)
        assert block == 200
        return SimpleNamespace(
            hash="0x" + "22" * 32,
            timestamp=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
        )

    async def metagraph(self, netuid, *, block, commitments):
        assert netuid == 66
        assert block == 200
        assert commitments is False
        if self.executed_intent is not None:
            return self.included_graph
        return self.graph

    async def commit_reveal_enabled(self, netuid, *, block):
        assert netuid == 66
        assert block == 200
        return self.commit_reveal

    async def query(self, descriptor, params, *, block):
        assert descriptor[0] == "SubtensorModule"
        assert params == [66]
        assert block == 200
        return {
            "MinAllowedWeights": self.minimum,
            "MaxWeightsLimit": self.maximum,
            "WeightsVersionKey": self.required_version,
        }[descriptor[1]]

    async def plan(self, intent, wallet):
        self.planned_intent = intent
        assert wallet.hotkeypub.ss58_address == VALIDATOR_HOTKEY
        return SimpleNamespace(ok=True, warnings=self.plan_warnings, violations=[])

    async def execute(self, intent, wallet, **kwargs):
        self.executed_intent = intent
        self.execute_kwargs = kwargs
        assert wallet.hotkeypub.ss58_address == VALIDATOR_HOTKEY
        return self.result


def _gateway(monkeypatch, client: _Client, **overrides) -> TreasuryWeightGateway:
    monkeypatch.setattr(bt, "Wallet", _Wallet)

    def subtensor(network, *, policy):
        assert network == "local"
        assert policy.allowed_netuids == [66]
        assert policy.max_fee_tao.rao == 1_000_000
        return client

    monkeypatch.setattr(bt, "Subtensor", subtensor)
    values = {
        "network": "local",
        "netuid": 66,
        "mechid": 0,
        "wallet_name": "validator",
        "wallet_hotkey": "validator-hotkey",
        "wallet_path": Path("/run/secrets/wallets"),
        "expected_genesis_hash": "0x" + "11" * 32,
        "expected_validator_hotkey": VALIDATOR_HOTKEY,
        "treasury_uid": 7,
        "treasury_hotkey": TREASURY_HOTKEY,
        "treasury_coldkey": TREASURY_COLDKEY,
        "weights_version_key": 0,
        "max_fee_rao": 1_000_000,
        "implementation_commit": "e0141de",
    }
    values.update(overrides)
    return TreasuryWeightGateway(**values)


def test_submits_exact_treasury_weight_and_returns_finalized_audit(monkeypatch):
    client = _Client()
    report = asyncio.run(_gateway(monkeypatch, client).submit())

    assert client.planned_intent.uids == [7]
    assert client.planned_intent.weights == [1.0]
    assert client.executed_intent is client.planned_intent
    assert client.execute_kwargs == {
        "wait_for_inclusion": True,
        "wait_for_finalization": True,
        "retries": 0,
    }
    assert report.schema == "conjectures-weight-submission/v1"
    assert report.treasury_uid == 7
    assert report.weight_u16 == U16_MAX
    assert report.commit_reveal is True
    assert report.reveal_round == 987
    assert report.extrinsic_reference == "200-0001"
    assert report.finalized_block == 200
    assert report.finalized_at == "2026-08-03T12:00:00+00:00"
    assert '"treasury_uid":7' in report.json()


def test_reports_plaintext_weight_path_without_a_reveal_round(monkeypatch):
    client = _Client()
    client.result = bt.ExtrinsicResult(
        success=True,
        message="finalized",
        block_hash="0x" + "22" * 32,
        extrinsic_id="200-0001",
        data={},
    )
    report = asyncio.run(_gateway(monkeypatch, client).submit())
    assert report.commit_reveal is False
    assert report.reveal_round is None


def test_refuses_treasury_uid_or_coldkey_drift(monkeypatch):
    wrong_uid = _Client(_Graph(target_uid=8))
    with pytest.raises(RuntimeError, match="resolved to uid 8"):
        asyncio.run(_gateway(monkeypatch, wrong_uid).submit())

    wrong_owner = _Client(_Graph(target_coldkey=OWNER_COLDKEY))
    with pytest.raises(RuntimeError, match="expected treasury coldkey"):
        asyncio.run(_gateway(monkeypatch, wrong_owner).submit())


def test_refuses_owner_controlled_treasury_without_explicit_override(monkeypatch):
    client = _Client(_Graph(target_coldkey=OWNER_COLDKEY, owner_coldkey=OWNER_COLDKEY))
    with pytest.raises(RuntimeError, match="burned or recycled"):
        asyncio.run(
            _gateway(
                monkeypatch,
                client,
                treasury_coldkey=OWNER_COLDKEY,
            ).submit()
        )

    report = asyncio.run(
        _gateway(
            monkeypatch,
            client,
            treasury_coldkey=OWNER_COLDKEY,
            allow_owner_controlled_treasury=True,
        ).submit()
    )
    assert report.treasury_coldkey == OWNER_COLDKEY


def test_refuses_nonself_weight_without_validator_authority(monkeypatch):
    client = _Client(_Graph(signer_permit=False))
    with pytest.raises(RuntimeError, match="lacks a permit"):
        asyncio.run(_gateway(monkeypatch, client).submit())


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("minimum", 2, "requires 2 nonzero weights"),
        ("maximum", U16_MAX - 1, "100% treasury allocation"),
        ("required_version", 1, "below required version 1"),
    ],
)
def test_refuses_chain_policy_that_changes_exact_allocation(
    monkeypatch, field, value, message
):
    client = _Client()
    setattr(client, field, value)
    with pytest.raises(RuntimeError, match=message):
        asyncio.run(_gateway(monkeypatch, client).submit())
    assert client.executed_intent is None


def test_self_weight_uses_chain_exemption_for_count_and_limit(monkeypatch):
    client = _Client(_Graph(signer_uid=3, signer_permit=False))
    client.minimum = 8
    client.maximum = 1
    report = asyncio.run(
        _gateway(
            monkeypatch,
            client,
            treasury_uid=3,
            treasury_hotkey=VALIDATOR_HOTKEY,
            treasury_coldkey=VALIDATOR_COLDKEY,
        ).submit()
    )
    assert report.treasury_uid == 3


def test_refuses_plan_warning_and_inclusion_block_uid_change(monkeypatch):
    warning = _Client()
    warning.plan_warnings = ["allocation would be clipped"]
    with pytest.raises(RuntimeError, match="plan was not exact"):
        asyncio.run(_gateway(monkeypatch, warning).submit())
    assert warning.executed_intent is None

    changed = _Client()
    changed.included_graph = _Graph(target_uid=8)
    with pytest.raises(RuntimeError, match="resolved to uid 8"):
        asyncio.run(_gateway(monkeypatch, changed).submit())


def test_requires_canonical_finalized_chain_reference(monkeypatch):
    missing_hash = _Client()
    missing_hash.result = bt.ExtrinsicResult(
        success=True,
        extrinsic_id="200-0001",
        block_hash=None,
    )
    with pytest.raises(RuntimeError, match="no block hash"):
        asyncio.run(_gateway(monkeypatch, missing_hash).submit())

    bad_reference = _Client()
    bad_reference.result = bt.ExtrinsicResult(
        success=True,
        extrinsic_id="200-1",
        block_hash="0x" + "22" * 32,
    )
    with pytest.raises(RuntimeError, match="noncanonical reference"):
        asyncio.run(_gateway(monkeypatch, bad_reference).submit())


def test_constructor_rejects_wrong_wallet_and_nonprimary_mechanism(monkeypatch):
    client = _Client()
    with pytest.raises(RuntimeError, match="expected hotkey"):
        _gateway(
            monkeypatch,
            client,
            expected_validator_hotkey=TREASURY_HOTKEY,
        )
    with pytest.raises(ValueError, match="primary mechanism 0"):
        _gateway(monkeypatch, client, mechid=1)


def test_refuses_unexpected_chain_genesis(monkeypatch):
    client = _Client()
    with pytest.raises(RuntimeError, match="expected genesis hash"):
        asyncio.run(
            _gateway(
                monkeypatch,
                client,
                expected_genesis_hash="0x" + "33" * 32,
            ).submit()
        )
    assert client.planned_intent is None
