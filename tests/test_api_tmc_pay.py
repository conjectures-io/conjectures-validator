"""Buying credits through TMC PAY.

Mostly about what must *not* happen, because this is the one funding path whose evidence is a
signed message rather than finalized chain state:

* an unsigned or wrongly-signed webhook credits nothing;
* a *correctly* signed webhook credits the amount the invoice locked, and never an amount from
  its own body — so a forged body cannot mint credits even against a real invoice;
* the same delivery applied twice credits once;
* an invoice worth less than the credits it sells is refused rather than sold;
* another account cannot see, poll, or be credited by somebody else's order.

Needs a real PostgreSQL server, like the rest of the account suite:

    docker compose -f docker-compose.pytest-db.yml up -d
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import hmac
import importlib
import json
import pathlib
import sys
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal

import pytest

pytest.importorskip("fastapi", reason="submission API tests need the service extra")
pytest.importorskip("sqlalchemy", reason="submission API tests need the db extra")
pytest.importorskip("httpx", reason="submission API tests need the service extra")
pytest.importorskip("psycopg", reason="submission API tests need the db extra")

from conftest_api import harness, postgres_dsn
from test_api_accounts import client, same_origin, sign_in_by_email

from sqlalchemy.exc import IntegrityError

from conjectures_subnet.db import credits as credit_store
from conjectures_subnet.db import tmc_pay as order_store
from conjectures_subnet.db.errors import RecordConflict, violated_constraint
from conjectures_subnet.db.models import CreditEntryKind, TmcPayOrderState
from submission_api import tmc_pay
from submission_api.settings import RAO_PER_TAO
from submission_api.rates import StaticTaoUsdPriceReader

# The reconciler is a script rather than a package, so `scripts/` goes on the path and it is
# imported by name. Tested through its own `_pass` deliberately: a reimplementation of the sweep
# here would prove nothing about the thing cron actually runs.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
reconciler = importlib.import_module("reconcile_tmc_pay")

pytestmark = pytest.mark.skipif(
    postgres_dsn() is None,
    reason="no database: run `docker compose -f docker-compose.pytest-db.yml up -d`",
)

SECRET = "tmc-pay-webhook-secret-for-tests"
MERCHANT = "11111111-2222-3333-4444-555555555555"
DEPOSIT_ADDRESS = "5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy"
CREDIT_PRICE_RAO = RAO_PER_TAO // 2  # 0.5 TAO, the shipped default
TAO_USD = Decimal("400")  # so one credit is $200 before the quote margin

ORDERS = "/v1/me/credits/tmc-pay/orders"
WEBHOOK = "/v1/webhooks/tmc-pay"


def run(coroutine):
    return asyncio.run(coroutine)


def tmc_pay_settings(**overrides: str) -> dict[str, str]:
    environ = {
        "TMC_PAY_API_BASE_URL": "https://api.pay.test",
        "TMC_PAY_API_KEY": "test-merchant-api-key",
        "TMC_PAY_WEBHOOK_SECRET": SECRET,
        "TMC_PAY_MERCHANT_ID": MERCHANT,
        "TMC_PAY_HOSTED_BASE_URL": "https://pay.test",
        # Zero, so the arithmetic in these tests is exactly the credit price and an assertion
        # about the amount is an assertion about the pricing rule rather than about a margin.
        "TMC_PAY_QUOTE_MARGIN_BPS": "0",
        # Zero slippage too, so the band is the credit price plus one cent of rounding and an
        # assertion about an amount is an assertion about the pricing rule rather than a tolerance.
        "TMC_PAY_MAX_SLIPPAGE_BPS": "0",
    }
    environ.update(overrides)
    return environ


def _iso(moment: dt.datetime) -> str:
    """UTC with a `Z` suffix, the way TMC PAY publishes timestamps."""
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def invoice_body(
    *,
    invoice_id: str,
    status: str = "created",
    crypto_amount: str = "0.5",
    external_id: str | None = None,
    merchant_id: str = MERCHANT,
    fiat_amount: str = "200.00",
    confirmed_at: str | None = None,
    crypto_currency: str = "TAO",
    crypto_network: str = "bittensor",
    exchange_rate: str = "0.0025",
    hosted_invoice_url: str | None = "https://pay.test/invoice/hosted-token-1",
) -> dict:
    """A TMC PAY invoice object, shaped exactly as the published payload schema.

    The timestamps are relative to now rather than fixed strings. An invoice whose `expires_at` is
    in the past stops counting against the account's open-order allowance the moment it lapses —
    see `db.tmc_pay.count_live_orders` — so hard-coded dates would make these tests pass or fail
    depending on when they were run.
    """
    now = dt.datetime.now(dt.UTC)
    return {
        "invoice_id": invoice_id,
        "merchant_id": merchant_id,
        "external_id": external_id,
        "description": "credits",
        "metadata": {"order_id": "unused"},
        "status": status,
        "fiat_amount": fiat_amount,
        "fiat_currency": "USD",
        "crypto_amount": crypto_amount,
        "crypto_currency": crypto_currency,
        "crypto_network": crypto_network,
        "deposit_address": DEPOSIT_ADDRESS,
        "exchange_rate": exchange_rate,
        "commission_amount": "2.00",
        "hosted_invoice_url": hosted_invoice_url,
        "created_at": _iso(now),
        "expires_at": _iso(now + dt.timedelta(minutes=30)),
        "confirmed_at": confirmed_at,
    }


def signed(body: dict, *, secret: str = SECRET, webhook_id: str | None = None) -> tuple:
    """The exact bytes and headers TMC PAY would send for `body`.

    Signed over the serialised bytes and posted as those same bytes — not re-serialised by the
    client — because that is the property the verifier depends on.
    """
    raw = json.dumps(body).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        tmc_pay.WEBHOOK_ID_HEADER: webhook_id or str(uuid.uuid4()),
        tmc_pay.WEBHOOK_TIMESTAMP_HEADER: "1786000000",
        tmc_pay.WEBHOOK_SIGNATURE_HEADER: f"sha256={signature}",
        tmc_pay.WEBHOOK_EVENT_HEADER: f"invoice.{body['status']}",
    }
    return raw, headers


# TMC PAY's live `/api/v1/currencies` shape, trimmed to four entries. `network_metadata` carries
# the precision and `networks` the ordering, exactly as the real response does.
CURRENCY_CATALOGUE = [
    {
        "code": "TAO",
        "networks": ["bittensor"],
        "network_metadata": [
            {"network": "bittensor", "decimals": 9, "display_decimals": 4}
        ],
        "decimals": 9,
        "display_decimals": 4,
    },
    {
        "code": "USDC",
        "networks": ["ethereum", "base"],
        "network_metadata": [
            {"network": "ethereum", "decimals": 6, "display_decimals": 2},
            {"network": "base", "decimals": 6, "display_decimals": 2},
        ],
        "decimals": 6,
        "display_decimals": 2,
    },
    {
        "code": "BTC",
        "networks": ["bitcoin"],
        "network_metadata": [
            {"network": "bitcoin", "decimals": 8, "display_decimals": 8}
        ],
        "decimals": 8,
        "display_decimals": 8,
    },
    # No metadata at all, so the currency-level decimals have to carry it.
    {"code": "XMR", "networks": ["monero"], "decimals": 12, "display_decimals": 8},
]


class FakeGateway:
    """An `InvoiceGateway` that answers from a script instead of a network.

    `invoices` is what `create_invoice` returns, in order, so a test can make the first quote come
    back short and check that the second attempt uses the locked rate. `reads` maps invoice ids to
    the body a poll should see.
    """

    def __init__(
        self,
        invoices: list[dict],
        reads: dict[str, dict] | None = None,
        currencies: list[dict] | None = None,
    ) -> None:
        self.invoices = list(invoices)
        self.reads = dict(reads or {})
        self.created: list[dict] = []
        self.read_calls: list[str] = []
        self.currency_calls = 0
        self.currencies = CURRENCY_CATALOGUE if currencies is None else list(currencies)
        self.error: Exception | None = None

    async def create_invoice(self, **kwargs) -> tmc_pay.Invoice:
        if self.error is not None:
            raise self.error
        self.created.append(kwargs)
        body = self.invoices.pop(0)
        return tmc_pay.parse_invoice(body)

    async def read_invoice(self, invoice_id: str) -> tmc_pay.Invoice:
        if self.error is not None:
            raise self.error
        self.read_calls.append(invoice_id)
        if invoice_id not in self.reads:
            # An unscripted poll behaves like an outage rather than blowing up, because reading an
            # open order refreshes it and most tests here are not about that. `read_calls` still
            # records the attempt, so a test that cares can assert on it either way.
            raise tmc_pay.TmcPayUnavailable(f"no scripted read for {invoice_id}")
        return tmc_pay.parse_invoice(self.reads[invoice_id])

    async def list_currencies(self) -> tuple[tmc_pay.PaymentCurrency, ...]:
        self.currency_calls += 1
        if self.error is not None:
            raise self.error
        return tmc_pay.parse_currencies(self.currencies)

    async def aclose(self) -> None:
        return None


def kit_with(gateway, *, tao_usd: Decimal | None = TAO_USD, **overrides: str):
    return harness(
        tmc_pay=gateway,
        tao_usd=StaticTaoUsdPriceReader(tao_usd) if tao_usd is not None else None,
        **tmc_pay_settings(**overrides),
    )


@asynccontextmanager
async def buyer(kit, email: str = "buyer@example.com"):
    """A signed-in browser session, closed on the way out.

    A context manager rather than a factory because `AsyncClient` may only be entered once, and
    signing in makes requests — so the client has to be open before the sign-in, not after.
    """
    async with await client(kit) as http:
        account = await sign_in_by_email(kit, http, email)
        yield http, account


# --- Pure money arithmetic ---------------------------------------------------------------
# No database and no HTTP: these are the conversions everything else depends on being exact.


def test_rao_conversion_is_exact_in_both_directions():
    assert tmc_pay.rao_from_tao("0.5") == 500_000_000
    assert tmc_pay.rao_from_tao("1") == RAO_PER_TAO
    assert tmc_pay.rao_from_tao("0.000000001") == 1
    assert tmc_pay.tao_from_rao(500_000_000) == "0.5"
    assert tmc_pay.tao_from_rao(RAO_PER_TAO + 1) == "1.000000001"
    # Round-trip a value a float could not hold exactly.
    assert tmc_pay.tao_from_rao(tmc_pay.rao_from_tao("12.345678901")) == "12.345678901"


def test_an_amount_finer_than_one_rao_is_refused_rather_than_rounded():
    """Rounding somebody's money is not a decision this code may take on its own."""
    with pytest.raises(tmc_pay.TmcPayRejected):
        tmc_pay.rao_from_tao("0.0000000001")
    for bad in ("", "abc", "-1", "0", "NaN", "Infinity"):
        with pytest.raises(tmc_pay.TmcPayRejected):
            tmc_pay.rao_from_tao(bad)


def test_the_fiat_quote_always_rounds_up_to_cover_the_credits():
    """A cent of rounding must land in the buyer's balance, never in a discount.

    One credit at 0.5 TAO, and a rate chosen so the exact answer needs a third decimal place: the
    quote has to be the cent above, not the cent below.
    """
    # 0.0025 TAO per USD => one credit (0.5 TAO) is exactly $200.
    exact = tmc_pay.quote_fiat_amount(
        CREDIT_PRICE_RAO,
        crypto_per_fiat_unit=Decimal("0.0025"),
        margin_bps=0,
        decimals=2,
    )
    assert exact == "200.00"

    # A rate that makes the honest answer $173.611111…, so quantising decides the outcome.
    rounded = tmc_pay.quote_fiat_amount(
        CREDIT_PRICE_RAO,
        crypto_per_fiat_unit=Decimal("0.00288"),
        margin_bps=0,
        decimals=2,
    )
    assert rounded == "173.62"
    assert Decimal(rounded) * Decimal("0.00288") * RAO_PER_TAO >= CREDIT_PRICE_RAO

    # The margin is added on top, and a zero-decimal currency quantises to whole units.
    assert (
        tmc_pay.quote_fiat_amount(
            CREDIT_PRICE_RAO,
            crypto_per_fiat_unit=Decimal("0.0025"),
            margin_bps=100,
            decimals=2,
        )
        == "202.00"
    )
    assert (
        tmc_pay.quote_fiat_amount(
            CREDIT_PRICE_RAO,
            crypto_per_fiat_unit=Decimal("0.0025"),
            margin_bps=0,
            decimals=0,
        )
        == "200"
    )


def test_no_rate_means_no_quote():
    for rate in (Decimal(0), Decimal(-1)):
        with pytest.raises(tmc_pay.TmcPayUnavailable):
            tmc_pay.quote_fiat_amount(
                CREDIT_PRICE_RAO, crypto_per_fiat_unit=rate, margin_bps=0, decimals=2
            )


def test_only_confirmed_and_overpaid_earn_credits_by_default():
    """A late payment is real money and still not automatic — see `credits_are_earned`."""
    for status in ("confirmed", "overpaid"):
        assert tmc_pay.credits_are_earned(status, late=False, credit_late_payments=False)
    for status in ("created", "pending", "confirming", "underpaid", "expired", "cancelled"):
        assert not tmc_pay.credits_are_earned(status, late=False, credit_late_payments=False)


def test_lateness_decides_a_paid_status_either_way():
    """The opt-in governs late payments alone, and never gates a punctual confirmation."""
    assert not tmc_pay.credits_are_earned("confirmed", late=True, credit_late_payments=False)
    assert tmc_pay.credits_are_earned("confirmed", late=True, credit_late_payments=True)
    # An unpaid status stays unpaid however the flag is set: there is nothing to credit.
    assert not tmc_pay.credits_are_earned("expired", late=False, credit_late_payments=True)
    assert tmc_pay.credits_are_earned("confirmed", late=False, credit_late_payments=False)


def test_the_signature_check_is_over_the_raw_bytes():
    body = invoice_body(invoice_id="inv-1")
    raw = json.dumps(body).encode("utf-8")
    digest = hmac.new(SECRET.encode("utf-8"), raw, hashlib.sha256).hexdigest()

    assert tmc_pay.signature_matches(raw, f"sha256={digest}", SECRET)
    # Upper-case hex is still the same MAC.
    assert tmc_pay.signature_matches(raw, f"sha256={digest.upper()}", SECRET)
    # Re-serialising the parse changes the bytes, so the signature no longer matches — the exact
    # mistake the TMC PAY documentation warns about.
    reserialised = json.dumps(json.loads(raw), indent=2).encode("utf-8")
    assert not tmc_pay.signature_matches(reserialised, f"sha256={digest}", SECRET)
    # Everything malformed is simply "no match", never an exception.
    for header in (None, "", digest, "sha512=" + digest, "sha256=zz", "sha256="):
        assert not tmc_pay.signature_matches(raw, header, SECRET)
    assert not tmc_pay.signature_matches(raw, f"sha256={digest}", "another-secret")
    assert not tmc_pay.signature_matches(raw, f"sha256={digest}", "")


# --- Buying ------------------------------------------------------------------------------


def test_buying_credits_creates_an_invoice_that_covers_them():
    async def scenario():
        gateway = FakeGateway([invoice_body(invoice_id="inv-1", crypto_amount="0.5")])
        kit = await kit_with(gateway).setup()
        try:
            async with buyer(kit) as (http, account):
                created = await http.post(
                    ORDERS, json={"credits": 1}, headers=same_origin(http)
                )
                assert created.status_code == 201, created.text
                body = created.json()
                order = body["order"]

                assert order["status"] == "CREATED"
                assert order["credits_expected"] == 1
                assert order["credit_price_rao"] == CREDIT_PRICE_RAO
                # Nothing is credited by creating an invoice: the buyer has not paid yet.
                assert order["credits_credited"] == 0
                assert body["balance"]["credits_available"] == 0

                # One purchase is exactly one credit at 0.5 TAO.
                assert order["amount_rao"] == CREDIT_PRICE_RAO
                assert order["amount_tao"] == "0.5"
                assert order["deposit_address"] == DEPOSIT_ADDRESS
                assert order["btcli_command"] == (
                    f"btcli wallet transfer --dest {DEPOSIT_ADDRESS} --amount 0.5"
                )
                # TMC PAY's own hosted URL, not one assembled from a base and the invoice id:
                # their public invoice route is keyed by an opaque token.
                assert order["payment_url"] == "https://pay.test/invoice/hosted-token-1"
                assert order["invoice_id"] == "inv-1"

                # The fiat request was sized from the rate: 0.5 TAO at $400/TAO is $200.
                assert gateway.created[0]["fiat_amount"] == "200.00"
                assert gateway.created[0]["fiat_currency"] == "USD"
                assert gateway.created[0]["ttl_minutes"] == 30
                # The idempotency key is the one this side minted and stored.
                async with kit.session() as session:
                    stored = await order_store.find_by_invoice(session, "inv-1")
                    assert gateway.created[0]["external_id"] == stored.external_id
                    assert stored.account_id == uuid.UUID(account["id"])
        finally:
            await kit.teardown()

    run(scenario())


def test_a_short_invoice_is_requoted_at_the_rate_it_locked():
    """The rate moved between the estimate and the lock. The retry uses the exact rate.

    This is the case the quote loop exists for: the first invoice is worth less than the credits
    cost, so selling against it would mean giving a credit away below price.
    """

    async def scenario():
        gateway = FakeGateway(
            [
                # Asked for 0.5 TAO worth, got 0.49 — the rate moved against us.
                invoice_body(invoice_id="short", crypto_amount="0.49"),
                invoice_body(invoice_id="good", crypto_amount="0.5"),
            ]
        )
        kit = await kit_with(gateway).setup()
        try:
            async with buyer(kit) as (http, _):
                created = await http.post(
                    ORDERS, json={"credits": 1}, headers=same_origin(http)
                )
                assert created.status_code == 201, created.text
                order = created.json()["order"]
                assert order["invoice_id"] == "good"
                assert order["amount_rao"] == CREDIT_PRICE_RAO

                assert len(gateway.created) == 2
                # The retry carries a *different* idempotency key. Reusing the first would have
                # returned the short invoice again, which is what TMC PAY's idempotency promises.
                assert (
                    gateway.created[1]["external_id"] != gateway.created[0]["external_id"]
                )
                # And it was priced from the rate the short invoice locked (0.0025 TAO per USD),
                # not from the TaoStats estimate.
                assert gateway.created[1]["fiat_amount"] == "200.00"
        finally:
            await kit.teardown()

    run(scenario())


def test_a_purchase_is_refused_when_every_quote_comes_back_short():
    async def scenario():
        gateway = FakeGateway(
            [
                invoice_body(invoice_id="short-1", crypto_amount="0.4"),
                invoice_body(invoice_id="short-2", crypto_amount="0.4"),
            ]
        )
        kit = await kit_with(gateway).setup()
        try:
            async with buyer(kit) as (http, _):
                refused = await http.post(ORDERS, json={"credits": 1}, headers=same_origin(http))
                assert refused.status_code == 503, refused.text
                assert refused.json()["reason_code"] == "TMC_PAY_QUOTE_FAILED"

                # The order exists and says why it cannot be paid, rather than being deleted:
                # two real invoices were created at TMC PAY and an operator may need to see that.
                listed = await http.get(ORDERS)
                orders = listed.json()["items"]
                assert [item["status"] for item in orders] == ["FAILED"]
                assert "below the" in orders[0]["failure_reason"]
                assert orders[0]["deposit_address"] is None
        finally:
            await kit.teardown()

    run(scenario())


def test_an_invoice_in_the_wrong_currency_is_refused_without_a_retry():
    """Not a pricing problem, so asking for more money would not fix it."""

    async def scenario():
        gateway = FakeGateway(
            [
                invoice_body(
                    invoice_id="wrong", crypto_amount="0.5", crypto_currency="BTC"
                )
            ]
        )
        kit = await kit_with(gateway).setup()
        try:
            async with buyer(kit) as (http, _):
                refused = await http.post(ORDERS, json={"credits": 1}, headers=same_origin(http))
                assert refused.status_code == 503
                assert refused.json()["reason_code"] == "TMC_PAY_QUOTE_FAILED"
                assert len(gateway.created) == 1
        finally:
            await kit.teardown()

    run(scenario())


def test_buying_needs_a_rate_and_a_configured_processor():
    async def scenario():
        # Configured, but no TAO/USD rate: there is no honest fiat figure to ask for.
        gateway = FakeGateway([invoice_body(invoice_id="never")])
        kit = await kit_with(gateway, tao_usd=None).setup()
        try:
            async with buyer(kit) as (http, _):
                refused = await http.post(ORDERS, json={"credits": 1}, headers=same_origin(http))
                assert refused.status_code == 503
                assert refused.json()["reason_code"] == "TMC_PAY_RATE_UNAVAILABLE"
                assert gateway.created == []
        finally:
            await kit.teardown()

        # Not configured at all: the method is not offered and the endpoint says so.
        plain = await harness().setup()
        try:
            async with buyer(plain) as (http, _):
                refused = await http.post(ORDERS, json={"credits": 1}, headers=same_origin(http))
                assert refused.status_code == 503
                assert refused.json()["reason_code"] == "TMC_PAY_NOT_CONFIGURED"

                pricing = await http.get("/v1/catalog/credit-pricing")
                assert pricing.json()["methods"] == ["btcli"]
        finally:
            await plain.teardown()

    run(scenario())


def test_the_pricing_page_offers_tmc_pay_once_it_is_configured():
    async def scenario():
        kit = await kit_with(FakeGateway([])).setup()
        try:
            async with await client(kit) as http:
                pricing = await http.get("/v1/catalog/credit-pricing")
                assert pricing.status_code == 200
                body = pricing.json()
                assert body["methods"] == ["btcli", "tmc_pay"]
                assert body["price_rao"] == CREDIT_PRICE_RAO
        finally:
            await kit.teardown()

    run(scenario())


def test_outstanding_invoices_are_capped_per_account():
    async def scenario():
        gateway = FakeGateway(
            [invoice_body(invoice_id=f"inv-{index}") for index in range(4)]
        )
        kit = await kit_with(gateway, TMC_PAY_MAX_OPEN_ORDERS="2").setup()
        try:
            async with buyer(kit) as (http, _):
                for _ in range(2):
                    accepted = await http.post(
                        ORDERS, json={"credits": 1}, headers=same_origin(http)
                    )
                    assert accepted.status_code == 201, accepted.text
                refused = await http.post(ORDERS, json={"credits": 1}, headers=same_origin(http))
                assert refused.status_code == 409
                assert refused.json()["reason_code"] == "TMC_PAY_TOO_MANY_OPEN_ORDERS"
                assert refused.json()["open_orders"] == 2
        finally:
            await kit.teardown()

    run(scenario())


def test_a_purchase_needs_a_browser_session_that_proves_where_it_came_from():
    async def scenario():
        gateway = FakeGateway([invoice_body(invoice_id="inv-1")])
        kit = await kit_with(gateway).setup()
        try:
            async with await client(kit) as anonymous:
                assert (
                    await anonymous.post(ORDERS, json={"credits": 1})
                ).status_code == 401

            async with buyer(kit) as (http, _):
                # Signed in, but neither initiator header: an ambient credential does not get
                # to spend money on the strength of being present.
                assert (await http.post(ORDERS, json={"credits": 1})).status_code == 403
                assert gateway.created == []
        finally:
            await kit.teardown()

    run(scenario())


def test_a_purchase_refuses_more_credits_than_it_may_pay_for():
    async def scenario():
        kit = await kit_with(FakeGateway([]), TMC_PAY_MAX_CREDITS="5").setup()
        try:
            async with buyer(kit) as (http, _):
                refused = await http.post(
                    ORDERS, json={"credits": 11}, headers=same_origin(http)
                )
                assert refused.status_code == 400
        finally:
            await kit.teardown()

    run(scenario())


# --- The webhook -------------------------------------------------------------------------


async def _order_for(http, *, credits_: int = 1) -> dict:
    """Buy `credits_` credits and return the new order, asserting the purchase succeeded."""
    created = await http.post(ORDERS, json={"credits": credits_}, headers=same_origin(http))
    assert created.status_code == 201, created.text
    return created.json()["order"]


def test_a_confirmed_webhook_credits_the_locked_amount_once():
    async def scenario():
        gateway = FakeGateway([invoice_body(invoice_id="inv-1", crypto_amount="0.5")])
        kit = await kit_with(gateway).setup()
        try:
            async with buyer(kit) as (http, account):
                order = await _order_for(http)

                raw, headers = signed(
                    invoice_body(
                        invoice_id="inv-1",
                        status="confirmed",
                        crypto_amount="0.5",
                        confirmed_at=_iso(dt.datetime.now(dt.UTC)),
                    ),
                    webhook_id="delivery-1",
                )
                applied = await http.post(WEBHOOK, content=raw, headers=headers)
                assert applied.status_code == 200, applied.text
                assert applied.json()["status"] == "credited"

                balance = await http.get("/v1/me/credits")
                assert balance.json()["credits_available"] == 1
                assert balance.json()["balance_rao"] == CREDIT_PRICE_RAO

                refreshed = await http.get(f"{ORDERS}/{order['id']}")
                assert refreshed.json()["status"] == "CONFIRMED"
                assert refreshed.json()["credits_credited"] == 1
                assert refreshed.json()["needs_review"] is False
                # Settled, so the read endpoint has nothing to poll for.
                assert gateway.read_calls == []

                # The very same delivery again: recognised, and credited nothing further.
                again = await http.post(WEBHOOK, content=raw, headers=headers)
                assert again.status_code == 200
                assert again.json()["status"] == "duplicate"

                # And a *different* delivery id for the same invoice: still one credit entry.
                raw2, headers2 = signed(
                    invoice_body(
                        invoice_id="inv-1", status="confirmed", crypto_amount="0.5"
                    ),
                    webhook_id="delivery-2",
                )
                assert (await http.post(WEBHOOK, content=raw2, headers=headers2)).status_code == 200
                assert (await http.get("/v1/me/credits")).json()["credits_available"] == 1

                ledger = (await http.get("/v1/me/credits/ledger")).json()["items"]
                deposits = [row for row in ledger if row["kind"] == "DEPOSIT"]
                assert len(deposits) == 1
                assert deposits[0]["amount_rao"] == CREDIT_PRICE_RAO
                # A TMC PAY credit names its order, not a chain deposit.
                assert deposits[0]["deposit_id"] is None

            async with kit.session() as session:
                stored = await order_store.find_by_invoice(session, "inv-1")
                assert stored.credited_ledger_id is not None
                entry = await session.get(
                    credit_store.CreditLedgerEntry, stored.credited_ledger_id
                )
                assert entry.kind is CreditEntryKind.DEPOSIT
                assert entry.tmc_pay_order_id == stored.id
                assert entry.deposit_id is None
        finally:
            await kit.teardown()

    run(scenario())


def test_a_deal_is_granted_its_bonus_once_however_many_webhooks_arrive():
    """The TMC PAY half of the package deals, and the property that keeps them safe.

    **Keyed on the order's declared credit count, not on the rao that arrived.** An invoice only
    has to *cover* the credits it was opened for, so `crypto_amount_rao` routinely carries a
    remainder — matched on the amount, this path would have advertised the deals and then quietly
    declined to grant them. The invoice here pays 2.6 TAO for a 5-credit order to pin that down:
    off-package as an amount, still the five-credit deal as a purchase.

    **Granted once.** The BONUS entry is written in the same transaction as the DEPOSIT entry, so
    the guards that already stop a duplicate webhook crediting twice stop it granting free credits
    twice — without a second unique index. TMC PAY reuses `X-Webhook-ID` on retry and also sends
    fresh ids for the same invoice, so both are replayed here.
    """

    async def scenario():
        gateway = FakeGateway([invoice_body(invoice_id="inv-deal", crypto_amount="2.6")])
        # Slippage is allowed here, unlike the rest of this file: the whole point is an invoice
        # that locks MORE than the credits cost, which the default zero band would refuse at
        # order creation before the deal could be tested at all.
        kit = await kit_with(
            gateway,
            CREDIT_PACKAGES="1,5:1,10:3",
            TMC_PAY_MAX_SLIPPAGE_BPS="500",
        ).setup()
        try:
            async with buyer(kit) as (http, _):
                order = await _order_for(http, credits_=5)

                raw, headers = signed(
                    invoice_body(
                        invoice_id="inv-deal",
                        status="confirmed",
                        crypto_amount="2.6",
                        confirmed_at=_iso(dt.datetime.now(dt.UTC)),
                    ),
                    webhook_id="deal-1",
                )
                assert (await http.post(WEBHOOK, content=raw, headers=headers)).status_code == 200

                # Five paid credits, one free, and the 0.1 TAO overpayment still the buyer's.
                balance = (await http.get("/v1/me/credits")).json()
                assert balance["credits_available"] == 6
                assert balance["remainder_rao"] == 100_000_000

                # The purchase page agrees with the ledger page beside it.
                refreshed = await http.get(f"{ORDERS}/{order['id']}")
                assert refreshed.json()["credits_credited"] == 6

                ledger = (await http.get("/v1/me/credits/ledger")).json()["items"]
                bonuses = [row for row in ledger if row["kind"] == "BONUS"]
                assert len(bonuses) == 1
                assert bonuses[0]["amount_rao"] == CREDIT_PRICE_RAO
                assert bonuses[0]["reason"] == (
                    "package bonus: 1 credit(s) granted with a 5-credit purchase"
                )
                # It names neither source. `credit_ledger_tmc_pay_idx` is unique across every
                # kind, so the DEPOSIT entry has already claimed the order and a second row
                # referencing it would violate the index.
                assert bonuses[0]["deposit_id"] is None

                # The same delivery again, then a different id for the same invoice.
                assert (await http.post(WEBHOOK, content=raw, headers=headers)).json()["status"] == "duplicate"
                raw2, headers2 = signed(
                    invoice_body(
                        invoice_id="inv-deal", status="confirmed", crypto_amount="2.6"
                    ),
                    webhook_id="deal-2",
                )
                assert (await http.post(WEBHOOK, content=raw2, headers=headers2)).status_code == 200

                # Still six. A bonus granted per delivery would be free credits on demand.
                assert (await http.get("/v1/me/credits")).json()["credits_available"] == 6
                after = (await http.get("/v1/me/credits/ledger")).json()["items"]
                assert len([row for row in after if row["kind"] == "BONUS"]) == 1
        finally:
            await kit.teardown()

    run(scenario())


def test_an_order_off_every_deal_earns_no_bonus():
    async def scenario():
        gateway = FakeGateway([invoice_body(invoice_id="inv-plain", crypto_amount="1.5")])
        kit = await kit_with(gateway, CREDIT_PACKAGES="1,5:1,10:3").setup()
        try:
            async with buyer(kit) as (http, _):
                await _order_for(http, credits_=3)
                raw, headers = signed(
                    invoice_body(
                        invoice_id="inv-plain",
                        status="confirmed",
                        crypto_amount="1.5",
                        confirmed_at=_iso(dt.datetime.now(dt.UTC)),
                    ),
                    webhook_id="plain-1",
                )
                assert (await http.post(WEBHOOK, content=raw, headers=headers)).status_code == 200
                assert (await http.get("/v1/me/credits")).json()["credits_available"] == 3
                ledger = (await http.get("/v1/me/credits/ledger")).json()["items"]
                assert [row["kind"] for row in ledger] == ["DEPOSIT"]
        finally:
            await kit.teardown()

    run(scenario())


def test_the_credited_amount_comes_from_the_invoice_and_not_from_the_webhook():
    """The property that makes this path safe: a body decides *whether*, never *how much*.

    The webhook here is correctly signed and claims a hundred times the amount. The ledger must
    still move by exactly what the invoice locked when it was created.
    """

    async def scenario():
        gateway = FakeGateway([invoice_body(invoice_id="inv-1", crypto_amount="0.5")])
        kit = await kit_with(gateway).setup()
        try:
            async with buyer(kit) as (http, _):
                await _order_for(http, credits_=1)

                raw, headers = signed(
                    invoice_body(
                        invoice_id="inv-1", status="confirmed", crypto_amount="50"
                    )
                )
                assert (await http.post(WEBHOOK, content=raw, headers=headers)).status_code == 200

                balance = (await http.get("/v1/me/credits")).json()
                assert balance["balance_rao"] == CREDIT_PRICE_RAO
                assert balance["credits_available"] == 1
        finally:
            await kit.teardown()

    run(scenario())


def test_an_unsigned_or_wrongly_signed_webhook_credits_nothing():
    async def scenario():
        gateway = FakeGateway([invoice_body(invoice_id="inv-1", crypto_amount="0.5")])
        kit = await kit_with(gateway).setup()
        try:
            async with buyer(kit) as (http, _):
                await _order_for(http, credits_=1)
                body = invoice_body(
                    invoice_id="inv-1", status="confirmed", crypto_amount="0.5"
                )

                raw, headers = signed(body, secret="the-wrong-secret")
                wrong = await http.post(WEBHOOK, content=raw, headers=headers)
                assert wrong.status_code == 401
                assert wrong.json()["reason_code"] == "TMC_PAY_SIGNATURE_INVALID"

                raw, headers = signed(body)
                del headers[tmc_pay.WEBHOOK_SIGNATURE_HEADER]
                assert (await http.post(WEBHOOK, content=raw, headers=headers)).status_code == 401

                # Signed correctly, then the body edited: the MAC no longer covers these bytes.
                raw, headers = signed(body)
                tampered = raw.replace(b'"0.5"', b'"5.0"')
                assert (
                    await http.post(WEBHOOK, content=tampered, headers=headers)
                ).status_code == 401

                # A valid signature but no delivery id: nothing to deduplicate on.
                raw, headers = signed(body)
                del headers[tmc_pay.WEBHOOK_ID_HEADER]
                missing = await http.post(WEBHOOK, content=raw, headers=headers)
                assert missing.status_code == 400
                assert missing.json()["reason_code"] == "TMC_PAY_WEBHOOK_MALFORMED"

                assert (await http.get("/v1/me/credits")).json()["credits_available"] == 0
        finally:
            await kit.teardown()

    run(scenario())


def test_a_webhook_for_another_merchant_or_an_unknown_invoice_is_recorded_and_ignored():
    async def scenario():
        gateway = FakeGateway([invoice_body(invoice_id="inv-1", crypto_amount="0.5")])
        kit = await kit_with(gateway).setup()
        try:
            async with buyer(kit) as (http, _):
                await _order_for(http, credits_=1)

                foreign = signed(
                    invoice_body(
                        invoice_id="inv-1",
                        status="confirmed",
                        crypto_amount="0.5",
                        merchant_id="99999999-9999-9999-9999-999999999999",
                    )
                )
                answered = await http.post(WEBHOOK, content=foreign[0], headers=foreign[1])
                assert answered.status_code == 200
                assert answered.json()["status"] == "ignored"

                unknown = signed(
                    invoice_body(
                        invoice_id="never-created", status="confirmed", crypto_amount="0.5"
                    )
                )
                answered = await http.post(WEBHOOK, content=unknown[0], headers=unknown[1])
                assert answered.status_code == 200
                assert answered.json()["status"] == "unknown"

                assert (await http.get("/v1/me/credits")).json()["credits_available"] == 0
        finally:
            await kit.teardown()

    run(scenario())


# --- Which integrity violations mean "duplicate" -----------------------------------------------
# `IntegrityError` is one exception class for uniqueness, CHECK and foreign-key violations. Each of
# these store functions catches it to recognise the duplicate it expects, so each has to be shown
# not to claim that for a violation it did not expect.


def test_a_duplicate_invoice_is_still_reported_as_one():
    async def scenario():
        kit = await kit_with(FakeGateway([])).setup()
        try:
            async with buyer(kit) as (http, _):
                await sign_in_by_email(kit, http)
            async with kit.session() as session:
                account = await _an_account(kit, session)
                first = await order_store.create_order(
                    session,
                    account_id=account,
                    credits_requested=1,
                    credit_price_rao=CREDIT_PRICE_RAO,
                    external_id="dup-a",
                )
                await _attach(session, first, invoice_id="shared-invoice")
                await session.commit()

                second = await order_store.create_order(
                    session,
                    account_id=account,
                    credits_requested=1,
                    credit_price_rao=CREDIT_PRICE_RAO,
                    external_id="dup-b",
                )
                with pytest.raises(RecordConflict) as caught:
                    await _attach(session, second, invoice_id="shared-invoice")
                assert caught.value.reason_code == "DUPLICATE_TMC_PAY_INVOICE"
        finally:
            await kit.teardown()

    run(scenario())


def test_a_check_violation_is_not_reported_as_a_duplicate_invoice():
    """The bug this guards: any refused write used to read as "belongs to another order".

    A fiat currency that is not three upper-case letters violates `tmc_pay_fiat_currency_shape`,
    which is exactly the shape of upstream surprise this path has to survive honestly — and the
    misreport sent a reader looking for a second order that was never there.
    """

    async def scenario():
        kit = await kit_with(FakeGateway([])).setup()
        try:
            async with kit.session() as session:
                account = await _an_account(kit, session)
                order = await order_store.create_order(
                    session,
                    account_id=account,
                    credits_requested=1,
                    credit_price_rao=CREDIT_PRICE_RAO,
                    external_id="badcurrency",
                )
                with pytest.raises(IntegrityError):
                    await _attach(
                        session, order, invoice_id="bad-fiat", fiat_currency="dollars"
                    )
        finally:
            await kit.teardown()

    run(scenario())


def test_a_duplicate_external_id_is_still_reported_as_one():
    async def scenario():
        kit = await kit_with(FakeGateway([])).setup()
        try:
            async with kit.session() as session:
                account = await _an_account(kit, session)
                await order_store.create_order(
                    session,
                    account_id=account,
                    credits_requested=1,
                    credit_price_rao=CREDIT_PRICE_RAO,
                    external_id="same-key",
                )
                await session.commit()
                with pytest.raises(RecordConflict) as caught:
                    await order_store.create_order(
                        session,
                        account_id=account,
                        credits_requested=1,
                        credit_price_rao=CREDIT_PRICE_RAO,
                        external_id="same-key",
                    )
                assert caught.value.reason_code == "DUPLICATE_TMC_PAY_ORDER"
        finally:
            await kit.teardown()

    run(scenario())


def test_a_repeated_delivery_id_is_still_a_duplicate_and_nothing_else_is():
    """`claim_delivery` returns False for a duplicate. Anything else must not be silent.

    False means "already handled, drop it", so a violation misread as one discards a webhook that
    should have issued credits — the worst available outcome on this path.
    """

    async def scenario():
        kit = await kit_with(FakeGateway([])).setup()
        try:
            async with kit.session() as session:
                assert await order_store.claim_delivery(
                    session, webhook_id="wh-1", invoice_id="i", event="paid", status="confirmed"
                )
                # The same id again is the duplicate this is for.
                assert not await order_store.claim_delivery(
                    session, webhook_id="wh-1", invoice_id="i", event="paid", status="confirmed"
                )
                await session.commit()

                # A different violation on the same insert is raised, not swallowed as a duplicate.
                with pytest.raises(IntegrityError):
                    await order_store.claim_delivery(
                        session,
                        webhook_id="wh-2",
                        invoice_id="i",
                        event="paid",
                        status="x" * 400,
                    )
        finally:
            await kit.teardown()

    run(scenario())


def test_an_unrecognised_violation_reads_as_not_the_expected_one():
    """`violated_constraint` returns None when it cannot tell, and None must never match."""
    assert violated_constraint(RuntimeError("no diagnostics here")) is None
    assert violated_constraint(IntegrityError("stmt", {}, Exception("orig"))) is None


async def _an_account(kit, session):
    """One account id, created directly: these tests are about the store, not about sign-in."""
    from conjectures_subnet.db import accounts as account_store

    account = await account_store.create_account(
        session, email=f"store-{uuid.uuid4()}@example.com", email_verified=True
    )
    await session.flush()
    return account.id


async def _attach(session, order, *, invoice_id: str, fiat_currency: str = "USD"):
    return await order_store.attach_invoice(
        session,
        order,
        invoice_id=invoice_id,
        merchant_id=MERCHANT,
        status=order_store.TmcPayOrderState.CREATED,
        fiat_amount="200.00",
        fiat_currency=fiat_currency,
        exchange_rate="0.0025",
        commission_amount="2.00",
        crypto_amount_rao=CREDIT_PRICE_RAO,
        deposit_address=DEPOSIT_ADDRESS,
        invoice_expires_at=None,
    )


def test_underpaid_and_overpaid_are_handled_differently_and_both_flag_for_review():
    async def scenario():
        gateway = FakeGateway(
            [
                invoice_body(invoice_id="under", crypto_amount="0.5"),
                invoice_body(invoice_id="over", crypto_amount="0.5"),
            ]
        )
        kit = await kit_with(gateway).setup()
        try:
            async with buyer(kit) as (http, _):
                under = await _order_for(http, credits_=1)
                over = await _order_for(http, credits_=1)

                raw, headers = signed(
                    invoice_body(invoice_id="under", status="underpaid", crypto_amount="0.5")
                )
                assert (await http.post(WEBHOOK, content=raw, headers=headers)).status_code == 200

                raw, headers = signed(
                    invoice_body(invoice_id="over", status="overpaid", crypto_amount="0.5")
                )
                assert (await http.post(WEBHOOK, content=raw, headers=headers)).status_code == 200

                # Underpaid credits nothing — part-crediting a whole credit is not automatic.
                under_body = (await http.get(f"{ORDERS}/{under['id']}")).json()
                assert under_body["status"] == "UNDERPAID"
                assert under_body["credits_credited"] == 0
                assert under_body["needs_review"] is True

                # Overpaid credits the invoice amount and leaves the surplus to a person.
                over_body = (await http.get(f"{ORDERS}/{over['id']}")).json()
                assert over_body["status"] == "OVERPAID"
                assert over_body["credits_credited"] == 1
                assert over_body["needs_review"] is True

                assert (await http.get("/v1/me/credits")).json()["credits_available"] == 1
        finally:
            await kit.teardown()

    run(scenario())


def test_the_payment_url_is_tmc_pays_own_and_survives_a_reread():
    """Stored at attach time, so a later read returns it without another outbound call."""

    async def scenario():
        gateway = FakeGateway(
            [invoice_body(invoice_id="hosted", hosted_invoice_url="https://pay.test/p/tok-9")]
        )
        kit = await kit_with(gateway).setup()
        try:
            async with buyer(kit) as (http, _):
                order = await _order_for(http, credits_=1)
                assert order["payment_url"] == "https://pay.test/p/tok-9"

                reread = (await http.get(f"{ORDERS}/{order['id']}")).json()
                assert reread["payment_url"] == "https://pay.test/p/tok-9"

                listed = (await http.get(ORDERS)).json()["items"]
                assert listed[0]["payment_url"] == "https://pay.test/p/tok-9"
        finally:
            await kit.teardown()

    run(scenario())


def test_an_invoice_without_a_hosted_url_falls_back_to_the_constructed_link():
    """Only reachable for rows recorded before the URL was stored, and better than no link."""

    async def scenario():
        gateway = FakeGateway(
            [invoice_body(invoice_id="nolink", hosted_invoice_url=None)]
        )
        kit = await kit_with(gateway).setup()
        try:
            async with buyer(kit) as (http, _):
                order = await _order_for(http, credits_=1)
                assert order["payment_url"] == "https://pay.test/i/nolink"
        finally:
            await kit.teardown()

    run(scenario())


def test_a_hostile_hosted_url_never_reaches_the_buyer():
    """The redirect target is validated at parse time, so it cannot become a javascript: URL."""

    async def scenario():
        gateway = FakeGateway(
            [invoice_body(invoice_id="evil", hosted_invoice_url="javascript:alert(1)")]
        )
        kit = await kit_with(gateway).setup()
        try:
            async with buyer(kit) as (http, _):
                order = await _order_for(http, credits_=1)
                # Dropped during parsing, so the response carries the constructed link instead.
                assert order["payment_url"] == "https://pay.test/i/evil"
                assert "javascript" not in (order["payment_url"] or "")
        finally:
            await kit.teardown()

    run(scenario())


CURRENCIES = "/v1/catalog/payment-currencies"


# --- Paying in something other than TAO -------------------------------------------------------
# The price stays `credits x CREDIT_PRICE_RAO` of TAO. It is converted to fiat, and TMC PAY
# converts that to the chosen currency. So what changes is what arrives, never what a credit costs.


def usdc_invoice(invoice_id: str, **overrides) -> dict:
    """An invoice TMC PAY would return for a USDC-on-Base purchase of one credit.

    `fiat_amount` is $200 because one credit is 0.5 TAO and the test rate is $400/TAO — the same
    figure a TAO invoice for this purchase carries, which is the point.

    The id is explicit because `tmc_pay_orders_invoice_idx` is unique and this module shares one
    database across every test in it.
    """
    return invoice_body(
        **{
            "invoice_id": invoice_id,
            "crypto_currency": "USDC",
            "crypto_network": "base",
            "crypto_amount": "200.00",
            "fiat_amount": "200.00",
            "exchange_rate": "1.0",
            **overrides,
        }
    )


def test_a_purchase_can_be_paid_in_another_currency_at_the_same_tao_price():
    async def scenario():
        gateway = FakeGateway([usdc_invoice("usdc-pair")])
        kit = await kit_with(gateway).setup()
        try:
            async with buyer(kit) as (http, _):
                created = await http.post(
                    ORDERS,
                    json={"credits": 1, "crypto_currency": "USDC", "crypto_network": "base"},
                    headers=same_origin(),
                )
                assert created.status_code == 201, created.text
                order = created.json()["order"]

                # The pair TMC PAY was asked for, and the pair it answered with.
                assert gateway.created[0]["crypto_currency"] == "USDC"
                assert gateway.created[0]["crypto_network"] == "base"
                assert order["crypto_currency"] == "USDC"
                assert order["crypto_network"] == "base"
                assert order["crypto_amount"] == "200.00"

                # The fiat figure is the same one a TAO purchase of one credit would carry: the
                # price is in TAO and the currency only changes what is sent.
                assert order["fiat_amount"] == "200.00"

                # No rao, and no btcli command: neither means anything for a USDC invoice.
                assert order["amount_rao"] is None
                assert order["amount_tao"] is None
                assert order["btcli_command"] is None
        finally:
            await kit.teardown()

    run(scenario())


def test_the_quote_margin_is_not_charged_on_a_non_tao_purchase():
    """The margin protects the locked TAO from rate movement. With no TAO locked, it is a surcharge."""

    async def scenario():
        gateway = FakeGateway([usdc_invoice("usdc-margin")])
        # The ceiling has to clear the margin or settings refuse to start; both are raised so the
        # margin is the only thing under test.
        kit = await kit_with(
            gateway, TMC_PAY_QUOTE_MARGIN_BPS="500", TMC_PAY_MAX_SLIPPAGE_BPS="1000"
        ).setup()
        try:
            async with buyer(kit) as (http, _):
                created = await http.post(
                    ORDERS,
                    json={"credits": 1, "crypto_currency": "USDC", "crypto_network": "base"},
                    headers=same_origin(),
                )
                assert created.status_code == 201, created.text
                # 5% would have asked for $210. The buyer is asked for the honest $200.
                assert gateway.created[0]["fiat_amount"] == "200.00"
        finally:
            await kit.teardown()

    run(scenario())


def test_a_non_tao_purchase_credits_the_tao_price_exactly():
    async def scenario():
        gateway = FakeGateway([usdc_invoice("usdc-credit")])
        kit = await kit_with(gateway).setup()
        try:
            async with buyer(kit) as (http, _):
                order = await http.post(
                    ORDERS,
                    json={"credits": 1, "crypto_currency": "USDC", "crypto_network": "base"},
                    headers=same_origin(),
                )
                order_id = order.json()["order"]["id"]

                raw, headers = signed(usdc_invoice("usdc-credit", status="confirmed"))
                assert (await http.post(WEBHOOK, content=raw, headers=headers)).status_code == 200

                body = (await http.get(f"{ORDERS}/{order_id}")).json()
                assert body["status"] == "CONFIRMED"
                assert body["credits_credited"] == 1
                assert body["needs_review"] is False

                balance = (await http.get("/v1/me/credits")).json()
                assert balance["credits_available"] == 1
                # Exactly the price, with no remainder: the fiat figure was computed from it.
                assert balance["balance_rao"] == CREDIT_PRICE_RAO
                assert balance["remainder_rao"] == 0
        finally:
            await kit.teardown()

    run(scenario())


def test_a_single_network_currency_needs_no_network():
    async def scenario():
        gateway = FakeGateway(
            [
                invoice_body(
                    invoice_id="btc-single",
                    crypto_currency="BTC",
                    crypto_network="bitcoin",
                    crypto_amount="0.00311",
                    fiat_amount="200.00",
                )
            ]
        )
        kit = await kit_with(gateway).setup()
        try:
            async with buyer(kit) as (http, _):
                created = await http.post(
                    ORDERS, json={"credits": 1, "crypto_currency": "BTC"}, headers=same_origin()
                )
                assert created.status_code == 201, created.text
                assert gateway.created[0]["crypto_network"] == "bitcoin"
        finally:
            await kit.teardown()

    run(scenario())


def test_a_multi_network_currency_must_name_one():
    async def scenario():
        kit = await kit_with(FakeGateway([])).setup()
        try:
            async with buyer(kit) as (http, _):
                answer = await http.post(
                    ORDERS, json={"credits": 1, "crypto_currency": "USDC"}, headers=same_origin()
                )
                assert answer.status_code == 400
                body = answer.json()
                assert body["reason_code"] == "TMC_PAY_UNSUPPORTED_PAIR"
                assert set(body["networks"]) == {"ethereum", "base"}
        finally:
            await kit.teardown()

    run(scenario())


def test_a_pair_tmc_pay_does_not_accept_is_refused_before_an_order_exists():
    """400 rather than 503: the deployment and the processor are both fine."""

    async def scenario():
        kit = await kit_with(FakeGateway([])).setup()
        try:
            async with buyer(kit) as (http, _):
                for payload, expected in (
                    ({"credits": 1, "crypto_currency": "DOGE"}, "DOGE"),
                    (
                        {"credits": 1, "crypto_currency": "BTC", "crypto_network": "base"},
                        "bitcoin",
                    ),
                ):
                    answer = await http.post(ORDERS, json=payload, headers=same_origin())
                    assert answer.status_code == 400, answer.text
                    assert answer.json()["reason_code"] == "TMC_PAY_UNSUPPORTED_PAIR"
                    del expected

                # Nothing was recorded for a request that never reached the processor.
                listed = (await http.get(ORDERS)).json()["items"]
                assert listed == []
        finally:
            await kit.teardown()

    run(scenario())


def test_a_network_without_a_currency_is_refused():
    async def scenario():
        kit = await kit_with(FakeGateway([])).setup()
        try:
            async with buyer(kit) as (http, _):
                answer = await http.post(
                    ORDERS, json={"credits": 1, "crypto_network": "base"}, headers=same_origin()
                )
                assert answer.status_code == 400
                assert answer.json()["reason_code"] == "TMC_PAY_UNSUPPORTED_PAIR"
        finally:
            await kit.teardown()

    run(scenario())


def test_an_invoice_for_the_wrong_fiat_amount_is_refused_and_not_requoted():
    """For a non-TAO pair the fiat figure is the whole contract, so a mismatch is structural."""

    async def scenario():
        gateway = FakeGateway([usdc_invoice("usdc-shortfiat", fiat_amount="150.00")])
        kit = await kit_with(gateway).setup()
        try:
            async with buyer(kit) as (http, _):
                answer = await http.post(
                    ORDERS,
                    json={"credits": 1, "crypto_currency": "USDC", "crypto_network": "base"},
                    headers=same_origin(),
                )
                assert answer.status_code == 503
                assert answer.json()["reason_code"] == "TMC_PAY_QUOTE_FAILED"
                # Structural, so exactly one invoice was attempted rather than a requote.
                assert len(gateway.created) == 1
        finally:
            await kit.teardown()

    run(scenario())


def test_an_invoice_in_a_currency_other_than_the_one_asked_for_is_refused():
    """The buyer chose USDC. An invoice in BTC funds something they did not agree to."""

    async def scenario():
        gateway = FakeGateway(
            [usdc_invoice("usdc-wrongpair", crypto_currency="BTC", crypto_network="bitcoin")]
        )
        kit = await kit_with(gateway).setup()
        try:
            async with buyer(kit) as (http, _):
                answer = await http.post(
                    ORDERS,
                    json={"credits": 1, "crypto_currency": "USDC", "crypto_network": "base"},
                    headers=same_origin(),
                )
                assert answer.status_code == 503
                assert answer.json()["reason_code"] == "TMC_PAY_QUOTE_FAILED"
        finally:
            await kit.teardown()

    run(scenario())


# --- Where the buyer lands when the payment window closes --------------------------------------


def test_the_invoice_carries_both_redirect_targets():
    """TMC PAY's hosted window is a separate tab; these are how the buyer gets back."""

    async def scenario():
        gateway = FakeGateway([invoice_body(invoice_id="redirects")])
        kit = await kit_with(gateway, WEBSITE_BASE_URL="https://conjectures.test").setup()
        try:
            async with buyer(kit) as (http, _):
                order = await _order_for(http, credits_=1)
                sent = gateway.created[0]

                assert sent["success_redirect_url"] == (
                    f"https://conjectures.test/tmc-pay/success?order={order['id']}"
                )
                assert sent["failure_redirect_url"] == (
                    f"https://conjectures.test/tmc-pay/failure?order={order['id']}"
                )
        finally:
            await kit.teardown()

    run(scenario())


def test_a_trailing_slash_on_the_website_base_does_not_double():
    async def scenario():
        gateway = FakeGateway([invoice_body(invoice_id="redirect-slash")])
        kit = await kit_with(gateway, WEBSITE_BASE_URL="https://conjectures.test/").setup()
        try:
            async with buyer(kit) as (http, _):
                await _order_for(http, credits_=1)
                assert "//tmc-pay" not in gateway.created[0]["success_redirect_url"]
        finally:
            await kit.teardown()

    run(scenario())


def test_a_requote_keeps_the_same_redirect_targets():
    """They name the order, not the attempt, so a second invoice lands in the same place."""

    async def scenario():
        gateway = FakeGateway(
            [
                # Short, so the first attempt is refused and a second is quoted.
                invoice_body(invoice_id="rq-1", crypto_amount="0.1"),
                invoice_body(invoice_id="rq-2", crypto_amount="0.5"),
            ]
        )
        kit = await kit_with(
            gateway, TMC_PAY_QUOTE_ATTEMPTS="2", WEBSITE_BASE_URL="https://conjectures.test"
        ).setup()
        try:
            async with buyer(kit) as (http, _):
                await _order_for(http, credits_=1)
                assert len(gateway.created) == 2
                first, second = gateway.created
                assert first["success_redirect_url"] == second["success_redirect_url"]
                assert first["failure_redirect_url"] == second["failure_redirect_url"]
        finally:
            await kit.teardown()

    run(scenario())


def test_a_client_cannot_choose_where_it_is_redirected():
    """Client-supplied targets would make this an open redirect wearing TMC PAY's domain."""

    async def scenario():
        gateway = FakeGateway([invoice_body(invoice_id="no-inject")])
        kit = await kit_with(gateway).setup()
        try:
            async with buyer(kit) as (http, _):
                refused = await http.post(
                    ORDERS,
                    json={
                        "credits": 1,
                        "success_redirect_url": "https://evil.example/harvest",
                    },
                    headers=same_origin(),
                )
                assert refused.status_code == 400, refused.text
                body = refused.json()
                assert body["reason_code"] == "MALFORMED_REQUEST"
                assert any(
                    e["location"] == "body.success_redirect_url"
                    and e["type"] == "extra_forbidden"
                    for e in body["errors"]
                ), body["errors"]
                # Refused before anything reached the processor.
                assert gateway.created == []
        finally:
            await kit.teardown()

    run(scenario())


def test_the_default_is_still_tao_on_bittensor():
    async def scenario():
        gateway = FakeGateway([invoice_body(invoice_id="tao-default")])
        kit = await kit_with(gateway).setup()
        try:
            async with buyer(kit) as (http, _):
                created = await http.post(
                    ORDERS, json={"credits": 1}, headers=same_origin()
                )
                assert created.status_code == 201, created.text
                assert gateway.created[0]["crypto_currency"] == "TAO"
                assert gateway.created[0]["crypto_network"] == "bittensor"
                # A default purchase never consults the catalogue.
                assert gateway.currency_calls == 0
                order = created.json()["order"]
                assert order["amount_rao"] == CREDIT_PRICE_RAO
                assert order["btcli_command"] is not None
        finally:
            await kit.teardown()

    run(scenario())




def test_the_currency_list_reports_every_pair_and_flags_what_is_payable():
    """The whole catalogue, so a page can distinguish "we do not" from "the processor does not"."""

    async def scenario():
        gateway = FakeGateway([])
        kit = await kit_with(gateway).setup()
        try:
            async with buyer(kit) as (http, _):
                body = (await http.get(CURRENCIES)).json()

                assert body["default_currency"] == "TAO"
                assert body["default_network"] == "bittensor"

                by_code = {c["code"]: c for c in body["currencies"]}
                assert set(by_code) == {"TAO", "USDC", "BTC", "XMR"}

                # No allowlist configured, so every pair TMC PAY offers is payable — which is
                # what the purchase endpoint accepts, and the two must not disagree.
                for code in ("TAO", "USDC", "BTC", "XMR"):
                    assert by_code[code]["payable"] is True, code
                    assert all(n["payable"] for n in by_code[code]["networks"]), code
                assert by_code["TAO"]["networks"][0]["network"] == "bittensor"

                # Precision comes from `network_metadata` where present.
                assert by_code["USDC"]["networks"][0] == {
                    "network": "ethereum",
                    "decimals": 6,
                    "display_decimals": 2,
                    "payable": True,
                }
                # ...and falls back to the currency's own decimals where it is absent.
                assert by_code["XMR"]["networks"][0]["decimals"] == 12
                assert by_code["XMR"]["networks"][0]["display_decimals"] == 8

                # Multi-chain currencies keep TMC PAY's ordering.
                assert [n["network"] for n in by_code["USDC"]["networks"]] == [
                    "ethereum",
                    "base",
                ]
        finally:
            await kit.teardown()

    run(scenario())


def test_an_allowlist_narrows_the_flag_and_the_purchase_together():
    """One rule, two endpoints. They disagreed once: the page said no while the POST said yes."""

    async def scenario():
        gateway = FakeGateway([usdc_invoice("usdc-allowed")])
        kit = await kit_with(
            gateway, TMC_PAY_PAYABLE_PAIRS="TAO:bittensor,USDC:base"
        ).setup()
        try:
            async with buyer(kit) as (http, _):
                body = (await http.get(CURRENCIES)).json()
                by_code = {c["code"]: c for c in body["currencies"]}

                assert by_code["TAO"]["payable"] is True
                assert by_code["USDC"]["payable"] is True
                # Allowlisted on base only, so ethereum is off even though TMC PAY offers it.
                networks = {n["network"]: n["payable"] for n in by_code["USDC"]["networks"]}
                assert networks == {"base": True, "ethereum": False}
                for code in ("BTC", "XMR"):
                    assert by_code[code]["payable"] is False, code

                # What the page advertises is what the purchase accepts.
                allowed = await http.post(
                    ORDERS,
                    json={"credits": 1, "crypto_currency": "USDC", "crypto_network": "base"},
                    headers=same_origin(),
                )
                assert allowed.status_code == 201, allowed.text

                for payload in (
                    {"credits": 1, "crypto_currency": "USDC", "crypto_network": "ethereum"},
                    {"credits": 1, "crypto_currency": "BTC"},
                ):
                    refused = await http.post(ORDERS, json=payload, headers=same_origin())
                    assert refused.status_code == 400, refused.text
                    assert refused.json()["reason_code"] == "TMC_PAY_UNSUPPORTED_PAIR"
        finally:
            await kit.teardown()

    run(scenario())


def test_a_bare_currency_in_the_allowlist_permits_all_its_networks():
    async def scenario():
        kit = await kit_with(FakeGateway([]), TMC_PAY_PAYABLE_PAIRS="USDT").setup()
        try:
            async with buyer(kit) as (http, _):
                by_code = {
                    c["code"]: c for c in (await http.get(CURRENCIES)).json()["currencies"]
                }
                # The fixture's USDT entry is absent, so assert on what is there instead: an
                # allowlist naming only USDT leaves everything else off, TAO included.
                for code in ("TAO", "USDC", "BTC", "XMR"):
                    assert by_code[code]["payable"] is False, code
        finally:
            await kit.teardown()

    run(scenario())


def test_the_currency_list_needs_no_session():
    """Unauthenticated on purpose: a visitor weighs the payment options before signing up.

    It sits beside `credit-pricing`, which is already anonymous and already says this deployment
    takes TMC PAY, so there is nothing here a session would be protecting.
    """

    async def scenario():
        gateway = FakeGateway([])
        kit = await kit_with(gateway).setup()
        try:
            http = await client(kit)
            async with http:
                answer = await http.get(CURRENCIES)
                assert answer.status_code == 200, answer.text
                body = answer.json()
                assert body["default_currency"] == "TAO"
                assert {c["code"] for c in body["currencies"]} == {
                    "TAO",
                    "USDC",
                    "BTC",
                    "XMR",
                }
                # Identical for every caller, so a shared cache may serve one copy to everyone.
                assert "public" in answer.headers.get("cache-control", "")
        finally:
            await kit.teardown()

    run(scenario())


def test_an_unreachable_processor_makes_the_currency_list_a_503():
    async def scenario():
        gateway = FakeGateway([])
        gateway.error = tmc_pay.TmcPayUnavailable("down")
        kit = await kit_with(gateway).setup()
        try:
            async with buyer(kit) as (http, _):
                answer = await http.get(CURRENCIES)
                assert answer.status_code == 503
                assert answer.json()["reason_code"] == "TMC_PAY_UNAVAILABLE"
        finally:
            await kit.teardown()

    run(scenario())


def test_an_unusable_catalogue_is_a_503_rather_than_an_empty_menu():
    """An empty list would read as "no way to pay", which is a different and wrong statement."""

    async def scenario():
        gateway = FakeGateway([], currencies=[{"code": "", "networks": []}])
        kit = await kit_with(gateway).setup()
        try:
            async with buyer(kit) as (http, _):
                answer = await http.get(CURRENCIES)
                assert answer.status_code == 503
                assert answer.json()["reason_code"] == "TMC_PAY_REFUSED"
        finally:
            await kit.teardown()

    run(scenario())


def test_a_cancelled_invoice_is_recorded_as_terminal_and_credits_nothing():
    """`cancelled` is in TMC PAY's published enum, and reaches the stored enum unchanged.

    Before it was accepted, parsing refused the body, the webhook answered 4xx, and TMC PAY was
    left retrying a delivery that described an ordinary cancellation.
    """

    async def scenario():
        gateway = FakeGateway([invoice_body(invoice_id="gone", crypto_amount="0.5")])
        kit = await kit_with(gateway).setup()
        try:
            async with buyer(kit) as (http, _):
                order = await _order_for(http, credits_=1)

                raw, headers = signed(
                    invoice_body(invoice_id="gone", status="cancelled", crypto_amount="0.5")
                )
                assert (await http.post(WEBHOOK, content=raw, headers=headers)).status_code == 200

                body = (await http.get(f"{ORDERS}/{order['id']}")).json()
                assert body["status"] == "CANCELLED"
                assert body["credits_credited"] == 0
                # Nothing arrived, so there is nothing for an operator to settle.
                assert body["needs_review"] is False
                assert (await http.get("/v1/me/credits")).json()["credits_available"] == 0
        finally:
            await kit.teardown()

    run(scenario())


def test_a_late_payment_credits_nothing_unless_the_operator_opted_in():
    """TMC PAY sends no `late_payment` status, so lateness is read off the timestamps.

    A confirmation stamped after the invoice's own `expires_at` is the exceptional case: the
    stored state is still LATE_PAYMENT and the operator opt-in still decides whether it credits.
    """
    # Comfortably past the 30-minute `expires_at` that `invoice_body` builds.
    late_confirmation = _iso(dt.datetime.now(dt.UTC) + dt.timedelta(minutes=45))

    async def scenario():
        for opted_in, expected_credits in ((False, 0), (True, 1)):
            gateway = FakeGateway([invoice_body(invoice_id="late", crypto_amount="0.5")])
            kit = await kit_with(
                gateway, TMC_PAY_CREDIT_LATE_PAYMENTS="true" if opted_in else "false"
            ).setup()
            try:
                async with buyer(kit) as (http, _):
                    order = await _order_for(http, credits_=1)
                    raw, headers = signed(
                        invoice_body(
                            invoice_id="late",
                            status="confirmed",
                            crypto_amount="0.5",
                            confirmed_at=late_confirmation,
                        )
                    )
                    assert (
                        await http.post(WEBHOOK, content=raw, headers=headers)
                    ).status_code == 200

                    body = (await http.get(f"{ORDERS}/{order['id']}")).json()
                    assert body["status"] == "LATE_PAYMENT"
                    assert body["needs_review"] is True
                    assert body["credits_credited"] == expected_credits
                    assert (
                        await http.get("/v1/me/credits")
                    ).json()["credits_available"] == expected_credits
            finally:
                await kit.teardown()

    run(scenario())


def test_a_webhook_recovers_an_order_whose_invoice_id_never_arrived():
    """The lost-create-response path: matched on the `external_id` this side minted."""

    async def scenario():
        kit = await kit_with(FakeGateway([])).setup()
        try:
            async with buyer(kit) as (http, account):
                # The row a create would have written before calling out, and nothing more.
                async with kit.session() as session:
                    order = await order_store.create_order(
                        session,
                        account_id=uuid.UUID(account["id"]),
                        credits_requested=2,
                        credit_price_rao=CREDIT_PRICE_RAO,
                        external_id="credits-orphaned",
                    )
                    order_id = str(order.id)
                    await session.commit()

                raw, headers = signed(
                    invoice_body(
                        invoice_id="inv-recovered",
                        status="confirmed",
                        crypto_amount="1",
                        external_id="credits-orphaned",
                    )
                )
                assert (await http.post(WEBHOOK, content=raw, headers=headers)).status_code == 200

                body = (await http.get(f"{ORDERS}/{order_id}")).json()
                assert body["invoice_id"] == "inv-recovered"
                assert body["status"] == "CONFIRMED"
                assert body["credits_credited"] == 2
                # This row emulates legacy durable state created before the one-credit limit.
                assert (await http.get("/v1/me/credits")).json()["credits_available"] == 2
        finally:
            await kit.teardown()

    run(scenario())


# --- Reading and reconciling -------------------------------------------------------------


def test_reading_an_open_order_refreshes_it_from_the_processor():
    """The safety net for a webhook that never arrived: the buyer's own polling credits them."""

    async def scenario():
        gateway = FakeGateway(
            [invoice_body(invoice_id="inv-1", crypto_amount="0.5")],
            reads={
                "inv-1": invoice_body(
                    invoice_id="inv-1", status="confirmed", crypto_amount="0.5"
                )
            },
        )
        kit = await kit_with(gateway, TMC_PAY_POLL_SECONDS="0").setup()
        try:
            async with buyer(kit) as (http, _):
                order = await _order_for(http, credits_=1)
                assert (await http.get("/v1/me/credits")).json()["credits_available"] == 0

                polled = await http.get(f"{ORDERS}/{order['id']}")
                assert polled.json()["status"] == "CONFIRMED"
                assert polled.json()["credits_credited"] == 1
                assert gateway.read_calls == ["inv-1"]

                # Settled now, so a further read asks TMC PAY nothing.
                await http.get(f"{ORDERS}/{order['id']}")
                assert gateway.read_calls == ["inv-1"]
                assert (await http.get("/v1/me/credits")).json()["credits_available"] == 1
        finally:
            await kit.teardown()

    run(scenario())


def test_a_processor_outage_does_not_break_reading_an_order():
    async def scenario():
        gateway = FakeGateway([invoice_body(invoice_id="inv-1", crypto_amount="0.5")])
        kit = await kit_with(gateway, TMC_PAY_POLL_SECONDS="0").setup()
        try:
            async with buyer(kit) as (http, _):
                order = await _order_for(http, credits_=1)
                gateway.error = tmc_pay.TmcPayUnavailable("down")

                # The stored state is still true and still worth returning; a 503 here would
                # break a payment page over a refresh it did not ask for.
                served = await http.get(f"{ORDERS}/{order['id']}")
                assert served.status_code == 200
                assert served.json()["status"] == "CREATED"
        finally:
            await kit.teardown()

    run(scenario())


def test_the_poll_interval_bounds_outbound_reads():
    async def scenario():
        gateway = FakeGateway(
            [invoice_body(invoice_id="inv-1", crypto_amount="0.5")],
            reads={"inv-1": invoice_body(invoice_id="inv-1", status="pending", crypto_amount="0.5")},
        )
        kit = await kit_with(gateway, TMC_PAY_POLL_SECONDS="3600").setup()
        try:
            async with buyer(kit) as (http, _):
                order = await _order_for(http, credits_=1)
                for _ in range(3):
                    assert (await http.get(f"{ORDERS}/{order['id']}")).status_code == 200
                # The first read polls, the rest are served from stored state.
                assert gateway.read_calls == ["inv-1"]
        finally:
            await kit.teardown()

    run(scenario())


def test_another_account_cannot_see_or_poll_an_order():
    async def scenario():
        gateway = FakeGateway(
            [invoice_body(invoice_id="inv-1", crypto_amount="0.5")],
            reads={
                "inv-1": invoice_body(
                    invoice_id="inv-1", status="confirmed", crypto_amount="0.5"
                )
            },
        )
        kit = await kit_with(gateway, TMC_PAY_POLL_SECONDS="0").setup()
        try:
            async with (
                buyer(kit, "mine@example.com") as (mine, _),
                buyer(kit, "theirs@example.com") as (theirs, _),
            ):
                order = await _order_for(mine, credits_=1)

                # Absent rather than forbidden, so an id cannot be probed for existence.
                probed = await theirs.get(f"{ORDERS}/{order['id']}")
                assert probed.status_code == 404
                assert probed.json()["reason_code"] == "NOT_FOUND"
                assert gateway.read_calls == []
                assert (await theirs.get(ORDERS)).json()["items"] == []
        finally:
            await kit.teardown()

    run(scenario())


def test_expiring_lapsed_orders_leaves_anything_that_might_hold_money():
    async def scenario():
        kit = await harness().setup()
        try:
            # An account to own the orders, and nothing else: this test is about the store's
            # sweeper rather than about any endpoint.
            async with buyer(kit) as (_, account):
                account_id = uuid.UUID(account["id"])
            past = dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)

            async with kit.session() as session:
                kept = []
                for index, state in enumerate(
                    (
                        TmcPayOrderState.CREATED,
                        TmcPayOrderState.PENDING,
                        TmcPayOrderState.CONFIRMING,
                        TmcPayOrderState.UNDERPAID,
                    )
                ):
                    order = await order_store.create_order(
                        session,
                        account_id=account_id,
                        credits_requested=1,
                        credit_price_rao=CREDIT_PRICE_RAO,
                        external_id=f"credits-lapsed-{index}",
                    )
                    await order_store.attach_invoice(
                        session,
                        order,
                        invoice_id=f"inv-lapsed-{index}",
                        merchant_id=MERCHANT,
                        status=state,
                        fiat_amount="200.00",
                        fiat_currency="USD",
                        exchange_rate="0.0025",
                        commission_amount=None,
                        crypto_amount_rao=CREDIT_PRICE_RAO,
                        deposit_address=DEPOSIT_ADDRESS,
                        invoice_expires_at=past,
                    )
                    kept.append((order.id, state))
                await session.commit()

                closed = await order_store.expire_lapsed(
                    session, now=dt.datetime.now(dt.UTC)
                )
                await session.commit()
                # Only the CREATED one: TMC PAY saw no deposit for it at all. The other three
                # have real money behind them and must be resolved, never timed out.
                assert closed == 1
                for order_id, state in kept:
                    row = await order_store.get_order(session, order_id, account_id)
                    expected = (
                        TmcPayOrderState.EXPIRED
                        if state is TmcPayOrderState.CREATED
                        else state
                    )
                    assert row.status is expected
        finally:
            await kit.teardown()

    run(scenario())


def test_the_reconciler_credits_an_invoice_no_webhook_ever_reported():
    """The sweep that turns a lost webhook into a delay instead of a loss.

    TMC PAY dispatches once and never retries by itself, so this path is what stands between "the
    delivery failed during a deploy" and "the buyer paid and got nothing". Driven through the
    script's own `_pass`, not through a reimplementation of it, so the wiring is what is tested.
    """

    async def scenario():
        gateway = FakeGateway(
            [invoice_body(invoice_id="inv-1", crypto_amount="0.5")],
            reads={
                "inv-1": invoice_body(
                    invoice_id="inv-1",
                    status="confirmed",
                    crypto_amount="0.5",
                    confirmed_at=_iso(dt.datetime.now(dt.UTC)),
                )
            },
        )
        kit = await kit_with(gateway).setup()
        try:
            async with buyer(kit) as (http, _):
                order = await _order_for(http, credits_=1)
                # No webhook arrives at all.
                assert (await http.get("/v1/me/credits")).json()["credits_available"] == 0

                read, credited, failed = await reconciler._pass(
                    sessions=kit.services.sessions,
                    services=reconciler._Gateway(gateway),
                    settings=kit.settings,
                    batch=10,
                    # Zero, so the order just created is eligible in this same pass.
                    min_age=0.0,
                    dry_run=False,
                )
                assert (read, credited, failed) == (1, 1, 0)

                after = (await http.get(f"{ORDERS}/{order['id']}")).json()
                assert after["status"] == "CONFIRMED"
                assert after["credits_credited"] == 1
                assert (await http.get("/v1/me/credits")).json()["credits_available"] == 1

                # Nothing is left in the queue, so a second pass is a no-op rather than a
                # second credit.
                again = await reconciler._pass(
                    sessions=kit.services.sessions,
                    services=reconciler._Gateway(gateway),
                    settings=kit.settings,
                    batch=10,
                    min_age=0.0,
                    dry_run=False,
                )
                assert again == (0, 0, 0)
                assert (await http.get("/v1/me/credits")).json()["credits_available"] == 1
        finally:
            await kit.teardown()

    run(scenario())


def test_the_reconciler_writes_nothing_on_a_dry_run():
    async def scenario():
        gateway = FakeGateway(
            [invoice_body(invoice_id="inv-1", crypto_amount="0.5")],
            reads={
                "inv-1": invoice_body(
                    invoice_id="inv-1", status="confirmed", crypto_amount="0.5"
                )
            },
        )
        kit = await kit_with(gateway).setup()
        try:
            async with buyer(kit) as (http, _):
                await _order_for(http, credits_=1)
                read, credited, failed = await reconciler._pass(
                    sessions=kit.services.sessions,
                    services=reconciler._Gateway(gateway),
                    settings=kit.settings,
                    batch=10,
                    min_age=0.0,
                    dry_run=True,
                )
                assert (read, credited, failed) == (1, 0, 0)
                assert gateway.read_calls == []
                assert (await http.get("/v1/me/credits")).json()["credits_available"] == 0
        finally:
            await kit.teardown()

    run(scenario())


def test_the_quote_ceiling_is_the_configured_slippage_plus_one_minor_unit():
    """Two parts, different in kind: slippage is policy, the cent is arithmetic.

    At 0.0025 TAO per USD and two decimals, one cent is 25 000 rao. So a 0.5 TAO purchase at zero
    slippage may cost at most 500 025 000 rao — the cent, and nothing else.
    """
    assert (
        tmc_pay.quote_ceiling(
            CREDIT_PRICE_RAO, exchange_rate="0.0025", slippage_bps=0, decimals=2
        )
        == CREDIT_PRICE_RAO + 25_000
    )
    # Slippage widens it by exactly the basis points asked for, on top of the cent.
    assert (
        tmc_pay.quote_ceiling(
            CREDIT_PRICE_RAO, exchange_rate="0.0025", slippage_bps=100, decimals=2
        )
        == CREDIT_PRICE_RAO * 10_100 // 10_000 + 25_000
    )
    assert (
        tmc_pay.quote_ceiling(
            CREDIT_PRICE_RAO, exchange_rate="0.0025", slippage_bps=250, decimals=2
        )
        == CREDIT_PRICE_RAO * 10_250 // 10_000 + 25_000
    )
    # A zero-decimal currency's minor unit is a whole unit, so the arithmetic part is much larger.
    assert (
        tmc_pay.quote_ceiling(
            CREDIT_PRICE_RAO, exchange_rate="0.0025", slippage_bps=0, decimals=0
        )
        == CREDIT_PRICE_RAO + 2_500_000
    )
    # An unusable rate is not a pricing problem and must not be retried as one.
    for rate in ("", "abc", "0", "-1"):
        assert (
            tmc_pay.quote_ceiling(
                CREDIT_PRICE_RAO, exchange_rate=rate, slippage_bps=0, decimals=2
            )
            is None
        )


def test_slippage_below_the_quote_margin_is_refused_at_startup():
    """Every ask adds the margin, so a tighter tolerance describes a band nothing can land in —
    every purchase would burn its attempts and fail. Refused at boot, not clamped."""
    from submission_api.settings import Settings, SettingsError

    base = tmc_pay_settings(TMC_PAY_QUOTE_MARGIN_BPS="100")
    environ = {
        "APP_MODE": "DEV",
        "PAYMENT_RECIPIENT_SS58": "5C4hrfjw9DjXZTzV3MwzrrAr9P1MJhSrvWGWqi1eSuyUpnhM",
        "DEVELOPMENT_HOTKEYS": "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty",
        **base,
    }
    with pytest.raises(SettingsError, match="TMC_PAY_MAX_SLIPPAGE_BPS"):
        Settings.from_env({**environ, "TMC_PAY_MAX_SLIPPAGE_BPS": "50"})

    # Equal is fine: the margin is exactly what the tolerance has to cover.
    equal = Settings.from_env({**environ, "TMC_PAY_MAX_SLIPPAGE_BPS": "100"})
    assert equal.tmc_pay_max_slippage_bps == 100
    assert equal.tmc_pay_quote_margin_bps == 100
    # And the shipped defaults are a valid pair — which is worth asserting, because the two
    # constants are chosen independently and nothing else would catch them drifting apart.
    shipped = Settings.from_env(
        {**environ, "TMC_PAY_QUOTE_MARGIN_BPS": "", "TMC_PAY_MAX_SLIPPAGE_BPS": ""}
    )
    assert shipped.tmc_pay_quote_margin_bps == 25
    assert shipped.tmc_pay_max_slippage_bps == 100


def test_configured_slippage_decides_whether_an_overshoot_is_requoted():
    """The same invoice is accepted or thrown away depending only on the configured tolerance.

    0.51 TAO for a 0.5 TAO credit is a 2% overshoot. At 250 bps it is inside tolerance and costs
    one invoice; at 100 bps it is outside and is requoted.
    """

    async def scenario():
        for slippage, expected_invoices, expected_id in (("250", 1, "loose"), ("100", 2, "tight")):
            gateway = FakeGateway(
                [
                    invoice_body(invoice_id="loose", crypto_amount="0.51"),
                    invoice_body(invoice_id="tight", crypto_amount="0.5"),
                ]
            )
            kit = await kit_with(gateway, TMC_PAY_MAX_SLIPPAGE_BPS=slippage).setup()
            try:
                async with buyer(kit) as (http, _):
                    created = await http.post(
                        ORDERS, json={"credits": 1}, headers=same_origin(http)
                    )
                    assert created.status_code == 201, created.text
                    assert created.json()["order"]["invoice_id"] == expected_id, slippage
                    assert len(gateway.created) == expected_invoices, slippage
            finally:
                await kit.teardown()

    run(scenario())


def test_an_invoice_that_would_overcharge_the_buyer_is_requoted():
    """A stale or wrong-currency rate estimate must not become an invoice the buyer overpays.

    The first invoice locks 0.6 TAO for one 0.5 TAO credit — 20% too much, which clears the floor
    and would have been accepted before the ceiling existed. The retry prices from the rate that
    invoice reported and lands on the honest amount.
    """

    async def scenario():
        gateway = FakeGateway(
            [
                invoice_body(invoice_id="rich", crypto_amount="0.6"),
                invoice_body(invoice_id="right", crypto_amount="0.5"),
            ]
        )
        kit = await kit_with(gateway).setup()
        try:
            async with buyer(kit) as (http, _):
                created = await http.post(ORDERS, json={"credits": 1}, headers=same_origin(http))
                assert created.status_code == 201, created.text
                order = created.json()["order"]
                assert order["invoice_id"] == "right"
                assert order["amount_rao"] == CREDIT_PRICE_RAO

                assert len(gateway.created) == 2
                assert gateway.created[1]["fiat_amount"] == "200.00"
        finally:
            await kit.teardown()

    run(scenario())


def test_an_overshoot_within_the_band_is_accepted_without_a_second_invoice():
    """The band has to be wide enough for what the quote legitimately adds, or every purchase
    would cost two invoices. One cent of rounding at this rate is 25 000 rao, and that is fine."""

    async def scenario():
        gateway = FakeGateway(
            [invoice_body(invoice_id="rounded", crypto_amount="0.500025")]
        )
        kit = await kit_with(gateway).setup()
        try:
            async with buyer(kit) as (http, _):
                created = await http.post(ORDERS, json={"credits": 1}, headers=same_origin(http))
                assert created.status_code == 201, created.text
                assert created.json()["order"]["invoice_id"] == "rounded"
                assert created.json()["order"]["amount_rao"] == CREDIT_PRICE_RAO + 25_000
                assert len(gateway.created) == 1
        finally:
            await kit.teardown()

    run(scenario())


def test_a_purchase_is_refused_when_every_quote_overcharges():
    async def scenario():
        gateway = FakeGateway(
            [
                invoice_body(invoice_id="rich-1", crypto_amount="0.9"),
                invoice_body(invoice_id="rich-2", crypto_amount="0.9"),
            ]
        )
        kit = await kit_with(gateway).setup()
        try:
            async with buyer(kit) as (http, _):
                refused = await http.post(ORDERS, json={"credits": 1}, headers=same_origin(http))
                assert refused.status_code == 503
                assert refused.json()["reason_code"] == "TMC_PAY_QUOTE_FAILED"

                listed = (await http.get(ORDERS)).json()["items"]
                assert listed[0]["status"] == "FAILED"
                assert "above the" in listed[0]["failure_reason"]
        finally:
            await kit.teardown()

    run(scenario())


def test_the_next_quote_is_seeded_from_the_rate_tmc_pay_itself_locked():
    """Our own invoices are a better rate source than a third-party feed.

    TMC PAY reports the `exchange_rate` it used on every invoice, and that is the same source that
    will price the next one — spread, rounding and all — already in the merchant's currency. So the
    second purchase must price from it and not from TaoStats.

    The two are deliberately far apart here: TaoStats says $400/TAO (0.0025 TAO per USD) while the
    invoice locked 0.005 TAO per USD ($200/TAO). One credit is therefore $200 if seeded externally
    and $100 if seeded from the invoice, so the fiat amount alone says which was used.
    """

    async def scenario():
        gateway = FakeGateway(
            [
                invoice_body(invoice_id="first", crypto_amount="0.5", exchange_rate="0.005"),
                invoice_body(invoice_id="second", crypto_amount="0.5", exchange_rate="0.005"),
            ]
        )
        kit = await kit_with(gateway).setup()
        try:
            async with buyer(kit) as (http, _):
                first = await http.post(ORDERS, json={"credits": 1}, headers=same_origin(http))
                assert first.status_code == 201, first.text
                # Nothing local to go on yet, so the external feed priced it.
                assert gateway.created[0]["fiat_amount"] == "200.00"

                second = await http.post(ORDERS, json={"credits": 1}, headers=same_origin(http))
                assert second.status_code == 201, second.text
                # Seeded from the first invoice's own locked rate, not from TaoStats.
                assert gateway.created[1]["fiat_amount"] == "100.00"
                assert len(gateway.created) == 2
        finally:
            await kit.teardown()

    run(scenario())


def test_a_zero_rate_ttl_always_asks_the_external_feed():
    """The honest way to say "never reuse a locked rate"."""

    async def scenario():
        gateway = FakeGateway(
            [
                invoice_body(invoice_id="first", crypto_amount="0.5", exchange_rate="0.005"),
                invoice_body(invoice_id="second", crypto_amount="0.5", exchange_rate="0.005"),
            ]
        )
        kit = await kit_with(gateway, TMC_PAY_RATE_TTL_SECONDS="0").setup()
        try:
            async with buyer(kit) as (http, _):
                for _ in range(2):
                    accepted = await http.post(
                        ORDERS, json={"credits": 1}, headers=same_origin(http)
                    )
                    assert accepted.status_code == 201, accepted.text
                assert [item["fiat_amount"] for item in gateway.created] == [
                    "200.00",
                    "200.00",
                ]
        finally:
            await kit.teardown()

    run(scenario())


def test_a_locked_rate_keeps_credits_on_sale_through_a_taostats_outage():
    """A stale local rate beats refusing the sale, because the band cannot be fooled by a bad seed.

    No external feed at all here — only a rate observed on an earlier invoice.
    """

    async def scenario():
        gateway = FakeGateway(
            [invoice_body(invoice_id="fresh", crypto_amount="0.5", exchange_rate="0.0025")]
        )
        kit = await kit_with(gateway, tao_usd=None).setup()
        try:
            async with buyer(kit) as (http, account):
                # An earlier purchase that recorded a rate, and nothing else.
                async with kit.session() as session:
                    earlier = await order_store.create_order(
                        session,
                        account_id=uuid.UUID(account["id"]),
                        credits_requested=1,
                        credit_price_rao=CREDIT_PRICE_RAO,
                        external_id="credits-earlier",
                    )
                    await order_store.attach_invoice(
                        session,
                        earlier,
                        invoice_id="inv-earlier",
                        merchant_id=MERCHANT,
                        status=TmcPayOrderState.EXPIRED,
                        fiat_amount="200.00",
                        fiat_currency="USD",
                        exchange_rate="0.0025",
                        commission_amount=None,
                        crypto_amount_rao=CREDIT_PRICE_RAO,
                        deposit_address=DEPOSIT_ADDRESS,
                        invoice_expires_at=dt.datetime.now(dt.UTC),
                    )
                    await session.commit()

                sold = await http.post(ORDERS, json={"credits": 1}, headers=same_origin(http))
                assert sold.status_code == 201, sold.text
                assert gateway.created[0]["fiat_amount"] == "200.00"
        finally:
            await kit.teardown()

    run(scenario())


def test_the_webhook_is_reachable_from_outside_the_browser_security_model():
    """TMC PAY is a server, not a browser, and the middleware stack must not stand in its way.

    Four things could plausibly block it, so all four are asserted against the *full* stack with
    CORS configured and the rate limiter on:

    * **CORS** — irrelevant to a server-to-server caller, and `ScopedCORSMiddleware` delegates to
      Starlette's implementation, which only *adds* response headers on a non-preflight request. A
      disallowed `Origin` withholds the grant from a browser; it does not refuse the request.
    * **The CSRF middleware** — exempt by path in `app.py`. It would pass anyway, since the
      `Origin` check fails open on absence, but the exemption makes that a decision rather than an
      accident.
    * **Authentication** — there is none to fail: the route names no principal dependency, and the
      HMAC over the raw body is the credential.
    * **The rate limiter** — applies, deliberately, and is the one thing an operator has to size.

    The final assertion is the important one: the exemption must not have leaked to the rest of
    `/v1`, so an account write from the same hostile origin is still refused.
    """

    async def scenario():
        gateway = FakeGateway([invoice_body(invoice_id="inv-1", crypto_amount="0.5")])
        kit = await kit_with(
            gateway,
            CORS_ALLOWED_ORIGINS="https://conjectures.io",
            RATE_LIMIT_ENABLED="true",
        ).setup()
        try:
            async with buyer(kit) as (http, _):
                await _order_for(http, credits_=1)

            # A bare server-to-server POST: no Origin, no cookie, no session. This is exactly the
            # shape TMC PAY sends.
            async with await client(kit) as outside:
                raw, headers = signed(
                    invoice_body(
                        invoice_id="inv-1", status="confirmed", crypto_amount="0.5"
                    ),
                    webhook_id="from-outside",
                )
                delivered = await outside.post(WEBHOOK, content=raw, headers=headers)
                assert delivered.status_code == 200, delivered.text
                assert delivered.json()["status"] == "credited"

                # The same call from a hostile Origin still lands: CORS is a browser mechanism and
                # cannot be relied on to refuse anything server-side.
                raw, headers = signed(
                    invoice_body(
                        invoice_id="inv-1", status="confirmed", crypto_amount="0.5"
                    ),
                    webhook_id="hostile-origin",
                )
                headers["Origin"] = "https://evil.example"
                spoofed = await outside.post(WEBHOOK, content=raw, headers=headers)
                # Reaches the handler, and is idempotent rather than a second credit — the HMAC and
                # the ledger are what protect this endpoint, not the origin.
                assert spoofed.status_code == 200, spoofed.text
                assert (
                    await outside.get("/v1/me/credits", headers={"Cookie": ""})
                ).status_code == 401

            # And the exemption did not leak: an account write from that origin is still refused.
            async with buyer(kit) as (http, _):
                blocked = await http.post(
                    ORDERS,
                    json={"credits": 1},
                    headers={**same_origin(http), "Origin": "https://evil.example"},
                )
                assert blocked.status_code == 403
                assert blocked.json()["reason_code"] == "CROSS_SITE_WRITE_REFUSED"
        finally:
            await kit.teardown()

    run(scenario())
