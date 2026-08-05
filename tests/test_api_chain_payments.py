"""Intake through the *chain* payment verifier, end to end, against a real PostgreSQL database.

Every other API test runs on the development verifier, which accepts a configured reference and
touches no chain. These run the production path — `SUBMISSION_PAYMENT_VERIFIER=chain` with a fake
finalized-transfer reader — because that is the path that was unbuilt, and because it is the only
one that spends a transfer.

The property that matters most here is the one neither funding path could enforce alone: **one
transfer buys one thing**. A miner must not be able to pay 0.5 TAO, cite it for a submission, and
also have the deposit watcher credit it to their account.

Skipped unless a PostgreSQL server is reachable:

    docker compose -f docker-compose.pytest-db.yml up -d
"""

from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import dataclass

import pytest

pytest.importorskip("fastapi", reason="submission API tests need the service extra")
pytest.importorskip("sqlalchemy", reason="submission API tests need the db extra")
pytest.importorskip("httpx", reason="submission API tests need the service extra")
pytest.importorskip("psycopg", reason="submission API tests need the db extra")

from conftest_api import (
    COLDKEY,
    HOTKEY,
    distinct_bundle,
    harness,
    new_key,
    postgres_dsn,
    submission_headers,
    valid_bundle,
)

from conjectures_subnet.db import transfers as store
from conjectures_subnet.db.engine import async_session_scope
from conjectures_subnet.db.models import (
    Account,
    AccountWallet,
    ChainTransfer,
    ChainTransferState,
    Submission,
)
from conjectures_subnet.transfers import IncomingTransfer
from conftest import DATABASE_SKIP_REASON
from submission_api.chain_payments import SubtensorTransferReader
from submission_api.payments import ChainPaymentVerifier

pytestmark = pytest.mark.skipif(postgres_dsn() is None, reason=DATABASE_SKIP_REASON)

TREASURY = "5C4hPGqPnDPP9jgWmBQfBAuxwiuFhWM6ttHwUvBAoMxLLJRD"
PRICE = 500_000_000
BLOCK = 8_769_916
WHEN = dt.datetime(2026, 8, 1, tzinfo=dt.UTC)

# The canonical identity of the transfer these tests pay with, `block-extrinsic-event`.
REFERENCE = f"{BLOCK}-13-151"
# The same transfer named the way a block explorer shows it, without the event index.
SHORT_REFERENCE = f"{BLOCK}-13"


def run(coroutine):
    return asyncio.run(coroutine)


@dataclass
class FakeSource:
    """The chain surface `SubtensorTransferReader` reads, with one transfer in it."""

    transfers: tuple = ()
    owners: dict | None = None
    head: int = BLOCK + 100

    async def finalized_head(self) -> int:
        return self.head

    async def transfers_in(self, *, block: int):
        return [item for item in self.transfers if item.block == block]

    async def coldkey_of(self, *, hotkey: str):
        owners = {HOTKEY: COLDKEY} if self.owners is None else self.owners
        return owners.get(hotkey)

    async def block(self, number):  # pragma: no cover - unused on this path
        raise AssertionError

    async def transfers_to(self, *, recipient, block):  # pragma: no cover - unused
        raise AssertionError

    async def hotkey_at(self, *, netuid, uid):  # pragma: no cover - unused
        raise AssertionError


def paid_transfer(*, amount: int = PRICE, to: str = TREASURY) -> IncomingTransfer:
    return IncomingTransfer(
        block=BLOCK,
        block_timestamp=WHEN,
        extrinsic_index=13,
        event_index=151,
        sender=COLDKEY,
        recipient=to,
        amount_rao=amount,
    )


def chain_kit(*, transfers=None, owners=None, **overrides):
    """The API wired to the chain verifier over a fake reader.

    `payments` is injected rather than built from settings, because `build_payment_verifier`
    would otherwise open a real Subtensor connection.
    """
    source = FakeSource(
        transfers=tuple(transfers if transfers is not None else (paid_transfer(),)),
        owners=owners,
    )
    verifier = ChainPaymentVerifier(
        recipient=TREASURY,
        amount_rao=PRICE,
        reader=SubtensorTransferReader(source=source),
    )
    return harness(
        payments=verifier,
        PAYMENT_RECIPIENT_SS58=TREASURY,
        PAYMENT_AMOUNT_RAO=str(PRICE),
        **overrides,
    )


async def _post(kit, bundle: bytes, **overrides):
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=kit.app), base_url="http://validator.test"
    ) as client:
        return await client.post(
            "/v1/submissions",
            content=bundle,
            headers=submission_headers(bundle, **overrides),
        )


async def _transfers(kit):
    from sqlalchemy import select

    async with async_session_scope(kit.services.sessions) as session:
        return list(
            (await session.execute(select(ChainTransfer).order_by(ChainTransfer.id)))
            .scalars()
            .all()
        )


async def _submissions(kit):
    from sqlalchemy import select

    async with async_session_scope(kit.services.sessions) as session:
        return list((await session.execute(select(Submission))).scalars().all())


# --- The path that was unbuilt -----------------------------------------------------------


def test_a_chain_confirmed_payment_admits_a_submission():
    """What `503 PAYMENT_VERIFIER_UNAVAILABLE` used to be returned for."""

    async def scenario():
        kit = await chain_kit().setup()
        try:
            response = await _post(kit, valid_bundle(), payment_reference=REFERENCE)
            assert response.status_code == 201, response.text

            rows = await _submissions(kit)
            assert len(rows) == 1
            assert rows[0].payment_reference == REFERENCE
            assert rows[0].payment_sender == COLDKEY
            assert rows[0].payment_amount_rao == PRICE
            assert rows[0].payment_block == BLOCK
        finally:
            await kit.teardown()

    run(scenario())


def test_a_submission_spends_the_transfer_so_it_cannot_also_buy_credits():
    """The record that stops the double spend. `chain_transfers` is the only table both funding
    paths share, so it is the only one that can arbitrate."""

    async def scenario():
        kit = await chain_kit().setup()
        try:
            assert (
                await _post(kit, valid_bundle(), payment_reference=REFERENCE)
            ).status_code == 201

            rows = await _transfers(kit)
            assert len(rows) == 1
            assert rows[0].extrinsic_reference == REFERENCE
            assert rows[0].status is ChainTransferState.IGNORED
            assert rows[0].credits_granted == 0
            assert "funded submission" in rows[0].note
            assert rows[0].amount_rao == PRICE
            assert rows[0].block_timestamp == WHEN
        finally:
            await kit.teardown()

    run(scenario())


def test_a_transfer_the_watcher_already_credited_cannot_fund_a_submission():
    """Whichever path gets there first wins. The miner was given credits, so they are told to
    spend one rather than being charged twice for the same TAO."""

    async def scenario():
        kit = await chain_kit().setup()
        try:
            # The watcher got there first. Credited through the real store call, so the row
            # satisfies `transfer_credited_needs_attribution` the way a live credit would — an
            # account, a deposit and a ledger entry, not a hand-set status.
            async with async_session_scope(kit.services.sessions) as session:
                account = Account(email="payer@example.com", email_verified=True)
                session.add(account)
                await session.flush()
                session.add(
                    AccountWallet(
                        account_id=account.id, coldkey=COLDKEY, signature=bytes(64)
                    )
                )
                recorded = await store.record(
                    session,
                    extrinsic_reference=REFERENCE,
                    block=BLOCK,
                    block_timestamp=WHEN,
                    extrinsic_index=13,
                    event_index=151,
                    sender_coldkey=COLDKEY,
                    recipient=TREASURY,
                    amount_rao=PRICE,
                )
                await store.credit(
                    session,
                    recorded.transfer,
                    account_id=account.id,
                    credit_price_rao=PRICE,
                    deposit_expires_at=WHEN + dt.timedelta(hours=24),
                    created_by="test",
                )

            response = await _post(kit, valid_bundle(), payment_reference=REFERENCE)

            assert response.status_code == 409, response.text
            assert response.json()["reason_code"] == "TRANSFER_ALREADY_CREDITED"
            # And no submission was written: the whole request rolled back.
            assert await _submissions(kit) == []
        finally:
            await kit.teardown()

    run(scenario())


def test_the_canonical_reference_is_stored_when_the_miner_cites_only_the_extrinsic():
    """`submissions.payment_reference` is unique, so storing what was typed would let two
    spellings of one transfer fund two submissions."""

    async def scenario():
        kit = await chain_kit().setup()
        try:
            response = await _post(
                kit, valid_bundle(), payment_reference=SHORT_REFERENCE
            )
            assert response.status_code == 201, response.text

            rows = await _submissions(kit)
            assert rows[0].payment_reference == REFERENCE  # not SHORT_REFERENCE
        finally:
            await kit.teardown()

    run(scenario())


def test_two_spellings_of_one_transfer_cannot_fund_two_submissions():
    """The reason the canonical form is what gets stored.

    Two genuinely different submissions — distinct proof bytes, distinct idempotency keys, so
    neither the proof-digest nor the idempotency constraint is what refuses the second — citing
    the same transfer under its two spellings. `block-extrinsic` and `block-extrinsic-event` must
    resolve to one payment, or one 0.5 TAO transfer buys two attempts.
    """

    async def scenario():
        kit = await chain_kit().setup()
        try:
            first_bundle, first_digest = distinct_bundle("chain-payment-one")
            first = await _post(
                kit,
                first_bundle,
                payment_reference=SHORT_REFERENCE,
                proof_digest=first_digest,
                idempotency_key=new_key(),
            )
            assert first.status_code == 201, first.text

            second_bundle, second_digest = distinct_bundle("chain-payment-two")
            second = await _post(
                kit,
                second_bundle,
                payment_reference=REFERENCE,
                proof_digest=second_digest,
                idempotency_key=new_key(),
            )
            assert second.status_code == 409, second.text
            assert second.json()["reason_code"] in {
                "DUPLICATE_PAYMENT",
                "TRANSFER_ALREADY_CREDITED",
            }
            assert len(await _submissions(kit)) == 1
        finally:
            await kit.teardown()

    run(scenario())


# --- Refusals ----------------------------------------------------------------------------


def test_a_reference_naming_no_transfer_is_refused_and_writes_nothing():
    async def scenario():
        kit = await chain_kit(transfers=()).setup()
        try:
            response = await _post(kit, valid_bundle(), payment_reference=REFERENCE)

            assert response.status_code == 402
            assert response.json()["reason_code"] == "PAYMENT_NOT_FINALIZED"
            assert await _submissions(kit) == []
            assert await _transfers(kit) == []
        finally:
            await kit.teardown()

    run(scenario())


def test_a_transfer_to_another_address_is_refused():
    async def scenario():
        wrong = paid_transfer(to="5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy")
        kit = await chain_kit(transfers=(wrong,)).setup()
        try:
            response = await _post(kit, valid_bundle(), payment_reference=REFERENCE)

            assert response.status_code == 402
            assert await _submissions(kit) == []
        finally:
            await kit.teardown()

    run(scenario())


def test_an_underpayment_is_refused():
    async def scenario():
        kit = await chain_kit(transfers=(paid_transfer(amount=PRICE - 1),)).setup()
        try:
            response = await _post(kit, valid_bundle(), payment_reference=REFERENCE)

            assert response.status_code == 402
            assert await _submissions(kit) == []
        finally:
            await kit.teardown()

    run(scenario())


def test_a_payer_who_does_not_own_the_submitting_hotkey_is_refused():
    """Otherwise a miner could cite a transfer somebody else made."""

    async def scenario():
        kit = await chain_kit(owners={}).setup()
        try:
            response = await _post(kit, valid_bundle(), payment_reference=REFERENCE)

            assert response.status_code == 402
            assert await _submissions(kit) == []
            # Nothing was spent either: the refusal happens before any write.
            assert await _transfers(kit) == []
        finally:
            await kit.teardown()

    run(scenario())


def test_an_unresolvable_reference_is_refused():
    """A hash is what a block explorer shows, and no node can resolve one — "get extrinsic by
    hash" is an indexer's service, not an RPC."""

    async def scenario():
        kit = await chain_kit().setup()
        try:
            response = await _post(
                kit, valid_bundle(), payment_reference="0x8b21abcdef"
            )

            assert response.status_code == 402
            assert await _submissions(kit) == []
        finally:
            await kit.teardown()

    run(scenario())


def test_an_ambiguous_reference_says_which_events_to_choose_between():
    """A batch that moved TAO to the treasury twice. Guessing would be choosing which payment
    the miner meant."""

    async def scenario():
        batch = (
            paid_transfer(),
            IncomingTransfer(
                block=BLOCK,
                block_timestamp=WHEN,
                extrinsic_index=13,
                event_index=160,
                sender=COLDKEY,
                recipient=TREASURY,
                amount_rao=PRICE,
            ),
        )
        kit = await chain_kit(transfers=batch).setup()
        try:
            response = await _post(
                kit, valid_bundle(), payment_reference=SHORT_REFERENCE
            )

            assert response.status_code == 400, response.text
            body = response.json()
            assert body["reason_code"] == "PAYMENT_REFERENCE_AMBIGUOUS"
            assert f"{BLOCK}-13-151" in body["detail"]
            assert f"{BLOCK}-13-160" in body["detail"]
            assert await _submissions(kit) == []
        finally:
            await kit.teardown()

    run(scenario())


def test_an_unfinalized_transfer_is_refused():
    """A transfer above the finalized head can still be reorganised away."""

    async def scenario():
        kit = await chain_kit().setup()
        kit.services.payments.reader.source.head = BLOCK - 1
        try:
            response = await _post(kit, valid_bundle(), payment_reference=REFERENCE)

            assert response.status_code == 402
            assert await _submissions(kit) == []
        finally:
            await kit.teardown()

    run(scenario())
