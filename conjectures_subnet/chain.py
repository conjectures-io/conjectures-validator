from __future__ import annotations

import asyncio
from dataclasses import dataclass
import re
from typing import Any

import bittensor as bt


# Bittensor's canonical ``ExtrinsicResult.extrinsic_id`` renders the extrinsic
# index with at least four digits (for example ``4210031-0002``). Requiring that
# exact spelling matters because payment-reference uniqueness is the replay
# boundary: accepting both padded and unpadded aliases would let one transfer
# be presented as two different database keys.
EXTRINSIC_REFERENCE = re.compile(
    r"^(?P<block>[1-9][0-9]*)-(?P<index>[0-9]{4}|[1-9][0-9]{4,})$"
)


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


def _plain(value: Any) -> Any:
    return getattr(value, "value", value)


def _ss58(value: Any) -> str | None:
    """Extract a decoded AccountId/MultiAddress without guessing bytes."""
    value = _plain(value)
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("Id", "id", "address", "ss58"):
            if key in value:
                return _ss58(value[key])
        if len(value) == 1:
            return _ss58(next(iter(value.values())))
    return None


def _call_args(call: dict[str, Any]) -> dict[str, Any]:
    args: dict[str, Any] = {}
    for item in call.get("call_args") or []:
        item = _plain(item)
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            args[item["name"]] = _plain(item.get("value"))
    return args


def _event_index(record: dict[str, Any]) -> int | None:
    if record.get("extrinsic_idx") is not None:
        return int(record["extrinsic_idx"])
    phase = _plain(record.get("phase"))
    if isinstance(phase, dict):
        for key in ("ApplyExtrinsic", "apply_extrinsic"):
            if key in phase:
                return int(phase[key])
    return None


def _successful_extrinsic(events: Any, index: int) -> bool:
    success = False
    for raw in events or []:
        record = _plain(raw)
        if not isinstance(record, dict) or _event_index(record) != index:
            continue
        event = _plain(record.get("event"))
        if not isinstance(event, dict):
            continue
        module = event.get("module_id") or event.get("module")
        name = event.get("event_id") or event.get("event")
        if module == "System" and name == "ExtrinsicFailed":
            return False
        if module == "System" and name == "ExtrinsicSuccess":
            success = True
    return success


def _event_parts(raw: Any, index: int) -> tuple[str, str, dict[str, Any]] | None:
    record = _plain(raw)
    if not isinstance(record, dict) or _event_index(record) != index:
        return None
    event = _plain(record.get("event"))
    if not isinstance(event, dict):
        return None
    module = event.get("module_id") or event.get("module")
    name = event.get("event_id") or event.get("event")
    attributes = _plain(event.get("attributes"))
    if not isinstance(module, str) or not isinstance(name, str) or not isinstance(attributes, dict):
        return None
    return module, name, attributes


def _successful_multisig_dispatch(
    events: Any,
    index: int,
    *,
    recipient: str,
    amount_rao: int,
) -> str | None:
    """Return the executing multisig account for one exact successful transfer."""

    multisig_accounts: list[str] = []
    transfers: list[tuple[str, str, int]] = []
    for raw in events or []:
        parts = _event_parts(raw, index)
        if parts is None:
            continue
        module, name, attributes = parts
        if module == "Multisig" and name == "MultisigExecuted":
            result = _plain(attributes.get("result"))
            account = _ss58(attributes.get("multisig"))
            if (
                not isinstance(result, dict)
                or "Ok" not in result
                or "Err" in result
                or account is None
            ):
                return None
            multisig_accounts.append(account)
        elif module == "Balances" and name == "Transfer":
            sender = _ss58(attributes.get("from") or attributes.get("source"))
            destination = _ss58(attributes.get("to") or attributes.get("dest"))
            amount = _plain(attributes.get("amount", attributes.get("value")))
            if sender is None or destination is None:
                return None
            try:
                transfers.append((sender, destination, int(amount)))
            except (TypeError, ValueError):
                return None

    if len(multisig_accounts) != 1 or len(transfers) != 1:
        return None
    account = multisig_accounts[0]
    if transfers[0] != (account, recipient, amount_rao):
        return None
    return account


async def _finalized_head(client: Any) -> int:
    blocks = client.blocks(finalized=True)
    try:
        return int((await anext(blocks)).number)
    finally:
        await blocks.aclose()


class BittensorTransferReader:
    """Read and validate one canonical finalized TAO transfer by block-index.

    The class owns no wallet and uses only public read calls. It rejects failed
    extrinsics, transfer-all, wrappers, undecodable addresses, and noncanonical
    references rather than trying to infer payment from ambiguous chain data.
    """

    def __init__(self, network: str):
        self.network = network

    async def finalized_transfer(self, *, reference: str):
        # Imported lazily to keep the chain layer independent of the HTTP layer.
        from submission_api.payments import FinalizedTransfer

        match = EXTRINSIC_REFERENCE.fullmatch(reference)
        if match is None:
            return None
        block = int(match.group("block"))
        index = int(match.group("index"))
        async with bt.Subtensor(self.network) as client:
            if await _finalized_head(client) < block:
                return None
            info = await client.block_info(block)
            if info is None or index >= len(info.extrinsics):
                return None
            decoded = _plain(info.extrinsics[index])
            if not isinstance(decoded, dict):
                return None
            call = _plain(decoded.get("call"))
            if not isinstance(call, dict):
                return None
            if call.get("call_module") != "Balances" or call.get("call_function") not in {
                "transfer_keep_alive",
                "transfer_allow_death",
            }:
                return None
            args = _call_args(call)
            sender = _ss58(decoded.get("address"))
            recipient = _ss58(args.get("dest"))
            amount = _plain(args.get("value"))
            if sender is None or recipient is None:
                return None
            try:
                amount_rao = int(amount)
            except (TypeError, ValueError):
                return None
            events = await client.query(("System", "Events"), block=block)
            if not _successful_extrinsic(events, index):
                return None
            return FinalizedTransfer(
                reference=reference,
                sender=sender,
                recipient=recipient,
                amount_rao=amount_rao,
                block=block,
            )

    async def coldkey_owns_hotkey(
        self, *, coldkey: str, hotkey: str, block: int
    ) -> bool:
        async with bt.Subtensor(self.network) as client:
            owner = await client.neurons.hotkey_owner(hotkey, block=block)
        return owner == coldkey


class BittensorMultisigTransferReader:
    """Read one finalized transfer executed by Substrate's Multisig pallet.

    Unlike ``BittensorTransferReader``, this requires a top-level
    ``Multisig.as_multi`` whose embedded call is one direct Balances transfer,
    plus matching successful ``MultisigExecuted`` and ``Balances.Transfer``
    events. Opening/hash-only approvals, failed inner dispatches, utility
    wrappers, batches, and multiple transfers are rejected.
    """

    def __init__(self, network: str):
        self.network = network

    async def finalized_transfer(self, *, reference: str):
        from submission_api.payments import FinalizedTransfer

        match = EXTRINSIC_REFERENCE.fullmatch(reference)
        if match is None:
            return None
        block = int(match.group("block"))
        index = int(match.group("index"))
        async with bt.Subtensor(self.network) as client:
            if await _finalized_head(client) < block:
                return None
            info = await client.block_info(block)
            if info is None or index >= len(info.extrinsics):
                return None
            decoded = _plain(info.extrinsics[index])
            if not isinstance(decoded, dict):
                return None
            outer = _plain(decoded.get("call"))
            if (
                not isinstance(outer, dict)
                or outer.get("call_module") != "Multisig"
                or outer.get("call_function") != "as_multi"
            ):
                return None
            inner = _plain(_call_args(outer).get("call"))
            if (
                not isinstance(inner, dict)
                or inner.get("call_module") != "Balances"
                or inner.get("call_function")
                not in {"transfer_keep_alive", "transfer_allow_death"}
            ):
                return None
            args = _call_args(inner)
            recipient = _ss58(args.get("dest"))
            amount = _plain(args.get("value"))
            if recipient is None:
                return None
            try:
                amount_rao = int(amount)
            except (TypeError, ValueError):
                return None

            events = await client.query(("System", "Events"), block=block)
            if not _successful_extrinsic(events, index):
                return None
            sender = _successful_multisig_dispatch(
                events,
                index,
                recipient=recipient,
                amount_rao=amount_rao,
            )
            if sender is None:
                return None
            return FinalizedTransfer(
                reference=reference,
                sender=sender,
                recipient=recipient,
                amount_rao=amount_rao,
                block=block,
            )
