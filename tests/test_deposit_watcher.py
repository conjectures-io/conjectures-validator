"""The deposit watcher against a real PostgreSQL database.

These are the properties that stop the watcher crediting money twice, crediting it to the wrong
account, or losing an arrival it could not attribute. The chain is a fake — the decoding it would
do is covered in `test_subnet_transfers.py` — so what is under test here is the durable half: the
cursor, the idempotency, the attribution, and the arithmetic that turns rao into credits.

Skipped unless a server is reachable. Start the fixed test stack:

    docker compose -f docker-compose.pytest-db.yml up -d
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from dataclasses import dataclass, field

import pytest
from conftest import DATABASE_SKIP_REASON, postgres_dsn
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from conjectures_subnet.db import credits as ledger
from conjectures_subnet.db import transfers as store
from conjectures_subnet.db.errors import RecordConflict
from conjectures_subnet.db.engine import (
    async_session_factory,
    async_session_scope,
    create_async_db_engine,
)
from conjectures_subnet.db.models import (
    Account,
    AccountWallet,
    Base,
    ChainTransfer,
    ChainTransferState,
    CreditEntryKind,
    CreditLedgerEntry,
    Deposit,
    DepositState,
)
from conjectures_subnet.transfers import IncomingTransfer, ObservedBlock
from deposit_watcher.settings import SettingsError, WatcherSettings
from deposit_watcher.watcher import CONFLICT_NOTE, DepositWatcher

pytestmark = pytest.mark.skipif(postgres_dsn() is None, reason=DATABASE_SKIP_REASON)

RECIPIENT = "5Gn2SyG6PmBstAjiPD93CTuxADqYaYqf6fKeFuezKsX7Chf9"
COLDKEY = "5HMqFHmvUpzuAjEnse3hzMKS5LsFL428hffCfenF2smuGNhs"
OTHER_COLDKEY = "5EZotmLfrufXYvD6CCGsRRELEFdg9SnjaEzTmaemiBPNofBP"
STRANGER = "5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy"

RAO_PER_TAO = 1_000_000_000
CREDIT_PRICE = RAO_PER_TAO // 2  # 0.5 TAO buys one submission
NETUID = 66
UID = 121

EPOCH = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
BLOCK_SECONDS = 12
# Two days into the fake chain, so the bisection has room to search either side.
WATCH_FROM = EPOCH + dt.timedelta(days=2)
START_BLOCK = int(WATCH_FROM.timestamp() - EPOCH.timestamp()) // BLOCK_SECONDS


def run(coroutine):
    return asyncio.run(coroutine)


def block_time(number: int) -> dt.datetime:
    return EPOCH + dt.timedelta(seconds=number * BLOCK_SECONDS)


# --- Fakes -------------------------------------------------------------------------------


@dataclass
class FakeChain:
    """A chain with twelve-second blocks and a scripted set of transfers per block."""

    head: int
    transfers: dict[int, list[IncomingTransfer]] = field(default_factory=dict)
    registered: str | None = RECIPIENT
    block_reads: list[int] = field(default_factory=list)
    transfer_reads: list[int] = field(default_factory=list)
    fail_on_block: int | None = None

    def add(
        self,
        *,
        block: int,
        sender: str,
        amount_rao: int,
        extrinsic_index: int = 1,
        event_index: int = 0,
        recipient: str = RECIPIENT,
    ) -> IncomingTransfer:
        arrival = IncomingTransfer(
            block=block,
            block_timestamp=block_time(block),
            extrinsic_index=extrinsic_index,
            event_index=event_index,
            sender=sender,
            recipient=recipient,
            amount_rao=amount_rao,
        )
        self.transfers.setdefault(block, []).append(arrival)
        return arrival

    async def finalized_head(self) -> int:
        return self.head

    async def block(self, number: int) -> ObservedBlock:
        self.block_reads.append(number)
        return ObservedBlock(
            number=number, hash=f"0x{number:064x}", timestamp=block_time(number)
        )

    async def transfers_to(self, *, recipient: str, block: int):
        self.transfer_reads.append(block)
        if block == self.fail_on_block:
            raise RuntimeError(f"the node refused block {block}")
        return [
            item for item in self.transfers.get(block, []) if item.recipient == recipient
        ]

    async def hotkey_at(self, *, netuid: int, uid: int) -> str | None:
        if (netuid, uid) != (NETUID, UID):
            return None
        return self.registered


def settings(**overrides) -> WatcherSettings:
    env = {
        "APP_MODE": "DEV",
        "DEPOSIT_WATCH_RECIPIENT_SS58": RECIPIENT,
        "DEPOSIT_WATCH_NETUID": str(NETUID),
        "DEPOSIT_WATCH_UID": str(UID),
        "DEPOSIT_WATCH_FROM": WATCH_FROM.isoformat(),
        "CREDIT_PRICE_RAO": str(CREDIT_PRICE),
        "DEPOSIT_WATCH_BATCH_BLOCKS": "50",
        "DEPOSIT_WATCH_POLL_SECONDS": "0.01",
    }
    env.update({key: str(value) for key, value in overrides.items()})
    return WatcherSettings.from_env(env)


@dataclass
class Kit:
    engine: AsyncEngine

    @classmethod
    async def setup(cls) -> Kit:
        engine = create_async_db_engine(postgres_dsn())
        async with engine.begin() as connection:
            # The server is reused across tests, so start from a clean schema each time.
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
        return cls(engine=engine)

    async def teardown(self) -> None:
        await self.engine.dispose()

    @property
    def sessions(self):
        return async_session_factory(self.engine)

    def watcher(self, chain: FakeChain, **overrides) -> DepositWatcher:
        return DepositWatcher(
            settings=settings(**overrides), sessions=self.sessions, source=chain
        )

    async def account(self, *, coldkey: str | None = None, payout: str | None = None):
        """One account, optionally with a signed wallet and/or a payout coldkey."""
        async with async_session_scope(self.sessions) as session:
            account = Account(
                email=f"{uuid.uuid4().hex[:12]}@example.com", email_verified=True
            )
            if payout is not None:
                account.payout_coldkey = payout
                account.payout_hotkey = RECIPIENT
            session.add(account)
            await session.flush()
            if coldkey is not None:
                session.add(
                    AccountWallet(
                        account_id=account.id, coldkey=coldkey, signature=bytes(64)
                    )
                )
            await session.flush()
            return account.id

    async def balance(self, account_id) -> ledger.CreditBalance:
        async with async_session_scope(self.sessions) as session:
            return await ledger.credit_balance(
                session,
                account_id,
                credit_price_rao=CREDIT_PRICE,
                now=dt.datetime.now(dt.UTC),
            )

    async def transfer_rows(self):
        async with async_session_scope(self.sessions) as session:
            return list(
                (
                    await session.execute(
                        select(ChainTransfer).order_by(ChainTransfer.id)
                    )
                )
                .scalars()
                .all()
            )

    async def counts(self):
        async with async_session_scope(self.sessions) as session:
            transfers = (
                await session.execute(select(func.count()).select_from(ChainTransfer))
            ).scalar_one()
            entries = (
                await session.execute(
                    select(func.count()).select_from(CreditLedgerEntry)
                )
            ).scalar_one()
            deposits = (
                await session.execute(select(func.count()).select_from(Deposit))
            ).scalar_one()
            return transfers, entries, deposits


def with_kit(body):
    """Run `body(kit)` against a freshly migrated database."""

    async def wrapper():
        kit = await Kit.setup()
        try:
            return await body(kit)
        finally:
            await kit.teardown()

    return run(wrapper())


# --- Startup -----------------------------------------------------------------------------


def test_the_watched_address_must_be_the_hotkey_at_the_configured_uid():
    """The only check that the money being watched is this validator's own.

    A mistyped address does not fail — it produces a watcher that runs forever, reads real
    blocks, and credits nothing, which looks exactly like "no customers today".
    """

    async def body(kit: Kit):
        chain = FakeChain(head=START_BLOCK + 10, registered=STRANGER)
        with pytest.raises(SettingsError, match="not the configured"):
            await kit.watcher(chain).verify_identity()

        empty = FakeChain(head=START_BLOCK + 10, registered=None)
        with pytest.raises(SettingsError, match="no registered"):
            await kit.watcher(empty).verify_identity()

        good = FakeChain(head=START_BLOCK + 10)
        assert await kit.watcher(good).verify_identity() == RECIPIENT

    with_kit(body)


def test_the_cursor_starts_at_the_block_the_genesis_timestamp_resolves_to():
    """Nothing scanned yet means `start_block - 1`. Off by one here would skip the very first
    block the genesis timestamp was chosen to include."""

    async def body(kit: Kit):
        chain = FakeChain(head=START_BLOCK + 500)
        cursor = await kit.watcher(chain).resolve_cursor()

        assert cursor.start_block == START_BLOCK
        assert cursor.last_scanned_block == START_BLOCK - 1
        assert cursor.recipient == RECIPIENT
        assert cursor.netuid == NETUID and cursor.uid == UID
        assert cursor.start_block_timestamp == WATCH_FROM

    with_kit(body)


def test_the_bisection_runs_once_and_the_start_block_is_never_recomputed():
    """A second search against a re-synced node could land either side of the same boundary,
    which would change which transfers the watcher believes exist at all."""

    async def body(kit: Kit):
        chain = FakeChain(head=START_BLOCK + 500)
        first = await kit.watcher(chain).resolve_cursor()
        probes = len(chain.block_reads)
        assert probes > 1  # it really did search

        chain.block_reads.clear()
        again = await kit.watcher(chain).resolve_cursor()

        assert again.start_block == first.start_block
        assert chain.block_reads == []  # loaded, not re-derived

    with_kit(body)


def test_a_cursor_that_disagrees_with_the_configuration_is_a_refusal():
    """Adopting a new address silently would leave the old one's arrivals uncredited; moving
    the genesis timestamp earlier silently would re-scan a range that was never meant to buy
    credits. Both are operator decisions."""

    async def body(kit: Kit):
        chain = FakeChain(head=START_BLOCK + 500)
        await kit.watcher(chain).resolve_cursor()

        with pytest.raises(SettingsError, match="recipient"):
            await kit.watcher(
                chain, DEPOSIT_WATCH_RECIPIENT_SS58=OTHER_COLDKEY
            ).resolve_cursor()
        with pytest.raises(SettingsError, match="watch_from"):
            await kit.watcher(
                chain,
                DEPOSIT_WATCH_FROM=(WATCH_FROM - dt.timedelta(days=1)).isoformat(),
            ).resolve_cursor()
        with pytest.raises(SettingsError, match="uid"):
            await kit.watcher(chain, DEPOSIT_WATCH_UID="7").resolve_cursor()

    with_kit(body)


# --- Crediting ---------------------------------------------------------------------------


def test_half_a_tao_buys_one_credit():
    async def body(kit: Kit):
        account_id = await kit.account(coldkey=COLDKEY)
        chain = FakeChain(head=START_BLOCK + 3)
        chain.add(block=START_BLOCK + 1, sender=COLDKEY, amount_rao=CREDIT_PRICE)

        watcher = kit.watcher(chain)
        await watcher.resolve_cursor()
        passes = await watcher.catch_up()

        assert sum(item.credited for item in passes) == 1
        assert sum(item.credits_granted for item in passes) == 1
        balance = await kit.balance(account_id)
        assert balance.credits_available == 1
        assert balance.balance_rao == CREDIT_PRICE
        assert balance.remainder_rao == 0

    with_kit(body)


def test_two_and_a_half_tao_buys_five_credits():
    """The price arithmetic on its own, with no deal on offer.

    `CREDIT_PACKAGES` is pinned to the single credit deliberately: 2.5 TAO is exactly the
    pay-for-5 deal in the shipped configuration, and this test is about rao dividing into
    credits, not about bonuses. The deals get their own tests below.
    """

    async def body(kit: Kit):
        account_id = await kit.account(coldkey=COLDKEY)
        chain = FakeChain(head=START_BLOCK + 3)
        chain.add(block=START_BLOCK + 1, sender=COLDKEY, amount_rao=5 * CREDIT_PRICE)

        watcher = kit.watcher(chain, CREDIT_PACKAGES="1")
        await watcher.resolve_cursor()
        await watcher.catch_up()

        assert (await kit.balance(account_id)).credits_available == 5

    with_kit(body)


# --- Package deals -----------------------------------------------------------------------
# The watcher is the service that grants a bonus for a treasury transfer, so this is where the
# advertised deal either becomes real or silently does not. `CREDIT_PACKAGES` is stated in each
# test rather than relying on the default, so a change to the shipped deals cannot quietly
# rewrite what these assert.

DEALS = "1,5:1,10:3"


def test_a_transfer_landing_on_a_deal_is_granted_its_bonus():
    async def body(kit: Kit):
        account_id = await kit.account(coldkey=COLDKEY)
        chain = FakeChain(head=START_BLOCK + 3)
        chain.add(block=START_BLOCK + 1, sender=COLDKEY, amount_rao=5 * CREDIT_PRICE)

        watcher = kit.watcher(chain, CREDIT_PACKAGES=DEALS)
        await watcher.resolve_cursor()
        await watcher.catch_up()

        # Six spendable credits for five credits' worth of TAO.
        balance = await kit.balance(account_id)
        assert balance.credits_available == 6
        assert balance.balance_rao == 6 * CREDIT_PRICE
        assert balance.remainder_rao == 0

        # Two entries: what arrived, and what was given. The bonus explains itself, because it
        # names no deposit — `credit_ledger_tmc_pay_idx` forbids that on the other path, so both
        # paths carry the provenance in `reason` instead.
        async with async_session_scope(kit.sessions) as session:
            rows = list(
                (
                    await session.execute(
                        select(CreditLedgerEntry).order_by(CreditLedgerEntry.id)
                    )
                )
                .scalars()
                .all()
            )
        assert [str(row.kind) for row in rows] == ["DEPOSIT", "BONUS"]
        assert rows[0].amount_rao == 5 * CREDIT_PRICE
        assert rows[1].amount_rao == CREDIT_PRICE
        assert rows[1].deposit_id is None
        assert rows[1].reason == (
            "package bonus: 1 credit(s) granted with a 5-credit purchase"
        )


    with_kit(body)


def test_the_larger_deal_grants_three_free_credits():
    async def body(kit: Kit):
        account_id = await kit.account(coldkey=COLDKEY)
        chain = FakeChain(head=START_BLOCK + 3)
        chain.add(block=START_BLOCK + 1, sender=COLDKEY, amount_rao=10 * CREDIT_PRICE)

        watcher = kit.watcher(chain, CREDIT_PACKAGES=DEALS)
        await watcher.resolve_cursor()
        await watcher.catch_up()

        assert (await kit.balance(account_id)).credits_available == 13

    with_kit(body)


def test_an_amount_that_misses_a_deal_earns_no_bonus():
    """Two accounts, two off-package amounts, one pass.

    Six paid credits is not a deal, and five credits' worth plus a single rao is not the
    five-credit deal — the rule needs a zero remainder, so there is no band of overpayment that
    silently qualifies. The full off-package matrix is checked against the pure lookup in
    `test_the_bonus_lookup_is_keyed_on_the_paid_credit_count`; this is the end-to-end half.
    """

    async def body(kit: Kit):
        six = await kit.account(coldkey=COLDKEY)
        just_over = await kit.account(coldkey=OTHER_COLDKEY)
        chain = FakeChain(head=START_BLOCK + 5)
        chain.add(block=START_BLOCK + 1, sender=COLDKEY, amount_rao=6 * CREDIT_PRICE)
        chain.add(
            block=START_BLOCK + 2,
            sender=OTHER_COLDKEY,
            amount_rao=5 * CREDIT_PRICE + 1,
        )

        watcher = kit.watcher(chain, CREDIT_PACKAGES=DEALS)
        await watcher.resolve_cursor()
        await watcher.catch_up()

        assert (await kit.balance(six)).credits_available == 6
        over = await kit.balance(just_over)
        assert over.credits_available == 5
        assert over.remainder_rao == 1

        # No BONUS entry was written at all, for either of them.
        async with async_session_scope(kit.sessions) as session:
            kinds = [
                str(row.kind)
                for row in (
                    await session.execute(
                        select(CreditLedgerEntry).order_by(CreditLedgerEntry.id)
                    )
                )
                .scalars()
                .all()
            ]
        assert kinds == ["DEPOSIT", "DEPOSIT"]

    with_kit(body)


def test_a_remainder_is_kept_and_counts_towards_the_next_credit():
    """0.7 TAO buys one credit and leaves 0.2. Discarding the remainder would quietly keep
    money that bought nothing; two 0.3 TAO transfers must add up to one credit."""

    async def body(kit: Kit):
        account_id = await kit.account(coldkey=COLDKEY)
        chain = FakeChain(head=START_BLOCK + 5)
        seven_tenths = 700_000_000
        chain.add(block=START_BLOCK + 1, sender=COLDKEY, amount_rao=seven_tenths)

        watcher = kit.watcher(chain)
        await watcher.resolve_cursor()
        await watcher.catch_up()

        balance = await kit.balance(account_id)
        assert balance.credits_available == 1
        assert balance.remainder_rao == seven_tenths - CREDIT_PRICE
        rows = await kit.transfer_rows()
        assert rows[0].credits_granted == 1  # what this transfer bought on its own

        # A second arrival, in a block beyond the head the first pass reached, tops the
        # remainder up over the line.
        chain.add(block=START_BLOCK + 6, sender=COLDKEY, amount_rao=300_000_000)
        chain.head = START_BLOCK + 8
        await watcher.catch_up()

        assert (await kit.balance(account_id)).credits_available == 2

    with_kit(body)


def test_a_transfer_smaller_than_one_credit_is_credited_but_buys_nothing_yet():
    async def body(kit: Kit):
        account_id = await kit.account(coldkey=COLDKEY)
        chain = FakeChain(head=START_BLOCK + 3)
        chain.add(block=START_BLOCK + 1, sender=COLDKEY, amount_rao=100_000_000)

        watcher = kit.watcher(chain)
        await watcher.resolve_cursor()
        await watcher.catch_up()

        balance = await kit.balance(account_id)
        assert balance.credits_available == 0
        assert balance.balance_rao == 100_000_000  # not lost
        rows = await kit.transfer_rows()
        assert rows[0].status is ChainTransferState.CREDITED
        assert rows[0].credits_granted == 0

    with_kit(body)


def test_every_credited_transfer_has_a_deposit_and_a_ledger_entry():
    """`credit_ledger`'s own `ledger_deposit_names_its_deposit` check requires it, and it is
    what keeps a single explanation of where each ledger entry came from."""

    async def body(kit: Kit):
        account_id = await kit.account(coldkey=COLDKEY)
        chain = FakeChain(head=START_BLOCK + 3)
        arrival = chain.add(
            block=START_BLOCK + 1, sender=COLDKEY, amount_rao=2 * CREDIT_PRICE
        )

        watcher = kit.watcher(chain)
        await watcher.resolve_cursor()
        await watcher.catch_up()

        async with async_session_scope(kit.sessions) as session:
            transfer = (
                await session.execute(select(ChainTransfer))
            ).scalar_one()
            deposit = await session.get(Deposit, transfer.deposit_id)
            entry = await session.get(CreditLedgerEntry, deposit.credited_ledger_id)

        assert transfer.account_id == account_id
        assert deposit.status is DepositState.CREDITED
        assert deposit.extrinsic_reference == arrival.reference
        assert deposit.sender_coldkey == COLDKEY
        assert deposit.observed_amount_rao == 2 * CREDIT_PRICE
        assert deposit.block == arrival.block
        assert entry.kind is CreditEntryKind.DEPOSIT
        assert entry.amount_rao == 2 * CREDIT_PRICE
        assert entry.created_by == "deposit-watcher"

    with_kit(body)


def test_an_arrival_settles_a_declared_deposit_of_the_same_amount():
    """A declared deposit exists so confirmation can *check* a transfer. When one matches, the
    arrival closes it rather than leaving it AWAITING_TRANSFER beside a second deposit row."""

    async def body(kit: Kit):
        account_id = await kit.account(coldkey=COLDKEY)
        async with async_session_scope(kit.sessions) as session:
            declared = await ledger.create_deposit(
                session,
                account_id=account_id,
                amount_rao=4 * CREDIT_PRICE,
                treasury_address=RECIPIENT,
                credit_price_rao=CREDIT_PRICE,
                expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(hours=24),
            )
            declared_id = declared.id

        chain = FakeChain(head=START_BLOCK + 3)
        chain.add(block=START_BLOCK + 1, sender=COLDKEY, amount_rao=4 * CREDIT_PRICE)
        watcher = kit.watcher(chain)
        await watcher.resolve_cursor()
        await watcher.catch_up()

        rows = await kit.transfer_rows()
        assert rows[0].deposit_id == declared_id
        _, _, deposits = await kit.counts()
        assert deposits == 1  # settled, not shadowed by a second row

    with_kit(body)


def test_a_declared_deposit_for_a_different_amount_is_left_alone():
    """Exact on the amount. A looser match would let one 10 TAO arrival close a 1 TAO
    declaration and leave the other 9 unaccounted for."""

    async def body(kit: Kit):
        account_id = await kit.account(coldkey=COLDKEY)
        async with async_session_scope(kit.sessions) as session:
            declared = await ledger.create_deposit(
                session,
                account_id=account_id,
                amount_rao=20 * CREDIT_PRICE,
                treasury_address=RECIPIENT,
                credit_price_rao=CREDIT_PRICE,
                expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(hours=24),
            )
            declared_id = declared.id

        chain = FakeChain(head=START_BLOCK + 3)
        chain.add(block=START_BLOCK + 1, sender=COLDKEY, amount_rao=2 * CREDIT_PRICE)
        watcher = kit.watcher(chain)
        await watcher.resolve_cursor()
        await watcher.catch_up()

        rows = await kit.transfer_rows()
        assert rows[0].deposit_id != declared_id
        async with async_session_scope(kit.sessions) as session:
            untouched = await session.get(Deposit, declared_id)
        assert untouched.status is DepositState.AWAITING_TRANSFER
        # Credited for what arrived, not for what was declared.
        assert (await kit.balance(account_id)).balance_rao == 2 * CREDIT_PRICE

    with_kit(body)


# --- Attribution -------------------------------------------------------------------------


def test_a_transfer_from_an_unknown_coldkey_is_recorded_and_left_for_a_human():
    """Money that arrived must exist in the database whether or not the validator can work out
    whose it is. Crediting a best-effort match would hand one account's money to another."""

    async def body(kit: Kit):
        chain = FakeChain(head=START_BLOCK + 3)
        chain.add(block=START_BLOCK + 1, sender=STRANGER, amount_rao=10 * CREDIT_PRICE)

        watcher = kit.watcher(chain)
        await watcher.resolve_cursor()
        passes = await watcher.catch_up()

        assert sum(item.unattributed for item in passes) == 1
        rows = await kit.transfer_rows()
        assert rows[0].status is ChainTransferState.UNATTRIBUTED
        assert rows[0].account_id is None
        assert rows[0].credits_granted == 0
        assert "no account owns" in rows[0].note
        transfers, entries, _ = await kit.counts()
        assert (transfers, entries) == (1, 0)  # recorded, nothing credited

        async with async_session_scope(kit.sessions) as session:
            queue = await store.unattributed(session, limit=10)
        assert [item.sender_coldkey for item in queue] == [STRANGER]

    with_kit(body)


def test_a_signed_wallet_coldkey_wins_over_a_payout_coldkey():
    """The wallet signature is the strongest claim the system has. A payout coldkey is a value
    the account typed rather than one it signed for."""

    async def body(kit: Kit):
        signed = await kit.account(coldkey=COLDKEY)
        await kit.account(payout=COLDKEY)

        async with async_session_scope(kit.sessions) as session:
            assert await store.account_for_coldkey(session, COLDKEY) == signed

    with_kit(body)


def test_a_payout_coldkey_shared_by_two_accounts_resolves_to_neither():
    """There is no tie to break, and crediting an arbitrary one would hand somebody else's
    money over on a typo. The transfer waits for a human, which cannot be wrong."""

    async def body(kit: Kit):
        await kit.account(payout=OTHER_COLDKEY)
        await kit.account(payout=OTHER_COLDKEY)

        async with async_session_scope(kit.sessions) as session:
            assert await store.account_for_coldkey(session, OTHER_COLDKEY) is None

        chain = FakeChain(head=START_BLOCK + 3)
        chain.add(block=START_BLOCK + 1, sender=OTHER_COLDKEY, amount_rao=CREDIT_PRICE)
        watcher = kit.watcher(chain)
        await watcher.resolve_cursor()
        await watcher.catch_up()

        rows = await kit.transfer_rows()
        assert rows[0].status is ChainTransferState.UNATTRIBUTED

    with_kit(body)


def test_the_treasury_paying_itself_is_ignored_with_a_reason():
    async def body(kit: Kit):
        chain = FakeChain(head=START_BLOCK + 3)
        chain.add(block=START_BLOCK + 1, sender=RECIPIENT, amount_rao=CREDIT_PRICE)

        watcher = kit.watcher(chain)
        await watcher.resolve_cursor()
        passes = await watcher.catch_up()

        assert sum(item.ignored for item in passes) == 1
        rows = await kit.transfer_rows()
        assert rows[0].status is ChainTransferState.IGNORED
        assert rows[0].note  # IGNORED requires one at the database level
        _, entries, _ = await kit.counts()
        assert entries == 0

    with_kit(body)


# --- One transfer is spent once, whichever path gets it ----------------------------------
#
# Both funding paths reach the same treasury address and share one canonical reference format, so
# without arbitration a miner could pay 0.5 TAO, cite it for a submission, AND have the watcher
# credit the same transfer to their account. `chain_transfers.extrinsic_reference` is unique, so
# that table is the only place that can decide. These are the two orders it can happen in.


def _arrival(block, *, sender=COLDKEY, amount=CREDIT_PRICE, extrinsic=3, event=1):
    return IncomingTransfer(
        block=block,
        block_timestamp=block_time(block),
        extrinsic_index=extrinsic,
        event_index=event,
        sender=sender,
        recipient=RECIPIENT,
        amount_rao=amount,
    )


def test_a_transfer_the_api_spent_on_a_submission_is_not_credited_as_well():
    """API first. The watcher must find the transfer already spent and credit nothing."""

    async def body(kit: Kit):
        account_id = await kit.account(coldkey=COLDKEY)
        arrival = _arrival(START_BLOCK + 1)
        submission_id = uuid.uuid4()

        # The API confirms the payment and spends it, in its own transaction.
        async with async_session_scope(kit.sessions) as session:
            await store.claim_for_submission(
                session,
                extrinsic_reference=arrival.reference,
                block=arrival.block,
                block_timestamp=arrival.block_timestamp,
                extrinsic_index=arrival.extrinsic_index,
                event_index=arrival.event_index,
                sender_coldkey=arrival.sender,
                recipient=arrival.recipient,
                amount_rao=arrival.amount_rao,
                note=f"funded submission {submission_id} directly",
            )

        chain = FakeChain(head=START_BLOCK + 3)
        chain.transfers.setdefault(arrival.block, []).append(arrival)
        watcher = kit.watcher(chain)
        await watcher.resolve_cursor()
        passes = await watcher.catch_up()

        assert sum(item.credited for item in passes) == 0
        rows = await kit.transfer_rows()
        assert rows[0].status is ChainTransferState.IGNORED
        assert str(submission_id) in rows[0].note
        # No credits, and no ledger entry at all.
        _, entries, _ = await kit.counts()
        assert entries == 0
        assert (await kit.balance(account_id)).credits_available == 0

    with_kit(body)


def test_a_transfer_the_watcher_credited_cannot_then_fund_a_submission():
    """Watcher first. The API must be refused, so the miner spends the credit they were given
    rather than getting an attempt for free on top of it."""

    async def body(kit: Kit):
        account_id = await kit.account(coldkey=COLDKEY)
        arrival = _arrival(START_BLOCK + 1, amount=2 * CREDIT_PRICE)

        chain = FakeChain(head=START_BLOCK + 3)
        chain.transfers.setdefault(arrival.block, []).append(arrival)
        watcher = kit.watcher(chain)
        await watcher.resolve_cursor()
        await watcher.catch_up()
        assert (await kit.balance(account_id)).credits_available == 2

        # Now the API tries to fund a submission with the same transfer.
        with pytest.raises(RecordConflict) as raised:
            async with async_session_scope(kit.sessions) as session:
                await store.claim_for_submission(
                    session,
                    extrinsic_reference=arrival.reference,
                    block=arrival.block,
                    block_timestamp=arrival.block_timestamp,
                    extrinsic_index=arrival.extrinsic_index,
                    event_index=arrival.event_index,
                    sender_coldkey=arrival.sender,
                    recipient=arrival.recipient,
                    amount_rao=arrival.amount_rao,
                    note="funded a submission directly",
                )

        assert raised.value.reason_code == "TRANSFER_ALREADY_CREDITED"
        # The message tells the miner what to do instead, because they were not robbed.
        assert "spend a credit" in raised.value.message
        # And the credits are untouched.
        assert (await kit.balance(account_id)).credits_available == 2

    with_kit(body)


def test_one_transfer_cannot_fund_two_submissions():
    """The second claim is refused before it can reach the submission write."""

    async def body(kit: Kit):
        arrival = _arrival(START_BLOCK + 1)
        async with async_session_scope(kit.sessions) as session:
            await store.claim_for_submission(
                session,
                extrinsic_reference=arrival.reference,
                block=arrival.block,
                block_timestamp=arrival.block_timestamp,
                extrinsic_index=arrival.extrinsic_index,
                event_index=arrival.event_index,
                sender_coldkey=arrival.sender,
                recipient=arrival.recipient,
                amount_rao=arrival.amount_rao,
                note="funded submission one",
            )

        with pytest.raises(RecordConflict) as raised:
            async with async_session_scope(kit.sessions) as session:
                await store.claim_for_submission(
                    session,
                    extrinsic_reference=arrival.reference,
                    block=arrival.block,
                    block_timestamp=arrival.block_timestamp,
                    extrinsic_index=arrival.extrinsic_index,
                    event_index=arrival.event_index,
                    sender_coldkey=arrival.sender,
                    recipient=arrival.recipient,
                    amount_rao=arrival.amount_rao,
                    note="funded submission two",
                )

        assert raised.value.reason_code == "DUPLICATE_PAYMENT"
        transfers, _, _ = await kit.counts()
        assert transfers == 1  # one transfer, one row, whatever tried to claim it

    with_kit(body)


def test_the_api_can_spend_a_transfer_the_watcher_left_unattributed():
    """The common case when the payer has no website account: the watcher records the arrival and
    leaves it for a human, and the miner then cites it for a submission. That must work."""

    async def body(kit: Kit):
        arrival = _arrival(START_BLOCK + 1, sender=STRANGER)
        chain = FakeChain(head=START_BLOCK + 3)
        chain.transfers.setdefault(arrival.block, []).append(arrival)
        watcher = kit.watcher(chain)
        await watcher.resolve_cursor()
        await watcher.catch_up()

        rows = await kit.transfer_rows()
        assert rows[0].status is ChainTransferState.UNATTRIBUTED

        async with async_session_scope(kit.sessions) as session:
            claimed = await store.claim_for_submission(
                session,
                extrinsic_reference=arrival.reference,
                block=arrival.block,
                block_timestamp=arrival.block_timestamp,
                extrinsic_index=arrival.extrinsic_index,
                event_index=arrival.event_index,
                sender_coldkey=arrival.sender,
                recipient=arrival.recipient,
                amount_rao=arrival.amount_rao,
                note="funded submission abc directly",
            )
        assert claimed.status is ChainTransferState.IGNORED
        transfers, entries, _ = await kit.counts()
        assert (transfers, entries) == (1, 0)

    with_kit(body)


# --- Idempotency and the cursor ----------------------------------------------------------


def test_re_reading_a_block_credits_nothing_a_second_time():
    """The watcher re-reads blocks after a restart, so this is the normal case rather than an
    error. Two unique indexes stand behind it."""

    async def body(kit: Kit):
        account_id = await kit.account(coldkey=COLDKEY)
        chain = FakeChain(head=START_BLOCK + 3)
        chain.add(block=START_BLOCK + 1, sender=COLDKEY, amount_rao=3 * CREDIT_PRICE)

        watcher = kit.watcher(chain)
        await watcher.resolve_cursor()
        await watcher.catch_up()

        # Rewind the cursor by hand, as a crash between recording and advancing would leave it,
        # and read the same blocks again.
        async with async_session_scope(kit.sessions) as session:
            cursor = await store.cursor(session)
            cursor.last_scanned_block = START_BLOCK - 1
        second = await watcher.catch_up()

        assert sum(item.recorded for item in second) == 0
        assert sum(item.credited for item in second) == 0
        transfers, entries, deposits = await kit.counts()
        assert (transfers, entries, deposits) == (1, 1, 1)
        assert (await kit.balance(account_id)).credits_available == 3

    with_kit(body)


def test_two_transfers_in_one_extrinsic_are_both_credited():
    """A utility.batch. Keying on the extrinsic alone would silently refuse the second one."""

    async def body(kit: Kit):
        account_id = await kit.account(coldkey=COLDKEY)
        chain = FakeChain(head=START_BLOCK + 3)
        chain.add(
            block=START_BLOCK + 1,
            sender=COLDKEY,
            amount_rao=CREDIT_PRICE,
            extrinsic_index=4,
            event_index=0,
        )
        chain.add(
            block=START_BLOCK + 1,
            sender=COLDKEY,
            amount_rao=CREDIT_PRICE,
            extrinsic_index=4,
            event_index=2,
        )

        watcher = kit.watcher(chain)
        await watcher.resolve_cursor()
        await watcher.catch_up()

        transfers, entries, _ = await kit.counts()
        assert (transfers, entries) == (2, 2)
        assert (await kit.balance(account_id)).credits_available == 2

    with_kit(body)


def test_a_conflicting_arrival_does_not_discard_an_earlier_one_in_the_same_block():
    """The regression this module's per-arrival transaction exists for.

    `credits.credit_deposit` rolls its session back on an insert conflict, and a rollback discards
    everything the transaction holds. With one transaction per block, a conflict on the second
    arrival threw away the first — and the cursor then advanced past both, so the first was money
    that arrived, was discarded, and was never read again.

    The conflict is reachable: the API's claim endpoint attaches an extrinsic reference to a
    deposit before any credit is issued, so a deposit can already hold the reference the watcher
    is about to write.
    """

    async def body(kit: Kit):
        account_id = await kit.account(coldkey=COLDKEY)
        chain = FakeChain(head=START_BLOCK + 2)
        first = chain.add(
            block=START_BLOCK + 1,
            sender=COLDKEY,
            amount_rao=2 * CREDIT_PRICE,
            extrinsic_index=1,
        )
        second = chain.add(
            block=START_BLOCK + 1,
            sender=COLDKEY,
            amount_rao=6 * CREDIT_PRICE,
            extrinsic_index=2,
        )
        # A deposit already carrying the second arrival's reference, as a claim would leave it.
        # Crediting that arrival must conflict on `deposits_extrinsic_idx`.
        async with async_session_scope(kit.sessions) as session:
            claimed = await ledger.create_deposit(
                session,
                account_id=account_id,
                amount_rao=99 * CREDIT_PRICE,
                treasury_address=RECIPIENT,
                credit_price_rao=CREDIT_PRICE,
                expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(hours=24),
            )
            await ledger.mark_seen(
                session, claimed, extrinsic_reference=second.reference
            )

        watcher = kit.watcher(chain)
        await watcher.resolve_cursor()
        passes = await watcher.catch_up()

        # The first arrival survived and was credited; the second is reported as a conflict.
        assert sum(item.credited for item in passes) == 1
        assert any(second.reference in item for pass_ in passes for item in pass_.errors)
        rows = {row.extrinsic_reference: row for row in await kit.transfer_rows()}
        assert rows[first.reference].status is ChainTransferState.CREDITED
        assert (await kit.balance(account_id)).credits_available == 2

        # And the second is still on the queue rather than lost: its row exists, uncredited, and
        # says why. Its own settlement rolled back, so committing the record separately is what
        # keeps the arrival visible at all.
        survivor = rows[second.reference]
        assert survivor.status is ChainTransferState.UNATTRIBUTED
        assert survivor.amount_rao == 6 * CREDIT_PRICE
        assert survivor.credits_granted == 0
        assert CONFLICT_NOTE in survivor.note
        # The conflicting deposit is untouched — it still holds the reference it claimed.
        async with async_session_scope(kit.sessions) as session:
            still_seen = (
                await session.execute(
                    select(Deposit).where(
                        Deposit.extrinsic_reference == second.reference
                    )
                )
            ).scalar_one()
        assert still_seen.status is DepositState.SEEN_UNFINALIZED

    with_kit(body)


def test_the_cursor_stops_at_a_block_the_chain_refused():
    """The cursor did not move, so the block is retried on the next pass. A node that answers
    badly costs a delay and never a missed transfer."""

    async def body(kit: Kit):
        account_id = await kit.account(coldkey=COLDKEY)
        chain = FakeChain(head=START_BLOCK + 5, fail_on_block=START_BLOCK + 3)
        chain.add(block=START_BLOCK + 1, sender=COLDKEY, amount_rao=CREDIT_PRICE)
        chain.add(block=START_BLOCK + 4, sender=COLDKEY, amount_rao=CREDIT_PRICE)

        watcher = kit.watcher(chain)
        await watcher.resolve_cursor()
        with pytest.raises(RuntimeError, match="refused block"):
            await watcher.catch_up()

        async with async_session_scope(kit.sessions) as session:
            cursor = await store.cursor(session)
        assert cursor.last_scanned_block == START_BLOCK + 2
        assert (await kit.balance(account_id)).credits_available == 1

        # The node recovers and the pass resumes from the block it stopped at.
        chain.fail_on_block = None
        await watcher.catch_up()
        assert (await kit.balance(account_id)).credits_available == 2

    with_kit(body)


def test_the_cursor_never_moves_backwards():
    """A fallback endpoint mid-sync can answer with a lower head. Rewinding would make the
    watcher re-read blocks it has already credited, forever."""

    async def body(kit: Kit):
        chain = FakeChain(head=START_BLOCK + 20)
        watcher = kit.watcher(chain)
        await watcher.resolve_cursor()
        await watcher.catch_up()

        async with async_session_scope(kit.sessions) as session:
            await store.advance_cursor(
                session, through_block=START_BLOCK + 5, now=dt.datetime.now(dt.UTC)
            )
            cursor = await store.cursor(session)
        assert cursor.last_scanned_block == START_BLOCK + 20

    with_kit(body)


def test_nothing_before_the_genesis_timestamp_is_ever_read():
    """The whole point of the genesis timestamp. A transfer in an earlier block buys nothing
    because the watcher never looks at that block at all."""

    async def body(kit: Kit):
        await kit.account(coldkey=COLDKEY)
        chain = FakeChain(head=START_BLOCK + 3)
        chain.add(block=START_BLOCK - 100, sender=COLDKEY, amount_rao=50 * CREDIT_PRICE)
        chain.add(block=START_BLOCK + 1, sender=COLDKEY, amount_rao=CREDIT_PRICE)

        watcher = kit.watcher(chain)
        await watcher.resolve_cursor()
        chain.transfer_reads.clear()
        await watcher.catch_up()

        assert min(chain.transfer_reads) == START_BLOCK
        transfers, _, _ = await kit.counts()
        assert transfers == 1

    with_kit(body)


def test_scan_once_returns_none_when_the_head_has_not_moved():
    async def body(kit: Kit):
        chain = FakeChain(head=START_BLOCK + 2)
        watcher = kit.watcher(chain)
        await watcher.resolve_cursor()
        await watcher.catch_up()

        assert await watcher.scan_once() is None

    with_kit(body)


def test_a_large_backlog_is_read_in_resumable_batches():
    """One enormous transaction is what a restart throws away. The batch bound is what makes
    progress visible and resumable."""

    async def body(kit: Kit):
        chain = FakeChain(head=START_BLOCK + 119)
        watcher = kit.watcher(chain, DEPOSIT_WATCH_BATCH_BLOCKS="50")
        await watcher.resolve_cursor()
        passes = await watcher.catch_up()

        assert [item.blocks for item in passes] == [50, 50, 20]
        async with async_session_scope(kit.sessions) as session:
            cursor = await store.cursor(session)
        assert cursor.last_scanned_block == START_BLOCK + 119

    with_kit(body)
