"""Decoding transfers off block events, and resolving a timestamp to a block.

The chain-facing half of the deposit watcher, tested without a chain. Both halves are the kind
that breaks silently: a runtime upgrade that changes the event envelope makes every transfer
invisible, which looks exactly like "nobody paid today", and a bisection that lands one block
early or late changes which transfers a validator believes exist.
"""

from __future__ import annotations

import asyncio
import datetime as dt

import pytest

from conjectures_subnet.transfers import (
    BLOCK_TIMESTAMP,
    SUBNET_KEYS,
    SYSTEM_EVENTS,
    AmbiguousReference,
    BittensorTransferSource,
    ChainUnavailable,
    IncomingTransfer,
    ObservedPayout,
    ObservedBlock,
    TransferReference,
    decode_ss58,
    finalized_transfer,
    first_block_at_or_after,
    parse_reference,
    payouts_in_events,
    transfers_in_events,
)

RECIPIENT = "5Gn2SyG6PmBstAjiPD93CTuxADqYaYqf6fKeFuezKsX7Chf9"
SENDER = "5HMqFHmvUpzuAjEnse3hzMKS5LsFL428hffCfenF2smuGNhs"
STRANGER = "5EZotmLfrufXYvD6CCGsRRELEFdg9SnjaEzTmaemiBPNofBP"
WHEN = dt.datetime(2026, 8, 1, tzinfo=dt.UTC)

RAO_PER_TAO = 1_000_000_000


def _record(module, event, attributes, *, extrinsic_idx=3, nested=True):
    """One event record in the shape bittensor 11 returns: fields at both levels."""
    body = {"module_id": module, "event_id": event, "attributes": attributes}
    record = {"phase": "ApplyExtrinsic", "extrinsic_idx": extrinsic_idx, "topics": []}
    if nested:
        record["event"] = dict(body)
    record.update(body)
    return record


def _transfer(*, to=RECIPIENT, sender=SENDER, amount=RAO_PER_TAO, extrinsic_idx=3):
    return _record(
        "Balances",
        "Transfer",
        {"from": sender, "to": to, "amount": amount},
        extrinsic_idx=extrinsic_idx,
    )


def _found(records):
    return transfers_in_events(
        records, recipient=RECIPIENT, block=100, block_timestamp=WHEN
    )


def _payout_events(
    *,
    amount=1_044_286_814_577,
    origin_coldkey=SENDER,
    destination_coldkey=RECIPIENT,
    origin_hotkey=STRANGER,
    destination_hotkey=RECIPIENT,
    extrinsic_idx=7,
):
    """The event sequence emitted by a same-subnet transfer_stake_and_hotkey call."""
    return [
        _record(
            "SubtensorModule",
            "StakeRemoved",
            [origin_coldkey, origin_hotkey, 400_000_000, amount, 66, 0],
            extrinsic_idx=extrinsic_idx,
        ),
        _record(
            "SubtensorModule",
            "StakeAdded",
            [destination_coldkey, destination_hotkey, 400_000_000, amount, 66, 0],
            extrinsic_idx=extrinsic_idx,
        ),
        _record(
            "SubtensorModule",
            "StakeAndHotkeyTransferred",
            {
                "origin_coldkey": origin_coldkey,
                "destination_coldkey": destination_coldkey,
                "origin_hotkey": origin_hotkey,
                "destination_hotkey": destination_hotkey,
                "origin_netuid": 66,
                "destination_netuid": 66,
                # Deliberately not the Alpha amount: the runtime reports TAO-equivalent here.
                "amount": 400_000_000,
            },
            extrinsic_idx=extrinsic_idx,
        ),
    ]


# --- Reading the events ------------------------------------------------------------------


def test_only_transfers_into_the_watched_address_are_returned():
    records = [
        _record("Balances", "Issued", {"amount": 500}),
        _transfer(to=STRANGER),  # somebody else's payment, in the same block
        _transfer(amount=2 * RAO_PER_TAO),
        _record("SubtensorModule", "StakeAdded", {"amount": 1}),
    ]
    found = _found(records)

    assert [item.amount_rao for item in found] == [2 * RAO_PER_TAO]
    assert found[0].sender == SENDER
    assert found[0].recipient == RECIPIENT
    assert found[0].block_timestamp == WHEN


def test_reference_names_the_event_not_just_the_extrinsic():
    """A utility.batch emits several Transfer events under one extrinsic.

    Keying a payment on the extrinsic alone would make two of them indistinguishable — and
    `deposits.extrinsic_reference` is unique, so the second would be silently refused rather
    than credited.
    """
    records = [
        _transfer(amount=RAO_PER_TAO, extrinsic_idx=7),
        _record("Balances", "Issued", {"amount": 1}, extrinsic_idx=7),
        _transfer(amount=3 * RAO_PER_TAO, extrinsic_idx=7),
    ]
    found = _found(records)

    assert [item.reference for item in found] == ["100-7-0", "100-7-2"]
    assert len({item.reference for item in found}) == 2


def test_payout_uses_the_exact_alpha_from_the_companion_stake_event():
    records = [
        _record("System", "ExtrinsicSuccess", [], extrinsic_idx=2),
        *_payout_events(),
        _record("Proxy", "ProxyExecuted", [{"Ok": None}], extrinsic_idx=7),
    ]

    found = payouts_in_events(records, block=100, block_timestamp=WHEN)

    assert found == [
        ObservedPayout(
            block=100,
            block_timestamp=WHEN,
            extrinsic_index=7,
            event_index=3,
            origin_coldkey=SENDER,
            destination_coldkey=RECIPIENT,
            origin_hotkey=STRANGER,
            destination_hotkey=RECIPIENT,
            origin_netuid=66,
            destination_netuid=66,
            amount_rao=1_044_286_814_577,
        )
    ]
    assert found[0].reference == "100-7-3"


def test_batched_payouts_to_one_destination_pair_with_their_own_alpha_amounts():
    records = [
        *_payout_events(amount=700, extrinsic_idx=9),
        *_payout_events(amount=800, extrinsic_idx=9),
    ]

    found = payouts_in_events(records, block=100, block_timestamp=WHEN)

    assert [item.amount_rao for item in found] == [700, 800]
    assert [item.reference for item in found] == ["100-9-2", "100-9-5"]


def test_a_payout_event_without_its_alpha_event_fails_loud():
    records = _payout_events()
    del records[1]

    with pytest.raises(ChainUnavailable, match="no matching preceding StakeAdded"):
        payouts_in_events(records, block=100, block_timestamp=WHEN)


def test_an_ordinary_stake_add_is_not_a_payout():
    records = [
        _record(
            "SubtensorModule",
            "StakeAdded",
            [RECIPIENT, STRANGER, 123, 456, 66, 0],
            extrinsic_idx=5,
        )
    ]

    assert payouts_in_events(records, block=100, block_timestamp=WHEN) == []


def test_events_are_read_from_the_nested_envelope_when_that_is_all_there_is():
    """Older decoders return only the nested `event` dict.

    Reading through `_event_body` rather than assuming one shape means a runtime upgrade cannot
    turn every transfer invisible.
    """
    flat_only = _transfer()
    flat_only.pop("event")
    nested_only = {
        "phase": "ApplyExtrinsic",
        "extrinsic_idx": 3,
        "event": {
            "module_id": "Balances",
            "event_id": "Transfer",
            "attributes": {"from": SENDER, "to": RECIPIENT, "amount": RAO_PER_TAO},
        },
    }

    assert len(_found([flat_only])) == 1
    assert len(_found([nested_only])) == 1


def test_positional_attributes_are_read_by_index():
    """A decoder that did not name the attributes still yields (from, to, amount) in order."""
    record = _record("Balances", "Transfer", [SENDER, RECIPIENT, RAO_PER_TAO])

    found = _found([record])
    assert found[0].sender == SENDER
    assert found[0].amount_rao == RAO_PER_TAO


def test_account_id_tuples_are_ss58_encoded():
    """Post-dTAO runtimes hand back AccountId32 as a tuple of bytes, flat or wrapped once."""
    public_key = tuple(bytes(range(32)))
    flat = decode_ss58(public_key)
    wrapped = decode_ss58((public_key,))

    assert flat == wrapped
    assert len(flat) == 48
    # An address already encoded passes through untouched, which is what Finney actually returns.
    assert decode_ss58(RECIPIENT) == RECIPIENT


def test_zero_value_transfers_are_skipped_rather_than_recorded():
    """A valid extrinsic, but not a payment — and `amount_rao > 0` is a database constraint."""
    assert _found([_transfer(amount=0)]) == []


def test_an_event_with_no_extrinsic_index_still_produces_a_unique_reference():
    """Initialisation-phase events carry no extrinsic_idx. It cannot be a user transfer today,
    but the reference must stay unique within the block if that ever changes."""
    record = _transfer()
    record.pop("extrinsic_idx")
    record["phase"] = "Initialization"

    found = _found([record])
    assert found[0].reference == "100-0-0"


def test_an_unreadable_event_record_is_an_error_not_a_silent_skip():
    """Refusing loudly is the point: skipping would under-report money that arrived."""
    with pytest.raises(ChainUnavailable):
        _found([{"phase": "ApplyExtrinsic", "topics": []}])


# --- Resolving the genesis timestamp -----------------------------------------------------


class _FakeChain:
    """Twelve-second blocks from a fixed epoch, counting every block read."""

    EPOCH = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    BLOCK_SECONDS = 12

    def __init__(self, head: int):
        self._head = head
        self.reads: list[int] = []

    async def finalized_head(self) -> int:
        return self._head

    async def block(self, number: int) -> ObservedBlock:
        self.reads.append(number)
        return ObservedBlock(
            number=number,
            hash=f"0x{number:064x}",
            timestamp=self.EPOCH + dt.timedelta(seconds=number * self.BLOCK_SECONDS),
        )

    def at(self, number: int) -> dt.datetime:
        return self.EPOCH + dt.timedelta(seconds=number * self.BLOCK_SECONDS)

    async def transfers_to(self, *, recipient, block):  # pragma: no cover - unused here
        return ()

    async def hotkey_at(self, *, netuid, uid):  # pragma: no cover - unused here
        return RECIPIENT


def test_bisection_finds_the_first_block_at_or_after_the_timestamp():
    chain = _FakeChain(head=2_000_000)
    target = 1_234_567
    when = chain.at(target)

    found = asyncio.run(first_block_at_or_after(chain, when))

    assert found.number == target
    assert found.timestamp == when


def test_a_timestamp_between_two_blocks_resolves_to_the_later_one():
    """"At or after" is the contract. Rounding down would credit a transfer from before the
    genesis timestamp, which is the one thing the timestamp exists to exclude."""
    chain = _FakeChain(head=2_000_000)
    target = 1_234_567
    when = chain.at(target) - dt.timedelta(seconds=5)

    found = asyncio.run(first_block_at_or_after(chain, when))

    assert found.number == target


def test_bisection_is_logarithmic_and_does_not_walk_back_to_genesis():
    """Doubling backwards from the head, then bisecting. Starting at block 1 would probe the
    chain's earliest history — the part a node is least likely to still hold."""
    chain = _FakeChain(head=2_000_000)
    when = chain.at(1_999_000)

    asyncio.run(first_block_at_or_after(chain, when))

    assert len(chain.reads) < 40
    assert min(chain.reads) > 1_900_000


def test_a_future_timestamp_is_refused_rather_than_clamped_to_the_head():
    """Returning the head would start crediting immediately from a timestamp that has not
    arrived — every transfer between now and then, in one pass."""
    chain = _FakeChain(head=1000)

    with pytest.raises(ChainUnavailable, match="before the requested"):
        asyncio.run(first_block_at_or_after(chain, chain.at(5000)))


def test_a_naive_timestamp_is_refused():
    """A bare wall clock is not an instant, and the two readers here are this process and a
    Subtensor block header."""
    chain = _FakeChain(head=1000)

    with pytest.raises(ValueError, match="timezone-aware"):
        asyncio.run(first_block_at_or_after(chain, dt.datetime(2026, 1, 1)))


# --- The connection the live source holds ------------------------------------------------


class _FakeClient:
    """Stands in for a connected bittensor client. Fails on demand, records what it was asked."""

    def __init__(self, *, fail_times: int = 0):
        self.fail_times = fail_times
        self.queries: list[tuple] = []
        self.closed = False

    async def query(self, item, params=None, *, block=None):
        self.queries.append((item, params, block))
        if self.fail_times > 0:
            self.fail_times -= 1
            raise OSError("websocket went away")
        if item == SYSTEM_EVENTS:
            return [_transfer(amount=RAO_PER_TAO)]
        if item == BLOCK_TIMESTAMP:
            return int(WHEN.timestamp() * 1000)
        if item == SUBNET_KEYS:
            return RECIPIENT
        raise AssertionError(f"unexpected query {item}")

    async def close(self):
        self.closed = True


def _source(clients):
    """A live source that hands out `clients` in order, one per real connect.

    Only the socket-opening step is replaced; the cache-hit path, the failure drop and the
    locking are the real ones, because those are what is under test.
    """
    source = BittensorTransferSource("finney")
    made = iter(clients)

    async def connect(network):
        held = source._clients.get(network)
        if held is not None:
            return held
        client = next(made)
        source._clients[network] = client
        return client

    source._connect = connect
    return source


def test_the_connection_is_reused_across_reads_rather_than_reopened():
    """The public Finney endpoints answer HTTP 429 to a client that opens a websocket per read,
    and this source reads every block."""
    client = _FakeClient()
    source = _source([client])

    async def body():
        await source.transfers_to(recipient=RECIPIENT, block=100)
        await source.transfers_to(recipient=RECIPIENT, block=101)
        await source.hotkey_at(netuid=66, uid=121)

    asyncio.run(body())

    # Two reads per block that had a transfer (events, then the timestamp), one for the hotkey —
    # all on the same client, which was never closed.
    assert len(client.queries) == 5
    assert not client.closed


def test_historical_payout_events_use_the_archive_connection():
    client = _FakeClient()
    source = BittensorTransferSource("finney", archive_network="archive-finney")
    connected: list[str] = []

    async def connect(network):
        connected.append(network)
        held = source._clients.get(network)
        if held is None:
            source._clients[network] = client
            held = client
        return held

    source._connect = connect
    asyncio.run(source.payouts_in(block=100))

    assert connected == ["archive-finney", "archive-finney"]


def test_a_block_with_no_arrivals_costs_one_read_not_two():
    """At one block every twelve seconds forever, the read that is skipped is the one that
    matters."""
    client = _FakeClient()
    source = _source([client])

    found = asyncio.run(source.transfers_to(recipient=STRANGER, block=100))

    assert found == ()
    assert [item[0] for item in client.queries] == [SYSTEM_EVENTS]


def test_a_failed_read_drops_the_connection_and_the_next_call_reconnects():
    """A stale connection would fail every subsequent read the same way. One failed pass is the
    intended cost of a node restart; a dead worker is not."""
    broken, fresh = _FakeClient(fail_times=1), _FakeClient()
    source = _source([broken, fresh])

    async def body():
        with pytest.raises(ChainUnavailable, match="chain read failed"):
            await source.transfers_to(recipient=RECIPIENT, block=100)
        assert broken.closed
        return await source.transfers_to(recipient=RECIPIENT, block=100)

    found = asyncio.run(body())

    assert [item.amount_rao for item in found] == [RAO_PER_TAO]
    assert found[0].block_timestamp == WHEN  # stamped from the Timestamp.Now read
    assert not fresh.closed


# --- Resolving a payment reference -------------------------------------------------------


def test_a_reference_may_name_the_extrinsic_or_the_exact_event():
    assert parse_reference("8769916-13-151") == TransferReference(8769916, 13, 151)
    assert parse_reference("8769916-13") == TransferReference(8769916, 13, None)
    assert parse_reference(" 8769916-13 ") == TransferReference(8769916, 13, None)


def test_an_unresolvable_reference_is_none_rather_than_an_error():
    """A hash is the shape a block explorer shows, and a substrate node cannot resolve one —
    "get extrinsic by hash" is an indexer's service. None becomes one refusal upstream."""
    for raw in ("0x8b21ab", "", "abc", "8769916", "-1-2", "8769916-13-151-7", "1e5-2"):
        assert parse_reference(raw) is None, raw


class _PaymentChain(_FakeChain):
    """A chain that also answers the two questions the payment verifier asks."""

    def __init__(self, head: int, transfers=(), owners=None):
        super().__init__(head=head)
        self._transfers = list(transfers)
        self._owners = owners or {}

    async def transfers_in(self, *, block: int):
        return [item for item in self._transfers if item.block == block]

    async def coldkey_of(self, *, hotkey: str):
        return self._owners.get(hotkey)


def _incoming(block, extrinsic, event, *, amount=RAO_PER_TAO, to=RECIPIENT):
    return IncomingTransfer(
        block=block,
        block_timestamp=WHEN,
        extrinsic_index=extrinsic,
        event_index=event,
        sender=SENDER,
        recipient=to,
        amount_rao=amount,
    )


def test_a_reference_resolves_to_the_transfer_it_names():
    chain = _PaymentChain(head=200, transfers=[_incoming(100, 5, 7)])

    found = asyncio.run(
        finalized_transfer(chain, TransferReference(100, 5, 7))
    )

    assert found is not None
    assert found.reference == "100-5-7"


def test_an_unfinalized_block_resolves_to_nothing():
    """A transfer above the finalized head can still be reorganised away, and a submission
    admitted against one is an attempt given away for a payment that never happened."""
    chain = _PaymentChain(head=99, transfers=[_incoming(100, 5, 7)])

    assert asyncio.run(finalized_transfer(chain, TransferReference(100, 5, 7))) is None


def test_a_two_part_reference_resolves_when_the_extrinsic_moved_tao_once():
    """What a block explorer shows. The canonical three-part identity comes back out."""
    chain = _PaymentChain(head=200, transfers=[_incoming(100, 5, 7)])

    found = asyncio.run(finalized_transfer(chain, TransferReference(100, 5, None)))

    assert found is not None
    assert found.reference == "100-5-7"


def test_a_two_part_reference_over_a_batch_is_refused_as_ambiguous():
    """Picking either would be deciding which payment the miner meant."""
    chain = _PaymentChain(
        head=200, transfers=[_incoming(100, 5, 7), _incoming(100, 5, 9)]
    )

    with pytest.raises(AmbiguousReference) as raised:
        asyncio.run(finalized_transfer(chain, TransferReference(100, 5, None)))

    # The message names the exact references to choose between, so it is actionable.
    assert "100-5-7" in str(raised.value)
    assert "100-5-9" in str(raised.value)


def test_a_three_part_reference_over_a_batch_is_exact():
    chain = _PaymentChain(
        head=200, transfers=[_incoming(100, 5, 7), _incoming(100, 5, 9, amount=42)]
    )

    found = asyncio.run(finalized_transfer(chain, TransferReference(100, 5, 9)))

    assert found is not None and found.amount_rao == 42


def test_a_reference_to_an_extrinsic_that_moved_no_tao_resolves_to_nothing():
    chain = _PaymentChain(head=200, transfers=[_incoming(100, 5, 7)])

    assert asyncio.run(finalized_transfer(chain, TransferReference(100, 6, None))) is None
    assert asyncio.run(finalized_transfer(chain, TransferReference(100, 5, 8))) is None


def test_a_transfer_to_the_wrong_address_still_resolves():
    """So the verifier can refuse with "went somewhere else" instead of "no such transfer" —
    different refusals for the miner reading them."""
    chain = _PaymentChain(head=200, transfers=[_incoming(100, 5, 7, to=STRANGER)])

    found = asyncio.run(finalized_transfer(chain, TransferReference(100, 5, 7)))

    assert found is not None and found.recipient == STRANGER


def test_all_transfers_in_a_block_are_returned_when_no_recipient_is_given():
    records = [_transfer(to=STRANGER), _transfer(to=RECIPIENT)]

    found = transfers_in_events(
        records, recipient=None, block=100, block_timestamp=WHEN
    )

    assert [item.recipient for item in found] == [STRANGER, RECIPIENT]


def test_close_releases_the_held_connection():
    client = _FakeClient()
    source = _source([client])

    async def body():
        await source.hotkey_at(netuid=66, uid=121)
        await source.close()
        await source.close()  # idempotent

    asyncio.run(body())
    assert client.closed
