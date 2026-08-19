"""TMC PAY credit orders: the rows, and the one transaction that turns one into credits.

The sibling of `credits.create_deposit`/`credit_deposit` for the processor-settled funding path.
Same money rules, restated because this is where a mistake would be expensive:

* **Integer rao only.** The credited amount is `crypto_amount_rao`, the TAO the invoice locked.
  Nothing here computes an amount from a fiat figure, and no float appears.
* **The ledger is append-only.** `settle` writes one DEPOSIT entry and points the order at it;
  nothing ever updates or deletes a ledger row.
* **Crediting is idempotent, and enforced by the schema rather than by checking first.** Three
  independent things stop a second credit: the status check under a row lock, the UNIQUE on
  `tmc_pay_orders.credited_ledger_id`, and the partial UNIQUE on
  `credit_ledger.tmc_pay_order_id`. That matters because two writers race here by design — a
  webhook and the reconciler can arrive within milliseconds of each other for the same invoice.
* **The account row is locked before the balance moves.** Not because a deposit can overdraw
  anything, but because `credits.lock_account` is the serialisation point every other credit
  operation takes, and a DEPOSIT written outside it would let a concurrent `open_intent` read a
  balance mid-write.

What this module deliberately does *not* do is decide whether an invoice was paid. That is
`submission_api.tmc_pay.credits_are_earned`, from a status TMC PAY reported. This layer records.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from conjectures_subnet.db import credits
from conjectures_subnet.db.errors import (
    RecordConflict,
    RecordNotFound,
    violated_constraint,
)
from conjectures_subnet.db.models import (
    CreditEntryKind,
    CreditLedgerEntry,
    TmcPayOrder,
    TmcPayOrderState,
    TmcPayWebhookDelivery,
)

# The currency whose amounts are directly comparable to a rao price, because rao is its own
# smallest unit. Spelled out here rather than imported from `submission_api.tmc_pay`: this package
# is the layer below, and importing upwards would invert the dependency. `PAYABLE_PAIRS` there is
# the operator-facing list; these two are the one currency this layer's arithmetic understands.
TAO_CURRENCY = "TAO"
TAO_NETWORK = "bittensor"

# The unique indexes this module expects to collide with, by the name PostgreSQL reports when one
# does. Spelled out so that catching `IntegrityError` can mean "the duplicate I was expecting"
# rather than "any way a write can be refused": a CHECK violation caught by the same clause used to
# be reported as a duplicate, which describes a record that does not exist and sends whoever reads
# it looking for one.
ORDER_EXTERNAL_ID_INDEX = "tmc_pay_orders_external_idx"
ORDER_INVOICE_ID_INDEX = "tmc_pay_orders_invoice_idx"
DELIVERY_ID_INDEX = "tmc_pay_webhook_deliveries_pkey"
# Two ways the same fact is enforced, so either name means "this order is already credited": the
# order's own pointer at its ledger entry, and the ledger's one-DEPOSIT-per-order index.
CREDITED_INDEXES = (
    "tmc_pay_orders_credited_ledger_id_key",
    "credit_ledger_tmc_pay_idx",
)

# Order states in which TMC PAY may still change its mind, so the reconciler keeps asking.
# UNDERPAID is here because TMC PAY documents it as non-terminal — the buyer may top up — and NEW
# is here because an order stuck in it means a create whose response never arrived.
OPEN_ORDER_STATES = (
    TmcPayOrderState.NEW,
    TmcPayOrderState.CREATED,
    TmcPayOrderState.PENDING,
    TmcPayOrderState.CONFIRMING,
    TmcPayOrderState.UNDERPAID,
)

# States in which an order is still worth a buyer's attention: it can still be paid. Used to bound
# how many invoices one account may have outstanding, which is the rate limit that matters for an
# endpoint whose side effect is an outbound invoice at a payment processor.
LIVE_ORDER_STATES = OPEN_ORDER_STATES

# What a delivery caused, recorded on `tmc_pay_webhook_deliveries.outcome`.
OUTCOME_CREDITED = "CREDITED"  # the order moved to a paid state and credits were issued
OUTCOME_RECORDED = "RECORDED"  # the status was applied; no credits were due
OUTCOME_IGNORED = "IGNORED"  # already applied, or nothing to do
OUTCOME_UNKNOWN = "UNKNOWN"  # no order here matches this invoice


@dataclass(frozen=True)
class ObservedRate:
    """A TAO/fiat rate TMC PAY itself locked, and when.

    Read back off the orders this deployment has already created. It is a better seed for the next
    quote than any third-party feed, for two reasons that have nothing to do with freshness:

    * it came from **TMC PAY's own rate source**, so it carries whatever spread or rounding that
      source applies — which a different feed cannot know about;
    * it is denominated in the **merchant's own currency**, so it needs no conversion and works
      for a merchant onboarded in something other than dollars.

    `observed_at` is the order's `created_at`, because the invoice was created inside that same
    request — so it is the age of the rate, not of the row.
    """

    exchange_rate: str
    observed_at: dt.datetime
    invoice_id: str | None


async def latest_exchange_rate(
    session: AsyncSession, *, fiat_currency: str
) -> ObservedRate | None:
    """The most recent rate TMC PAY locked for this currency, if any order has one.

    Scoped to the currency because a rate is meaningless without one: a EUR rate seeding a USD
    quote is the same mistake as using a USD feed for a EUR merchant, just in the other direction.
    """
    statement = (
        select(
            TmcPayOrder.exchange_rate,
            TmcPayOrder.created_at,
            TmcPayOrder.invoice_id,
        )
        .where(
            TmcPayOrder.exchange_rate.is_not(None),
            TmcPayOrder.fiat_currency == fiat_currency,
        )
        .order_by(TmcPayOrder.created_at.desc())
        .limit(1)
    )
    row = (await session.execute(statement)).first()
    if row is None:
        return None
    return ObservedRate(exchange_rate=row[0], observed_at=row[1], invoice_id=row[2])


@dataclass(frozen=True)
class SettledOrder:
    """The result of applying a paid status: the entry written, and the balance after it."""

    order: TmcPayOrder
    entry: CreditLedgerEntry
    balance: credits.CreditBalance


async def create_order(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    credits_requested: int,
    credit_price_rao: int,
    external_id: str,
) -> TmcPayOrder:
    """Record the intent to buy, before any invoice exists.

    The row comes first deliberately. `external_id` is TMC PAY's idempotency key, and minting it
    here — rather than deriving it from an invoice we do not have yet — is what makes a lost
    create-response recoverable: the same key re-sent returns the original invoice, and a webhook
    for an invoice whose id we never learned still carries this value and can be matched on it.
    """
    if credits_requested <= 0:
        raise ValueError("a credit purchase must be for at least one credit")
    order = TmcPayOrder(
        account_id=account_id,
        credits=credits_requested,
        credit_price_rao=credit_price_rao,
        external_id=external_id,
    )
    session.add(order)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        if violated_constraint(exc) != ORDER_EXTERNAL_ID_INDEX:
            raise
        raise RecordConflict(
            "that purchase reference has already been used",
            reason_code="DUPLICATE_TMC_PAY_ORDER",
            external_id=external_id,
        ) from exc
    return order


def paid_rao(order: TmcPayOrder) -> int | None:
    """What this order is worth, in the unit the credit ledger uses, or None before an invoice.

    Two cases, and they are different in kind rather than in degree:

    * **Invoiced in TAO.** `crypto_amount_rao` is the amount that arrived, guaranteed to cover the
      credits by `tmc_pay_invoice_covers_the_credits`. The surplus from rounding the fiat figure up
      belongs to the buyer and reaches them as `CreditBalance.remainder_rao`.
    * **Invoiced in anything else.** There is no rao on the row to compare. The fiat figure that
      was collected was computed *from* `credits * credit_price_rao`, so that product is what the
      purchase is worth — exactly, with no remainder. Converting the crypto amount instead would
      mean choosing an exchange rate after the money arrived.

    Mirrors `submission_api.tmc_pay.credited_rao`, which answers the same question from an invoice
    rather than from a row. Two callers, one rule, stated twice on purpose: the API layer decides
    whether to accept an invoice and this layer decides what to write, and a single helper shared
    across that boundary would put a money rule in whichever module happened to import first.
    """
    if order.invoice_id is None:
        return None
    if order.crypto_currency is None or order.crypto_currency == TAO_CURRENCY:
        return order.crypto_amount_rao
    return order.credits * order.credit_price_rao


async def attach_invoice(
    session: AsyncSession,
    order: TmcPayOrder,
    *,
    invoice_id: str,
    merchant_id: str,
    status: TmcPayOrderState,
    fiat_amount: str,
    fiat_currency: str,
    exchange_rate: str,
    commission_amount: str | None,
    crypto_amount_rao: int | None,
    deposit_address: str,
    invoice_expires_at: dt.datetime | None,
    hosted_invoice_url: str | None = None,
    crypto_amount: str | None = None,
    crypto_currency: str = TAO_CURRENCY,
    crypto_network: str = TAO_NETWORK,
) -> TmcPayOrder:
    """Record the invoice TMC PAY created for this order.

    `crypto_amount_rao` must cover the credits being bought; the caller checks it and the schema
    checks it again (`tmc_pay_invoice_covers_the_credits`). That property is the whole reason
    crediting the locked amount is safe, so it is asserted in both places rather than trusted to
    one.
    """
    required = order.credits * order.credit_price_rao
    if crypto_currency == TAO_CURRENCY:
        # The covering check applies to TAO alone, because it is the only currency whose amount is
        # comparable to a rao price. The schema enforces the same condition under the same
        # restriction; see `tmc_pay_invoice_covers_the_credits`.
        if crypto_amount_rao is None:
            raise ValueError("a TAO invoice must carry an amount in rao")
        if crypto_amount_rao < required:
            raise ValueError(
                f"invoice locks {crypto_amount_rao} rao, which is less than the "
                f"{required} rao the credits cost"
            )
    elif crypto_amount_rao is not None:
        raise ValueError(
            f"an invoice in {crypto_currency} must not carry a rao amount; rao is TAO's unit"
        )
    order.invoice_id = invoice_id
    order.merchant_id = merchant_id
    order.status = status
    order.fiat_amount = fiat_amount
    order.fiat_currency = fiat_currency
    order.exchange_rate = exchange_rate
    order.commission_amount = commission_amount
    order.crypto_amount_rao = crypto_amount_rao
    order.crypto_amount = crypto_amount
    order.crypto_currency = crypto_currency
    order.crypto_network = crypto_network
    order.deposit_address = deposit_address
    order.invoice_expires_at = invoice_expires_at
    # Only ever set, never cleared. A later invoice read that omits the field must not take away a
    # link the buyer is looking at.
    if hosted_invoice_url is not None:
        order.hosted_invoice_url = hosted_invoice_url
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        # Only the invoice-id index means what the message below says. Anything else — a CHECK on
        # the amount, the currency shape, the completeness of an invoiced row — is a row this
        # process built wrongly, and it is raised as itself so the log names the real constraint.
        if violated_constraint(exc) != ORDER_INVOICE_ID_INDEX:
            raise
        raise RecordConflict(
            "that invoice already belongs to another order",
            reason_code="DUPLICATE_TMC_PAY_INVOICE",
            invoice_id=invoice_id,
        ) from exc
    return order


async def fail_order(
    session: AsyncSession, order: TmcPayOrder, *, reason: str
) -> TmcPayOrder:
    """No invoice could be created, so nothing can ever be paid against this order.

    Terminal, and it holds no money: an order that never reached TMC PAY cannot have a deposit
    address for anyone to send TAO to.
    """
    order.status = TmcPayOrderState.FAILED
    order.failure_reason = reason
    await session.flush()
    return order


async def get_order(
    session: AsyncSession,
    order_id: uuid.UUID,
    account_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> TmcPayOrder:
    """One order, scoped to its owner.

    Another account's order is reported absent rather than forbidden, so an identifier cannot be
    probed for existence — the rule `credits.get_deposit` and `intents.get_intent` follow.
    """
    statement = select(TmcPayOrder).where(TmcPayOrder.id == order_id)
    if for_update:
        statement = statement.with_for_update()
    order = (await session.execute(statement)).scalar_one_or_none()
    if order is None or order.account_id != account_id:
        raise RecordNotFound("order not found")
    return order


async def find_by_invoice(
    session: AsyncSession, invoice_id: str, *, for_update: bool = False
) -> TmcPayOrder | None:
    """The order an invoice belongs to, if this deployment created it.

    Unscoped by account on purpose: the caller is a webhook, which is authenticated by the
    merchant's signing secret rather than by a session, and the account is a *result* of the
    lookup rather than an input to it.
    """
    statement = select(TmcPayOrder).where(TmcPayOrder.invoice_id == invoice_id)
    if for_update:
        statement = statement.with_for_update()
    return (await session.execute(statement)).scalar_one_or_none()


async def find_by_external_id(
    session: AsyncSession, external_id: str, *, for_update: bool = False
) -> TmcPayOrder | None:
    """The order behind an `external_id`.

    The recovery path for an invoice whose create-response was lost: the row exists, its
    `invoice_id` is NULL, and the webhook echoes the `external_id` this side minted.
    """
    statement = select(TmcPayOrder).where(TmcPayOrder.external_id == external_id)
    if for_update:
        statement = statement.with_for_update()
    return (await session.execute(statement)).scalar_one_or_none()


async def orders_for(
    session: AsyncSession, account_id: uuid.UUID, *, limit: int
) -> Sequence[TmcPayOrder]:
    statement = (
        select(TmcPayOrder)
        .where(TmcPayOrder.account_id == account_id)
        .order_by(TmcPayOrder.created_at.desc())
        .limit(limit)
    )
    return list((await session.execute(statement)).scalars())


async def count_live_orders(
    session: AsyncSession, account_id: uuid.UUID, *, now: dt.datetime
) -> int:
    """How many invoices this account has outstanding.

    An expired-by-timestamp order is not counted even if its status has not caught up yet, for
    the same reason `credits.held_rao` excludes a lapsed hold: the sweeper runs on its own
    schedule, and a buyer should not be blocked by a row that stopped mattering an hour ago.
    """
    statement = select(func.count(TmcPayOrder.id)).where(
        TmcPayOrder.account_id == account_id,
        TmcPayOrder.status.in_(LIVE_ORDER_STATES),
        (TmcPayOrder.invoice_expires_at.is_(None))
        | (TmcPayOrder.invoice_expires_at > now),
    )
    return (await session.execute(statement)).scalar_one()


async def open_orders(
    session: AsyncSession, *, limit: int, before: dt.datetime | None = None
) -> Sequence[TmcPayOrder]:
    """The reconciler's queue: orders TMC PAY may still have news about.

    Oldest first, so a backlog drains in the order it accumulated rather than starving the rows
    that have been waiting longest. `before` bounds it to orders untouched since some instant,
    which is how the reconciler avoids re-reading an invoice it polled seconds ago.
    """
    statement = (
        select(TmcPayOrder)
        .where(TmcPayOrder.status.in_(OPEN_ORDER_STATES))
        .order_by(TmcPayOrder.created_at)
        .limit(limit)
    )
    if before is not None:
        statement = statement.where(
            (TmcPayOrder.last_polled_at.is_(None)) | (TmcPayOrder.last_polled_at < before)
        )
    return list((await session.execute(statement)).scalars())


async def record_status(
    session: AsyncSession,
    order: TmcPayOrder,
    *,
    status: TmcPayOrderState,
    confirmed_at: dt.datetime | None = None,
    needs_review: bool = False,
    event_id: str | None = None,
    polled_at: dt.datetime | None = None,
) -> TmcPayOrder:
    """Apply a status TMC PAY reported. Issues no credits.

    Deliberately not a state machine. TMC PAY enforces its own transitions and warns that its
    webhooks can arrive out of order, so a second machine here would either duplicate that logic
    or contradict it. What must not be overwritten is a *terminal, already-credited* outcome, and
    that is guarded where it matters — in `settle`, under a row lock — rather than by refusing
    transitions here.

    `needs_review` only ever goes from false to true. An operator clears it deliberately; a later
    webhook must not clear it for them.
    """
    if order.credited_ledger_id is not None and status not in (
        TmcPayOrderState.CONFIRMED,
        TmcPayOrderState.OVERPAID,
        TmcPayOrderState.LATE_PAYMENT,
    ):
        # A credited order that is now being told it expired. Keep the paid status: the credits
        # are real, the ledger is append-only, and the honest record is "we credited this" plus a
        # review flag for the operator who has to ask TMC PAY what happened.
        order.needs_review = True
        if event_id is not None:
            order.last_event_id = event_id
        if polled_at is not None:
            order.last_polled_at = polled_at
        await session.flush()
        return order

    order.status = status
    if confirmed_at is not None:
        order.confirmed_at = confirmed_at
    if needs_review:
        order.needs_review = True
    if event_id is not None:
        order.last_event_id = event_id
    if polled_at is not None:
        order.last_polled_at = polled_at
    await session.flush()
    return order


async def settle(
    session: AsyncSession,
    order: TmcPayOrder,
    *,
    status: TmcPayOrderState,
    confirmed_at: dt.datetime | None,
    needs_review: bool = False,
    event_id: str | None = None,
    polled_at: dt.datetime | None = None,
    now: dt.datetime,
    bonus_schedule: Mapping[int, int] | None = None,
    created_by: str = "system",
) -> SettledOrder:
    """Issue credits for a paid invoice, once.

    The order of writes is the correctness requirement, and it mirrors `credits.credit_deposit`:

    1. lock the account, so a concurrent `open_intent` cannot read a balance mid-write;
    2. re-read this order under a row lock and refuse if it is already credited — the check has
       to happen after the lock, not before it;
    3. append the DEPOSIT entry, naming the order;
    4. append a BONUS entry if the paid amount earns a package bonus, in this same transaction so
       the two are granted or rolled back together — see `credits.credit_deposit` for why that,
       rather than a new unique index, is what stops a duplicate webhook granting free credits
       twice, and why the BONUS entry cannot name the order;
    5. point the order at the DEPOSIT entry and record the status.

    What is credited is `paid_rao`, never anything from a webhook body. For a TAO invoice that is
    `crypto_amount_rao` — the TAO that arrived, which TMC PAY requires before reporting a paid
    status. It is never below `credits * credit_price_rao`
    (`tmc_pay_invoice_covers_the_credits`), and the difference belongs to the buyer, where
    `CreditBalance.remainder_rao` keeps it visible. For any other currency it is
    `credits * credit_price_rao` exactly, because that product is what the collected fiat figure
    was computed from and there is no rao amount to have arrived.

    Raises `RecordConflict` if the order was already credited, which is the normal outcome of a
    duplicate webhook and must be handled as success by the caller, not as an error.
    """
    if paid_rao(order) is None:
        raise RecordConflict(
            "that order has no invoice to settle",
            reason_code="TMC_PAY_ORDER_NOT_INVOICED",
            order_id=str(order.id),
        )

    await credits.lock_account(session, order.account_id)
    locked = (
        await session.execute(
            select(TmcPayOrder).where(TmcPayOrder.id == order.id).with_for_update()
        )
    ).scalar_one()
    if locked.credited_ledger_id is not None:
        raise RecordConflict(
            "that order has already been credited",
            reason_code="TMC_PAY_ORDER_ALREADY_CREDITED",
            order_id=str(locked.id),
        )

    # Re-derived from the locked row rather than reused from `order`: the row was re-read under
    # `FOR UPDATE` above and is the only version of it that a concurrent settle cannot have moved.
    amount_rao = paid_rao(locked)
    if amount_rao is None:
        raise RecordConflict(
            "that order has no invoice to settle",
            reason_code="TMC_PAY_ORDER_NOT_INVOICED",
            order_id=str(locked.id),
        )
    entry = await credits.record_entry(
        session,
        account_id=locked.account_id,
        kind=CreditEntryKind.DEPOSIT,
        amount_rao=amount_rao,
        tmc_pay_order_id=locked.id,
        created_by=created_by,
    )
    # Keyed on `locked.credits`, the count this invoice was opened for, NOT on the rao that
    # arrived. An invoice only has to cover the credits it names, so `crypto_amount_rao` usually
    # carries a remainder — matching on the amount would have advertised these deals and then
    # silently declined to grant them on this path. See `credits.package_bonus_rao`.
    bonus_rao = credits.bonus_rao_for_credits(
        paid_credits=locked.credits,
        credit_price_rao=locked.credit_price_rao,
        bonus_schedule=bonus_schedule,
    )
    if bonus_rao:
        await credits.record_entry(
            session,
            account_id=locked.account_id,
            kind=CreditEntryKind.BONUS,
            amount_rao=bonus_rao,
            reason=credits.bonus_reason(
                paid_credits=locked.credits,
                bonus_rao=bonus_rao,
                credit_price_rao=locked.credit_price_rao,
            ),
            created_by=created_by,
        )
    locked.status = status
    locked.confirmed_at = confirmed_at or now
    locked.credited_ledger_id = entry.id
    if needs_review:
        locked.needs_review = True
    if event_id is not None:
        locked.last_event_id = event_id
    if polled_at is not None:
        locked.last_polled_at = polled_at
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        if violated_constraint(exc) not in CREDITED_INDEXES:
            raise
        raise RecordConflict(
            "that order has already been credited",
            reason_code="TMC_PAY_ORDER_ALREADY_CREDITED",
            order_id=str(order.id),
        ) from exc

    balance = await credits.credit_balance(
        session,
        locked.account_id,
        credit_price_rao=locked.credit_price_rao,
        now=now,
    )
    return SettledOrder(order=locked, entry=entry, balance=balance)


async def expire_lapsed(session: AsyncSession, *, now: dt.datetime) -> int:
    """Close orders whose invoice TTL passed with nothing confirmed.

    Housekeeping, like `intents.expire_lapsed`. Only rows that cannot hold money are touched:
    CREATED means TMC PAY saw no deposit at all, and NEW means no invoice was ever created. A
    PENDING, CONFIRMING or UNDERPAID order has real money behind it and must be resolved by TMC
    PAY or by a human — timing it out would abandon a payment that did happen.
    """
    result = await session.execute(
        update(TmcPayOrder)
        .where(
            TmcPayOrder.status.in_((TmcPayOrderState.NEW, TmcPayOrderState.CREATED)),
            TmcPayOrder.invoice_expires_at.is_not(None),
            TmcPayOrder.invoice_expires_at <= now,
        )
        .values(status=TmcPayOrderState.EXPIRED)
    )
    return result.rowcount


# --- Webhook deliveries ----------------------------------------------------------------------


async def claim_delivery(
    session: AsyncSession,
    *,
    webhook_id: str,
    invoice_id: str | None,
    event: str | None,
    status: str | None,
) -> bool:
    """Claim a delivery id, or report that it has already been seen.

    The insert *is* the deduplication: TMC PAY reuses `X-Webhook-ID` across retries, so a repeat
    conflicts on the primary key. Returns False for a repeat, which the caller answers with 2xx —
    the event was already processed, and telling TMC PAY otherwise would invite a retry that can
    only conflict again.

    Flushed in a SAVEPOINT so that a conflict does not poison the surrounding transaction. The
    alternative — SELECT then INSERT — is not the same thing: two concurrent deliveries of the
    same id would both find nothing and both proceed.

    The `add` happens **inside** the savepoint deliberately. Added outside it, the pending object
    survives the rollback and the next flush re-attempts the same doomed insert — which surfaces
    much later, as a `PendingRollbackError` on an unrelated statement.
    """
    delivery = TmcPayWebhookDelivery(
        webhook_id=webhook_id,
        invoice_id=invoice_id,
        event=event,
        status=status,
        outcome=OUTCOME_IGNORED,
    )
    try:
        async with session.begin_nested():
            session.add(delivery)
            await session.flush()
    except IntegrityError as exc:
        # Only a collision on the delivery id means "already claimed". Any other violation is a row
        # this process built wrongly, and returning False for it would be the worst outcome
        # available on this path: the caller reads False as "a duplicate, already handled" and
        # drops the delivery, so a webhook that should have issued credits is discarded in silence.
        # Raised instead, which fails the request and leaves TMC PAY's own delivery record unacked.
        if violated_constraint(exc) != DELIVERY_ID_INDEX:
            raise
        return False
    return True


async def note_delivery_outcome(
    session: AsyncSession,
    *,
    webhook_id: str,
    outcome: str,
    order_id: uuid.UUID | None = None,
) -> None:
    """Record what a claimed delivery ended up doing, for the audit trail."""
    values: dict[str, object] = {"outcome": outcome}
    if order_id is not None:
        values["order_id"] = order_id
    await session.execute(
        update(TmcPayWebhookDelivery)
        .where(TmcPayWebhookDelivery.webhook_id == webhook_id)
        .values(**values)
    )


async def deliveries_for(
    session: AsyncSession, order_id: uuid.UUID, *, limit: int
) -> Sequence[TmcPayWebhookDelivery]:
    statement = (
        select(TmcPayWebhookDelivery)
        .where(TmcPayWebhookDelivery.order_id == order_id)
        .order_by(TmcPayWebhookDelivery.received_at.desc())
        .limit(limit)
    )
    return list((await session.execute(statement)).scalars())


__all__ = [
    "LIVE_ORDER_STATES",
    "OPEN_ORDER_STATES",
    "OUTCOME_CREDITED",
    "OUTCOME_IGNORED",
    "OUTCOME_RECORDED",
    "OUTCOME_UNKNOWN",
    "ObservedRate",
    "SettledOrder",
    "attach_invoice",
    "claim_delivery",
    "count_live_orders",
    "create_order",
    "deliveries_for",
    "expire_lapsed",
    "fail_order",
    "find_by_external_id",
    "find_by_invoice",
    "get_order",
    "latest_exchange_rate",
    "note_delivery_outcome",
    "open_orders",
    "orders_for",
    "record_status",
    "settle",
]
