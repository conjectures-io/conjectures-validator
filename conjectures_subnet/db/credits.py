"""The credit ledger and the deposits that fund it.

Money, so the rules are strict and stated here rather than assumed by callers.

* **Integer rao only.** No float appears anywhere in this module. A credit is one
  verification attempt at the price in force, and the credit *count* is derived from
  the balance rather than stored — two numbers that can disagree is exactly the bug
  a ledger exists to prevent.
* **Append-only.** Nothing here updates or deletes a ledger row. Make that true in
  deployment too: REVOKE UPDATE, DELETE from the service role.
* **Holds are not ledger entries.** An open intent claims part of the balance, but a
  claim is not a movement: it either becomes a SPEND or evaporates. Writing it to an
  append-only ledger would put an entry there that later has to be undone. So
  ``available`` is the balance minus live holds, and the holds live on
  ``submission_intents``.
* **Concurrent spends are serialised on the account row.** ``lock_account`` takes
  ``SELECT ... FOR UPDATE`` before any balance is read for a decision. Without it,
  two requests both read a balance of one credit and both open an intent, and the
  account goes overdrawn — a check that reads before it writes is not a check.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from conjectures_subnet.db.errors import RecordConflict, RecordNotFound
from conjectures_subnet.db.models import (
    Account,
    CreditEntryKind,
    CreditLedgerEntry,
    Deposit,
    DepositState,
    IntentState,
    SubmissionIntent,
)

# States in which an intent still claims part of the balance.
LIVE_INTENT_STATES = (IntentState.OPEN, IntentState.BUNDLE_ATTACHED)


@dataclass(frozen=True)
class CreditBalance:
    """What an account can spend right now.

    ``balance_rao`` is the ledger total; ``held_rao`` is what open intents have
    claimed; ``credits_available`` is what is left, in whole attempts.
    ``remainder_rao`` is the leftover that is not a whole credit — surfaced because
    otherwise a reader with 1.9 credits' worth of rao sees "1 credit" and concludes
    the rest vanished.
    """

    balance_rao: int
    held_rao: int
    credits_available: int
    remainder_rao: int
    credit_price_rao: int
    low_balance: bool

    @property
    def available_rao(self) -> int:
        return self.balance_rao - self.held_rao


async def lock_account(session: AsyncSession, account_id: uuid.UUID) -> None:
    """Serialise credit operations for one account.

    Every path that decides "is there enough to spend" must call this first, in the
    same transaction as the write that follows. It is a row lock on `accounts`, held
    to the end of the transaction, so concurrent intents for one account queue
    instead of both reading the same balance and both succeeding.

    Deliberately per-account: two different accounts never contend.
    """
    statement = select(Account.id).where(Account.id == account_id).with_for_update()
    if (await session.execute(statement)).first() is None:
        raise RecordNotFound("account not found")


async def balance_rao(session: AsyncSession, account_id: uuid.UUID) -> int:
    """The ledger total. Zero for an account with no entries, never None."""
    statement = select(func.coalesce(func.sum(CreditLedgerEntry.amount_rao), 0)).where(
        CreditLedgerEntry.account_id == account_id
    )
    return (await session.execute(statement)).scalar_one()


async def held_rao(
    session: AsyncSession, account_id: uuid.UUID, *, now: dt.datetime
) -> int:
    """What live intents have claimed.

    An expired intent is excluded by the timestamp rather than by waiting for a
    sweeper to change its status, so a lapsed hold stops counting against the
    balance the moment it lapses. The sweeper only tidies the row afterwards.
    """
    statement = select(
        func.coalesce(
            func.sum(SubmissionIntent.credits_held * SubmissionIntent.credit_price_rao),
            0,
        )
    ).where(
        SubmissionIntent.account_id == account_id,
        SubmissionIntent.status.in_(LIVE_INTENT_STATES),
        SubmissionIntent.expires_at > now,
    )
    return (await session.execute(statement)).scalar_one()


async def credit_balance(
    session: AsyncSession,
    account_id: uuid.UUID,
    *,
    credit_price_rao: int,
    now: dt.datetime,
    low_balance_credits: int = 1,
) -> CreditBalance:
    """The balance as the API reports it.

    Floors to whole credits and clamps at zero. An ADJUSTMENT can in principle take
    a balance negative — an operator correcting an over-credit — and Python's floor
    division would turn a small negative into ``-1`` available credits, which is not
    a thing a caller should ever have to reason about.
    """
    total = await balance_rao(session, account_id)
    held = await held_rao(session, account_id, now=now)
    available = total - held
    if available <= 0:
        credits_available, remainder = 0, 0
    else:
        credits_available, remainder = divmod(available, credit_price_rao)
    return CreditBalance(
        balance_rao=total,
        held_rao=held,
        credits_available=credits_available,
        remainder_rao=remainder,
        credit_price_rao=credit_price_rao,
        low_balance=credits_available <= low_balance_credits,
    )


async def ledger_page(
    session: AsyncSession,
    account_id: uuid.UUID,
    *,
    limit: int,
    after_id: int | None = None,
) -> Sequence[CreditLedgerEntry]:
    """One page of the ledger, newest first, keyset-paginated on the identity column.

    ``id`` is monotonic and unique, so it is the whole cursor — no timestamp tie to
    break, unlike the public result feeds.
    """
    statement = (
        select(CreditLedgerEntry)
        .where(CreditLedgerEntry.account_id == account_id)
        .order_by(CreditLedgerEntry.id.desc())
        .limit(limit)
    )
    if after_id is not None:
        statement = statement.where(CreditLedgerEntry.id < after_id)
    return list((await session.execute(statement)).scalars())


def bonus_rao_for_credits(
    *,
    paid_credits: int,
    credit_price_rao: int,
    bonus_schedule: Mapping[int, int] | None,
) -> int:
    """The bonus, in rao, that a purchase of ``paid_credits`` earns. Zero if it earns none.

    Pure, and the one place the lookup lives, so a `btcli` transfer and a TMC PAY invoice cannot
    come to different answers about the same deal. What differs between the two funding paths is
    only how the paid count is *established* — see ``package_bonus_rao`` for the chain path, and
    `tmc_pay.settle` for the path that has the count recorded on the order.

    **The bonus is expressed in rao because the ledger is.** Credits are derived
    (``balance // credit_price_rao``), never stored, so "one free credit" is written as one
    credit's worth of rao. That is what keeps a bonus indistinguishable from any other credit at
    spend time, and why granting one needed no schema change.
    """
    if not bonus_schedule:
        return 0
    return bonus_schedule.get(paid_credits, 0) * credit_price_rao


def package_bonus_rao(
    *,
    paid_rao: int,
    credit_price_rao: int,
    bonus_schedule: Mapping[int, int] | None,
) -> int:
    """The bonus earned by a payment known only as an amount of rao.

    For the chain path, where there may be no declared purchase at all: an arrival is credited on
    what it *is*, so the paid count has to be read back out of the amount.

    **The amount must land exactly on a package.** A payment of 2.6 TAO against a 5-credit deal
    buys five credits and leaves a remainder towards the sixth; it does not buy the deal.
    Requiring a zero remainder is what makes the rule predictable in both directions — there is no
    band of overpayment that silently does or does not qualify, and nobody can discover a cheaper
    route to the bonus by rounding.

    **Do not reach for this on the TMC PAY path.** An invoice is only required to *cover* the
    credits it was opened for (`tmc_pay_invoice_covers_the_credits`), so `crypto_amount_rao`
    routinely carries a remainder that belongs to the buyer — and a remainder here means no
    bonus. Keyed on the amount, that path would have quietly granted a deal it advertised. It
    uses `bonus_rao_for_credits` with the count the order already records instead.
    """
    if not bonus_schedule:
        return 0
    paid_credits, remainder = divmod(paid_rao, credit_price_rao)
    if remainder:
        return 0
    return bonus_rao_for_credits(
        paid_credits=paid_credits,
        credit_price_rao=credit_price_rao,
        bonus_schedule=bonus_schedule,
    )


def bonus_reason(*, paid_credits: int, bonus_rao: int, credit_price_rao: int) -> str:
    """Why a BONUS entry exists, in the words the account holder's ledger page will show.

    The entry names no deposit or order — see ``credit_deposit`` — so this string is the only
    provenance it carries, and it has to be enough for an auditor to reconstruct the deal from
    the row alone: what was paid for, and what was granted on top.
    """
    return (
        f"package bonus: {bonus_rao // credit_price_rao} credit(s) granted with a "
        f"{paid_credits}-credit purchase"
    )


async def record_entry(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    kind: CreditEntryKind,
    amount_rao: int,
    credit_price_rao: int | None = None,
    deposit_id: uuid.UUID | None = None,
    intent_id: uuid.UUID | None = None,
    tmc_pay_order_id: uuid.UUID | None = None,
    reason: str | None = None,
    created_by: str = "system",
) -> CreditLedgerEntry:
    """Append one entry. The schema fixes the sign per kind, so a debit cannot be
    recorded as a credit by passing the wrong one.

    A DEPOSIT must name exactly one source: ``deposit_id`` for rao this validator read
    off finalized chain state, or ``tmc_pay_order_id`` for a purchase TMC PAY settled.
    The schema enforces the exclusive-or, so passing both or neither is a constraint
    violation rather than a silently untraceable credit."""
    entry = CreditLedgerEntry(
        account_id=account_id,
        kind=kind,
        amount_rao=amount_rao,
        credit_price_rao=credit_price_rao,
        deposit_id=deposit_id,
        intent_id=intent_id,
        tmc_pay_order_id=tmc_pay_order_id,
        reason=reason,
        created_by=created_by,
    )
    session.add(entry)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise RecordConflict(
            "that credit movement has already been recorded",
            reason_code="DUPLICATE_LEDGER_ENTRY",
        ) from exc
    return entry


# --- Deposits ----------------------------------------------------------------------


async def create_deposit(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    amount_rao: int,
    treasury_address: str,
    credit_price_rao: int,
    expires_at: dt.datetime,
) -> Deposit:
    """Record what the account says it will send, and where.

    Captured at creation so confirmation has something to compare against, rather
    than crediting whatever happens to arrive.
    """
    deposit = Deposit(
        account_id=account_id,
        amount_rao=amount_rao,
        treasury_address=treasury_address,
        credit_price_rao=credit_price_rao,
        expires_at=expires_at,
    )
    session.add(deposit)
    await session.flush()
    return deposit


async def get_deposit(
    session: AsyncSession, deposit_id: uuid.UUID, account_id: uuid.UUID
) -> Deposit:
    """One deposit, scoped to its owner.

    Another account's deposit is reported as absent rather than forbidden, so an
    identifier cannot be probed for existence — the rule ``get_for_miner`` follows.
    """
    deposit = await session.get(Deposit, deposit_id)
    if deposit is None or deposit.account_id != account_id:
        raise RecordNotFound("deposit not found")
    return deposit


async def deposits_for(
    session: AsyncSession, account_id: uuid.UUID, *, limit: int
) -> Sequence[Deposit]:
    statement = (
        select(Deposit)
        .where(Deposit.account_id == account_id)
        .order_by(Deposit.created_at.desc())
        .limit(limit)
    )
    return list((await session.execute(statement)).scalars())


async def mark_seen(
    session: AsyncSession,
    deposit: Deposit,
    *,
    extrinsic_reference: str,
    sender_coldkey: str | None = None,
) -> Deposit:
    """Record that a transfer is visible but not yet final.

    No credits are issued here. `seen_unfinalized` exists precisely so the account
    can be told "we can see it, we are waiting for finality" instead of being shown
    nothing while a transfer settles.
    """
    deposit.status = DepositState.SEEN_UNFINALIZED
    deposit.extrinsic_reference = extrinsic_reference
    if sender_coldkey is not None:
        deposit.sender_coldkey = sender_coldkey
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise RecordConflict(
            "that transfer already funds another deposit",
            reason_code="DUPLICATE_DEPOSIT",
            extrinsic_reference=extrinsic_reference,
        ) from exc
    return deposit


async def credit_deposit(
    session: AsyncSession,
    deposit: Deposit,
    *,
    extrinsic_reference: str,
    sender_coldkey: str,
    observed_amount_rao: int,
    block: int,
    bonus_schedule: Mapping[int, int] | None = None,
    created_by: str = "system",
) -> CreditLedgerEntry:
    """Issue credits for a finalized transfer, once.

    The ledger entry is written first and the deposit then points at it, which is
    what makes this safe to retry: the unique index on ``credited_ledger_id`` and the
    partial unique index on ``extrinsic_reference`` mean a second attempt for the
    same transfer conflicts rather than crediting twice.

    The amount credited is the amount **observed on chain**, not the amount the
    account said it would send. Crediting the intent would let someone promise 10
    TAO, send 1, and be credited for 10.

    A package bonus, if the observed amount earns one, is appended as a second BONUS entry in
    **this same transaction**. That is what makes the grant safe to retry without a new unique
    index: the DEPOSIT entry it accompanies is already guarded by ``credited_ledger_id`` and the
    partial unique index on ``extrinsic_reference``, so a duplicate attempt rolls both entries
    back together. A bonus written in a later transaction could be granted twice.

    The BONUS entry names no source row. ``credit_ledger_tmc_pay_idx`` is unique on
    ``tmc_pay_order_id`` across every kind, so on the TMC PAY path the DEPOSIT entry has already
    claimed the order and a second row cannot reference it; the deposit path could carry
    ``deposit_id`` but then the two paths would disagree about the shape of the same grant. V003
    anticipated exactly this — ``reason`` is described there as free text for the kinds that have
    no other row behind them — so the reason string is where the provenance goes.
    """
    if deposit.status == DepositState.CREDITED:
        raise RecordConflict(
            "that deposit has already been credited",
            reason_code="DEPOSIT_ALREADY_CREDITED",
            deposit_id=str(deposit.id),
        )
    entry = await record_entry(
        session,
        account_id=deposit.account_id,
        kind=CreditEntryKind.DEPOSIT,
        amount_rao=observed_amount_rao,
        deposit_id=deposit.id,
        created_by=created_by,
    )
    bonus_rao = package_bonus_rao(
        paid_rao=observed_amount_rao,
        credit_price_rao=deposit.credit_price_rao,
        bonus_schedule=bonus_schedule,
    )
    if bonus_rao:
        await record_entry(
            session,
            account_id=deposit.account_id,
            kind=CreditEntryKind.BONUS,
            amount_rao=bonus_rao,
            reason=bonus_reason(
                paid_credits=observed_amount_rao // deposit.credit_price_rao,
                bonus_rao=bonus_rao,
                credit_price_rao=deposit.credit_price_rao,
            ),
            created_by=created_by,
        )
    deposit.status = DepositState.CREDITED
    deposit.extrinsic_reference = extrinsic_reference
    deposit.sender_coldkey = sender_coldkey
    deposit.observed_amount_rao = observed_amount_rao
    deposit.block = block
    deposit.credited_ledger_id = entry.id
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise RecordConflict(
            "that transfer already funds another deposit",
            reason_code="DUPLICATE_DEPOSIT",
            extrinsic_reference=extrinsic_reference,
        ) from exc
    return entry


async def fail_deposit(
    session: AsyncSession, deposit: Deposit, *, reason: str
) -> Deposit:
    """A transfer was seen but does not fund this deposit — wrong recipient or amount."""
    deposit.status = DepositState.FAILED
    deposit.failure_reason = reason
    await session.flush()
    return deposit


async def expire_open_deposits(session: AsyncSession, *, now: dt.datetime) -> int:
    """Close the window on deposits nothing ever arrived for.

    Only ``AWAITING_TRANSFER`` expires. A deposit already ``SEEN_UNFINALIZED`` has
    real money behind it and must be resolved by a human or by finality, never timed
    out — expiring it would abandon a transfer that did happen.
    """
    result = await session.execute(
        update(Deposit)
        .where(
            Deposit.status == DepositState.AWAITING_TRANSFER,
            Deposit.expires_at <= now,
        )
        .values(status=DepositState.EXPIRED)
    )
    return result.rowcount


__all__ = [
    "LIVE_INTENT_STATES",
    "CreditBalance",
    "balance_rao",
    "bonus_rao_for_credits",
    "bonus_reason",
    "credit_balance",
    "credit_deposit",
    "create_deposit",
    "deposits_for",
    "expire_open_deposits",
    "fail_deposit",
    "get_deposit",
    "held_rao",
    "ledger_page",
    "lock_account",
    "mark_seen",
    "package_bonus_rao",
    "record_entry",
]
