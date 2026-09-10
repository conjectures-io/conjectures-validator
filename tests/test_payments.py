"""Payment confirmation at intake: the five things a verifier must establish, and its refusals.

V035 changed the fifth. Entitlement to cite a transfer used to be "the paying coldkey owns the
submitting hotkey", which needed a `SubtensorModule.Owner` read; it is now the equality
`transfer.sender == signer_coldkey`, computed from values already in hand. So the reader has one
method rather than two, and the tests below no longer stub a chain ownership table.

`docs/API.md` lists what confirmation has to prove before any write, because nothing downstream
re-checks it. These are those five, one test each, plus the failures that are ours rather than the
miner's — a chain that cannot be read must never be recorded as a refused payment.

No chain and no database here: the reader is a fake, and what is under test is the policy
`ChainPaymentVerifier` applies to what the reader says. `test_subnet_transfers.py` covers resolving
a reference against a chain, and `test_deposit_watcher.py` covers spending one.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import dataclass

import pytest

from conjectures_subnet.transfers import AmbiguousReference, ChainUnavailable
from submission_api.chain_payments import SubtensorTransferReader
from submission_api.errors import PaymentRequired
from submission_api.payments import (
    REASON_AMBIGUOUS,
    REASON_NOT_FINALIZED,
    REASON_UNAVAILABLE,
    ChainPaymentVerifier,
    DevelopmentPaymentVerifier,
    FinalizedTransfer,
)

TREASURY = "5Gn2SyG6PmBstAjiPD93CTuxADqYaYqf6fKeFuezKsX7Chf9"
# The coldkey that sent the transfer, and therefore — since V035 — the one that must have signed
# the submission. One key, where this file used to need a (coldkey, hotkey) pair.
COLDKEY = "5HMqFHmvUpzuAjEnse3hzMKS5LsFL428hffCfenF2smuGNhs"
# A different coldkey, for the case that matters most here: citing a transfer somebody else sent.
OTHER_COLDKEY = "5EZotmLfrufXYvD6CCGsRRELEFdg9SnjaEzTmaemiBPNofBP"
STRANGER = "5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy"

PRICE = 500_000_000  # 0.5 TAO
WHEN = dt.datetime(2026, 8, 1, tzinfo=dt.UTC)
REFERENCE = "8769916-13-151"


def run(coroutine):
    return asyncio.run(coroutine)


@dataclass
class FakeReader:
    """A `payments.TransferReader` that answers from what the test set up."""

    transfer: FinalizedTransfer | None = None
    raises: Exception | None = None

    async def finalized_transfer(self, *, reference: str):
        if self.raises is not None:
            raise self.raises
        return self.transfer


def transfer(**overrides) -> FinalizedTransfer:
    fields = {
        "reference": REFERENCE,
        "sender": COLDKEY,
        "recipient": TREASURY,
        "amount_rao": PRICE,
        "block": 8769916,
        "block_timestamp": WHEN,
    }
    fields.update(overrides)
    return FinalizedTransfer(**fields)


def verifier(**overrides) -> ChainPaymentVerifier:
    reader = overrides.pop("reader", FakeReader(transfer=transfer()))
    return ChainPaymentVerifier(
        recipient=overrides.pop("recipient", TREASURY),
        amount_rao=overrides.pop("amount_rao", PRICE),
        reader=reader,
    )


def confirm(
    v: ChainPaymentVerifier, *, reference: str = REFERENCE, signer_coldkey: str = COLDKEY
):
    """Confirm as the coldkey that sent the fixture transfer, which is the entitled case."""
    return run(v.confirm(reference=reference, signer_coldkey=signer_coldkey))


# --- The happy path ----------------------------------------------------------------------


def test_a_finalized_transfer_of_the_right_amount_from_the_signing_coldkey_confirms():
    payment = confirm(verifier())

    assert payment.reference == REFERENCE
    assert payment.sender == COLDKEY
    assert payment.amount_rao == PRICE
    assert payment.block == 8769916
    # Carried so `spend` can record the transfer without reading the chain a second time.
    assert payment.recipient == TREASURY
    assert payment.extrinsic_index == 13
    assert payment.event_index == 151
    assert payment.block_timestamp == WHEN


def test_the_canonical_reference_is_stored_not_the_one_the_miner_typed():
    """`submissions.payment_reference` is unique, so storing what was typed would let
    `block-extrinsic` and `block-extrinsic-event` fund two submissions from one transfer.

    The reader resolves and returns the canonical three-part identity; this proves the verifier
    passes *that* on rather than echoing the request.
    """
    payment = confirm(verifier(), reference="8769916-13")

    assert payment.reference == "8769916-13-151"


# --- The five things it must establish ---------------------------------------------------


def test_a_verifier_with_no_reader_fails_closed():
    """The only safe default for a component that gates money: refuse everything rather than
    admit one unpaid submission."""
    bare = ChainPaymentVerifier(recipient=TREASURY, amount_rao=PRICE)

    with pytest.raises(PaymentRequired) as raised:
        confirm(bare)

    assert raised.value.reason_code == REASON_UNAVAILABLE
    assert raised.value.status_code == 503


def test_a_reference_with_no_finalized_transfer_is_refused():
    with pytest.raises(PaymentRequired) as raised:
        confirm(verifier(reader=FakeReader(transfer=None)))

    assert raised.value.reason_code == REASON_NOT_FINALIZED


def test_a_transfer_to_another_address_is_refused():
    with pytest.raises(PaymentRequired, match="payment address"):
        confirm(verifier(reader=FakeReader(transfer=transfer(recipient=STRANGER))))


def test_an_amount_that_is_not_exactly_the_price_is_refused_either_way():
    """Exact, not "at least". Underpaying buys nothing, and overpaying on this path is a
    mistake to tell the miner about rather than silently keep."""
    for amount in (PRICE - 1, PRICE + 1, 2 * PRICE):
        with pytest.raises(PaymentRequired, match="does not equal the submission price"):
            confirm(verifier(reader=FakeReader(transfer=transfer(amount_rao=amount))))


def test_a_transfer_sent_by_another_coldkey_is_refused():
    """Otherwise a miner could cite somebody else's transfer and submit on their payment.

    The replacement for the old ownership check, and strictly stronger: that one accepted a
    hotkey signature plus a chain claim that the payer owned it, whereas this requires the
    paying key itself to have signed the request.
    """
    reader = FakeReader(transfer=transfer(sender=OTHER_COLDKEY))

    with pytest.raises(PaymentRequired, match="not sent by the coldkey that signed"):
        confirm(verifier(reader=reader))


def test_the_signer_alone_does_not_entitle_a_caller_to_an_unrelated_transfer():
    """The mirror of the test above, driven from the signer rather than the sender: the same
    fixture transfer, cited by a key that did not send it."""
    with pytest.raises(PaymentRequired, match="not sent by the coldkey that signed"):
        confirm(verifier(), signer_coldkey=STRANGER)


# --- Failures that are ours, not the miner's ---------------------------------------------


def test_an_unreadable_chain_is_a_503_and_not_a_refused_payment():
    """The miner did nothing wrong. Recording this as a payment failure would blame them for
    our node being down, and `api_rejection_log` would fill with refusals that never happened."""
    reader = FakeReader(raises=ChainUnavailable("websocket went away"))

    with pytest.raises(PaymentRequired) as raised:
        confirm(verifier(reader=reader))

    assert raised.value.reason_code == REASON_UNAVAILABLE
    assert raised.value.status_code == 503


def test_an_ambiguous_reference_is_a_400_that_says_how_to_fix_it():
    """A batch moved TAO twice. Guessing would be choosing which payment the miner meant."""
    reader = FakeReader(
        raises=AmbiguousReference("emitted 2 transfers; name the event index as well, e.g. 1-2-3, 1-2-5")
    )

    with pytest.raises(PaymentRequired) as raised:
        confirm(verifier(reader=reader))

    assert raised.value.reason_code == REASON_AMBIGUOUS
    assert raised.value.status_code == 400
    assert "1-2-3" in str(raised.value)


# --- The reader that adapts the chain ----------------------------------------------------


@dataclass
class FakeSource:
    """The `conjectures_subnet.transfers.PaymentSource` surface the reader uses."""

    head: int = 9_000_000
    transfers: tuple = ()

    async def finalized_head(self):
        return self.head

    async def transfers_in(self, *, block: int):
        return [item for item in self.transfers if item.block == block]

    async def block(self, number):  # pragma: no cover - unused by the reader
        raise AssertionError

    async def transfers_to(self, *, recipient, block):  # pragma: no cover - unused
        raise AssertionError

    async def hotkey_at(self, *, netuid, uid):  # pragma: no cover - unused
        raise AssertionError


def test_the_reader_returns_none_for_a_reference_it_cannot_resolve():
    """A hash cannot be resolved by a node, so it is not a reference this validator accepts."""
    reader = SubtensorTransferReader(source=FakeSource())

    assert run(reader.finalized_transfer(reference="0x8b21ab")) is None


def test_the_reader_has_no_ownership_query_left():
    """`coldkey_owns_hotkey` and `hotkey_is_registered` were both removed by V035, along with
    the `SubtensorModule.Owner` read behind them. Asserted rather than merely deleted, because
    a reintroduced ownership query would be a chain round trip on the intake path that nothing
    needs — and a route calling one would fail closed only if the method is genuinely absent.
    """
    reader = SubtensorTransferReader(source=FakeSource())

    assert not hasattr(reader, "coldkey_owns_hotkey")
    assert not hasattr(reader, "hotkey_is_registered")


# --- The development verifier ------------------------------------------------------------


def test_the_development_verifier_spends_nothing():
    """It must not write a synthetic `chain_transfers` row. That table is the record of what was
    observed on chain, and invented block positions in it would make the operator's
    unattributed-money queue untrustworthy."""
    dev = DevelopmentPaymentVerifier(amount_rao=PRICE)
    payment = run(dev.confirm(reference="0xpayment-0001", signer_coldkey=COLDKEY))

    # No session is touched, so passing None proves it writes nothing.
    run(dev.spend(None, payment, submission_id=None))
