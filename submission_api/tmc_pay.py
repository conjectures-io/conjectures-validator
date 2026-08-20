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

import asyncio
import datetime as dt
import hashlib
import hmac
import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from collections.abc import Callable, Mapping, Sequence
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


# What an invoice is denominated in unless a buyer picks otherwise. TAO on Bittensor, because
# credits are priced in rao: an invoice in TAO can be checked against `credits * CREDIT_PRICE_RAO`
# in the same unit the ledger uses, which is what makes crediting the amount that arrived safe.
CRYPTO_CURRENCY = "TAO"
CRYPTO_NETWORK = "bittensor"

# An operator's allowlist of currency/network pairs, as parsed from `TMC_PAY_PAYABLE_PAIRS`.
# Empty means every pair TMC PAY offers, which is the default: the credit price is in TAO and the
# fiat figure carries it across, so no currency is unsafe on its own. The switch exists because
# some are unattractive for other reasons — a chain that is awkward to reconcile, or a token thin
# enough that the processor's own rate is not worth trusting — and turning one off should not need
# a deploy of new code.
#
# A pair with no network means every network TMC PAY offers for that currency.
PayablePairs = tuple[tuple[str, str | None], ...]


def pair_is_payable(
    currency: str, network: str, *, allowlist: PayablePairs
) -> bool:
    """Whether this deployment will issue an invoice in this pair.

    One rule, read by both the endpoint that advertises a pair and the endpoint that invoices in
    one. Kept as a function rather than two comparisons, because the failure mode of having two is
    exactly what shipped once already: a purchase page reporting a currency unavailable while the
    POST behind it accepted that currency perfectly happily.

    An empty allowlist permits everything. That is the default, and it means an operator who has
    not thought about this gets what TMC PAY supports rather than a silently narrowed list.
    """
    if not allowlist:
        return True
    return any(
        code == currency and (name is None or name == network)
        for code, name in allowlist
    )

# TMC PAY's currency catalogue, and how long it is held. Public, no credential. Cached generously
# because the set of chains a processor supports changes on the order of months, and a purchase
# page should not cost an outbound call per render.
CURRENCIES_PATH = "/api/v1/currencies"
CURRENCIES_CACHE_SECONDS = 600
MAX_CURRENCIES_BYTES = 64 * 1024

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

# What TMC PAY prefixes the digest with. Not `sha256=`: the label names a signing *version*,
# which is theirs to change if the scheme changes, so an unrecognised one is a refusal and not a
# guess at what they meant.
SIGNATURE_PREFIX = "v1="

# Separates the delivery timestamp from the body inside the signed message.
SIGNED_MESSAGE_SEPARATOR = b"."

# How much of a candidate MAC the troubleshooting log prints. Long enough that a match is
# unambiguous, far too short to forge a signature with.
PREFIX_LENGTH = 12

# A webhook body is a small JSON object. Bounded before it is read, because the endpoint is
# unauthenticated until the signature is checked and the signature cannot be checked without
# buffering the body first.
MAX_WEBHOOK_BYTES = 64 * 1024

# What an invoice response may weigh. Same reasoning in the other direction: a processor that
# started streaming would otherwise be able to exhaust this process's memory.
MAX_RESPONSE_BYTES = 256 * 1024

# The hosted payment page's URL. TMC PAY publishes no length for it; 2048 is the bound their own
# redirect-URL fields carry, and it is the practical ceiling for something that has to survive a
# browser address bar.
MAX_HOSTED_URL_LENGTH = 2048

# What TMC PAY accepts for the two post-payment redirect targets. Their schema caps both at 2048,
# and a longer one is refused by them rather than truncated — so it is checked here, where the
# message can say which setting is too long instead of arriving as a validation error about a
# field name the operator never typed.
MAX_REDIRECT_URL_LENGTH = 2048

# Bounds on the currency catalogue's own text and numbers. A ticker is a handful of characters and
# a chain name a short word; 36 decimals is past every token in circulation.
MAX_CURRENCY_CODE_LENGTH = 16
MAX_NETWORK_NAME_LENGTH = 32
# A deposit address is minted by TMC PAY on the buyer's chosen chain, so it is bounded rather than
# shaped: 42 characters for Ethereum, 42 for a bech32 Bitcoin address, 95 for Monero. The same
# number is the `tmc_pay_deposit_address_length` check on the column — see V027, which exists
# because that column used to insist on a 48-character Substrate address.
MAX_DEPOSIT_ADDRESS_LENGTH = 128
MAX_DECIMAL_PLACES = 36

# The only schemes a hosted payment URL may use. This value is handed to a browser as a
# navigation target, so anything else — `javascript:` above all — must not survive parsing. The
# provenance is authenticated either way (TLS on a read, a verified signature on a webhook), so
# this is defence in depth rather than the primary control.
HOSTED_URL_SCHEMES = ("https://", "http://")


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
class PaymentNetwork:
    """One chain a currency can be paid over, with the precision TMC PAY reports for it."""

    network: str
    decimals: int
    display_decimals: int


@dataclass(frozen=True)
class PaymentCurrency:
    """One currency TMC PAY accepts, across every chain it accepts for it."""

    code: str
    networks: tuple[PaymentNetwork, ...]

    def payable_networks(self, allowlist: PayablePairs) -> tuple[PaymentNetwork, ...]:
        """The networks this deployment would actually invoice in — see `pair_is_payable`."""
        return tuple(
            network
            for network in self.networks
            if pair_is_payable(self.code, network.network, allowlist=allowlist)
        )


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
    # The same amount in rao, and only when `crypto_currency` is TAO. See `parse_invoice`.
    crypto_amount_rao: int | None
    crypto_currency: str
    crypto_network: str
    deposit_address: str
    exchange_rate: str
    commission_amount: str | None
    # Where TMC PAY wants the buyer sent to pay. Authoritative, and not something to construct:
    # their public invoice route is keyed by an opaque `hosted_token`, not by the invoice id, so a
    # URL assembled from a base and an id points at nothing.
    hosted_invoice_url: str | None
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


def credited_rao(invoice: Invoice, *, required_rao: int) -> int:
    """How much rao an invoice is worth, in the unit the credit ledger uses.

    Credits are priced in rao and that does not change with what the buyer sends — a purchase is
    `credits x CREDIT_PRICE_RAO` of value, quoted through fiat and collected in whatever currency
    was chosen. So there are two cases and they are different in kind:

    * **TAO.** The locked amount *is* rao, and it is at least `required_rao` because the quote band
      refused it otherwise. Credit what arrived, so the rounding-up surplus lands in the buyer's
      balance as `remainder_rao` rather than being kept.
    * **Anything else.** There is no rao to compare against. What was collected is the fiat figure
      this side asked for, and that figure was computed *from* `required_rao` — so the purchase is
      worth exactly `required_rao` and no more. Crediting a converted crypto amount instead would
      mean inventing an exchange rate after the fact, which is the one thing this module refuses to
      do anywhere else either.
    """
    if invoice.crypto_currency == CRYPTO_CURRENCY and invoice.crypto_amount_rao is not None:
        return invoice.crypto_amount_rao
    return required_rao


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


def signed_message(raw_body: bytes, timestamp: str | None) -> bytes:
    """The octets TMC PAY signs: the delivery timestamp, a dot, then the body verbatim.

    Their documentation says the body on its own. It is wrong, and this is measured rather than
    assumed: `signature_diagnosis` computed both schemes against real deliveries and this one
    matched every time while the documented one matched none. The timestamp is used exactly as it
    arrived — an ISO-8601 string, not an epoch — because it is signed as text and reformatting it
    would change the message.

    Binding the timestamp is the better design anyway, and it is why no freshness window is
    checked here. Re-signing a captured body under a newer timestamp needs the secret, so it is
    already out of reach; replaying the delivery verbatim is caught by the `X-Webhook-ID`
    deduplication the handler does next. A clock-skew rejection would only cost a paying customer
    their credits for a delivery that is provably genuine.
    """
    return (timestamp or "").encode("utf-8") + SIGNED_MESSAGE_SEPARATOR + raw_body


def signature_matches(
    raw_body: bytes, header: str | None, secret: str, *, timestamp: str | None
) -> bool:
    """Whether `header` is TMC PAY's HMAC-SHA256 over this delivery.

    Four things this gets right on purpose, because each has a well-known way of going wrong:

    * **The raw body, verbatim.** Not the re-serialised parse. A JSON round trip changes key
      order and whitespace, and the signature is over the octets that arrived.
    * **The timestamp too**, per `signed_message`. A `timestamp` of `None` therefore cannot
      verify, which is the wanted answer: without it there is nothing to check the signature of.
    * **Constant-time comparison.** `compare_digest`, not `==`: a byte-at-a-time comparison of a
      MAC leaks how much of a guess was right.
    * **No exception on malformed input.** A missing header, an unknown prefix, non-hex digits and
      non-ASCII bytes are all just "does not match". Distinguishing them would answer questions
      for an attacker and helps a legitimate sender not at all.
    """
    if not header or not secret:
        return False
    candidate = header.strip()
    if not candidate.startswith(SIGNATURE_PREFIX):
        return False
    try:
        offered = candidate[len(SIGNATURE_PREFIX) :].lower().encode("ascii")
    except UnicodeEncodeError:
        # `compare_digest` raises on non-ASCII text, and this function promises never to raise.
        # A byte outside ASCII cannot be part of a hex digest regardless.
        return False
    expected = hmac.new(
        secret.encode("utf-8"), signed_message(raw_body, timestamp), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(offered, expected.encode("ascii"))


def signature_diagnosis(
    raw_body: bytes, *, secret: str, timestamp: str | None, webhook_id: str | None
) -> tuple[tuple[str, str], ...]:
    """Which signing scheme, if any, would have matched this body — for troubleshooting only.

    A rejected webhook has exactly two plausible causes and they need different fixes: the secret
    is wrong, or the message being signed is not what this code thinks it is. One bit of
    information — "did any scheme match" — separates them, and guessing between them by redeploying
    is slow and inconclusive.

    So each candidate is computed and reported as a **short prefix**. A prefix is enough to compare
    against the offered signature by eye and useless for forging one, which matters because this
    runs on a body an unauthenticated caller supplied: logging a full valid MAC for
    attacker-chosen bytes would hand out exactly the signature they could not compute themselves.

    The candidates are the schemes payment processors actually use. The first is the one
    `signature_matches` implements; the second is the one TMC PAY documents. They are not the
    same, which is what this helper was built to discover, and is why it stays: a processor whose
    published payload schema does not match what it sends may change its signing without saying
    so either.
    """
    if not secret:
        return ()
    key = secret.encode("utf-8")
    ts = (timestamp or "").encode("utf-8")
    wid = (webhook_id or "").encode("utf-8")
    candidates: tuple[tuple[str, bytes], ...] = (
        ("timestamp.body (ours)", signed_message(raw_body, timestamp)),
        ("raw body (documented)", raw_body),
        ("timestamp + body", ts + raw_body),
        ("id.timestamp.body", wid + b"." + ts + b"." + raw_body),
        ("body.timestamp", raw_body + b"." + ts),
    )
    return tuple(
        (label, hmac.new(key, message, hashlib.sha256).hexdigest()[:PREFIX_LENGTH])
        for label, message in candidates
    )


# --- Parsing ---------------------------------------------------------------------------------


def parse_currencies(payload: object) -> tuple[PaymentCurrency, ...]:
    """TMC PAY's currency catalogue, reduced to the pairs and precisions it publishes.

    The endpoint answers a list of entries shaped like:

        {"code": "USDT", "networks": ["ethereum", "tron", "base"],
         "network_metadata": [{"network": "ethereum", "decimals": 6, "display_decimals": 2}, ...],
         "decimals": 6, "display_decimals": 2}

    `networks` and `network_metadata` are two views of the same thing, and only the second carries
    precision. This trusts neither to be complete: a network named in `networks` with no metadata
    falls back to the currency-level decimals, which is what the entry itself says they are.

    **A malformed entry is skipped, not fatal.** The catalogue is a menu; one unreadable line
    should cost that one currency rather than every payment option on the page. Anything skipped
    is logged, because a currency silently missing from a purchase page is otherwise invisible.
    """
    if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes)):
        raise TmcPayRejected("TMC PAY returned something that is not a currency list")

    currencies: list[PaymentCurrency] = []
    for entry in payload:
        parsed = _currency_entry(entry)
        if parsed is not None:
            currencies.append(parsed)
    if not currencies:
        raise TmcPayRejected("TMC PAY returned no usable currencies")
    return tuple(currencies)


def _currency_entry(entry: object) -> PaymentCurrency | None:
    if not isinstance(entry, Mapping):
        logger.warning("skipping a TMC PAY currency entry that is not an object")
        return None
    code = entry.get("code")
    if not isinstance(code, str) or not (1 <= len(code) <= MAX_CURRENCY_CODE_LENGTH):
        logger.warning("skipping a TMC PAY currency with an unusable code: %r", code)
        return None
    code = code.upper()

    fallback = _decimal_places(entry, "decimals")
    fallback_display = _decimal_places(entry, "display_decimals")
    metadata = entry.get("network_metadata")
    by_network: dict[str, PaymentNetwork] = {}
    if isinstance(metadata, Sequence) and not isinstance(metadata, (str, bytes)):
        for item in metadata:
            if not isinstance(item, Mapping):
                continue
            name = item.get("network")
            if not isinstance(name, str) or not (1 <= len(name) <= MAX_NETWORK_NAME_LENGTH):
                continue
            decimals = _decimal_places(item, "decimals")
            by_network[name.lower()] = PaymentNetwork(
                network=name.lower(),
                decimals=fallback if decimals is None else decimals,
                display_decimals=(
                    _decimal_places(item, "display_decimals")
                    or fallback_display
                    or (fallback if decimals is None else decimals)
                    or 0
                ),
            )

    names = entry.get("networks")
    ordered: list[PaymentNetwork] = []
    if isinstance(names, Sequence) and not isinstance(names, (str, bytes)):
        for name in names:
            if not isinstance(name, str) or not (1 <= len(name) <= MAX_NETWORK_NAME_LENGTH):
                continue
            key = name.lower()
            ordered.append(
                by_network.get(
                    key,
                    PaymentNetwork(
                        network=key,
                        decimals=fallback or 0,
                        display_decimals=fallback_display or fallback or 0,
                    ),
                )
            )
    # A currency with no readable network cannot be paid over anything, so it is not an option.
    if not ordered:
        logger.warning("skipping TMC PAY currency %s: it names no usable network", code)
        return None
    return PaymentCurrency(code=code, networks=tuple(ordered))


def _decimal_places(payload: Mapping[str, Any], key: str) -> int | None:
    """A plausible decimal count, or None. Never raises on upstream text."""
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= MAX_DECIMAL_PLACES else None


def parse_invoice(payload: object) -> Invoice:
    """One `Invoice` from a TMC PAY JSON object, or `TmcPayRejected`.

    Used for both the create/read responses and the webhook body, which carry the same shape —
    so an invoice that has been through a webhook is validated exactly as strictly as one read
    back from the API, and neither is trusted to be well formed.

    Four fields are read under either of two names, because TMC PAY's REST API and its webhook
    are two different serialisations of the same invoice:

        REST `InvoiceResponse`   webhook body
        ----------------------   --------------------------
        id                       invoice_id
        metadata_json            metadata
        crypto_amount            expected_crypto_amount
        fiat_amount              expected_fiat_amount

    That is established rather than assumed: their OpenAPI document defines `InvoiceResponse` with
    the left-hand names and defines no webhook payload schema at all — the delivery record types it
    `additionalProperties: true` — so the right-hand shape is undocumented and knowable only from
    real deliveries. Accepting both spellings costs one lookup and removes a whole class of silent
    breakage; the REST name is preferred where a body carries both.

    The webhook splits each amount four ways: `expected_`, `received_`, `remaining_`, `excess_`.
    `expected_` is the counterpart of the REST field — the amount the invoice asks for, locked at
    `exchange_rate`, and the figure the quote band already checked against the credit price. It is
    deliberately not `received_`, which reads `"0"` on every `created` and `pending` delivery:
    whether the money arrived is what `status` reports, and crediting a received figure straight
    from the wire would credit nothing on exactly the deliveries that arrive first.
    """
    if not isinstance(payload, Mapping):
        raise TmcPayRejected("TMC PAY returned something that is not a JSON object")

    invoice_id = _either_text(payload, "id", "invoice_id", limit=64)
    merchant_id = _text(payload, "merchant_id", limit=64)
    status = _text(payload, "status", limit=32).lower()
    if status not in INVOICE_STATUSES:
        raise TmcPayRejected(f"unknown invoice status {status!r}")

    crypto_amount = _either_text(
        payload, "crypto_amount", "expected_crypto_amount", limit=64
    )
    crypto_currency = _text(payload, "crypto_currency", limit=MAX_CURRENCY_CODE_LENGTH).upper()
    return Invoice(
        invoice_id=invoice_id,
        merchant_id=merchant_id,
        status=status,
        external_id=_optional_text(payload, "external_id", limit=128),
        fiat_amount=_either_text(
            payload, "fiat_amount", "expected_fiat_amount", limit=64
        ),
        fiat_currency=_text(payload, "fiat_currency", limit=8).upper(),
        crypto_amount=crypto_amount,
        # Rao only when the invoice is actually denominated in TAO. Every other currency has its
        # own precision — BTC 8 places, USDC 6, ETH 18 — so running one through `rao_from_tao`
        # would either be refused for having too many decimals or, worse, silently rescaled into a
        # rao figure that means nothing. `None` is the honest answer, and it is what keeps the
        # ledger's unit and this field's unit the same thing wherever it is not None.
        crypto_amount_rao=(
            rao_from_tao(crypto_amount) if crypto_currency == CRYPTO_CURRENCY else None
        ),
        crypto_currency=crypto_currency,
        crypto_network=_text(payload, "crypto_network", limit=MAX_NETWORK_NAME_LENGTH).lower(),
        deposit_address=_text(
            payload, "deposit_address", limit=MAX_DEPOSIT_ADDRESS_LENGTH
        ),
        exchange_rate=_text(payload, "exchange_rate", limit=64),
        commission_amount=_optional_text(payload, "commission_amount", limit=64),
        hosted_invoice_url=_hosted_url(payload, "hosted_invoice_url"),
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


def _hosted_url(payload: Mapping[str, Any], key: str) -> str | None:
    """An absolute http(s) URL, or None — never a raise.

    Absent is ordinary: an invoice read back from the API carries one, and a webhook body may not.
    A *present but unusable* value is treated the same way rather than refused, because the payment
    page is a convenience and rejecting the whole invoice over it would fail a purchase that is
    otherwise fine. The refusal is logged so a URL this never surfaces is visible.
    """
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        logger.warning("TMC PAY sent a non-string %s; ignoring it", key)
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > MAX_HOSTED_URL_LENGTH:
        logger.warning("TMC PAY sent an empty or oversized %s; ignoring it", key)
        return None
    # Case-folded, because a scheme is case-insensitive and `JavaScript:` must not slip past a
    # comparison that only knows the lowercase spelling.
    if not candidate.casefold().startswith(HOSTED_URL_SCHEMES):
        logger.warning("TMC PAY sent a %s that is not an http(s) URL; ignoring it", key)
        return None
    return candidate


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
        crypto_currency: str = CRYPTO_CURRENCY,
        crypto_network: str = CRYPTO_NETWORK,
        success_redirect_url: str | None = None,
        failure_redirect_url: str | None = None,
    ) -> Invoice: ...

    async def read_invoice(self, invoice_id: str) -> Invoice: ...

    async def list_currencies(self) -> tuple[PaymentCurrency, ...]:
        """Every currency and network TMC PAY accepts, whatever this deployment will honour."""
        ...

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

    async def list_currencies(self) -> tuple[PaymentCurrency, ...]:
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
        now: Callable[[], dt.datetime] | None = None,
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
        # Injected so the catalogue's cache boundary is testable without waiting ten minutes.
        self._now = now or (lambda: dt.datetime.now(dt.UTC))
        self._currencies_lock = asyncio.Lock()
        self._currencies: tuple[PaymentCurrency, ...] | None = None
        self._currencies_expire_at: dt.datetime | None = None

    async def create_invoice(
        self,
        *,
        fiat_amount: str,
        fiat_currency: str,
        external_id: str,
        description: str,
        metadata: Mapping[str, Any],
        ttl_minutes: int,
        crypto_currency: str = CRYPTO_CURRENCY,
        crypto_network: str = CRYPTO_NETWORK,
        success_redirect_url: str | None = None,
        failure_redirect_url: str | None = None,
    ) -> Invoice:
        """One invoice for `fiat_amount`, payable in the given pair.

        The pair defaults to TAO on Bittensor and the caller is responsible for having checked any
        other against `list_currencies` — this method sends what it is given, because a second
        allowlist here would be a second place for the two to disagree.


        `external_id` is TMC PAY's idempotency key: a repeat with the same value returns the
        existing invoice in its current state rather than creating a second one, which is what
        makes a retry after a lost response safe. This integration passes the order's own
        identifier, so the key is stable for exactly as long as the order it belongs to.
        """
        body = {
            "fiat_amount": fiat_amount,
            "fiat_currency": fiat_currency,
            "crypto_currency": crypto_currency,
            "crypto_network": crypto_network,
            "external_id": external_id,
            "description": description,
            "metadata": dict(metadata),
            "ttl_minutes": ttl_minutes,
        }
        # Omitted rather than sent as null when unset. TMC PAY treats both the same, but a body
        # that only carries the fields this integration is actually using is easier to read in a
        # capture, and it leaves their default behaviour to them.
        for field, value in (
            ("success_redirect_url", success_redirect_url),
            ("failure_redirect_url", failure_redirect_url),
        ):
            if not value:
                continue
            if len(value) > MAX_REDIRECT_URL_LENGTH:
                raise TmcPayRejected(
                    f"{field} is longer than the {MAX_REDIRECT_URL_LENGTH} characters TMC PAY "
                    "accepts; shorten WEBSITE_BASE_URL"
                )
            body[field] = value
        payload = await self._request("POST", INVOICES_PATH, json=body)
        return parse_invoice(payload)

    async def read_invoice(self, invoice_id: str) -> Invoice:
        payload = await self._request("GET", invoice_path(invoice_id))
        return parse_invoice(payload)

    async def list_currencies(self) -> tuple[PaymentCurrency, ...]:
        """The currency catalogue, cached for `CURRENCIES_CACHE_SECONDS`.

        Cached in the client rather than in the router because it is a property of the processor,
        not of a request: every account's purchase page wants the same answer, and the set changes
        on the order of months.

        A failed refresh is never cached, and a previously fetched catalogue keeps being served
        while every call retries. The same reasoning as the rate readers: a menu one window out of
        date is a far better answer than an empty payment page.
        """
        now = self._now()
        if (
            self._currencies is not None
            and self._currencies_expire_at is not None
            and now < self._currencies_expire_at
        ):
            return self._currencies

        async with self._currencies_lock:
            now = self._now()
            if (
                self._currencies is not None
                and self._currencies_expire_at is not None
                and now < self._currencies_expire_at
            ):
                return self._currencies
            try:
                payload = await self._request("GET", CURRENCIES_PATH, authenticated=False)
                currencies = parse_currencies(payload)
            except (TmcPayUnavailable, TmcPayRejected):
                if self._currencies is not None:
                    logger.warning(
                        "serving a stale TMC PAY currency catalogue; the upstream is unreachable"
                    )
                    return self._currencies
                raise
            self._currencies = currencies
            self._currencies_expire_at = now + dt.timedelta(
                seconds=CURRENCIES_CACHE_SECONDS
            )
            return currencies

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        authenticated: bool = True,
    ) -> object:
        url = f"{self._base_url}{path}"
        # TMC PAY declares the credential as a required `X-API-Key` header parameter on every
        # invoice endpoint, not as a bearer token. Sending `Authorization` instead is not an auth
        # failure at their end: FastAPI reports the missing header as a 422 validation error.
        #
        # The currency catalogue declares no credential and needs none, so it is fetched without
        # one: a merchant key on a request that does not require it is exposure bought for nothing.
        headers = {API_KEY_HEADER: self._api_key} if authenticated else {}
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
    "CURRENCIES_CACHE_SECONDS",
    "CURRENCIES_PATH",
    "INVOICE_STATUSES",
    "MAX_CURRENCY_CODE_LENGTH",
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
    "PayablePairs",
    "PaymentCurrency",
    "PaymentNetwork",
    "TmcPayClient",
    "TmcPayError",
    "TmcPayRejected",
    "TmcPayUnavailable",
    "UnavailableGateway",
    "credits_are_earned",
    "invoice_path",
    "pair_is_payable",
    "parse_currencies",
    "parse_invoice",
    "payment_was_late",
    "quote_ceiling",
    "quote_fiat_amount",
    "rao_from_tao",
    "signature_diagnosis",
    "signature_matches",
    "signed_message",
    "tao_from_rao",
]
