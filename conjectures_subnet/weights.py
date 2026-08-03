"""Submit one finalized Subnet 66 treasury weight allocation.

Weights address subnet UIDs, not wallet addresses.  The operator therefore pins
the expected UID, hotkey and coldkey together.  The worker resolves that tuple
at a finalized block before signing and checks it again at the inclusion block,
so a recycled UID cannot silently redirect the validator's allocation.

This is deliberately a one-shot command.  An external scheduler invokes it no
more often than the subnet's weight-setting interval, while the chain remains
the durable source of truth for every successful submission.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import bittensor as bt

from conjectures_subnet.chain import EXTRINSIC_REFERENCE


U16_MAX = (1 << 16) - 1
COMMIT = re.compile(r"^[0-9a-f]{7,40}$")
BLOCK_HASH = re.compile(r"^0x[0-9a-f]{64}$")


@dataclass(frozen=True)
class TreasuryWeightReport:
    schema: str
    network: str
    genesis_hash: str
    netuid: int
    mechid: int
    validator_hotkey: str
    treasury_uid: int
    treasury_hotkey: str
    treasury_coldkey: str
    weight_u16: int
    weights_version_key: int
    commit_reveal: bool
    reveal_round: int | None
    extrinsic_reference: str
    finalized_block: int
    block_hash: str
    finalized_at: str
    bittensor_version: str
    implementation_commit: str

    def json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))


async def _finalized_head(client: Any) -> int:
    blocks = client.blocks(finalized=True)
    try:
        return int((await anext(blocks)).number)
    finally:
        await blocks.aclose()


def _raw_int(value: Any, *, name: str) -> int:
    value = getattr(value, "value", value)
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"chain returned an invalid {name}") from exc


class TreasuryWeightGateway:
    """Resolve, submit and prove one treasury-only weight transaction."""

    def __init__(
        self,
        *,
        network: str,
        netuid: int,
        mechid: int,
        wallet_name: str,
        wallet_hotkey: str,
        wallet_path: Path,
        expected_genesis_hash: str,
        expected_validator_hotkey: str,
        treasury_uid: int,
        treasury_hotkey: str,
        treasury_coldkey: str,
        weights_version_key: int,
        max_fee_rao: int,
        implementation_commit: str,
        allow_owner_controlled_treasury: bool = False,
    ):
        if not network or len(network) > 255 or "\x00" in network:
            raise ValueError("network must contain 1 to 255 non-NUL characters")
        if not 0 < netuid <= U16_MAX:
            raise ValueError("netuid must fit a positive u16")
        # The repository currently launches the subnet's primary mechanism.
        # Refuse to pretend the metagraph validation below covers another one.
        if mechid != 0:
            raise ValueError("only primary mechanism 0 is currently supported")
        if not 0 <= treasury_uid <= U16_MAX:
            raise ValueError("treasury uid must fit a non-negative u16")
        if not 0 <= weights_version_key <= (1 << 64) - 1:
            raise ValueError("weights version key must fit a non-negative u64")
        if not 0 < max_fee_rao <= (1 << 63) - 1:
            raise ValueError("maximum fee must fit a positive PostgreSQL BIGINT")
        expected_genesis_hash = expected_genesis_hash.lower()
        if BLOCK_HASH.fullmatch(expected_genesis_hash) is None:
            raise ValueError("expected genesis hash must be canonical 0x-prefixed hex")
        if COMMIT.fullmatch(implementation_commit) is None:
            raise ValueError(
                "implementation commit must be a lowercase 7 to 40 character hash"
            )

        self.network = network
        self.netuid = netuid
        self.mechid = mechid
        self.treasury_uid = treasury_uid
        self.treasury_hotkey = treasury_hotkey
        self.treasury_coldkey = treasury_coldkey
        self.weights_version_key = weights_version_key
        self.max_fee_rao = max_fee_rao
        self.expected_genesis_hash = expected_genesis_hash
        self.implementation_commit = implementation_commit
        self.allow_owner_controlled_treasury = allow_owner_controlled_treasury
        self.wallet = bt.Wallet(
            name=wallet_name,
            hotkey=wallet_hotkey,
            path=str(wallet_path),
        )
        self.validator_hotkey = self.wallet.hotkeypub.ss58_address
        if self.validator_hotkey != expected_validator_hotkey:
            raise RuntimeError("loaded validator wallet does not match the expected hotkey")

    def _validate_metagraph(self, graph: Any, *, included: bool = False) -> Any:
        phase = "inclusion-block" if included else "pre-signing"
        if graph is None or int(graph.netuid) != self.netuid:
            raise RuntimeError(f"{phase} metagraph for netuid {self.netuid} was unavailable")

        target = graph.by_hotkey(self.treasury_hotkey)
        if target is None:
            raise RuntimeError(f"treasury hotkey is not registered on netuid {self.netuid}")
        if int(target.uid) != self.treasury_uid:
            raise RuntimeError(
                f"treasury hotkey resolved to uid {target.uid}, expected {self.treasury_uid}"
            )
        if target.coldkey != self.treasury_coldkey:
            raise RuntimeError("treasury hotkey is not owned by the expected treasury coldkey")
        if (
            target.coldkey == graph.owner_coldkey
            and not self.allow_owner_controlled_treasury
        ):
            raise RuntimeError(
                "treasury target is controlled by the subnet-owner coldkey; "
                "owner-controlled miner emission may be burned or recycled"
            )

        signer = graph.by_hotkey(self.validator_hotkey)
        if signer is None:
            raise RuntimeError(f"validator hotkey is not registered on netuid {self.netuid}")
        if (
            target.uid != signer.uid
            and not signer.validator_permit
            and self.validator_hotkey != graph.owner_hotkey
        ):
            raise RuntimeError(
                "validator lacks a permit and is not the subnet-owner hotkey; "
                "it cannot set a non-self treasury weight"
            )
        return target

    async def submit(self) -> TreasuryWeightReport:
        policy = bt.Policy(
            max_fee_tao=bt.Balance.from_rao(self.max_fee_rao),
            allowed_netuids=[self.netuid],
        )
        async with bt.Subtensor(self.network, policy=policy) as client:
            snapshot_block = await _finalized_head(client)
            genesis = await client.block_info(0)
            if genesis is None or not genesis.hash:
                raise RuntimeError("chain did not return its genesis hash")
            genesis_hash = str(genesis.hash).lower()
            if BLOCK_HASH.fullmatch(genesis_hash) is None:
                raise RuntimeError("chain returned a noncanonical genesis hash")
            if genesis_hash != self.expected_genesis_hash:
                raise RuntimeError("connected chain does not match the expected genesis hash")

            graph = await client.subnets.metagraph(
                self.netuid,
                block=snapshot_block,
                commitments=False,
            )
            target = self._validate_metagraph(graph)

            min_raw, max_raw, required_version_raw = await asyncio.gather(
                client.query(
                    ("SubtensorModule", "MinAllowedWeights"),
                    [self.netuid],
                    block=snapshot_block,
                ),
                client.query(
                    ("SubtensorModule", "MaxWeightsLimit"),
                    [self.netuid],
                    block=snapshot_block,
                ),
                client.query(
                    ("SubtensorModule", "WeightsVersionKey"),
                    [self.netuid],
                    block=snapshot_block,
                ),
            )
            minimum = _raw_int(min_raw or 0, name="minimum allowed weights")
            maximum = _raw_int(
                U16_MAX if max_raw is None else max_raw,
                name="maximum weight limit",
            )
            required_version = _raw_int(
                required_version_raw or 0,
                name="required weights version key",
            )

            signer = graph.by_hotkey(self.validator_hotkey)
            assert signer is not None  # checked by _validate_metagraph
            is_self_weight = int(signer.uid) == int(target.uid)
            if not is_self_weight and minimum > 1:
                raise RuntimeError(
                    f"subnet requires {minimum} nonzero weights; treasury-only allocation "
                    "would be rejected"
                )
            if not is_self_weight and maximum < U16_MAX:
                raise RuntimeError(
                    f"subnet max weight limit is {maximum}/{U16_MAX}; a 100% treasury "
                    "allocation would be clipped or rejected"
                )
            if self.weights_version_key < required_version:
                raise RuntimeError(
                    f"weights version key {self.weights_version_key} is below required "
                    f"version {required_version}"
                )

            intent = bt.SetWeights(
                netuid=self.netuid,
                mechid=self.mechid,
                uids=[self.treasury_uid],
                weights=[1.0],
                version_key=self.weights_version_key,
            )
            plan = await client.plan(intent, self.wallet)
            if not plan.ok:
                raise RuntimeError("weight plan violated policy: " + "; ".join(plan.violations))
            if plan.warnings:
                raise RuntimeError("weight plan was not exact: " + "; ".join(plan.warnings))

            # No SDK retries.  The call waits for finalization, and an ambiguous
            # exception is left for an operator to inspect before another run.
            result = await client.execute(
                intent,
                self.wallet,
                wait_for_inclusion=True,
                wait_for_finalization=True,
                retries=0,
            )
            result.raise_for_failure()
            if result.extrinsic_id is None:
                raise RuntimeError("finalized weight submission returned no reference")
            match = EXTRINSIC_REFERENCE.fullmatch(result.extrinsic_id)
            if match is None:
                raise RuntimeError("weight submission returned a noncanonical reference")
            included_block = int(match.group("block"))
            if result.block_hash is None:
                raise RuntimeError("finalized weight submission returned no block hash")
            block_hash = str(result.block_hash).lower()
            if BLOCK_HASH.fullmatch(block_hash) is None:
                raise RuntimeError("weight submission returned a noncanonical block hash")

            block_info = await client.block_info(included_block)
            if block_info is None or str(block_info.hash).lower() != block_hash:
                raise RuntimeError("weight reference does not match the finalized block hash")
            if await _finalized_head(client) < included_block:
                raise RuntimeError("weight submission inclusion block is not finalized")

            included_graph = await client.subnets.metagraph(
                self.netuid,
                block=included_block,
                commitments=False,
            )
            self._validate_metagraph(included_graph, included=True)

            timestamp = getattr(block_info, "timestamp", None)
            if not isinstance(timestamp, datetime):
                raise RuntimeError("finalized weight block returned no timestamp")
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)

            reveal_round_raw = result.data.get("reveal_round")
            reveal_round = (
                None
                if reveal_round_raw is None
                else _raw_int(reveal_round_raw, name="reveal round")
            )
            try:
                bittensor_version = importlib.metadata.version("bittensor")
            except importlib.metadata.PackageNotFoundError:
                bittensor_version = "unknown"

            return TreasuryWeightReport(
                schema="conjectures-weight-submission/v1",
                network=self.network,
                genesis_hash=genesis_hash,
                netuid=self.netuid,
                mechid=self.mechid,
                validator_hotkey=self.validator_hotkey,
                treasury_uid=self.treasury_uid,
                treasury_hotkey=self.treasury_hotkey,
                treasury_coldkey=self.treasury_coldkey,
                weight_u16=U16_MAX,
                weights_version_key=self.weights_version_key,
                commit_reveal=reveal_round is not None,
                reveal_round=reveal_round,
                extrinsic_reference=result.extrinsic_id,
                finalized_block=included_block,
                block_hash=block_hash,
                finalized_at=timestamp.astimezone(timezone.utc).isoformat(),
                bittensor_version=bittensor_version,
                implementation_commit=self.implementation_commit,
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="set one finalized Subnet 66 treasury weight allocation"
    )
    parser.add_argument("--network", default="finney")
    parser.add_argument("--netuid", type=int, default=66)
    parser.add_argument("--mechid", type=int, default=0)
    parser.add_argument("--wallet-name", required=True)
    parser.add_argument("--wallet-hotkey", required=True)
    parser.add_argument("--wallet-path", type=Path, required=True)
    parser.add_argument("--expected-genesis-hash", required=True)
    parser.add_argument("--expected-validator-hotkey", required=True)
    parser.add_argument("--treasury-uid", type=int, required=True)
    parser.add_argument("--treasury-hotkey", required=True)
    parser.add_argument("--treasury-coldkey", required=True)
    parser.add_argument("--weights-version-key", type=int, default=0)
    parser.add_argument("--max-fee-rao", type=int, required=True)
    parser.add_argument("--implementation-commit", required=True)
    parser.add_argument(
        "--allow-owner-controlled-treasury",
        action="store_true",
        help=(
            "override the owner-UID burn/recycle guard only after confirming the "
            "target subnet's current emission policy"
        ),
    )
    return parser


async def run(args: argparse.Namespace) -> TreasuryWeightReport:
    gateway = TreasuryWeightGateway(
        network=args.network,
        netuid=args.netuid,
        mechid=args.mechid,
        wallet_name=args.wallet_name,
        wallet_hotkey=args.wallet_hotkey,
        wallet_path=args.wallet_path,
        expected_genesis_hash=args.expected_genesis_hash,
        expected_validator_hotkey=args.expected_validator_hotkey,
        treasury_uid=args.treasury_uid,
        treasury_hotkey=args.treasury_hotkey,
        treasury_coldkey=args.treasury_coldkey,
        weights_version_key=args.weights_version_key,
        max_fee_rao=args.max_fee_rao,
        implementation_commit=args.implementation_commit,
        allow_owner_controlled_treasury=args.allow_owner_controlled_treasury,
    )
    return await gateway.submit()


def main() -> None:
    report = asyncio.run(run(build_parser().parse_args()))
    print(report.json())


if __name__ == "__main__":
    main()
