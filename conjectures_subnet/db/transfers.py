"""Observed transfers, the watcher's cursor, and the attribution that turns one into credits.

The chain-facing half lives in `conjectures_subnet/transfers.py`; this is everything durable.

Money, so the rules are stated rather than assumed:

* **A transfer is recorded before it is judged.** `record` writes the row first and attribution
  happens after, in a separate step. An arrival nobody can be matched to is money that arrived,
  and it must exist in the database whether or not the validator can work out whose it is.
* **Crediting goes through `credits.credit_deposit`.** Nothing here appends to the ledger
  directly. That function already owns the once-only rule — the unique index on
  `credited_ledger_id` and the partial one on `extrinsic_reference` — and a second path into the
  ledger would be a second place for the double-credit bug to live.
* **Idempotent on the extrinsic reference.** The watcher re-reads blocks after a restart, so
  `record` returning "already have this one" is the normal case, not an error.
* **Credits are derived, never stored.** `chain_transfers.credits_granted` is what one transfer
  bought on its own, for an operator reading the table. The balance is the ledger's rao and
  `credits.credit_balance` divides it, so the 0.2 TAO left over from a 0.7 TAO transfer counts
  towards the next credit instead of being discarded.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from conjectures_subnet.db import credits as ledger
from conjectures_subnet.db.errors import RecordConflict, RecordNotFound
from conjectures_subnet.db.models import (
    Account,
    AccountWallet,
    ChainTransfer,
    ChainTransferState,
    ChainWatchCursor,
    CreditLedgerEntry,
    Deposit,
    DepositState,
)

# The watcher this module's cursor row belongs to. A name rather than a bare row so a second
# watcher — a different treasury, a different subnet — is a new row and not a schema change.
DEPOSIT_WATCHER = "deposits"


@dataclass(frozen=True)
class RecordedTransfer:
    """One row after `record`, and whether this call is what created it.

    `created` is False for every block the watcher re-reads, which is the normal case after a
    restart. The caller uses it to decide whether attribution still has to be attempted, not to
    decide whether something went wrong.
    """

    transfer: ChainTransfer
    created: bool


# --- The cursor ---------------------------------------------------------------------


async def cursor(
    session: AsyncSession, *, watcher: str = DEPOSIT_WATCHER
) -> ChainWatchCursor | None:
    """The watcher's high-water mark, or None if it has never run."""
    return await session.get(ChainWatchCursor, watcher)


async def open_cursor(
    session: AsyncSession,
    *,
    watcher: str = DEPOSIT_WATCHER,
    recipient: str,
    netuid: int,
    uid: int,
    watch_from: dt.datetime,
    start_block: int,
    start_block_timestamp: dt.datetime,
) -> ChainWatchCursor:
    """Create the cursor for a watcher that has never run.

    `last_scanned_block` starts at `start_block - 1`: nothing has been read, and the first pass
    must therefore include `start_block` itself. Off by one here would skip the very first block
    the genesis timestamp was chosen to include.
    """
    row = ChainWatchCursor(
        watcher=watcher,
        recipient=recipient,
        netuid=netuid,
        uid=uid,
        watch_from=watch_from,
        start_block=start_block,
        start_block_timestamp=start_block_timestamp,
        last_scanned_block=start_block - 1,
    )
    session.add(row)
    await session.flush()
    return row


async def advance_cursor(
    session: AsyncSession,
    *,
    watcher: str = DEPOSIT_WATCHER,
    through_block: int,
    now: dt.datetime,
) -> ChainWatchCursor:
    """Move the high-water mark forward, never backwards.

    Monotonic on purpose. A node that answers with a lower finalized head than the last one —
    a fallback endpoint mid-sync, a restarted archive — would otherwise rewind the cursor and
    make the watcher re-read blocks it has already credited. That is safe (the unique index
    catches every duplicate) but it is wasted work that never ends, so it is refused here
    instead.
    """
    row = await session.get(ChainWatchCursor, watcher, with_for_update=True)
    if row is None:
        raise RecordNotFound(f"no cursor for watcher {watcher!r}")
    if through_block > row.last_scanned_block:
        row.last_scanned_block = through_block
        row.last_scanned_at = now
        await session.flush()
    return row


# --- Recording what arrived ---------------------------------------------------------


async def record(
    session: AsyncSession,
    *,
    extrinsic_reference: str,
    block: int,
    block_timestamp: dt.datetime,
    extrinsic_index: int,
    event_index: int,
    sender_coldkey: str,
    recipient: str,
    amount_rao: int,
) -> RecordedTransfer:
    """Write one observed transfer, or return the row that already holds it.

    `ON CONFLICT DO NOTHING` on the reference rather than a read-then-write: two watchers, or one
    watcher overlapping its own restart, must not race into two rows for one transfer. The insert
    is the check.
    """
    statement = (
        insert(ChainTransfer)
        .values(
            extrinsic_reference=extrinsic_reference,
            block=block,
            block_timestamp=block_timestamp,
            extrinsic_index=extrinsic_index,
            event_index=event_index,
            sender_coldkey=sender_coldkey,
            recipient=recipient,
            amount_rao=amount_rao,
            status=ChainTransferState.UNATTRIBUTED,
        )
        .on_conflict_do_nothing(index_elements=["extrinsic_reference"])
        .returning(ChainTransfer)
    )
    inserted = (await session.execute(statement)).scalar_one_or_none()
    if inserted is not None:
        return RecordedTransfer(transfer=inserted, created=True)
    existing = (
        await session.execute(
            select(ChainTransfer).where(
                ChainTransfer.extrinsic_reference == extrinsic_reference
            )
        )
    ).scalar_one()
    return RecordedTransfer(transfer=existing, created=False)


async def unattributed(
    session: AsyncSession, *, limit: int, after_block: int = 0
) -> Sequence[ChainTransfer]:
    """Transfers that arrived and belong to nobody yet, oldest first.

    The operator's queue, and the read behind a "claim a transfer you made" flow. Oldest first
    because the person waiting longest is the one to resolve next.
    """
    statement = (
        select(ChainTransfer)
        .where(
            ChainTransfer.status == ChainTransferState.UNATTRIBUTED,
            ChainTransfer.block > after_block,
        )
        .order_by(ChainTransfer.block, ChainTransfer.id)
        .limit(limit)
    )
    return list((await session.execute(statement)).scalars())


async def transfers_from(
    session: AsyncSession, *, sender_coldkey: str, limit: int
) -> Sequence[ChainTransfer]:
    """Everything one coldkey has sent the treasury, newest first."""
    statement = (
        select(ChainTransfer)
        .where(ChainTransfer.sender_coldkey == sender_coldkey)
        .order_by(ChainTransfer.block.desc(), ChainTransfer.id.desc())
        .limit(limit)
    )
    return list((await session.execute(statement)).scalars())


# --- Attribution --------------------------------------------------------------------


async def account_for_coldkey(
    session: AsyncSession, coldkey: str
) -> uuid.UUID | None:
    """Which account, if any, owns a sending coldkey.

    Two sources, in this order:

    1. `account_wallets` — a coldkey the account proved control of by signing a login challenge.
       That signature is the strongest claim the system has, so it wins.
    2. `accounts.payout_coldkey` — where the account asked to be paid. Weaker: it is a value the
       account typed rather than one it signed for, and it is not unique in the schema.

    A `payout_coldkey` shared by two accounts therefore resolves to neither. Crediting an arbitrary
    one of them would hand somebody else's money over on a typo, and there is no tie to break: the
    transfer stays UNATTRIBUTED for a human, which is the only answer that cannot be wrong.
    """
    wallet = (
        await session.execute(
            select(AccountWallet.account_id).where(AccountWallet.coldkey == coldkey)
        )
    ).scalars().all()
    if len(wallet) == 1:
        return wallet[0]
    if len(wallet) > 1:  # pragma: no cover - coldkey is the primary key of that table
        return None

    payout = (
        await session.execute(
            select(Account.id).where(Account.payout_coldkey == coldkey).limit(2)
        )
    ).scalars().all()
    if len(payout) == 1:
        return payout[0]
    return None


async def _matching_open_deposit(
    session: AsyncSession, *, account_id: uuid.UUID, amount_rao: int, recipient: str
) -> Deposit | None:
    """An open deposit this arrival settles: same account, same address, same amount.

    Exact on the amount, not "at least". A deposit is a declaration of one specific transfer, and
    a looser match would let one 10 TAO arrival close a 1 TAO declaration and leave the other 9
    unaccounted for. What does not match is still credited — as an undeclared arrival, below — so
    nothing is lost by being strict here.

    Oldest first, so an account that declared the same amount twice settles them in order.
    `SEEN_UNFINALIZED` is eligible as well as `AWAITING_TRANSFER`: a row the API's claim endpoint
    already marked as seen is precisely one waiting for a watcher to confirm it.
    """
    statement = (
        select(Deposit)
        .where(
            Deposit.account_id == account_id,
            Deposit.treasury_address == recipient,
            Deposit.amount_rao == amount_rao,
            Deposit.status.in_(
                (DepositState.AWAITING_TRANSFER, DepositState.SEEN_UNFINALIZED)
            ),
            Deposit.credited_ledger_id.is_(None),
        )
        .order_by(Deposit.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    return (await session.execute(statement)).scalars().first()


async def credit(
    session: AsyncSession,
    transfer: ChainTransfer,
    *,
    account_id: uuid.UUID,
    credit_price_rao: int,
    deposit_expires_at: dt.datetime,
    created_by: str,
    bonus_schedule: Mapping[int, int] | None = None,
) -> CreditLedgerEntry:
    """Attribute one observed transfer to an account and credit the amount that arrived.

    The account row is locked first, as `credits.lock_account` requires of every path that
    changes a balance, so a transfer and a concurrent spend cannot interleave.

    A deposit row is required — `credit_ledger`'s own `ledger_deposit_names_its_deposit` check
    says a DEPOSIT entry must name one — so an undeclared arrival gets one created for it here.
    That is not a fiction: it records the same four facts a declared deposit does, and it is what
    keeps a single explanation of where every ledger entry came from. `amount_rao` on the created
    row is the observed amount, which is trivially what was "declared" for an arrival that
    declared nothing.

    What is credited is always the amount **observed on chain**. `credits.credit_deposit`
    enforces that against a declared deposit; there is nothing else it could be for an undeclared
    one.

    `bonus_schedule` is the package deals in force, and it is matched against the observed amount
    — so an arrival that lands exactly on a package earns its bonus whether or not a deposit was
    declared for it. That follows from crediting what arrived rather than what was promised: an
    adopted transfer of exactly five credits' worth is the same purchase as a declared one, and
    paying the deal price is the only thing either of them did to earn it.
    """
    if transfer.status is ChainTransferState.CREDITED:
        raise RecordConflict(
            "that transfer has already been credited",
            reason_code="TRANSFER_ALREADY_CREDITED",
            extrinsic_reference=transfer.extrinsic_reference,
        )
    await ledger.lock_account(session, account_id)

    deposit = await _matching_open_deposit(
        session,
        account_id=account_id,
        amount_rao=transfer.amount_rao,
        recipient=transfer.recipient,
    )
    if deposit is None:
        deposit = await ledger.create_deposit(
            session,
            account_id=account_id,
            amount_rao=transfer.amount_rao,
            treasury_address=transfer.recipient,
            credit_price_rao=credit_price_rao,
            expires_at=deposit_expires_at,
        )

    entry = await ledger.credit_deposit(
        session,
        deposit,
        extrinsic_reference=transfer.extrinsic_reference,
        sender_coldkey=transfer.sender_coldkey,
        observed_amount_rao=transfer.amount_rao,
        block=transfer.block,
        bonus_schedule=bonus_schedule,
        created_by=created_by,
    )

    transfer.status = ChainTransferState.CREDITED
    transfer.account_id = account_id
    transfer.deposit_id = deposit.id
    transfer.credit_price_rao = credit_price_rao
    # What this transfer bought on its own. The remainder is not lost: it stays in the ledger
    # balance and counts towards the next credit. See the module docstring.
    transfer.credits_granted = transfer.amount_rao // credit_price_rao
    transfer.note = None
    await session.flush()
    return entry


async def claim_for_submission(
    session: AsyncSession,
    *,
    extrinsic_reference: str,
    block: int,
    block_timestamp: dt.datetime,
    extrinsic_index: int,
    event_index: int,
    sender_coldkey: str,
    recipient: str,
    amount_rao: int,
    note: str,
) -> ChainTransfer:
    """Spend one transfer on a directly-funded submission, so it cannot also buy credits.

    THIS IS WHAT STOPS ONE TRANSFER BEING SPENT TWICE. The two funding paths are separate tables
    with separate uniqueness — `submissions.payment_reference` and `deposits.extrinsic_reference` —
    and nothing joined them. With both paths reading the same treasury address and the same
    canonical reference format, a miner could pay 0.5 TAO, cite it for a submission, and have the
    watcher credit the same transfer to their account as well. One payment, two attempts.

    `chain_transfers.extrinsic_reference` is unique, so this table is the one place that can
    arbitrate. The row is taken `FOR UPDATE`, which serialises this against the watcher's own
    settlement — `deposit_watcher.watcher._settle` locks the same row — so exactly one of them
    decides what the transfer was spent on.

    `IGNORED` rather than a new state: it means "deliberately not credited, and it says why", which
    is exactly the case. The note records the submission, and `submissions.payment_reference`
    remains the authoritative record of what the money bought.

    Raises `RecordConflict` if the watcher already credited it. That is a real refusal and the
    caller must not write a submission: the money became credits, and the miner should spend one of
    those instead. Whichever path got there first wins, and the loser is told.
    """
    recorded = await record(
        session,
        extrinsic_reference=extrinsic_reference,
        block=block,
        block_timestamp=block_timestamp,
        extrinsic_index=extrinsic_index,
        event_index=event_index,
        sender_coldkey=sender_coldkey,
        recipient=recipient,
        amount_rao=amount_rao,
    )
    # Re-read under a row lock. `record` may have found an existing row, and the watcher may be
    # deciding what to do with it at this very moment.
    locked = (
        await session.execute(
            select(ChainTransfer)
            .where(ChainTransfer.id == recorded.transfer.id)
            .with_for_update()
        )
    ).scalar_one()

    if locked.status is ChainTransferState.CREDITED:
        raise RecordConflict(
            "that transfer has already been credited to an account as credits; "
            "spend a credit instead of citing the transfer",
            reason_code="TRANSFER_ALREADY_CREDITED",
            extrinsic_reference=extrinsic_reference,
        )
    if locked.status is ChainTransferState.IGNORED:
        # Already spent on a submission — a replay, or a second submission citing the same
        # transfer. `submissions.payment_reference` would refuse the latter anyway; refusing here
        # means it never reaches that write.
        raise RecordConflict(
            "that transfer has already funded a submission",
            reason_code="DUPLICATE_PAYMENT",
            extrinsic_reference=extrinsic_reference,
        )

    locked.status = ChainTransferState.IGNORED
    locked.note = note[:500]
    await session.flush()
    return locked


async def leave_unattributed(
    session: AsyncSession, transfer: ChainTransfer, *, note: str
) -> ChainTransfer:
    """Record why an arrival could not be matched, without moving it out of the queue.

    The status does not change — the transfer is still waiting for somebody — but the reason is
    written so an operator reading the table is not left to guess at it.
    """
    transfer.note = note[:500]
    await session.flush()
    return transfer


async def ignore(
    session: AsyncSession, transfer: ChainTransfer, *, note: str
) -> ChainTransfer:
    """Take an arrival out of the queue without crediting it, on the record.

    For transfers that are real but are not a purchase: the validator moving its own funds, a
    refund coming back, an operator's decision. `IGNORED` requires a note at the database level,
    because "why does this one not count" is the question this state exists to answer.
    """
    if transfer.status is ChainTransferState.CREDITED:
        raise RecordConflict(
            "a credited transfer cannot be ignored; correct it with an ADJUSTMENT entry",
            reason_code="TRANSFER_ALREADY_CREDITED",
            extrinsic_reference=transfer.extrinsic_reference,
        )
    transfer.status = ChainTransferState.IGNORED
    transfer.note = note[:500]
    await session.flush()
    return transfer


__all__ = [
    "DEPOSIT_WATCHER",
    "RecordedTransfer",
    "account_for_coldkey",
    "advance_cursor",
    "claim_for_submission",
    "credit",
    "cursor",
    "ignore",
    "leave_unattributed",
    "open_cursor",
    "record",
    "transfers_from",
    "unattributed",
]
