"""TMC PAY: buying credits with TAO through a payment processor.

A second funding path beside the direct treasury transfer in `credits.py`. The account still
pays TAO and still gets one credit per `CREDIT_PRICE_RAO`, but the transfer goes to an invoice
address TMC PAY derives and settles to the treasury later, in batches, net of commission.

**This path is processor-trusted, and that is a real difference from the rest of the codebase.**
Everywhere else, credits exist only for a transfer this validator read off finalized Subtensor
state itself — `payments.py` and `deposit_watcher/` both do exactly that, and `SECURITY.md`
requires it. Here the deposit address belongs to TMC PAY, not to us, so there is nothing of ours
on chain to read at the moment of purchase: the evidence is a signed webhook plus a
re-readable invoice. Consequences, stated rather than discovered later:

* The ledger entry a TMC PAY purchase writes names `tmc_pay_order_id`, never `deposit_id`. An
  operator auditing the ledger can therefore separate chain-confirmed rao from processor-
  confirmed rao with a WHERE clause, and the on-chain deposit invariants in
  `V003__accounts_credits_intents.sql` stay exactly as strict as they were.
* Nothing here credits an amount a caller supplied. What is credited is `crypto_amount_rao`,
  the TAO the *invoice* locked at creation time, which is also the amount TMC PAY requires
  before it will report `confirmed`. A webhook body is never read for an amount.
* An invoice is quoted in fiat, so the TAO figure is a *consequence* of TMC PAY's locked
  exchange rate rather than something we can ask for. `quote_fiat_amount` sizes the fiat
  request so the locked TAO lands at or above the price of the credits being bought, and
  `create_order` re-checks the invoice it got back rather than assuming it did.

Money rules, same as everywhere else in this repository: integer rao for anything that will be
credited, `Decimal` for the fiat quote, and no `float` in either. TMC PAY publishes amounts as
decimal strings for the same reason, and they stay strings until they become integer rao.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from collections.abc import Mapping
from typing import Any, Protocol

import httpx

from submission_api.settings import RAO_PER_TAO

logger = logging.getLogger("submission_api.tmc_pay")

# The invoice endpoints, relative to the configured API base URL. Paths rather than full URLs
# because the base is per-deployment: the published documentation quotes `api.example.com`, so
# the real host is something an operator is told and this module must not guess.
INVOICES_PATH = "/api/v1/invoices/"

# The merchant credential goes in this header, verbatim, on both invoice endpoints.
API_KEY_HEADER = "X-API-Key"


def invoice_path(invoice_id: str) -> str:
    """The single-invoice path, whether or not `INVOICES_PATH` carries a trailing separator."""
    return f"{INVOICES_PATH.rstrip('/')}/{invoice_id}"


# The crypto side is fixed. TMC PAY supports BTC, ETH, USDT and TAO; this integration buys
# credits, credits are priced in rao, and accepting anything else would mean holding a currency
# the credit price is not denominated in.
CRYPTO_CURRENCY = "TAO"
CRYPTO_NETWORK = "bittensor"

# Invoice statuses, verbatim from TMC PAY's `InvoiceStatus` enum. Non-terminal ones may still
# move; the terminal ones will not, except that UNDERPAID is documented as non-terminal because a
# buyer can top up on most chains.
#
# There is deliberately no `late_payment` here. Earlier documentation described one, but the
# live schema does not publish it, so accepting it would mean reserving a label TMC PAY cannot
# send. A payment that lands after the TTL arrives as `confirmed` with a `confirmed_at` past
# `expires_at` instead — see `payment_was_late`, which is what now drives the manual-review
# path that status used to.
STATUS_CREATED = "created"
STATUS_PENDING = "pending"
STATUS_CONFIRMING = "confirming"
STATUS_UNDERPAID = "underpaid"
STATUS_CONFIRMED = "confirmed"
STATUS_OVERPAID = "overpaid"
STATUS_EXPIRED = "expired"
STATUS_CANCELLED = "cancelled"

INVOICE_STATUSES = (
    STATUS_CREATED,
    STATUS_PENDING,
    STATUS_CONFIRMING,
    STATUS_UNDERPAID,
    STATUS_CONFIRMED,
    STATUS_OVERPAID,
    STATUS_EXPIRED,
    STATUS_CANCELLED,
)

# Statuses in which TMC PAY has confirmed that the invoice's locked amount arrived. These are the
# only two that may cause credits to exist.
PAID_STATUSES = frozenset({STATUS_CONFIRMED, STATUS_OVERPAID})

# Statuses from which nothing further will happen, so a poller can stop asking. UNDERPAID is
# excluded on purpose: the buyer may still top up.
SETTLED_STATUSES = frozenset(
    {STATUS_CONFIRMED, STATUS_OVERPAID, STATUS_EXPIRED, STATUS_CANCELLED}
)

# Webhook delivery headers, verbatim.
WEBHOOK_ID_HEADER = "X-Webhook-ID"
WEBHOOK_TIMESTAMP_HEADER = "X-Webhook-Timestamp"
WEBHOOK_SIGNATURE_HEADER = "X-Webhook-Signature"
WEBHOOK_EVENT_HEADER = "X-Webhook-Event"

SIGNATURE_PREFIX = "sha256="

# A webhook body is a small JSON object. Bounded before it is read, because the endpoint is
# unauthenticated until the signature is checked and the signature cannot be checked without
# buffering the body first.
MAX_WEBHOOK_BYTES = 64 * 1024

# What an invoice response may weigh. Same reasoning in the other direction: a processor that
# started streaming would otherwise be able to exhaust this process's memory.
MAX_RESPONSE_BYTES = 256 * 1024


class TmcPayError(RuntimeError):
    """Any failure to get a usable answer out of TMC PAY."""


class TmcPayUnavailable(TmcPayError):
    """TMC PAY could not be reached, or answered with a server error.

    Retryable, and the caller must translate it into a 503 rather than a refusal: the buyer did
    nothing wrong and there is no invoice either way.
    """


class TmcPayRejected(TmcPayError):
    """TMC PAY answered, and the answer is not one this integration can use.

    A 4xx, or a 2xx whose body does not describe an invoice denominated in TAO on Bittensor for
    at least the amount asked for. Not retryable without changing something.
    """


@dataclass(frozen=True)
class Invoice:
    """One TMC PAY invoice, as much of it as this integration relies on.

    `crypto_amount_rao` is derived from the `crypto_amount` string at parse time and is the only
    amount anything downstream is allowed to credit. `fiat_amount` and `exchange_rate` stay
    strings: they are recorded for the account's receipt and for an operator reconciling against
    the dashboard, and neither is arithmetic input again.
    """

    invoice_id: str
    merchant_id: str
    status: str
    external_id: str | None
    fiat_amount: str
    fiat_currency: str
    crypto_amount: str
    crypto_amount_rao: int
    crypto_currency: str
    crypto_network: str
    deposit_address: str
    exchange_rate: str
    commission_amount: str | None
    created_at: dt.datetime | None
    expires_at: dt.datetime | None
    confirmed_at: dt.datetime | None
    metadata: Mapping[str, Any] | None

    @property
    def is_paid(self) -> bool:
        return self.status in PAID_STATUSES

    @property
    def is_settled(self) -> bool:
        return self.status in SETTLED_STATUSES


# --- Amounts ---------------------------------------------------------------------------------


def rao_from_tao(amount: str) -> int:
    """Integer rao from a decimal TAO string, exactly or not at all.

    TAO carries nine decimal places, so every legitimate amount is an integer number of rao. A
    value with a tenth decimal place is not a TAO amount this validator can credit without
    rounding, and rounding somebody's money is not a decision this function is allowed to take —
    it refuses instead.
    """
    try:
        value = Decimal(amount.strip())
    except (InvalidOperation, AttributeError) as exc:
        raise TmcPayRejected(f"invoice amount is not a decimal number: {amount!r}") from exc
    if not value.is_finite() or value <= 0:
        raise TmcPayRejected(f"invoice amount is not finite and positive: {amount!r}")
    scaled = value * RAO_PER_TAO
    if scaled != scaled.to_integral_value():
        raise TmcPayRejected(
            f"invoice amount {amount!r} is finer than one rao and cannot be credited exactly"
        )
    return int(scaled)


def tao_from_rao(amount_rao: int) -> str:
    """A decimal TAO string from integer rao, by string arithmetic.

    `amount_rao / 1e9` is exactly the step that silently loses a rao, so the split is integral —
    the same reasoning, and the same shape, as `credits.btcli_command`.
    """
    if amount_rao < 0:
        raise ValueError("a TAO amount cannot be negative")
    whole, fraction = divmod(amount_rao, RAO_PER_TAO)
    digits = len(str(RAO_PER_TAO)) - 1
    rendered = f"{whole}.{fraction:0{digits}d}".rstrip("0").rstrip(".")
    return rendered or "0"


def quote_fiat_amount(
    required_rao: int,
    *,
    crypto_per_fiat_unit: Decimal,
    margin_bps: int,
    decimals: int,
) -> str:
    """The fiat amount to ask for, so the invoice locks at least `required_rao`.

    TMC PAY quotes in fiat and derives the crypto amount from a rate it locks at creation:
    `crypto_amount = fiat_amount * exchange_rate`, where the rate is crypto per one fiat unit.
    So the fiat figure is what this integration controls and the TAO figure is what it needs,
    which makes this an inversion — plus two deliberate biases:

    * **Rounded up, always.** Quantising to the currency's minor unit has to go somewhere, and
      down means selling a credit for less than `CREDIT_PRICE_RAO`. The overshoot is at most one
      cent of TAO, it lands in the buyer's own balance as `remainder_rao`, and it is therefore
      never lost — see `credits.CreditBalance`.
    * **A margin.** `crypto_per_fiat_unit` here is an *estimate* of the rate TMC PAY will lock a
      moment later, and an estimate that is 0.1% optimistic produces an invoice worth 0.999
      credits. The margin absorbs ordinary movement between the estimate and the lock;
      `create_order` still verifies the invoice it got back, so the margin is an optimisation
      rather than the guarantee.
    """
    if required_rao <= 0:
        raise ValueError("the required amount must be positive")
    if not crypto_per_fiat_unit.is_finite() or crypto_per_fiat_unit <= 0:
        raise TmcPayUnavailable("no usable TAO exchange rate is available")
    if margin_bps < 0:
        raise ValueError("the quote margin cannot be negative")
    if decimals < 0:
        raise ValueError("a currency cannot have negative decimals")

    required_tao = Decimal(required_rao) / Decimal(RAO_PER_TAO)
    fiat = required_tao / crypto_per_fiat_unit * (Decimal(10_000 + margin_bps) / Decimal(10_000))
    minor_unit = Decimal(1).scaleb(-decimals)
    quantised = fiat.quantize(minor_unit, rounding=ROUND_CEILING)
    if quantised <= 0:  # pragma: no cover - required_rao > 0 and the rate is positive
        quantised = minor_unit
    return f"{quantised:f}"


def quote_ceiling(
    required_rao: int,
    *,
    exchange_rate: str,
    slippage_bps: int,
    decimals: int,
) -> int | None:
    """The most rao an invoice for `required_rao` may lock and still be an honest price.

    `quote_fiat_amount` guards the buyer against being sold a credit below its price. This is the
    other side of the same coin, and it matters just as much: the fiat figure is derived from an
    *estimated* rate, so an estimate that is too high produces an invoice the buyer would overpay.
    Three ways that happens, none of them exotic:

    * the TaoStats quote is stale and TAO has fallen since;
    * the merchant is onboarded in a currency the rate source does not price, so the estimate is a
      dollar figure being spent as euros;
    * a fat-fingered `TMC_PAY_QUOTE_MARGIN_BPS`.

    Without a ceiling all three are *accepted*, because they clear the floor. With one, they are
    requoted at the rate the invoice itself locked and converge on the honest amount.

    The tolerance has two parts, and they are different in kind:

    * **`slippage_bps` — how much the buyer may be overcharged, as a policy.** Operator-set via
      `TMC_PAY_MAX_SLIPPAGE_BPS`, because "what counts as an acceptable overcharge" is a business
      decision and not something this function should infer. It must be at least
      `TMC_PAY_QUOTE_MARGIN_BPS` or every invoice would fall outside the deployment's own
      tolerance; `Settings` refuses that combination at startup rather than letting it produce a
      purchase that can never succeed.
    * **One minor unit of fiat, converted at the invoice's own locked rate.** Not slippage —
      arithmetic. `quote_fiat_amount` must round *up* to the currency's minor unit, so the cent it
      rounds by is unavoidable and is added on top of the policy. Converted rather than fixed
      because the same cent is a rounding error on a $2000 invoice and a fifth of a $0.05 one.

    Returns None when the invoice's `exchange_rate` is not a usable number, which is not a pricing
    problem and must not be retried as one.
    """
    try:
        rate = Decimal(exchange_rate.strip())
    except (InvalidOperation, AttributeError):
        return None
    if not rate.is_finite() or rate <= 0:
        return None
    if slippage_bps < 0:
        raise ValueError("acceptable slippage cannot be negative")
    allowance = Decimal(required_rao) * Decimal(10_000 + slippage_bps) / Decimal(10_000)
    # One minor unit of fiat is `minor_unit * rate` TAO, which is that many rao.
    minor_unit_rao = Decimal(1).scaleb(-decimals) * rate * RAO_PER_TAO
    return int((allowance + minor_unit_rao).to_integral_value(rounding=ROUND_CEILING))


def payment_was_late(invoice: Invoice) -> bool:
    """Whether TMC PAY confirmed this invoice after its own TTL had elapsed.

    TMC PAY has no `late_payment` status to report, so lateness is read off the two timestamps it
    does publish. Both must be present and the confirmation strictly later than the expiry: an
    invoice confirmed exactly at its deadline was paid on time, and a missing timestamp is not
    evidence of anything.

    Unpaid statuses cannot be late. `expired` means the TTL elapsed with nothing confirmed, which
    is an ordinary abandonment rather than the exceptional case this guards.
    """
    if invoice.status not in PAID_STATUSES:
        return False
    if invoice.confirmed_at is None or invoice.expires_at is None:
        return False
    return invoice.confirmed_at > invoice.expires_at


def credits_are_earned(status: str, *, late: bool, credit_late_payments: bool) -> bool:
    """Whether a status means credits may be issued.

    `confirmed` and `overpaid` are unambiguous: TMC PAY saw the locked amount arrive with enough
    confirmations. Every other status is a no, including two cases worth naming:

    * `underpaid` — real money arrived, but less than the invoice. Non-terminal, because the
      buyer may top up, and crediting a part-payment for a whole credit is not something to do
      automatically.
    * a *late* payment — the amount arrived after the invoice expired, per `payment_was_late`.
      Real money again, but this integration cannot tell from the outside whether such a payment
      will be settled to the treasury or returned to the sender, so it is a reconciliation case
      rather than a fulfilment case. Held by default; an operator who has established with TMC
      PAY that late payments do settle sets `TMC_PAY_CREDIT_LATE_PAYMENTS`.

    Note the order: lateness overrides an otherwise-paid status, so the opt-in is what decides a
    late payment either way and a plain `confirmed` is never gated by it.
    """
    if late:
        return credit_late_payments
    return status in PAID_STATUSES


# --- Webhook signatures ----------------------------------------------------------------------


def signature_matches(raw_body: bytes, header: str | None, secret: str) -> bool:
    """Whether `header` is TMC PAY's HMAC-SHA256 over exactly these bytes.

    Three things this gets right on purpose, because each has a well-known way of going wrong:

    * **The raw body, verbatim.** Not the re-serialised parse. A JSON round trip changes key
      order and whitespace, and the signature is over the octets that arrived.
    * **Constant-time comparison.** `compare_digest`, not `==`: a byte-at-a-time comparison of a
      MAC leaks how much of a guess was right.
    * **No exception on malformed input.** A missing header, a wrong prefix or non-hex digits are
      all just "does not match". Distinguishing them would answer questions for an attacker and
      helps a legitimate sender not at all.
    """
    if not header or not secret:
        return False
    candidate = header.strip()
    if not candidate.startswith(SIGNATURE_PREFIX):
        return False
    offered = candidate[len(SIGNATURE_PREFIX) :].lower()
    expected = hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(offered, expected)


# --- Parsing ---------------------------------------------------------------------------------


def parse_invoice(payload: object) -> Invoice:
    """One `Invoice` from a TMC PAY JSON object, or `TmcPayRejected`.

    Used for both the create/read responses and the webhook body, which carry the same shape —
    so an invoice that has been through a webhook is validated exactly as strictly as one read
    back from the API, and neither is trusted to be well formed.

    Two fields are read under either of two names. TMC PAY's live `InvoiceResponse` calls them
    `id` and `metadata_json`, while earlier documentation — and possibly the webhook body, which
    the published schema leaves untyped — calls them `invoice_id` and `metadata`. Accepting both
    costs one lookup and removes a whole class of silent breakage; the live name is preferred
    where a body carries both.
    """
    if not isinstance(payload, Mapping):
        raise TmcPayRejected("TMC PAY returned something that is not a JSON object")

    invoice_id = _either_text(payload, "id", "invoice_id", limit=64)
    merchant_id = _text(payload, "merchant_id", limit=64)
    status = _text(payload, "status", limit=32).lower()
    if status not in INVOICE_STATUSES:
        raise TmcPayRejected(f"unknown invoice status {status!r}")

    crypto_amount = _text(payload, "crypto_amount", limit=64)
    return Invoice(
        invoice_id=invoice_id,
        merchant_id=merchant_id,
        status=status,
        external_id=_optional_text(payload, "external_id", limit=128),
        fiat_amount=_text(payload, "fiat_amount", limit=64),
        fiat_currency=_text(payload, "fiat_currency", limit=8).upper(),
        crypto_amount=crypto_amount,
        crypto_amount_rao=rao_from_tao(crypto_amount),
        crypto_currency=_text(payload, "crypto_currency", limit=16).upper(),
        crypto_network=_text(payload, "crypto_network", limit=32).lower(),
        deposit_address=_text(payload, "deposit_address", limit=128),
        exchange_rate=_text(payload, "exchange_rate", limit=64),
        commission_amount=_optional_text(payload, "commission_amount", limit=64),
        created_at=_optional_timestamp(payload, "created_at"),
        expires_at=_optional_timestamp(payload, "expires_at"),
        confirmed_at=_optional_timestamp(payload, "confirmed_at"),
        metadata=_either_mapping(payload, "metadata_json", "metadata"),
    )


def _text(payload: Mapping[str, Any], key: str, *, limit: int) -> str:
    value = payload.get(key)
    # Numbers are accepted and stringified for the decimal fields only through this path, so a
    # processor that starts sending `fiat_amount: 49.99` as a JSON number does not break the
    # integration — but it never becomes a float on the way in, because `Decimal(str(...))` of
    # the original token is what `rao_from_tao` sees.
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = repr(value) if isinstance(value, float) else str(value)
    if not isinstance(value, str) or not value.strip():
        raise TmcPayRejected(f"invoice field {key!r} is missing or empty")
    text = value.strip()
    if len(text) > limit:
        raise TmcPayRejected(f"invoice field {key!r} is implausibly long")
    return text


def _either_text(
    payload: Mapping[str, Any], preferred: str, fallback: str, *, limit: int
) -> str:
    """One required string under whichever of two names carries it.

    The error names the preferred key alone. A message listing both spellings would invite the
    reader to try the deprecated one.
    """
    if payload.get(preferred) is None and payload.get(fallback) is not None:
        return _text(payload, fallback, limit=limit)
    return _text(payload, preferred, limit=limit)


def _either_mapping(
    payload: Mapping[str, Any], preferred: str, fallback: str
) -> Mapping[str, Any] | None:
    """One optional JSON object under whichever of two names carries it."""
    for key in (preferred, fallback):
        value = payload.get(key)
        if isinstance(value, Mapping):
            return value
    return None


def _optional_text(payload: Mapping[str, Any], key: str, *, limit: int) -> str | None:
    if payload.get(key) is None:
        return None
    return _text(payload, key, limit=limit)


def _optional_timestamp(payload: Mapping[str, Any], key: str) -> dt.datetime | None:
    raw = payload.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise TmcPayRejected(f"invoice field {key!r} is not an ISO-8601 string")
    try:
        # TMC PAY publishes UTC with a `Z` suffix, which `fromisoformat` accepts from 3.11.
        value = dt.datetime.fromisoformat(raw)
    except ValueError as exc:
        raise TmcPayRejected(f"invoice field {key!r} is not an ISO-8601 timestamp") from exc
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)


# --- The client ------------------------------------------------------------------------------


class InvoiceGateway(Protocol):
    """What the router and the reconciler need from TMC PAY. Injected, so tests need no network."""

    async def create_invoice(
        self,
        *,
        fiat_amount: str,
        fiat_currency: str,
        external_id: str,
        description: str,
        metadata: Mapping[str, Any],
        ttl_minutes: int,
    ) -> Invoice: ...

    async def read_invoice(self, invoice_id: str) -> Invoice: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True)
class UnavailableGateway:
    """The explicit result when TMC PAY is not configured on this deployment.

    Fails closed, in the same spirit as `ChainPaymentVerifier` with no reader: an unconfigured
    processor refuses to sell credits rather than pretending to.
    """

    async def create_invoice(self, **_: object) -> Invoice:
        raise TmcPayUnavailable("TMC PAY is not configured on this deployment")

    async def read_invoice(self, invoice_id: str) -> Invoice:
        del invoice_id
        raise TmcPayUnavailable("TMC PAY is not configured on this deployment")

    async def aclose(self) -> None:
        return None


class TmcPayClient:
    """`InvoiceGateway` over TMC PAY's merchant API.

    Holds one `httpx.AsyncClient` for the process, because a TLS handshake per purchase is both
    slower and, on a shared endpoint, a good way to be rate limited — the same reason
    `SubtensorTransferReader` keeps its connection.

    The API key is a merchant-wide credential: it can create invoices that will be paid to *our*
    merchant account. It is never logged, never returned by any endpoint, and lives in the
    settings object marked `repr=False`.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("a TMC PAY base URL is required")
        if not api_key:
            raise ValueError("a TMC PAY API key is required")
        if timeout_seconds <= 0:
            raise ValueError("the TMC PAY timeout must be positive")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(
            timeout=timeout_seconds,
            headers={
                "Accept": "application/json",
                "User-Agent": "conjectures-validator/0.1",
            },
        )
        self._owns_client = client is None

    async def create_invoice(
        self,
        *,
        fiat_amount: str,
        fiat_currency: str,
        external_id: str,
        description: str,
        metadata: Mapping[str, Any],
        ttl_minutes: int,
    ) -> Invoice:
        """One invoice, in TAO on Bittensor, for `fiat_amount`.

        `external_id` is TMC PAY's idempotency key: a repeat with the same value returns the
        existing invoice in its current state rather than creating a second one, which is what
        makes a retry after a lost response safe. This integration passes the order's own
        identifier, so the key is stable for exactly as long as the order it belongs to.
        """
        body = {
            "fiat_amount": fiat_amount,
            "fiat_currency": fiat_currency,
            "crypto_currency": CRYPTO_CURRENCY,
            "crypto_network": CRYPTO_NETWORK,
            "external_id": external_id,
            "description": description,
            "metadata": dict(metadata),
            "ttl_minutes": ttl_minutes,
        }
        payload = await self._request("POST", INVOICES_PATH, json=body)
        return parse_invoice(payload)

    async def read_invoice(self, invoice_id: str) -> Invoice:
        payload = await self._request("GET", invoice_path(invoice_id))
        return parse_invoice(payload)

    async def _request(
        self, method: str, path: str, *, json: Mapping[str, Any] | None = None
    ) -> object:
        url = f"{self._base_url}{path}"
        # TMC PAY declares the credential as a required `X-API-Key` header parameter on every
        # invoice endpoint, not as a bearer token. Sending `Authorization` instead is not an auth
        # failure at their end: FastAPI reports the missing header as a 422 validation error.
        headers = {API_KEY_HEADER: self._api_key}
        try:
            response = await self._client.request(method, url, json=json, headers=headers)
        except httpx.HTTPError as exc:
            # Never include the exception's request in the message: httpx renders the URL, and
            # the URL is fine, but keeping the habit means a future header-bearing repr cannot
            # leak the key into a log line.
            raise TmcPayUnavailable(f"TMC PAY is unreachable: {type(exc).__name__}") from exc

        if response.status_code >= 500:
            raise TmcPayUnavailable(
                f"TMC PAY answered {response.status_code}; the request may be retried"
            )
        if response.status_code >= 400:
            raise TmcPayRejected(
                f"TMC PAY refused the request with {response.status_code}: "
                f"{_short(response.text)}"
            )
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise TmcPayRejected("TMC PAY returned an implausibly large response")
        try:
            return response.json()
        except ValueError as exc:
            raise TmcPayRejected("TMC PAY returned a body that is not JSON") from exc

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _short(text: str, *, limit: int = 200) -> str:
    """A processor's error text, bounded, for a log line and never for a response body."""
    collapsed = " ".join(text.split())
    return collapsed[:limit]


__all__ = [
    "API_KEY_HEADER",
    "CRYPTO_CURRENCY",
    "CRYPTO_NETWORK",
    "INVOICE_STATUSES",
    "MAX_WEBHOOK_BYTES",
    "PAID_STATUSES",
    "SETTLED_STATUSES",
    "STATUS_CANCELLED",
    "STATUS_CONFIRMED",
    "STATUS_CONFIRMING",
    "STATUS_CREATED",
    "STATUS_EXPIRED",
    "STATUS_OVERPAID",
    "STATUS_PENDING",
    "STATUS_UNDERPAID",
    "WEBHOOK_EVENT_HEADER",
    "WEBHOOK_ID_HEADER",
    "WEBHOOK_SIGNATURE_HEADER",
    "WEBHOOK_TIMESTAMP_HEADER",
    "Invoice",
    "InvoiceGateway",
    "TmcPayClient",
    "TmcPayError",
    "TmcPayRejected",
    "TmcPayUnavailable",
    "UnavailableGateway",
    "credits_are_earned",
    "invoice_path",
    "parse_invoice",
    "payment_was_late",
    "quote_ceiling",
    "quote_fiat_amount",
    "rao_from_tao",
    "signature_matches",
    "tao_from_rao",
]
