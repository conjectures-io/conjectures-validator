"""What `TmcPayClient` actually puts on the wire.

The rest of the TMC PAY suite drives the router through a fake `InvoiceGateway`, which is the
right shape for testing purchase and webhook behavior but leaves the real HTTP client — the only
part TMC PAY itself ever sees — completely uncovered. That gap shipped two defects: the
credential in an `Authorization: Bearer` header, which TMC PAY rejects with a 422 naming the
missing `X-API-Key`, and a single-invoice URL with a doubled separator.

So these tests assert the request, not the response handling: header name, method, and URL.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json

import pytest

pytest.importorskip("httpx", reason="the TMC PAY client needs the service extra")

import httpx

from submission_api import tmc_pay


def run(coroutine):
    return asyncio.run(coroutine)

BASE_URL = "https://pay-api.example.com"
API_KEY = "tmc-test-key"

INVOICE_BODY = {
    "id": "1f0c6a2e-0000-4000-8000-000000000001",
    "merchant_id": "merchant-1",
    "status": tmc_pay.STATUS_CREATED,
    "external_id": "order-1",
    "fiat_amount": "49.99",
    "fiat_currency": "usd",
    "crypto_amount": "1.250000000",
    "crypto_currency": "tao",
    "crypto_network": "Bittensor",
    "deposit_address": "5C4hrfjw9DjXZTzV3MwzrrAr9P1MJhSrvWGWqi1eSuyUpnhM",
    "exchange_rate": "39.99",
    "hosted_invoice_url": "https://pay.example.com/invoice/9f3c1a2e-hosted-token",
}


class Upstream:
    """A stand-in TMC PAY that records every request it is sent."""

    def __init__(self, *, status: int = 200, body: object = INVOICE_BODY) -> None:
        self.requests: list[httpx.Request] = []
        self.status = status
        self.body = body

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, text=json.dumps(self.body))


def gateway(upstream: Upstream) -> tmc_pay.TmcPayClient:
    return tmc_pay.TmcPayClient(
        base_url=BASE_URL, api_key=API_KEY, client=upstream.client()
    )


async def create(client: tmc_pay.TmcPayClient) -> tmc_pay.Invoice:
    return await client.create_invoice(
        fiat_amount="49.99",
        fiat_currency="USD",
        external_id="order-1",
        description="1 verification credit(s) for conjectures.io",
        metadata={"order_id": "order-1"},
        ttl_minutes=30,
    )


# --- The credential --------------------------------------------------------------------------


def test_create_sends_the_api_key_header() -> None:
    """`X-API-Key`, which is what TMC PAY declares as required on the invoice endpoints."""
    upstream = Upstream()
    run(create(gateway(upstream)))

    headers = upstream.requests[0].headers
    assert headers["X-API-Key"] == API_KEY
    # A bearer token is not merely redundant here: it leaves `X-API-Key` unset, and TMC PAY
    # answers 422 rather than 401, which reads like a malformed body instead of a missing key.
    assert "authorization" not in headers


def test_read_sends_the_api_key_header() -> None:
    """The polling path carries the credential too; it is a merchant-scoped read."""
    upstream = Upstream()
    run(gateway(upstream).read_invoice(INVOICE_BODY["id"]))

    assert upstream.requests[0].headers["X-API-Key"] == API_KEY


def test_the_key_never_reaches_the_url() -> None:
    """A credential in a query string lands in access logs at both ends."""
    upstream = Upstream()
    run(create(gateway(upstream)))

    assert API_KEY not in str(upstream.requests[0].url)


# --- The URLs --------------------------------------------------------------------------------


def test_create_posts_to_the_invoices_collection() -> None:
    upstream = Upstream()
    run(create(gateway(upstream)))

    request = upstream.requests[0]
    assert request.method == "POST"
    assert str(request.url) == f"{BASE_URL}{tmc_pay.INVOICES_PATH}"


def test_read_gets_one_invoice_without_a_doubled_separator() -> None:
    """`/api/v1/invoices//<id>` is a different path, and not one TMC PAY routes."""
    upstream = Upstream()
    invoice_id = INVOICE_BODY["id"]
    run(gateway(upstream).read_invoice(invoice_id))

    request = upstream.requests[0]
    assert request.method == "GET"
    assert str(request.url) == f"{BASE_URL}/api/v1/invoices/{invoice_id}"
    assert "//" not in str(request.url).removeprefix("https://")


def test_invoice_path_survives_a_base_without_a_trailing_slash() -> None:
    """The helper owns the separator, so editing `INVOICES_PATH` cannot double it."""
    assert tmc_pay.invoice_path("abc") == "/api/v1/invoices/abc"


# --- The body the collection is asked for ----------------------------------------------------


def test_create_asks_for_tao_on_bittensor_with_the_order_as_idempotency_key() -> None:
    upstream = Upstream()
    run(create(gateway(upstream)))

    body = json.loads(upstream.requests[0].content)
    assert body["crypto_currency"] == tmc_pay.CRYPTO_CURRENCY
    assert body["crypto_network"] == tmc_pay.CRYPTO_NETWORK
    assert body["external_id"] == "order-1"
    assert body["ttl_minutes"] == 30


# --- What a refusal is classified as ---------------------------------------------------------


def test_a_422_is_a_rejection_carrying_the_upstream_detail() -> None:
    """The 422 that started this: the message must name the field, or nobody can debug it."""
    detail = {"detail": [{"type": "missing", "loc": ["header", "X-API-Key"]}]}
    upstream = Upstream(status=422, body=detail)

    with pytest.raises(tmc_pay.TmcPayRejected, match="422") as caught:
        run(create(gateway(upstream)))
    assert "X-API-Key" in str(caught.value)


def test_a_502_is_unavailable_rather_than_a_rejection() -> None:
    """Retryable, and distinct from a refusal: the router maps the two to different reasons."""
    upstream = Upstream(status=502, body={"detail": "bad gateway"})

    with pytest.raises(tmc_pay.TmcPayUnavailable):
        run(create(gateway(upstream)))


def test_an_unreachable_processor_does_not_leak_the_key_into_the_message() -> None:
    class Dead(Upstream):
        def _handle(self, request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            raise httpx.ConnectError("no route", request=request)

    upstream = Dead()
    with pytest.raises(tmc_pay.TmcPayUnavailable) as caught:
        run(create(gateway(upstream)))
    assert API_KEY not in str(caught.value)


# --- The field names the live schema actually publishes ---------------------------------------


def test_the_live_id_field_is_read() -> None:
    """`InvoiceResponse` names it `id`; requiring `invoice_id` rejected every real response."""
    upstream = Upstream()
    invoice = run(create(gateway(upstream)))

    assert invoice.invoice_id == INVOICE_BODY["id"]


def test_the_documented_invoice_id_field_is_still_accepted() -> None:
    """The webhook body is untyped in the published schema, so both spellings must parse."""
    body = {k: v for k, v in INVOICE_BODY.items() if k != "id"}
    body["invoice_id"] = "legacy-shaped-id"

    invoice = run(create(gateway(Upstream(body=body))))
    assert invoice.invoice_id == "legacy-shaped-id"


def test_the_live_id_wins_when_a_body_carries_both() -> None:
    body = dict(INVOICE_BODY, invoice_id="stale")
    assert run(create(gateway(Upstream(body=body)))).invoice_id == INVOICE_BODY["id"]


def test_a_body_with_neither_id_is_refused() -> None:
    body = {k: v for k, v in INVOICE_BODY.items() if k != "id"}
    with pytest.raises(tmc_pay.TmcPayRejected, match="id"):
        run(create(gateway(Upstream(body=body))))


def test_metadata_is_read_under_either_name() -> None:
    """Live responses use `metadata_json`; the older documentation used `metadata`."""
    live = run(create(gateway(Upstream(body=dict(INVOICE_BODY, metadata_json={"a": "1"})))))
    assert live.metadata == {"a": "1"}

    legacy = run(create(gateway(Upstream(body=dict(INVOICE_BODY, metadata={"b": "2"})))))
    assert legacy.metadata == {"b": "2"}

    assert run(create(gateway(Upstream()))).metadata is None


# --- The status vocabulary -------------------------------------------------------------------


def test_cancelled_parses_as_a_terminal_status() -> None:
    """It is in TMC PAY's published enum; refusing it made a cancellation a retried webhook."""
    invoice = run(create(gateway(Upstream(body=dict(INVOICE_BODY, status="cancelled")))))

    assert invoice.status == tmc_pay.STATUS_CANCELLED
    assert invoice.status in tmc_pay.SETTLED_STATUSES
    assert invoice.status not in tmc_pay.PAID_STATUSES


def test_late_payment_is_no_longer_an_accepted_status() -> None:
    """It is absent from the live enum, so a body claiming it is not something to trust."""
    assert "late_payment" not in tmc_pay.INVOICE_STATUSES
    with pytest.raises(tmc_pay.TmcPayRejected, match="unknown invoice status"):
        run(create(gateway(Upstream(body=dict(INVOICE_BODY, status="late_payment")))))


def test_every_published_status_parses() -> None:
    """The live `InvoiceStatus` enum, verbatim. A new label upstream should fail here first."""
    published = (
        "created",
        "pending",
        "confirming",
        "confirmed",
        "expired",
        "cancelled",
        "overpaid",
        "underpaid",
    )
    assert set(published) == set(tmc_pay.INVOICE_STATUSES)
    for status in published:
        body = dict(INVOICE_BODY, status=status)
        assert run(create(gateway(Upstream(body=body)))).status == status


# --- Lateness, derived from the timestamps ---------------------------------------------------


def invoice_with(*, status: str, confirmed_offset_minutes: int | None) -> tmc_pay.Invoice:
    """One parsed invoice expiring 30 minutes from now, confirmed at the given offset."""
    now = dt.datetime.now(dt.UTC)
    body = dict(
        INVOICE_BODY,
        status=status,
        expires_at=(now + dt.timedelta(minutes=30)).isoformat(),
        confirmed_at=(
            None
            if confirmed_offset_minutes is None
            else (now + dt.timedelta(minutes=confirmed_offset_minutes)).isoformat()
        ),
    )
    return run(create(gateway(Upstream(body=body))))


def test_a_confirmation_after_expiry_is_late() -> None:
    assert tmc_pay.payment_was_late(
        invoice_with(status="confirmed", confirmed_offset_minutes=45)
    )


def test_a_punctual_confirmation_is_not_late() -> None:
    """The case that must not regress: an ordinary payment stays automatic."""
    assert not tmc_pay.payment_was_late(
        invoice_with(status="confirmed", confirmed_offset_minutes=10)
    )


def test_a_confirmation_without_a_timestamp_is_not_late() -> None:
    """A missing timestamp is not evidence. Guessing here would hold ordinary payments."""
    assert not tmc_pay.payment_was_late(
        invoice_with(status="confirmed", confirmed_offset_minutes=None)
    )


def test_an_unpaid_status_is_never_late() -> None:
    """`expired` is an ordinary abandonment, not the exceptional case this guards."""
    assert not tmc_pay.payment_was_late(
        invoice_with(status="expired", confirmed_offset_minutes=45)
    )


def test_an_overpayment_can_also_be_late() -> None:
    assert tmc_pay.payment_was_late(
        invoice_with(status="overpaid", confirmed_offset_minutes=45)
    )


# --- The hosted payment page ------------------------------------------------------------------
# The URL the buyer is redirected to. Taken from TMC PAY rather than constructed, because their
# public invoice route is keyed by an opaque `hosted_token` and not by the invoice id.


def test_the_hosted_payment_url_is_read() -> None:
    invoice = run(create(gateway(Upstream())))
    assert invoice.hosted_invoice_url == INVOICE_BODY["hosted_invoice_url"]


def test_an_absent_hosted_url_is_not_an_error() -> None:
    """A webhook body need not carry one, and a purchase must not fail over a convenience."""
    body = {k: v for k, v in INVOICE_BODY.items() if k != "hosted_invoice_url"}
    invoice = run(create(gateway(Upstream(body=body))))
    assert invoice.hosted_invoice_url is None
    # Everything else still parsed, so the order is recorded and payable.
    assert invoice.invoice_id == INVOICE_BODY["id"]


def test_a_non_http_hosted_url_is_dropped_rather_than_trusted() -> None:
    """This value becomes a browser navigation target, so only http(s) may survive parsing."""
    for hostile in (
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        "data:text/html;base64,PHNjcmlwdD4=",
        "  javascript:alert(1)",
        "file:///etc/passwd",
        "//pay.example.com/invoice/x",
        "",
        "   ",
        "x" * (tmc_pay.MAX_HOSTED_URL_LENGTH + 1),
    ):
        body = dict(INVOICE_BODY, hosted_invoice_url=hostile)
        invoice = run(create(gateway(Upstream(body=body))))
        assert invoice.hosted_invoice_url is None, hostile
        # Dropped, never fatal: the rest of the invoice is still usable.
        assert invoice.invoice_id == INVOICE_BODY["id"]


def test_a_non_string_hosted_url_is_dropped() -> None:
    for hostile in (12345, {"url": "https://x"}, ["https://x"], True):
        body = dict(INVOICE_BODY, hosted_invoice_url=hostile)
        assert run(create(gateway(Upstream(body=body)))).hosted_invoice_url is None, hostile


def test_an_http_hosted_url_is_accepted() -> None:
    """Permitted for a development processor; production URLs are https in practice."""
    body = dict(INVOICE_BODY, hosted_invoice_url="http://localhost:8080/invoice/tok")
    invoice = run(create(gateway(Upstream(body=body))))
    assert invoice.hosted_invoice_url == "http://localhost:8080/invoice/tok"


# --- Paying in something other than TAO -------------------------------------------------------


def test_the_requested_pair_is_what_is_sent() -> None:
    upstream = Upstream(body=dict(INVOICE_BODY, crypto_currency="usdc", crypto_network="Base"))
    run(
        gateway(upstream).create_invoice(
            fiat_amount="200.00",
            fiat_currency="USD",
            external_id="order-1",
            description="d",
            metadata={},
            ttl_minutes=30,
            crypto_currency="USDC",
            crypto_network="base",
        )
    )
    body = json.loads(upstream.requests[0].content)
    assert body["crypto_currency"] == "USDC"
    assert body["crypto_network"] == "base"


def test_the_default_pair_is_tao_on_bittensor() -> None:
    upstream = Upstream()
    run(create(gateway(upstream)))
    body = json.loads(upstream.requests[0].content)
    assert body["crypto_currency"] == tmc_pay.CRYPTO_CURRENCY
    assert body["crypto_network"] == tmc_pay.CRYPTO_NETWORK


def test_rao_is_only_derived_for_a_tao_invoice() -> None:
    """A BTC amount run through `rao_from_tao` would be a rao figure that means nothing."""
    tao = run(create(gateway(Upstream())))
    assert tao.crypto_amount_rao == 1_250_000_000  # 1.25 TAO

    for currency, amount in (
        ("BTC", "0.00311"),
        ("USDC", "200.00"),
        # Eighteen decimals: `rao_from_tao` would refuse this outright rather than rescale it.
        ("ETH", "0.104382919283746152"),
    ):
        body = dict(INVOICE_BODY, crypto_currency=currency, crypto_amount=amount)
        invoice = run(create(gateway(Upstream(body=body))))
        assert invoice.crypto_amount_rao is None, currency
        # The amount itself is kept verbatim, because it is what the buyer must send.
        assert invoice.crypto_amount == amount
        assert invoice.crypto_currency == currency


def test_what_a_purchase_is_worth_in_rao() -> None:
    """TAO credits what arrived; anything else credits the price the fiat figure came from."""
    required = 500_000_000

    tao = run(create(gateway(Upstream())))
    assert tmc_pay.credited_rao(tao, required_rao=required) == tao.crypto_amount_rao

    usdc = run(
        create(gateway(Upstream(body=dict(INVOICE_BODY, crypto_currency="USDC"))))
    )
    assert tmc_pay.credited_rao(usdc, required_rao=required) == required


def test_the_catalogue_is_fetched_without_the_merchant_key() -> None:
    catalogue = [
        {
            "code": "TAO",
            "networks": ["bittensor"],
            "network_metadata": [
                {"network": "bittensor", "decimals": 9, "display_decimals": 4}
            ],
            "decimals": 9,
            "display_decimals": 4,
        }
    ]
    upstream = Upstream(body=catalogue)
    currencies = run(gateway(upstream).list_currencies())

    assert [c.code for c in currencies] == ["TAO"]
    request = upstream.requests[0]
    assert request.url.path == tmc_pay.CURRENCIES_PATH
    assert "x-api-key" not in request.headers
    assert "authorization" not in request.headers
