from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path
from types import SimpleNamespace

import bittensor as bt
import pytest

from frontier_subnet.auth import MetagraphValidatorPolicy
from frontier_subnet.chain import BittensorChainView, publish_axon
from frontier_subnet.commitments import build_proof_commitment, build_proof_reveal
from frontier_subnet.config import MinerSettings
from frontier_subnet.miner_cli import main
from frontier_subnet.protocol import TaskReference
from frontier_subnet.task_registry import GoldTaskRegistry, TaskNotAllowed
from frontier_subnet.verifier_adapter import ProductionVerifierAdapter
from verifier.gold_pool import (
    EXCLUDED_SOURCE_PREFIXES,
    GOLD_POOL_SCHEMA_VERSION,
    GOLD_POOL_SELECTION,
    MINIMUM_ERDOS_TASKS,
)
from verifier.hashing import sha256_bytes


def _keypair(uri: str = "//Alice"):
    return bt.sp_core.Keypair.create_from_uri(uri)


def test_miner_settings_enforce_chain_and_round_bounds(tmp_path):
    settings = MinerSettings(
        network="local",
        netuid=2,
        database_path=tmp_path / "miner.sqlite3",
        round_blocks=100,
        commit_blocks=20,
        reveal_blocks=80,
    )
    assert settings.round_start(279) == 200
    assert settings.reveal_after_block(200) == 220
    assert settings.expires_at_block(200) == 280

    for update in (
        {"network": " local"},
        {"netuid": 65536},
        {"auth_max_age_seconds": math.nan},
        {"min_validator_tao": math.inf},
        {"commit_blocks": 80, "reveal_blocks": 80},
        {"round_blocks": 79, "reveal_blocks": 80},
    ):
        values = {
            "network": "local",
            "netuid": 2,
            "database_path": tmp_path / "invalid.sqlite3",
            "round_blocks": 100,
            "commit_blocks": 20,
            "reveal_blocks": 80,
            **update,
        }
        with pytest.raises(ValueError):
            MinerSettings(**values)


def test_metagraph_validator_policy_checks_permit_and_tao():
    permitted = _keypair("//Alice").ss58_address
    no_permit = _keypair("//Bob").ss58_address
    low_stake = _keypair("//Charlie").ss58_address
    metagraph = SimpleNamespace(
        neurons=[
            SimpleNamespace(
                hotkey=permitted,
                validator_permit=True,
                tao_stake=bt.tao(10),
            ),
            SimpleNamespace(
                hotkey=no_permit,
                validator_permit=False,
                tao_stake=bt.tao(100),
            ),
            SimpleNamespace(
                hotkey=low_stake,
                validator_permit=True,
                tao_stake=bt.tao(1),
            ),
        ]
    )

    async def load():
        return metagraph

    policy = MetagraphValidatorPolicy(
        network="local",
        netuid=2,
        min_validator_tao=5,
        loader=load,
    )
    assert asyncio.run(policy.allowed(permitted))
    assert not asyncio.run(policy.allowed(no_permit))
    assert not asyncio.run(policy.allowed(low_stake))

    async def unavailable():
        raise RuntimeError("chain offline")

    closed = MetagraphValidatorPolicy(
        network="local",
        netuid=2,
        loader=unavailable,
    )
    assert not asyncio.run(closed.allowed(permitted))


class _FakeResult:
    def __init__(self):
        self.checked = False

    def raise_for_failure(self):
        self.checked = True


class _FakeSubtensor:
    def __init__(self, metagraph, *, block: int = 123):
        self._metagraph = metagraph
        self.finalized_block_number = block
        self.result = _FakeResult()
        self.executed = []
        self.block_info_calls = 0
        self.finalized_stream_calls = 0
        self.subnets = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def block(self):
        raise AssertionError("round timing must not use the reorgable best head")

    async def blocks(self, *, finalized=False):
        assert finalized is True
        self.finalized_stream_calls += 1
        yield SimpleNamespace(number=self.finalized_block_number)

    async def block_info(self, block):
        assert block == 0
        self.block_info_calls += 1
        return SimpleNamespace(hash="0x" + "12" * 32)

    async def metagraph(self, netuid, commitments):
        assert netuid == 2
        assert commitments is False
        return self._metagraph

    async def execute(self, intent, wallet):
        self.executed.append((intent, wallet))
        return self.result


def test_chain_snapshot_uses_finalized_head_and_caches_genesis_hash(monkeypatch):
    clients = []

    def factory(_network):
        client = _FakeSubtensor(None, block=100 + len(clients))
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


def test_publish_axon_compares_full_record_and_canonicalizes_ipv6(monkeypatch):
    keypair = _keypair()
    raw = {
        "ip": int(__import__("ipaddress").ip_address("2001:db8::1")),
        "ip_type": 6,
        "port": 8091,
        "protocol": 4,
        "version": 1,
    }
    neuron = SimpleNamespace(
        uid=0,
        hotkey=keypair.ss58_address,
        axon="[2001:db8::1]:8091",
    )
    metagraph = SimpleNamespace(
        raw={"axons": [raw]},
        by_hotkey=lambda hotkey: neuron if hotkey == keypair.ss58_address else None,
    )
    client = _FakeSubtensor(metagraph)
    monkeypatch.setattr(bt, "Subtensor", lambda _network: client)

    unchanged = asyncio.run(
        publish_axon(
            network="local",
            netuid=2,
            public_ip="2001:0db8:0:0:0:0:0:1",
            public_port=8091,
            wallet=keypair,
        )
    )
    assert unchanged is None
    assert client.executed == []

    changed = asyncio.run(
        publish_axon(
            network="local",
            netuid=2,
            public_ip="2001:db8::1",
            public_port=8091,
            wallet=keypair,
            version=2,
        )
    )
    assert changed is client.result
    assert client.result.checked
    intent, signer = client.executed[0]
    assert intent.ip == "2001:db8::1"
    assert intent.version == 2
    assert signer is keypair


def test_production_verifier_adapter_passes_only_safe_arguments(
    monkeypatch, tmp_path
):
    calls = []

    def fake_verify(**kwargs):
        calls.append(kwargs)
        assert Path(kwargs["submission_path"]).read_bytes() == (
            b"theorem Bounty.target : True := by\n  trivial\n"
        )
        return "verified"

    monkeypatch.setattr("frontier_subnet.verifier_adapter.verify", fake_verify)
    submission = b"theorem Bounty.target : True := by\n  trivial\n"
    task = TaskReference(
        task_id="fc-adapter-test-positive-v1",
        task_bundle_sha256="sha256:" + "ef" * 32,
    )
    salt = b"\x55" * 32
    commitment = build_proof_commitment(
        genesis_hash="0x" + "34" * 32,
        netuid=2,
        round_start_block=100,
        reveal_after_block=110,
        expires_at_block=200,
        task=task,
        submission_sha256=sha256_bytes(submission),
        salt=salt,
        wallet=_keypair(),
    )
    reveal = build_proof_reveal(
        commitment=commitment,
        submission=submission,
        salt=salt,
        wallet=_keypair(),
    )
    adapter = ProductionVerifierAdapter(project_root=tmp_path)
    assert adapter.verify_reveal(task_dir=tmp_path / "task", reveal=reveal) == "verified"
    assert calls == [
        {
            "task_dir": tmp_path / "task",
            "submission_path": calls[0]["submission_path"],
            "project_root": tmp_path,
            "expected_task_sha256": task.task_bundle_sha256,
        }
    ]
    assert set(calls[0]) == {
        "task_dir",
        "submission_path",
        "project_root",
        "expected_task_sha256",
    }


def test_cli_load_is_local_only_and_copies_submission(tmp_path, capsys):
    project_root = Path(__file__).resolve().parents[1]
    task = next(
        path
        for path in sorted((project_root / "tasks/gold").iterdir())
        if path.is_dir()
    )
    source = tmp_path / "Main.lean"
    source.write_text("theorem Bounty.target : True := by\n  trivial\n", encoding="utf-8")
    database = tmp_path / "state" / "miner.sqlite3"

    assert (
        main(
            [
                "load",
                "--database",
                str(database),
                "--task-dir",
                str(task),
                "--submission",
                str(source),
                "--allowlist",
                str(project_root / "gold/allowlist.json"),
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["loaded"] is True
    assert output["submission_bytes"] == source.stat().st_size
    assert database.is_file()


def test_task_registry_rejects_non_deny_or_unknown_schema(tmp_path):
    source_type = "sha256:" + "c" * 64
    base = {
        "schema_version": GOLD_POOL_SCHEMA_VERSION,
        "default": "DENY",
        "repository_commit": "a" * 40,
        "audit_date_utc": "2026-07-23",
        "pool_policy": {
            "classification": "DIRECT_PROP",
            "compiled_target_validation": True,
            "exact_source_type": True,
            "excluded_source_prefixes": list(EXCLUDED_SOURCE_PREFIXES),
            "minimum_erdos_tasks": MINIMUM_ERDOS_TASKS,
            "mode": "formalized",
            "one_task_per_source_path": False,
            "pool_size": 1,
            "retired_source_theorems_sha256": "sha256:" + "d" * 64,
            "selection": GOLD_POOL_SELECTION,
            "selection_audit_sha256": "sha256:" + "e" * 64,
            "source_category": "research open",
            "synthetic_negation": False,
        },
        "allowed_source_theorems": [
            {
                "index": 1,
                "source_path": "FormalConjectures/ErdosProblems/9999.lean",
                "source_type_sha256": source_type,
                "theorem": "Fixture.test",
            }
        ],
        "allowed_task_bundles": [
            {
                "mode": "formalized",
                "source_index": 1,
                "source_path": "FormalConjectures/ErdosProblems/9999.lean",
                "task_id": "fc-test-formalized-v1",
                "task_bundle_sha256": "sha256:" + "b" * 64,
                "target_type_sha256": source_type,
                "theorem": "Fixture.test",
            }
        ],
    }
    valid = tmp_path / "valid.json"
    valid.write_text(json.dumps(base), encoding="utf-8")
    assert len(GoldTaskRegistry.load(valid).tasks) == 1
    for name, update in (
        ("schema", {"schema_version": 1}),
        ("boolean-schema", {"schema_version": True}),
        ("default", {"default": "ALLOW"}),
    ):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps({**base, **update}), encoding="utf-8")
        with pytest.raises(TaskNotAllowed):
            GoldTaskRegistry.load(path)
