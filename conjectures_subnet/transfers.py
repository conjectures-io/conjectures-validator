"""Reading incoming transfers off finalized Subtensor state. Read-only, holds no keys.

`submission_api/payments.py` describes the reader the payment component needs and states its
constraints: it answers "was this transfer finalized", it holds no wallet keys, and it signs
nothing. This is that reader, widened from one lookup by reference to a forward scan, because the
watcher's question is "what arrived" rather than "did this particular thing arrive".

**Events, not extrinsics.** A transfer is read from the `Balances.Transfer` events in
`System.Events`, which is what `ETL-Pipelines/bittensor_info/coldkey/_bt.py` does and for the same
two reasons. An extrinsic list says what was *attempted* — a `balances.transfer_allow_death` that
ran out of funds is in there and moved nothing — and it misses transfers made from inside another
call, which is every `utility.batch`, `proxy.proxy` and `sudo.sudo`. The event is emitted only when
the balance actually moved, whatever emitted it.

**Finality is the boundary.** Every read is against a block at or below the finalized head, taken
from the `blocks(finalized=True)` subscription rather than `block()`, which answers with the best
block and can be reorganised out from under a credit. `conjectures_subnet/chain.py` reads the same
stream the same way.

**A block is identified by its number here, not its hash.** That is sound only because of the
paragraph above: below the finalized head there is exactly one block per height.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol, TypeVar, cast

import bittensor as bt
from bittensor.sp_core import ss58_encode

logger = logging.getLogger("conjectures_subnet.transfers")

T = TypeVar("T")

# The pallet and event that mean free TAO moved between two accounts.
TRANSFER_EVENT = ("Balances", "Transfer")
# Storage holding one block's events. Read with `query`, because bittensor 11 exposes no
# events accessor of its own — `Client.block_info` decodes extrinsics, which is the wrong
# source for the reason in the module docstring.
SYSTEM_EVENTS = ("System", "Events")
# One block's own time, from the `Timestamp.set` inherent. A single storage read, and
# deliberately not `block_info`: that fetches and decodes every extrinsic in the block to
# arrive at the same millisecond.
BLOCK_TIMESTAMP = ("Timestamp", "Now")
# Which hotkey is registered at a (netuid, uid). The watcher's startup identity check.
SUBNET_KEYS = ("SubtensorModule", "Keys")
# The coldkey that owns a hotkey. What proves a payer is entitled to submit under the hotkey they
# named, so a miner cannot cite somebody else's transfer.
HOTKEY_OWNER = ("SubtensorModule", "Owner")

# The canonical identity of one transfer: `block-extrinsic[-event]`.
#
# Positional rather than a hash, because a substrate node can resolve a position and cannot resolve
# an extrinsic hash — "get extrinsic by hash" is an indexer's service (Subscan, Taostats), not an
# RPC. A reference this validator cannot resolve against a node of its own is one it would have to
# take somebody else's word for.
#
# The event index is optional on input and always present on output: `finalized_transfer` resolves
# a two-part reference and returns an `IncomingTransfer`, whose `.reference` is the three-part form.
# Storing the canonical form is what makes the uniqueness constraint on a payment reference bite —
# two spellings of one transfer must not fund two submissions.
REFERENCE = re.compile(r"^(\d{1,12})-(\d{1,6})(?:-(\d{1,6}))?$")

# Bittensor uses the generic substrate SS58 format. Pinned as a literal rather than imported:
# the constant has moved between bittensor major versions, and its value has not.
SS58_FORMAT = 42

# The all-zero AccountId. `SubtensorModule.Owner` answers with this for a hotkey nobody has
# registered, rather than with nothing — verified against Finney. Without treating it as "no owner",
# an unregistered hotkey would appear to be owned, and by an address no one holds the key to.
ZERO_ACCOUNT = "5C4hrfjw9DjXZTzV3MwzrrAr9P1MJhSrvWGWqi1eSuyUpnhM"

# Twelve seconds a block, so a day is about this many. The bisection's opening guess, not a
# constant anything depends on being right — it only decides how many probes the search takes.
BLOCKS_PER_DAY = 7200


class ChainUnavailable(RuntimeError):
    """The chain could not answer. Transient by assumption: the caller retries."""


class AmbiguousReference(ValueError):
    """A reference names an extrinsic that emitted more than one transfer.

    Not transient and not resolvable by guessing. A `utility.batch` can move TAO to the treasury
    twice, and picking either one would be deciding which payment a miner meant — so the caller is
    told to name the event index instead. Fail-closed on money.
    """


@dataclass(frozen=True)
class TransferReference:
    """A parsed `block-extrinsic[-event]`.

    `event_index` is None when the caller named only the extrinsic, which is what a block explorer
    shows. Resolving it needs the block, so it happens in `finalized_transfer`.
    """

    block: int
    extrinsic_index: int
    event_index: int | None = None

    def __str__(self) -> str:
        if self.event_index is None:
            return f"{self.block}-{self.extrinsic_index}"
        return f"{self.block}-{self.extrinsic_index}-{self.event_index}"


def parse_reference(raw: str) -> TransferReference | None:
    """A payment reference, or None if it is not one this validator can resolve.

    None rather than an exception: the caller turns it into a refusal with a reason code, and an
    unparseable reference is a client mistake rather than an exceptional condition.
    """
    matched = REFERENCE.fullmatch(raw.strip())
    if matched is None:
        return None
    event = matched.group(3)
    return TransferReference(
        block=int(matched.group(1)),
        extrinsic_index=int(matched.group(2)),
        event_index=None if event is None else int(event),
    )


@dataclass(frozen=True)
class ObservedBlock:
    """One finalized block's identity and its own idea of when it happened."""

    number: int
    hash: str
    # From the block's `Timestamp.set` inherent — the chain's time, not the reader's clock.
    timestamp: dt.datetime


@dataclass(frozen=True)
class IncomingTransfer:
    """One `Balances.Transfer` event whose recipient is the address being watched."""

    block: int
    block_timestamp: dt.datetime
    extrinsic_index: int
    event_index: int
    sender: str  # the coldkey that paid
    recipient: str
    amount_rao: int

    @property
    def reference(self) -> str:
        """The canonical identity of this transfer, `block-extrinsic-event`.

        The event index and not just the extrinsic. A `utility.batch` emits several Transfer
        events under one extrinsic, and a reference that named only the extrinsic would make two
        payments indistinguishable — and `deposits.extrinsic_reference` is unique, so the second
        would be silently refused rather than credited.
        """
        return f"{self.block}-{self.extrinsic_index}-{self.event_index}"


class TransferSource(Protocol):
    """What the watcher needs of a chain. Narrow on purpose, so a fake is a small class."""

    async def finalized_head(self) -> int: ...

    async def block(self, number: int) -> ObservedBlock: ...

    async def transfers_to(
        self, *, recipient: str, block: int
    ) -> Sequence[IncomingTransfer]: ...

    async def hotkey_at(self, *, netuid: int, uid: int) -> str | None: ...


class PaymentSource(TransferSource, Protocol):
    """What the API's payment verifier additionally needs.

    Separate from `TransferSource` because the two callers ask different questions. The watcher
    sweeps forwards and only cares about one address; the payment verifier resolves one reference
    and must see every transfer in that extrinsic — including one to the wrong recipient, so it can
    say *that* rather than "no such transfer".
    """

    async def transfers_in(self, *, block: int) -> Sequence[IncomingTransfer]: ...

    async def coldkey_of(self, *, hotkey: str) -> str | None: ...


def decode_ss58(value: Any) -> str:
    """An SS58 address from whatever the runtime decoder produced.

    bittensor 11 against Finney returns event account fields already encoded, so the string path
    is the one that runs. The tuple paths are the shape a SCALE-decoded `AccountId32` takes —
    flat, or wrapped once — and they are handled because which one appears depends on the
    metadata version the node is running, not on anything this code controls. Getting it wrong
    would mean crediting a nonsense address.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, bytes | bytearray):
        return ss58_encode(bytes(value), SS58_FORMAT)
    if isinstance(value, tuple | list):
        inner = value
        if len(value) == 1 and isinstance(value[0], tuple | list):
            inner = value[0]
        return ss58_encode(bytes(inner), SS58_FORMAT)
    raise ChainUnavailable(f"cannot read an SS58 address from {type(value).__name__}")


def _event_body(record: Any) -> dict[str, Any]:
    """The `(module_id, event_id, attributes)` triple, whichever envelope it arrived in.

    bittensor 11 returns each record with the fields both at the top level and nested under
    `event`; older decoders returned only the nested form, and pre-dTAO ones exposed
    `value_serialized`. Reading through this rather than picking one shape means a runtime
    upgrade cannot turn every transfer invisible — which is a failure that looks exactly like
    "nobody paid today".
    """
    if hasattr(record, "value_serialized"):
        record = record.value_serialized
    if not isinstance(record, dict):
        raise ChainUnavailable(f"unexpected event record: {type(record).__name__}")
    if "module_id" in record and "event_id" in record:
        return record
    nested = record.get("event")
    if isinstance(nested, dict):
        return nested
    raise ChainUnavailable("event record carries no module_id/event_id")


def _attribute(attributes: Any, key: str, index: int) -> Any:
    """One event attribute, by name if the decoder named them and by position if it did not."""
    if isinstance(attributes, dict):
        return attributes[key]
    if isinstance(attributes, tuple | list):
        return attributes[index]
    raise ChainUnavailable(
        f"cannot read attribute {key!r} from {type(attributes).__name__}"
    )


def transfers_in_events(
    records: Sequence[Any],
    *,
    recipient: str | None,
    block: int,
    block_timestamp: dt.datetime,
) -> list[IncomingTransfer]:
    """Transfers among one block's event records, optionally filtered by recipient.

    Split out from the client so the decoding is testable without a chain, which is the part that
    breaks on a runtime upgrade.

    `recipient=None` returns every transfer in the block. That is what the payment verifier needs:
    given a reference, it has to be able to report "that transfer went somewhere else" rather than
    "no such transfer", which are different refusals for the miner reading them.

    With a recipient set, a record that does not match is skipped without decoding anything else. A
    block carries a hundred-odd events and all but a handful are irrelevant, so that is the hot
    path the watcher runs once per block forever.
    """
    found: list[IncomingTransfer] = []
    for position, record in enumerate(records):
        body = _event_body(record)
        if (body.get("module_id"), body.get("event_id")) != TRANSFER_EVENT:
            continue
        attributes = body.get("attributes")
        to = decode_ss58(_attribute(attributes, "to", 1))
        if recipient is not None and to != recipient:
            continue
        sender = decode_ss58(_attribute(attributes, "from", 0))
        amount = int(_attribute(attributes, "amount", 2))
        if amount <= 0:
            # The domain refuses a non-positive amount and a zero-value transfer buys nothing.
            # Skipped rather than raised: it is a valid extrinsic, just not a payment.
            continue
        # `extrinsic_idx` is absent on events from a block's initialisation or finalisation
        # phase, which cannot be a user transfer — but a runtime that changes that must not make
        # the reference ambiguous, so the enumeration position is the fallback and the pair
        # (extrinsic, event) stays unique within the block either way.
        outer = record if isinstance(record, dict) else {}
        extrinsic_index = outer.get("extrinsic_idx")
        found.append(
            IncomingTransfer(
                block=block,
                block_timestamp=block_timestamp,
                extrinsic_index=int(extrinsic_index) if extrinsic_index is not None else 0,
                event_index=position,
                sender=sender,
                recipient=to,
                amount_rao=amount,
            )
        )
    return found


class BittensorTransferSource:
    """`TransferSource` over a live Subtensor node.

    **One connection, held open.** Not the connect-per-call shape of `BittensorChainView`, which
    is right for a snapshot taken now and then and wrong here: this reads every block, and the
    public Finney endpoints answer HTTP 429 to a client that opens a websocket per read. The
    connection is re-established on the next call after any failure, so a node restart costs one
    failed pass rather than a dead worker.

    The lock serialises calls because the underlying client is not safe to drive concurrently.
    Serialising is not a limitation the watcher feels: it reads blocks in order.

    Two networks, one connection each, opened lazily. Resolving the genesis timestamp bisects
    over blocks usually far outside a lite node's pruned-state window, so that search may need an
    archive endpoint; following the head does not. Same split as ninja-validator's chain watcher.
    """

    def __init__(self, network: str, *, archive_network: str | None = None) -> None:
        self.network = network
        self.archive_network = archive_network or network
        self._lock = asyncio.Lock()
        self._clients: dict[str, Any] = {}

    async def close(self) -> None:
        """Close every held connection. Idempotent, and safe to call after a failure."""
        async with self._lock:
            for network in list(self._clients):
                await self._drop(network)

    async def finalized_head(self) -> int:
        """The highest block that can no longer be reorganised away.

        The `blocks(finalized=True)` subscription and not `block()`. `block()` answers with the
        best block, which can be reorganised out from under a credit — and a credit issued for a
        transfer that then does not exist is money given away.
        """

        async def read(client: Any) -> int:
            # bittensor declares blocks() as AsyncIterator, but it is an async generator
            # function, so the object does have aclose(). Closing it is what cancels the block
            # subscription deterministically instead of at collection time — which on a held
            # connection matters more than it does on one about to be closed anyway. Same
            # reasoning as conjectures_subnet/chain.py.
            stream = cast(
                AsyncGenerator[bt.BlockHeader, None], client.blocks(finalized=True)
            )
            try:
                header = await anext(stream)
            finally:
                await stream.aclose()
            return int(header.number)

        return await self._read(self.network, read)

    async def block(self, number: int) -> ObservedBlock:
        """One block's identity and its own timestamp, from the archive connection.

        `block_info` here rather than the cheaper storage read `transfers_to` uses, because this
        is also where the hash comes from — and it is called by the bisection, which runs a few
        dozen times in the life of a deployment.
        """

        async def read(client: Any) -> ObservedBlock:
            info = await client.block_info(number)
            if info is None or not info.hash or info.timestamp is None:
                raise ChainUnavailable(f"chain returned no usable block at {number}")
            return ObservedBlock(
                number=int(info.number),
                hash=str(info.hash),
                timestamp=_aware(info.timestamp),
            )

        return await self._read(self.archive_network, read)

    async def transfers_to(
        self, *, recipient: str, block: int
    ) -> Sequence[IncomingTransfer]:
        """Every transfer into `recipient` in one finalized block.

        One storage read for the overwhelmingly common case of a block with nothing in it for us,
        and a second only when something arrived. At one block every twelve seconds forever, the
        read that is skipped is the one that matters.
        """
        return await self._transfers(block=block, recipient=recipient)

    async def transfers_in(self, *, block: int) -> Sequence[IncomingTransfer]:
        """Every transfer in one block, whoever it went to.

        The payment verifier's read. It resolves a reference the miner supplied, so it must be able
        to find a transfer that went to the wrong address and say so, rather than reporting that
        the extrinsic does not exist.
        """
        return await self._transfers(block=block, recipient=None)

    async def _transfers(
        self, *, block: int, recipient: str | None
    ) -> Sequence[IncomingTransfer]:
        async def read_events(client: Any) -> Any:
            return await client.query(SYSTEM_EVENTS, block=block)

        records = await self._read(self.network, read_events)
        if records is None:
            raise ChainUnavailable(f"block {block} returned no event list")
        # Stamped below. Decoding first means a block with nothing in it costs no second read, and
        # the pure function stays the one thing the decode tests exercise.
        found = transfers_in_events(
            records, recipient=recipient, block=block, block_timestamp=_EPOCH
        )
        if not found:
            return ()

        async def read_timestamp(client: Any) -> dt.datetime:
            millis = await client.query(BLOCK_TIMESTAMP, block=block)
            if millis is None:
                raise ChainUnavailable(f"block {block} returned no timestamp")
            return dt.datetime.fromtimestamp(int(millis) / 1000, tz=dt.UTC)

        stamp = await self._read(self.network, read_timestamp)
        return [replace(item, block_timestamp=stamp) for item in found]

    async def hotkey_at(self, *, netuid: int, uid: int) -> str | None:
        """The hotkey registered at one (netuid, uid), or None if that uid is empty."""

        async def read(client: Any) -> Any:
            return await client.query(SUBNET_KEYS, [netuid, uid])

        value = await self._read(self.network, read)
        return None if value is None else decode_ss58(value)

    async def coldkey_of(self, *, hotkey: str) -> str | None:
        """The coldkey that owns a hotkey, or None if the chain knows no owner for it.

        `SubtensorModule.Owner` is the authority on this, and it is what lets the payment verifier
        establish that the coldkey which paid is entitled to submit under the hotkey named — so a
        miner cannot cite a transfer somebody else made.
        """

        async def read(client: Any) -> Any:
            return await client.query(HOTKEY_OWNER, [hotkey])

        value = await self._read(self.network, read)
        if value is None:
            return None
        owner = decode_ss58(value)
        # An unregistered hotkey reads back as the zero account rather than as absent. Treated as
        # "no owner", because the alternative is that anyone who can produce the zero account's
        # address inherits every unregistered hotkey.
        return None if owner == ZERO_ACCOUNT else owner

    # --- connection handling ------------------------------------------------------------

    async def _read(
        self, network: str, operation: Callable[[Any], Awaitable[T]]
    ) -> T:
        """Run one chain read on the held connection, dropping it if it fails.

        Only chain I/O goes in here. Decoding happens in the caller, so a runtime shape this
        module cannot read is not mistaken for a broken socket and does not cost a reconnect.
        """
        async with self._lock:
            client = await self._connect(network)
            try:
                return await operation(client)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # The connection is suspect either way, and a stale one would fail every
                # subsequent read the same way. Dropped here; the next call reconnects.
                await self._drop(network)
                if isinstance(exc, ChainUnavailable):
                    raise
                raise ChainUnavailable(f"chain read failed on {network}: {exc}") from exc

    async def _connect(self, network: str) -> Any:
        client = self._clients.get(network)
        if client is not None:
            return client
        # Awaiting the instance connects it on this loop and returns the async client, which is
        # the bittensor 11 contract — `async with` would close it on the way out, and the point
        # here is to keep it.
        client = await bt.Subtensor(network)
        self._clients[network] = client
        logger.info("connected to %s", network)
        return client

    async def _drop(self, network: str) -> None:
        client = self._clients.pop(network, None)
        if client is None:
            return
        try:
            await client.close()
        except Exception:  # pragma: no cover - closing a broken socket is best-effort
            logger.debug("closing the %s connection failed", network, exc_info=True)


# A placeholder, replaced with the block's real timestamp before any transfer leaves
# `transfers_to`. Never written anywhere: see the two-read note there.
_EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)


def _aware(stamp: dt.datetime) -> dt.datetime:
    if stamp.tzinfo is None:  # pragma: no cover - bittensor returns UTC-aware stamps
        return stamp.replace(tzinfo=dt.UTC)
    return stamp


async def finalized_transfer(
    source: PaymentSource, reference: TransferReference
) -> IncomingTransfer | None:
    """Resolve one reference to the transfer it names, or None if there is no such finalized one.

    The finality check is the first thing and it is not optional: a transfer in a block above the
    finalized head can still be reorganised away, and a submission admitted against one is an
    attempt given away for a payment that never happened. `blocks(finalized=True)` is the boundary.

    None covers all of "not finalized yet", "no such block", "no such extrinsic" and "that
    extrinsic moved no TAO". They are deliberately not distinguished: the caller turns any of them
    into one refusal, and telling a client which of them applied would let an unpaid caller probe
    the chain through this endpoint.

    Raises `AmbiguousReference` when the caller named only an extrinsic that emitted more than one
    transfer. That is the one case worth separating, because it is fixable by the client and
    guessing would mean choosing which payment they meant.
    """
    head = await source.finalized_head()
    if reference.block > head:
        return None
    transfers = await source.transfers_in(block=reference.block)
    in_extrinsic = [
        item for item in transfers if item.extrinsic_index == reference.extrinsic_index
    ]
    if not in_extrinsic:
        return None
    if reference.event_index is not None:
        for item in in_extrinsic:
            if item.event_index == reference.event_index:
                return item
        return None
    if len(in_extrinsic) > 1:
        raise AmbiguousReference(
            f"extrinsic {reference} emitted {len(in_extrinsic)} transfers; name the event index "
            "as well, e.g. " + ", ".join(item.reference for item in in_extrinsic)
        )
    return in_extrinsic[0]


async def first_block_at_or_after(
    source: TransferSource, when: dt.datetime, *, head: int | None = None
) -> ObservedBlock:
    """The earliest finalized block whose own timestamp is at or after `when`.

    How a wall-clock genesis timestamp becomes a block height. Bisection over block numbers,
    which is exact rather than estimated: block times drift, and a validator that started
    watching a few hundred blocks late would silently not credit the transfers in between.

    Run once per deployment and then recorded on the cursor. `chain_watch_cursor.start_block` is
    never recomputed, because a second search against a re-synced node could land either side of
    the same boundary and change which transfers the watcher believes exist.

    Raises `ChainUnavailable` if `when` is in the future relative to the finalized head — there is
    no block to return, and inventing the head would start crediting immediately from a timestamp
    that has not arrived.
    """
    if when.tzinfo is None:
        raise ValueError("the genesis timestamp must be timezone-aware")
    top = await source.finalized_head() if head is None else head
    tip = await source.block(top)
    if tip.timestamp < when:
        raise ChainUnavailable(
            f"the finalized head {top} is at {tip.timestamp.isoformat()}, before the "
            f"requested {when.isoformat()}"
        )

    # A lower bound that is genuinely before `when`, found by doubling backwards from the head
    # rather than starting the search at block 1. Bisecting from 1 would probe the chain's
    # earliest history — the part a node is least likely to still hold — on the way to a boundary
    # that is almost always recent.
    low, high = 1, top
    step = BLOCKS_PER_DAY
    while True:
        candidate = top - step
        if candidate <= 1:
            break
        probe = await source.block(candidate)
        if probe.timestamp < when:
            low = candidate
            break
        high = candidate
        step *= 2

    # Invariant: block(low).timestamp < when <= block(high).timestamp, with `high` known to
    # satisfy it and `low` either verified above or block 1, whose timestamp precedes any
    # timestamp a deployment would configure.
    while low + 1 < high:
        middle = (low + high) // 2
        probe = await source.block(middle)
        if probe.timestamp < when:
            low = middle
        else:
            high = middle
    return await source.block(high)


__all__ = [
    "BLOCKS_PER_DAY",
    "BLOCK_TIMESTAMP",
    "HOTKEY_OWNER",
    "REFERENCE",
    "SUBNET_KEYS",
    "SYSTEM_EVENTS",
    "TRANSFER_EVENT",
    "ZERO_ACCOUNT",
    "AmbiguousReference",
    "BittensorTransferSource",
    "ChainUnavailable",
    "IncomingTransfer",
    "ObservedBlock",
    "PaymentSource",
    "TransferReference",
    "TransferSource",
    "decode_ss58",
    "finalized_transfer",
    "first_block_at_or_after",
    "parse_reference",
    "transfers_in_events",
]
