"""Buying credits through TMC PAY: the order endpoints and the webhook.

Four routes, and they divide along the only line that matters here — who is talking:

    POST   /v1/me/credits/tmc-pay/orders        the account, from a browser
    GET    /v1/me/credits/tmc-pay/orders        the account
    GET    /v1/me/credits/tmc-pay/orders/{id}   the account, polling while it pays
    POST   /v1/webhooks/tmc-pay                 TMC PAY, authenticated by HMAC

The account routes sit under `/v1/me` and follow that router's rules exactly: ownership enforced
in the query, another account's order reported **absent** rather than forbidden, and `no-store` on
every response. Creating an order is a `CookieWriterDep` — the same gate `POST /v1/me/deposits`
uses, because both spend a person's money and a CLI bearer token is the weaker credential.

The webhook route sits apart, and everything about it is deliberate:

* **It is unauthenticated in the session sense and authenticated in the only sense available.**
  There is no cookie and no bearer token; there is an HMAC over the raw body, keyed by the
  merchant's webhook secret. So the signature check happens before the body is parsed, before the
  invoice is looked up, and before anything is written.
* **It is exempt from the cross-site write guard by path.** Not because forgery does not apply,
  but because it cannot: TMC PAY is not a browser, sends no `Origin`, and carries no ambient
  credential for a cross-site page to abuse. Left un-exempt it would still pass — a request with
  neither initiator header is `UNPROVEN` rather than refused — but relying on that would make the
  route's correctness depend on a middleware detail rather than on a decision someone made.
* **It credits from stored state, never from the payload.** The amount credited is the
  `crypto_amount_rao` recorded when the invoice was created. A webhook body decides *whether* to
  credit, by carrying a status; it never decides *how much*. This is the single most important
  property in this file: a forged body cannot mint credits even if the secret leaks, beyond the
  amount of an invoice that already exists.
* **A duplicate delivery is a success, not an error.** TMC PAY reuses `X-Webhook-ID` on retry, and
  answering anything but 2xx to an event already applied invites a retry that can only conflict
  again.

What is *not* here: a poller. TMC PAY dispatches once per transition and never retries
automatically, so a delivery lost to a deploy is lost. `GET .../orders/{id}` refreshes from TMC PAY
while the owner is watching, and `scripts/reconcile_tmc_pay.py` sweeps everything else.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Path, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from conjectures_subnet.axiom import get_axiom
from conjectures_subnet.db import credits as credit_store
from conjectures_subnet.db import tmc_pay as order_store
from conjectures_subnet.db.errors import RecordConflict
from conjectures_subnet.db.models import TmcPayOrder, TmcPayOrderState
from submission_api import credits as credit_config
from submission_api import schemas_account as schemas
from submission_api import tmc_pay
from submission_api.dependencies import (
    CookieWriterDep,
    PrincipalDep,
    Services,
    ServicesDep,
    SessionDep,
)
from submission_api.errors import (
    BadRequest,
    Conflict,
    NotFound,
    ServiceUnavailable,
    Unauthorized,
)
from submission_api.routers._account import utc
from submission_api.settings import (
    DEFAULT_PAGE_SIZE,
    EXTERNAL_RATE_CURRENCY,
    MAX_PAGE_SIZE,
    RAO_PER_TAO,
    Settings,
)

logger = logging.getLogger("submission_api.routers.tmc_pay")

router = APIRouter(tags=["account"])

UUID_LENGTH = 36

REASON_NOT_CONFIGURED = "TMC_PAY_NOT_CONFIGURED"
REASON_TOO_MANY_OPEN_ORDERS = "TMC_PAY_TOO_MANY_OPEN_ORDERS"
REASON_NO_RATE = "TMC_PAY_RATE_UNAVAILABLE"
REASON_UPSTREAM_UNAVAILABLE = "TMC_PAY_UNAVAILABLE"
REASON_UPSTREAM_REFUSED = "TMC_PAY_REFUSED"
REASON_QUOTE_FAILED = "TMC_PAY_QUOTE_FAILED"
REASON_SIGNATURE_INVALID = "TMC_PAY_SIGNATURE_INVALID"
REASON_WEBHOOK_MALFORMED = "TMC_PAY_WEBHOOK_MALFORMED"

# `X-Webhook-ID` is TMC PAY's deduplication key and the primary key of the delivery table, so a
# delivery without one cannot be deduplicated and is refused rather than processed twice.
MAX_WEBHOOK_ID_LENGTH = 64


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PurchaseRequest(Payload):
    """Declared in whole credits, exactly as `POST /v1/me/deposits` is.

    Buying credits is the operation; TAO is how it is paid, and the fiat figure on the invoice is
    an artefact of the processor quoting in fiat. Accepting an amount instead of a count would ask
    the buyer to do the conversion, and then to explain the remainder.
    """

    credits: int = Field(ge=1, le=credit_config.MAX_CREDITS_PER_PURCHASE)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _require_enabled(settings: Settings) -> None:
    if not settings.tmc_pay_enabled:
        raise ServiceUnavailable(
            "paying with TMC PAY is not available on this deployment",
            reason_code=REASON_NOT_CONFIGURED,
        )


def _as_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        # Absent, not malformed: the identifier space is opaque to the caller, and a distinct
        # error for "not a UUID" tells them nothing they can act on.
        raise NotFound("order not found") from exc


def _state(status_text: str) -> TmcPayOrderState:
    """TMC PAY's lowercase invoice status as the stored state.

    A straight upper-case, because the enum labels were chosen to match. `parse_invoice` has
    already refused anything outside TMC PAY's published set, so this cannot invent a state.
    """
    return TmcPayOrderState(status_text.upper())


def _payment_url(order: TmcPayOrder, *, hosted_base_url: str) -> str | None:
    """Where to send the buyer to pay.

    TMC PAY's own `hosted_invoice_url` when the order has one, because it is the only correct
    answer: their public invoice route is keyed by an opaque `hosted_token`, so a URL built from a
    base and an invoice id addresses nothing.

    The constructed link survives only as a fallback for orders created before that URL was
    recorded. It is very probably wrong for them too, but it is what those rows already returned,
    and replacing a bad link with no link would take the payment page away from an order that is
    still open.
    """
    if order.hosted_invoice_url:
        return order.hosted_invoice_url
    if hosted_base_url and order.invoice_id:
        return f"{hosted_base_url}/i/{order.invoice_id}"
    return None


def _order(order: TmcPayOrder, *, settings: Settings) -> schemas.TmcPayOrder:
    amount_rao = order.crypto_amount_rao
    # The package bonus this order earned, if it earned one. Recomputed from the same schedule
    # `settle` granted it from, rather than read back off the ledger: the BONUS entry names no
    # order — `credit_ledger_tmc_pay_idx` is unique across every kind, so it cannot — and the
    # inputs are both immutable, so the two cannot drift.
    bonus_rao = credit_store.bonus_rao_for_credits(
        paid_credits=order.credits,
        credit_price_rao=order.credit_price_rao,
        bonus_schedule=credit_config.bonus_schedule_for(
            settings.credit_packages, credit_price_rao=settings.payment_amount_rao
        ),
    )
    hosted = settings.tmc_pay_hosted_base_url
    return schemas.TmcPayOrder(
        id=order.id,
        status=str(order.status),
        credits_expected=order.credits,
        credit_price_rao=order.credit_price_rao,
        # Derived from what was actually credited rather than from the credit count asked for, so
        # this field says what the balance gained and not what was hoped for. Zero until the ledger
        # entry exists, which is the same instant the credits become spendable.
        #
        # The package bonus is included, because it is part of what the balance gained: an order
        # for ten credits on the `10:3` deal reports thirteen. Reporting only the paid amount here
        # would have the purchase page contradict the ledger page beside it.
        credits_credited=(
            (amount_rao + bonus_rao) // order.credit_price_rao
            if order.credited_ledger_id is not None and amount_rao is not None
            else 0
        ),
        amount_rao=amount_rao,
        amount_tao=tmc_pay.tao_from_rao(amount_rao) if amount_rao is not None else None,
        deposit_address=order.deposit_address,
        # The same convenience the treasury path offers, pointed at TMC PAY's invoice address.
        # Integer rao rendered by string arithmetic; see `credits.btcli_command`.
        btcli_command=(
            credit_config.btcli_command(
                treasury=order.deposit_address,
                amount_rao=amount_rao,
                rao_per_tao=RAO_PER_TAO,
            )
            if order.deposit_address is not None and amount_rao is not None
            else None
        ),
        fiat_amount=order.fiat_amount,
        fiat_currency=order.fiat_currency,
        exchange_rate=order.exchange_rate,
        commission_amount=order.commission_amount,
        invoice_id=order.invoice_id,
        payment_url=_payment_url(order, hosted_base_url=hosted),
        needs_review=order.needs_review,
        failure_reason=order.failure_reason,
        expires_at=utc(order.invoice_expires_at) if order.invoice_expires_at else None,
        confirmed_at=utc(order.confirmed_at) if order.confirmed_at else None,
        created_at=utc(order.created_at),
        updated_at=utc(order.updated_at),
    )


# --- Buying ----------------------------------------------------------------------------------


@router.post(
    "/v1/me/credits/tmc-pay/orders",
    response_model=schemas.TmcPayPurchase,
    status_code=status.HTTP_201_CREATED,
    summary="Buy credits through TMC PAY and get an invoice to pay",
)
async def create_order(
    payload: PurchaseRequest,
    principal: CookieWriterDep,
    services: ServicesDep,
    session: SessionDep,
) -> schemas.TmcPayPurchase:
    """Create a TMC PAY invoice worth the credits asked for. Credits nothing.

    The sequence, and why it is this one:

    1. **The order row first**, holding the `external_id` this side mints. TMC PAY treats that
       value as an idempotency key, so a create whose response is lost can be repeated without
       making a second invoice — and a webhook for an invoice whose id never reached us still
       echoes this value and can be matched on it. Committed before the outbound call, so the
       recovery path exists even if this process dies mid-request.
    2. **Estimate the fiat amount** from TaoStats' TAO/USD rate, rounded up, plus a small margin.
       TMC PAY quotes in fiat and locks its own rate a moment later, so this is an estimate by
       construction.
    3. **Create the invoice, then check what came back.** The invoice must be TAO on Bittensor,
       for this merchant, and worth at least `credits * CREDIT_PRICE_RAO`. If the locked rate made
       it worth less, try again with the rate that invoice actually used — which is exact — and
       give up after `TMC_PAY_QUOTE_ATTEMPTS`. Selling a credit for less than its price is not an
       outcome to paper over.

    Nothing is credited here and nothing can be: the buyer has not paid yet. Credits appear when
    TMC PAY reports a paid status, through the webhook below or the reconciler.
    """
    settings = services.settings
    _require_enabled(settings)
    if payload.credits > settings.tmc_pay_max_credits:
        raise BadRequest(
            f"a single TMC PAY purchase may be for at most {settings.tmc_pay_max_credits} "
            "credits",
            extra={"maximum_credits": settings.tmc_pay_max_credits},
        )

    now = _now()
    open_orders = await order_store.count_live_orders(
        session, principal.account.id, now=now
    )
    if open_orders >= settings.tmc_pay_max_open_orders:
        # The side effect of this endpoint is an invoice at a payment processor, so the ceiling is
        # on outstanding invoices rather than on requests per minute. 409 with the count, so the
        # page can say "pay or wait for one of your open invoices" instead of "try again".
        raise Conflict(
            "you already have the maximum number of unpaid TMC PAY invoices; pay one or let "
            "it expire before starting another",
            reason_code=REASON_TOO_MANY_OPEN_ORDERS,
            extra={"open_orders": open_orders, "maximum": settings.tmc_pay_max_open_orders},
        )

    credit_price_rao = settings.payment_amount_rao
    required_rao = payload.credits * credit_price_rao

    # Resolved before the order row exists, so a purchase that cannot be priced leaves nothing
    # behind to explain.
    seed = await _seed_rate(session, services, settings, now=now)

    external_id = f"credits-{uuid.uuid4()}"
    order = await order_store.create_order(
        session,
        account_id=principal.account.id,
        credits_requested=payload.credits,
        credit_price_rao=credit_price_rao,
        external_id=external_id,
    )
    # Committed before the outbound call. If TMC PAY creates the invoice and this process dies
    # before reading the response, the row and its idempotency key survive — which is the whole
    # reason it is written first.
    await session.commit()

    quote = await _quote_invoice(
        services,
        settings,
        order=order,
        required_rao=required_rao,
        seed=seed,
        session=session,
    )
    invoice = quote.invoice

    await order_store.attach_invoice(
        session,
        order,
        invoice_id=invoice.invoice_id,
        merchant_id=invoice.merchant_id,
        status=_state(invoice.status),
        fiat_amount=invoice.fiat_amount,
        fiat_currency=invoice.fiat_currency,
        exchange_rate=invoice.exchange_rate,
        commission_amount=invoice.commission_amount,
        crypto_amount_rao=invoice.crypto_amount_rao,
        deposit_address=invoice.deposit_address,
        invoice_expires_at=invoice.expires_at,
        hosted_invoice_url=invoice.hosted_invoice_url,
    )
    balance = await credit_store.credit_balance(
        session, principal.account.id, credit_price_rao=credit_price_rao, now=now
    )
    await session.commit()

    get_axiom().info(
        source="api",
        event_type="tmc_pay_order_created",
        order_id=str(order.id),
        invoice_id=invoice.invoice_id,
        credits=payload.credits,
        crypto_amount_rao=invoice.crypto_amount_rao,
        required_rao=required_rao,
        fiat_amount=invoice.fiat_amount,
        fiat_currency=invoice.fiat_currency,
        # Which rate priced the estimate, so "are we still dependent on TaoStats" and "how often
        # does a purchase cost two invoices" are both one query.
        rate_source=seed.source,
        quote_attempts=quote.attempts,
    )
    return schemas.TmcPayPurchase(
        order=_order(order, settings=settings),
        balance=_balance(balance),
    )


@dataclass(frozen=True)
class RateSeed:
    """The rate a quote starts from: TAO per one fiat unit, and where it came from."""

    value: Decimal
    source: str


# Where a seed came from, for the telemetry field. Worth distinguishing because they have different
# accuracies and the difference is actionable: a deployment whose seeds are mostly external is one
# where nobody buys credits often enough to keep a locked rate warm, and one seeing a
# `-currency-mismatch` suffix is paying an extra invoice per purchase. The external labels are
# whatever the price source called itself — see `rates.SOURCE_*`.
SEED_INVOICE = "invoice"
SEED_INVOICE_STALE = "invoice-stale"
MISMATCH_SUFFIX = "-currency-mismatch"


async def _seed_rate(
    session: AsyncSession,
    services: Services,
    settings: Settings,
    *,
    now: dt.datetime,
) -> RateSeed:
    """The best available estimate of the rate TMC PAY is about to lock.

    **Preferring our own past invoices over a third-party feed is the point of this function.**
    Every invoice TMC PAY creates reports the `exchange_rate` it used, and that is a better seed
    than TaoStats for reasons that are not about freshness:

    * it is **TMC PAY's own rate source**, so it already carries whatever spread or rounding that
      source applies — which an outside feed cannot know about, and which otherwise shows up as a
      systematic bias the quote margin has to absorb;
    * it is denominated in the **merchant's own currency**, so a merchant onboarded in euros needs
      no conversion and no euro price feed.

    So the ladder is: a fresh rate off one of our own invoices, then an external feed
    (TaoMarketCap's own candles first, TaoStats second — see `rates.build_tao_usd_reader`), then a
    stale rate off our own invoices. The last rung exists because the quote band makes a bad seed a
    wasted round trip rather than a wrong price — so an hour-old rate is a far better answer to a
    feed outage than refusing to sell credits.

    An external feed is demoted below a stale local rate whenever the merchant currency is not the
    one it can price, because being in the right currency matters more than being recent.
    """
    observed = await order_store.latest_exchange_rate(
        session, fiat_currency=settings.tmc_pay_fiat_currency
    )
    local = _positive_decimal(observed.exchange_rate) if observed is not None else None
    if local is not None and observed is not None:
        age = (now - observed.observed_at).total_seconds()
        if age <= settings.tmc_pay_rate_ttl_seconds:
            return RateSeed(local, SEED_INVOICE)

    can_price = settings.tmc_pay_fiat_currency == EXTERNAL_RATE_CURRENCY
    quote = await services.tao_usd.tao_usd()
    # `exchange_rate` is crypto per one fiat unit, so a price *per TAO* is its reciprocal. Decimal
    # throughout: this is money, and a float here would round the price.
    external = Decimal(1) / quote.price if quote is not None else None

    if external is not None and quote is not None and can_price:
        return RateSeed(external, quote.source)
    if local is not None:
        logger.info(
            "seeding a TMC PAY quote from a stale locked rate; the quote band will correct it"
        )
        return RateSeed(local, SEED_INVOICE_STALE)
    if external is not None and quote is not None:
        logger.warning(
            "seeding a %s invoice from a TAO/%s rate; expect a requote on the first purchase",
            settings.tmc_pay_fiat_currency,
            EXTERNAL_RATE_CURRENCY,
        )
        return RateSeed(external, f"{quote.source}{MISMATCH_SUFFIX}")

    # Nothing to price from. Inventing a rate would mean selling credits at a number nobody chose,
    # so the sale is refused. 503: the buyer did nothing wrong.
    raise ServiceUnavailable(
        "no TAO exchange rate is available, so an invoice cannot be priced right now; "
        "retry shortly",
        reason_code=REASON_NO_RATE,
    )


def _positive_decimal(value: str | None) -> Decimal | None:
    """A finite, positive `Decimal`, or None. Never raises on stored or upstream text."""
    if not value:
        return None
    try:
        parsed = Decimal(value.strip())
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() and parsed > 0 else None


@dataclass(frozen=True)
class Quote:
    """A usable invoice, and how many it took to get one."""

    invoice: tmc_pay.Invoice
    attempts: int


async def _quote_invoice(
    services: Services,
    settings: Settings,
    *,
    order: TmcPayOrder,
    required_rao: int,
    seed: RateSeed,
    session: AsyncSession,
) -> Quote:
    """An invoice priced inside the band, or a refusal with the order marked FAILED.

    The loop exists because the fiat figure is the only thing this side controls and the TAO
    figure is the only one that matters. The first attempt uses `seed`; a later attempt uses the
    rate the previous invoice locked, which is not an estimate at all. Two attempts is the default
    and is enough unless the rate is moving faster than a round trip.

    An abandoned invoice from a mispriced attempt is left to expire. It cannot be cancelled through
    the API, and cancelling it locally would be worse than leaving it: a buyer who pays an invoice
    this side has forgotten would have sent real TAO with nothing to credit it to.
    """
    crypto_per_fiat_unit = seed.value
    failure = "TMC PAY could not price this purchase"
    for attempt in range(1, settings.tmc_pay_quote_attempts + 1):
        fiat_amount = tmc_pay.quote_fiat_amount(
            required_rao,
            crypto_per_fiat_unit=crypto_per_fiat_unit,
            margin_bps=settings.tmc_pay_quote_margin_bps,
            decimals=settings.tmc_pay_fiat_decimals,
        )
        try:
            invoice = await services.tmc_pay.create_invoice(
                fiat_amount=fiat_amount,
                fiat_currency=settings.tmc_pay_fiat_currency,
                # A distinct key per attempt: the same key would return the *previous*,
                # too-small invoice rather than a new one, which is exactly what TMC PAY's
                # idempotency promises and exactly the wrong thing here.
                external_id=(
                    order.external_id
                    if attempt == 1
                    else f"{order.external_id}-r{attempt}"
                ),
                description=f"{order.credits} verification credit(s) for conjectures.io",
                metadata={
                    "order_id": str(order.id),
                    "account_id": str(order.account_id),
                    "credits": order.credits,
                    "credit_price_rao": order.credit_price_rao,
                },
                ttl_minutes=settings.tmc_pay_ttl_minutes,
            )
        except tmc_pay.TmcPayUnavailable as exc:
            await _fail(session, order, reason=str(exc))
            raise ServiceUnavailable(
                "TMC PAY is temporarily unavailable; retry shortly",
                reason_code=REASON_UPSTREAM_UNAVAILABLE,
            ) from exc
        except tmc_pay.TmcPayRejected as exc:
            await _fail(session, order, reason=str(exc))
            logger.warning("TMC PAY refused an invoice for order %s: %s", order.id, exc)
            raise ServiceUnavailable(
                "TMC PAY refused to create an invoice for this purchase",
                reason_code=REASON_UPSTREAM_REFUSED,
            ) from exc

        problem = _invoice_problem(invoice, settings=settings, required_rao=required_rao)
        if problem is None:
            return Quote(invoice=invoice, attempts=attempt)

        failure, repriceable = problem
        logger.warning(
            "TMC PAY invoice %s is unusable for order %s on attempt %d: %s",
            invoice.invoice_id,
            order.id,
            attempt,
            failure,
        )
        if not repriceable:
            # Wrong currency, wrong network, wrong merchant, or a rate that is not a number.
            # Asking for a different amount would not change any of those.
            break
        # The rate this invoice actually locked. Exact, so the next attempt is not a guess — and it
        # corrects an estimate that was too high as readily as one that was too low.
        crypto_per_fiat_unit = Decimal(invoice.exchange_rate)

    await _fail(session, order, reason=failure)
    raise ServiceUnavailable(
        "TMC PAY could not price this purchase at the current TAO rate; retry shortly",
        reason_code=REASON_QUOTE_FAILED,
    )


def _invoice_problem(
    invoice: tmc_pay.Invoice, *, settings: Settings, required_rao: int
) -> tuple[str, bool] | None:
    """Why this invoice cannot be used and whether requoting could fix it, or None if it can.

    Every check here is a way the invoice could be a real invoice that funds the wrong thing, so
    all of them are refusals rather than warnings. The flag separates the two kinds:

    * **Structural, so not repriceable.** A currency or network other than TAO on Bittensor is
      money this validator does not price credits in and cannot credit. A merchant other than the
      configured one means the API key or base URL points somewhere unexpected, and the payment
      would settle to somebody else. An `exchange_rate` that is not a number leaves nothing to
      requote *from*. Asking for a different amount fixes none of these.
    * **Repriceable.** The locked TAO is outside the band the credit price allows. The invoice
      reported the rate it used, so the next attempt is arithmetic rather than an estimate.

    Both edges of the band are enforced, and the second one is not symmetry for its own sake:

    * below `required_rao` sells a credit for less than `CREDIT_PRICE_RAO`;
    * above the ceiling **overcharges the buyer**, which a stale rate or a non-USD merchant
      currency produces just as easily as a mistyped margin. See `tmc_pay.quote_ceiling`.
    """
    if invoice.crypto_currency != tmc_pay.CRYPTO_CURRENCY:
        return f"invoice is denominated in {invoice.crypto_currency}, not TAO", False
    if invoice.crypto_network != tmc_pay.CRYPTO_NETWORK:
        return f"invoice is on the {invoice.crypto_network} network, not Bittensor", False
    if (
        settings.tmc_pay_merchant_id
        and invoice.merchant_id != settings.tmc_pay_merchant_id
    ):
        return (
            f"invoice belongs to merchant {invoice.merchant_id}, not this deployment's",
            False,
        )
    ceiling = tmc_pay.quote_ceiling(
        required_rao,
        exchange_rate=invoice.exchange_rate,
        slippage_bps=settings.tmc_pay_max_slippage_bps,
        decimals=settings.tmc_pay_fiat_decimals,
    )
    if ceiling is None:
        return (
            f"invoice reports an unusable exchange rate {invoice.exchange_rate!r}",
            False,
        )
    if invoice.crypto_amount_rao < required_rao:
        return (
            (
                f"invoice locks {invoice.crypto_amount_rao} rao, below the "
                f"{required_rao} rao the credits cost"
            ),
            True,
        )
    if invoice.crypto_amount_rao > ceiling:
        return (
            (
                f"invoice locks {invoice.crypto_amount_rao} rao, above the {ceiling} rao a "
                f"{required_rao} rao purchase may cost at "
                f"{settings.tmc_pay_max_slippage_bps} bps slippage — the estimated rate was "
                "too high"
            ),
            True,
        )
    return None


async def _fail(session: AsyncSession, order: TmcPayOrder, *, reason: str) -> None:
    """Mark the order unusable and commit, so the refusal survives the response."""
    await order_store.fail_order(session, order, reason=reason[:500])
    await session.commit()


def _balance(balance: credit_store.CreditBalance) -> schemas.CreditBalance:
    return schemas.CreditBalance(
        credits_available=balance.credits_available,
        balance_rao=balance.balance_rao,
        held_rao=balance.held_rao,
        remainder_rao=balance.remainder_rao,
        credit_price_rao=balance.credit_price_rao,
        low_balance=balance.low_balance,
    )


# --- Reading ---------------------------------------------------------------------------------


@router.get(
    "/v1/me/credits/tmc-pay/orders",
    response_model=schemas.CursorPage[schemas.TmcPayOrder],
    summary="This account's TMC PAY purchases",
)
async def list_orders(
    response: Response,
    principal: PrincipalDep,
    services: ServicesDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> schemas.CursorPage[schemas.TmcPayOrder]:
    """Newest first. Not paginated beyond a limit: an account has a handful of these, not a feed."""
    rows = await order_store.orders_for(session, principal.account.id, limit=limit)
    _no_store(response)
    return schemas.CursorPage[schemas.TmcPayOrder](
        items=tuple(_order(row, settings=services.settings) for row in rows),
        next_cursor=None,
    )


@router.get(
    "/v1/me/credits/tmc-pay/orders/{order_id}",
    response_model=schemas.TmcPayOrder,
    summary="One TMC PAY purchase, refreshed from the processor",
)
async def read_order(
    order_id: Annotated[str, Path(min_length=UUID_LENGTH, max_length=UUID_LENGTH)],
    response: Response,
    principal: PrincipalDep,
    services: ServicesDep,
    session: SessionDep,
) -> schemas.TmcPayOrder:
    """The order, and — while it is still open — TMC PAY's current view of the invoice.

    This is a poll endpoint by design: TMC PAY's own guidance for a self-hosted payment page is to
    poll every few seconds, and this validator needs the same thing for a stronger reason.
    Webhooks are dispatched once with no automatic retry, so a delivery lost to a restart is lost
    for good; a buyer refreshing the page is then the fastest path to their credits existing.

    Two things keep that from turning into an outbound request per keystroke:

    * settled orders are never refreshed — there is nothing left to learn;
    * an order polled within `TMC_PAY_POLL_SECONDS` is served from stored state.

    An upstream failure is **not** an error here. The stored state is still true and still worth
    returning; a 503 would break a payment page over a transient blip in a refresh it did not ask
    for.
    """
    settings = services.settings
    order = await order_store.get_order(
        session, _as_uuid(order_id), principal.account.id
    )
    if _should_refresh(order, settings=settings, now=_now()):
        try:
            order = await refresh_order(
                session, order, services=services, settings=settings, now=_now()
            )
        except tmc_pay.TmcPayError as exc:
            logger.info("could not refresh TMC PAY order %s: %s", order.id, exc)
    _no_store(response)
    return _order(order, settings=settings)


def _should_refresh(
    order: TmcPayOrder, *, settings: Settings, now: dt.datetime
) -> bool:
    if not settings.tmc_pay_enabled:
        return False
    if order.status not in order_store.OPEN_ORDER_STATES:
        return False
    if order.invoice_id is None:
        # Nothing to read back: the invoice was never created. The reconciler cannot help either;
        # the order is either mid-creation or orphaned, and a webhook echoing its `external_id`
        # is what will resolve it.
        return False
    if order.last_polled_at is None:
        return True
    age = (now - order.last_polled_at).total_seconds()
    return age >= settings.tmc_pay_poll_seconds


async def refresh_order(
    session: AsyncSession,
    order: TmcPayOrder,
    *,
    services: Services,
    settings: Settings,
    now: dt.datetime,
) -> TmcPayOrder:
    """Read the invoice back from TMC PAY and apply what it says. Commits.

    Shared with `scripts/reconcile_tmc_pay.py` on purpose: a poll and a sweep must reach the same
    conclusion from the same status, and two implementations of "does this mean credits" is one
    more than the number of places that decision may live.

    Raises `TmcPayError` if TMC PAY could not be read. The caller decides whether that is fatal —
    it is not, for a refresh.
    """
    if order.invoice_id is None:  # pragma: no cover - callers check first
        return order
    invoice = await services.tmc_pay.read_invoice(order.invoice_id)
    return await apply_invoice(
        session,
        order,
        invoice,
        settings=settings,
        now=now,
        polled_at=now,
        source="poll",
    )


async def apply_invoice(
    session: AsyncSession,
    order: TmcPayOrder,
    invoice: tmc_pay.Invoice,
    *,
    settings: Settings,
    now: dt.datetime,
    polled_at: dt.datetime | None = None,
    event_id: str | None = None,
    source: str,
) -> TmcPayOrder:
    """Apply an invoice's status to an order, crediting it if the status means paid. Commits.

    The one place a TMC PAY status turns into money, reached from both the webhook and the
    reconciler. What it credits is `order.crypto_amount_rao` — recorded when the invoice was
    created, never taken from `invoice` — so a status is all the authority this path grants to
    anything arriving from outside.

    Three outcomes:

    * **paid** — one DEPOSIT entry, once. A second attempt raises `RecordConflict` from the store
      and is treated as success, because a duplicate delivery has already achieved what it wanted.
    * **paid, but not cleanly** — `overpaid`, or a credited late payment. Credits are issued for
      the invoice amount and the order is flagged for review, because the surplus (or the
      lateness) is something a person has to settle with TMC PAY.
    * **not paid** — the status is recorded and nothing moves. `underpaid` is flagged for review:
      real money arrived, and part-crediting a whole credit is not a decision to automate.
    """
    # TMC PAY reports no status for a payment that confirmed after the TTL, so the stored state
    # is the one thing here that is not a straight relabel of `invoice.status`: lateness comes
    # from the timestamps and maps onto LATE_PAYMENT, which is what the operator queue looks for.
    late = tmc_pay.payment_was_late(invoice)
    state = TmcPayOrderState.LATE_PAYMENT if late else _state(invoice.status)
    earned = tmc_pay.credits_are_earned(
        invoice.status,
        late=late,
        credit_late_payments=settings.tmc_pay_credit_late_payments,
    )
    # Money arrived but the amount or the timing is not what the invoice asked for. Flagged rather
    # than resolved: only a person with the TMC PAY dashboard can settle a surplus or chase a
    # part-payment, and quietly ignoring it would lose somebody's TAO.
    review = late or invoice.status in (
        tmc_pay.STATUS_OVERPAID,
        tmc_pay.STATUS_UNDERPAID,
    )

    if earned and order.credited_ledger_id is None:
        # Read before the write, because a `RecordConflict` rolls the transaction back and every
        # attribute on `order` expires with it.
        order_id, account_id = order.id, order.account_id
        try:
            settled = await order_store.settle(
                session,
                order,
                status=state,
                confirmed_at=invoice.confirmed_at,
                needs_review=review,
                event_id=event_id,
                polled_at=polled_at,
                now=now,
                bonus_schedule=credit_config.bonus_schedule_for(
                    settings.credit_packages,
                    credit_price_rao=settings.payment_amount_rao,
                ),
            )
        except RecordConflict:
            # Already credited by a concurrent webhook or reconciler pass. Nothing to do, and not
            # a failure: the account has its credits, which is the only outcome that matters.
            #
            # The rollback also discards the delivery row the webhook path claimed a moment ago,
            # which means a retry of that same delivery would be processed rather than recognised
            # as a duplicate. That is the safe direction — a re-processed delivery reaches this
            # same conflict and credits nothing, whereas committing the claim before applying the
            # invoice would let a failed apply mark the event done and lose someone's credits.
            await session.rollback()
            return await order_store.get_order(session, order_id, account_id)
        await session.commit()
        get_axiom().info(
            source="api",
            event_type="tmc_pay_order_credited",
            order_id=str(settled.order.id),
            invoice_id=invoice.invoice_id,
            invoice_status=invoice.status,
            amount_rao=settled.entry.amount_rao,
            credits_available=settled.balance.credits_available,
            needs_review=settled.order.needs_review,
            applied_by=source,
        )
        return settled.order

    await order_store.record_status(
        session,
        order,
        status=state,
        confirmed_at=invoice.confirmed_at,
        needs_review=review,
        event_id=event_id,
        polled_at=polled_at,
    )
    await session.commit()
    return order


# --- The webhook -----------------------------------------------------------------------------

# Mounted outside `/v1/me` because there is no `me`: the caller is TMC PAY. Under `/v1` so it
# still gets the security headers, the rate limiter and the request event, and exempted from the
# cross-site write guard by path in `app.py` — see this module's docstring.
WEBHOOK_PATH = "/v1/webhooks/tmc-pay"


@router.post(
    WEBHOOK_PATH,
    status_code=status.HTTP_200_OK,
    summary="TMC PAY invoice events",
    include_in_schema=False,
)
async def receive_webhook(
    request: Request,
    response: Response,
    services: ServicesDep,
    session: SessionDep,
) -> dict[str, str]:
    """Apply one TMC PAY invoice event.

    Ordered so that nothing untrusted is acted on before it is authenticated:

    1. read the raw body, bounded;
    2. verify the HMAC over exactly those bytes, with the merchant's webhook secret;
    3. only then parse the JSON, and only then look anything up.

    Step 2 uses the raw bytes and not a re-serialised parse — a JSON round trip changes key order
    and whitespace, and the signature is over the octets that arrived. That is why this handler
    takes a `Request` rather than a Pydantic body model: FastAPI would have parsed the body
    before the signature was checked, and the raw bytes would be gone.

    Answers 200 in every case it has understood, including for an event it decided not to act on.
    TMC PAY marks a non-2xx delivery failed and does not retry automatically, so a 4xx spent on
    "already processed" or "not our invoice" only creates work for an operator. The refusals that
    do go out are the two an operator needs to see: a bad signature and a missing delivery id.
    """
    settings = services.settings
    if not settings.tmc_pay_enabled:
        # Not 404: the route exists, and a deployment that has not configured the secret cannot
        # authenticate the caller, which is a 503 rather than a denial.
        raise ServiceUnavailable(
            "TMC PAY is not configured on this deployment",
            reason_code=REASON_NOT_CONFIGURED,
        )

    raw = await request.body()
    if len(raw) > tmc_pay.MAX_WEBHOOK_BYTES:
        raise BadRequest("webhook body is implausibly large", reason_code=REASON_WEBHOOK_MALFORMED)

    if not tmc_pay.signature_matches(
        raw,
        request.headers.get(tmc_pay.WEBHOOK_SIGNATURE_HEADER),
        settings.tmc_pay_webhook_secret,
    ):
        # 401 and nothing else: no hint about which part failed, and no lookup performed. The
        # event is logged because a signature failure in production is either a rotation somebody
        # forgot to coordinate or someone probing the endpoint, and both need to be visible.
        logger.warning(
            "rejected a TMC PAY webhook with an invalid signature (%d bytes)", len(raw)
        )
        get_axiom().warn(
            source="api",
            event_type="tmc_pay_webhook_rejected",
            error="signature did not verify",
            body_bytes=len(raw),
        )
        raise Unauthorized(
            "webhook signature did not verify", reason_code=REASON_SIGNATURE_INVALID
        )

    webhook_id = (request.headers.get(tmc_pay.WEBHOOK_ID_HEADER) or "").strip()
    if not webhook_id or len(webhook_id) > MAX_WEBHOOK_ID_LENGTH:
        # Without it there is no deduplication key, and a retry would be applied twice. Refused
        # loudly rather than processed on trust.
        raise BadRequest(
            f"{tmc_pay.WEBHOOK_ID_HEADER} is required",
            reason_code=REASON_WEBHOOK_MALFORMED,
        )
    event = (request.headers.get(tmc_pay.WEBHOOK_EVENT_HEADER) or "").strip()[:64] or None

    try:
        invoice = tmc_pay.parse_invoice(_json(raw))
    except tmc_pay.TmcPayRejected as exc:
        # The signature verified, so this body really is from TMC PAY and really is unusable —
        # a schema change, most likely. 400 is right: the delivery shows as failed in the
        # dashboard, which is exactly where an operator should see it.
        logger.error("a signed TMC PAY webhook could not be parsed: %s", exc)
        raise BadRequest(str(exc), reason_code=REASON_WEBHOOK_MALFORMED) from exc

    if (
        settings.tmc_pay_merchant_id
        and invoice.merchant_id != settings.tmc_pay_merchant_id
    ):
        # A delivery aimed at another integration, signed with a secret that happens to verify
        # here. Recorded and ignored; it must never reach an order lookup.
        logger.warning(
            "ignored a TMC PAY webhook for merchant %s", invoice.merchant_id
        )
        return await _answer(
            session,
            response,
            webhook_id=webhook_id,
            invoice=invoice,
            event=event,
            outcome=order_store.OUTCOME_IGNORED,
            claimed=await order_store.claim_delivery(
                session,
                webhook_id=webhook_id,
                invoice_id=invoice.invoice_id,
                event=event,
                status=invoice.status,
            ),
        )

    claimed = await order_store.claim_delivery(
        session,
        webhook_id=webhook_id,
        invoice_id=invoice.invoice_id,
        event=event,
        status=invoice.status,
    )
    if not claimed:
        # A retry of a delivery already processed. 200, because it was.
        await session.commit()
        _no_store(response)
        return {"status": "duplicate"}

    order = await order_store.find_by_invoice(session, invoice.invoice_id)
    if order is None and invoice.external_id:
        # The recovery path: the invoice was created but its id never reached this side. The
        # `external_id` echoed here is the one this side minted, so the order is still findable.
        order = await order_store.find_by_external_id(session, invoice.external_id)
    if order is None:
        logger.warning(
            "TMC PAY webhook names invoice %s, which no order here claims", invoice.invoice_id
        )
        get_axiom().warn(
            source="api",
            event_type="tmc_pay_webhook_unmatched",
            invoice_id=invoice.invoice_id,
            external_id=invoice.external_id,
            invoice_status=invoice.status,
        )
        return await _answer(
            session,
            response,
            webhook_id=webhook_id,
            invoice=invoice,
            event=event,
            outcome=order_store.OUTCOME_UNKNOWN,
            claimed=True,
        )

    if order.invoice_id is None:
        # Found by `external_id`. Fill in what the lost create-response would have recorded, so
        # the order becomes payable and pollable like any other. The invoice is checked against
        # the order first: `attach_invoice` refuses one that does not cover the credits.
        await order_store.attach_invoice(
            session,
            order,
            invoice_id=invoice.invoice_id,
            merchant_id=invoice.merchant_id,
            status=_state(invoice.status),
            fiat_amount=invoice.fiat_amount,
            fiat_currency=invoice.fiat_currency,
            exchange_rate=invoice.exchange_rate,
            commission_amount=invoice.commission_amount,
            crypto_amount_rao=invoice.crypto_amount_rao,
            deposit_address=invoice.deposit_address,
            invoice_expires_at=invoice.expires_at,
            hosted_invoice_url=invoice.hosted_invoice_url,
        )

    before = order.credited_ledger_id
    order = await apply_invoice(
        session,
        order,
        invoice,
        settings=settings,
        now=_now(),
        event_id=webhook_id,
        source="webhook",
    )
    outcome = (
        order_store.OUTCOME_CREDITED
        if before is None and order.credited_ledger_id is not None
        else order_store.OUTCOME_RECORDED
    )
    await order_store.note_delivery_outcome(
        session, webhook_id=webhook_id, outcome=outcome, order_id=order.id
    )
    await session.commit()
    _no_store(response)
    return {"status": outcome.lower()}


async def _answer(
    session: AsyncSession,
    response: Response,
    *,
    webhook_id: str,
    invoice: tmc_pay.Invoice,
    event: str | None,
    outcome: str,
    claimed: bool,
) -> dict[str, str]:
    """Record the outcome of a delivery this validator is not acting on, and answer 200."""
    del invoice, event
    if claimed:
        await order_store.note_delivery_outcome(
            session, webhook_id=webhook_id, outcome=outcome
        )
    await session.commit()
    _no_store(response)
    return {"status": outcome.lower()}


def _json(raw: bytes) -> object:
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise tmc_pay.TmcPayRejected("webhook body is not valid JSON") from exc


__all__ = ["WEBHOOK_PATH", "apply_invoice", "refresh_order", "router"]
