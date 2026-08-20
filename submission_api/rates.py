"""TAO/USD price sources, for pricing a TMC PAY invoice.

One job, and it is a money job: answer "how many dollars is one TAO" well enough to size an invoice
whose locked TAO amount must land on `credits × CREDIT_PRICE_RAO`. `routers/tmc_pay.py` explains
what happens to the answer; this module is about where it comes from and how it is cached.

**No float, ever.** The upstream APIs publish prices as bare JSON numbers — `"close": 200.2` — and
`json.loads` would turn that into a binary float that is not exactly 200.2. Every parser here reads
the response with `parse_float=Decimal`, so the number that reaches the arithmetic is the number
that was published.

Three sources, tried in order, because they are not equally good:

* `TmcPayRatesPriceReader` — TMC PAY's own `/api/v1/rates`. **Preferred**, because this is not
  market data about the platform, it is the platform quoting the rate it prices invoices with. The
  candle feed below was chosen on the argument that TaoMarketCap *is* TMC PAY; this is the same
  argument's conclusion, one step nearer the source. Public — no API key, and deliberately none
  sent.
* `TaoMarketCapPriceReader` — TaoMarketCap's own 5-minute candles. The same platform's market data,
  kept as the first fallback. Public, no API key.
* `TaoStatsTaoUsdPriceReader` (in `taostats.py`) — the last resort. Needs `TAOSTATS_API_KEY`, and is
  kept because redundancy on the path that prices money is worth thirty lines.

Above both of them sits a better source still, which is not in this module: the `exchange_rate` TMC
PAY reported on the last invoice it created for us. See `routers/tmc_pay._seed_rate` for the full
ladder — these are what it falls back to.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)

# Where a price came from. Reported alongside the number rather than inferred by the caller,
# because "which source answered" is part of the answer — it is what makes "are we still leaning on
# the secondary" a question the telemetry can settle.
SOURCE_TMC_PAY = "tmc-pay"
SOURCE_TAOMARKETCAP = "taomarketcap"
SOURCE_TAOSTATS = "taostats"
SOURCE_STATIC = "static"

TAOMARKETCAP_API_BASE_URL = "https://api.taomarketcap.com"
CANDLE_PATH = "/public/v1/market/candle-data/"

# TMC PAY's own rate endpoint, on the merchant API host. Public: the schema declares no
# `X-API-Key` for it, and no credential is sent — a merchant key on a request that does not
# need one is exposure bought for nothing.
RATES_PATH = "/api/v1/rates"

# The fiat currency this module can report, and the key the response must agree on. Mirrors
# `settings.EXTERNAL_RATE_CURRENCY`, declared here rather than imported because `settings`
# imports this module.
RATES_FIAT_CURRENCY = "USD"

# How long one rate is served before another is fetched. Unlike the candle feed there is no
# published boundary to align to — the endpoint answers with whatever it holds now — so this is a
# plain TTL, and a short one: the request is a few hundred bytes against a host this integration
# already depends on, and a fresher seed is one fewer requote.
RATES_CACHE_SECONDS = 60

# A rate table for a handful of currencies. Bounded for the same reason as the candle response.
MAX_RATES_BYTES = 64 * 1024

# The candle period to ask for, and its length. TaoMarketCap refreshes these every five minutes,
# which is what makes the cache boundary below the natural one.
CANDLE_PERIOD = "5m"
CANDLE_PERIOD_SECONDS = 300

# How the endpoint wants `from_timestamp`: a naive ISO-8601 instant, understood as UTC. Matched
# exactly rather than sent as an aware timestamp, because a query parameter's accepted format is
# not something to guess at.
CANDLE_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S"

# A handful of candles is a few hundred bytes. Bounded anyway: an upstream that starts streaming
# must not be able to exhaust this process's memory.
MAX_CANDLE_BYTES = 256 * 1024

# How far behind the clock the newest candle may be before it stops counting as "current".
#
# Requesting from one period back should return the in-progress candle plus the one before it. If
# the newest is older than this, the feed has a gap and reporting its `close` as the current price
# would be quietly wrong — so it reports nothing instead and the caller falls down the ladder.
MAX_CANDLE_AGE_SECONDS = 3 * CANDLE_PERIOD_SECONDS

# How long a price already fetched may still be served after its own cache window closed, when the
# upstream has since become unreachable.
#
# The rule this implements is "never cache a failure": a failed refresh must not overwrite a good
# price with nothing, and it must not stop the next call from trying again. Serving the last good
# number while retrying is strictly better than refusing to sell credits, because the quote band in
# `routers/tmc_pay.py` cannot be fooled by a stale seed — a bad estimate costs one wasted invoice,
# never a wrong price. Bounded all the same: seeding from a price that is hours old would burn an
# invoice on every purchase, which is a different kind of broken.
MAX_STALE_SECONDS = 3600


@dataclass(frozen=True)
class TaoUsdQuote:
    """Dollars per whole TAO, and who said so."""

    price: Decimal
    source: str


class TaoUsdPriceReader(Protocol):
    """A cached source for the current USD price of one whole TAO.

    Distinct from `AlphaUsdPriceReader` in `taostats.py`, and not a slice of it, because the two are
    used for opposite purposes. Alpha-in-USD is *display* metadata on a bounty: absent is an
    acceptable answer and the page renders without it. TAO-in-USD is an *input to a purchase* — TMC
    PAY quotes an invoice in fiat and locks the crypto amount from it, so without this there is no
    way to size an invoice at 0.5 TAO per credit, and the honest outcome is to refuse the sale
    rather than to sell at a made-up price.
    """

    async def tao_usd(self) -> TaoUsdQuote | None:
        """The current quote, or None when no trustworthy one is available."""
        ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True)
class UnavailableTaoUsdPriceReader:
    """The explicit result when no price source is configured."""

    async def tao_usd(self) -> None:
        return None

    async def aclose(self) -> None:
        return None


@dataclass(frozen=True)
class StaticTaoUsdPriceReader:
    """A deterministic source for tests and local callers."""

    price: Decimal
    source: str = SOURCE_STATIC

    async def tao_usd(self) -> TaoUsdQuote:
        return TaoUsdQuote(price=self.price, source=self.source)

    async def aclose(self) -> None:
        return None


@dataclass(frozen=True)
class FallbackTaoUsdPriceReader:
    """The first source that answers, in order.

    A plain loop rather than anything cleverer: the readers cache independently, so a primary that
    is down costs one failed request per cache window and the secondary carries the load until it
    recovers. `source` on the returned quote says which one answered, so a deployment silently
    running on its fallback is visible rather than merely working.
    """

    readers: tuple[TaoUsdPriceReader, ...]

    @classmethod
    def of(cls, *readers: TaoUsdPriceReader | None) -> TaoUsdPriceReader:
        """Build a chain, dropping the sources that are not configured.

        Returns the single reader when only one survives, and the unavailable reader when none do,
        so a caller never has to special-case the arity.
        """
        present = tuple(reader for reader in readers if reader is not None)
        if not present:
            return UnavailableTaoUsdPriceReader()
        if len(present) == 1:
            return present[0]
        return cls(readers=present)

    async def tao_usd(self) -> TaoUsdQuote | None:
        for reader in self.readers:
            quote = await reader.tao_usd()
            if quote is not None:
                return quote
        return None

    async def aclose(self) -> None:
        for reader in self.readers:
            await reader.aclose()


class TmcPayRatesPriceReader:
    """TMC PAY's `/api/v1/rates`, cached for `RATES_CACHE_SECONDS`.

    **What the endpoint means.** It answers with the fiat currency it priced in and a table of
    crypto codes, each mapping to the price of one whole unit in that currency:

        {"fiat_currency": "USD", "rates": {"TAO": "191.52707097907592", "USDC": "1.0", ...}}

    So the TAO entry is dollars per TAO, which is exactly what `TaoUsdQuote.price` means and the
    same orientation as the other readers. It is the *reciprocal* of an invoice's `exchange_rate`,
    which TMC PAY publishes as crypto per fiat unit — `routers/tmc_pay._seed_rate` performs that
    inversion, in one place, for whichever source answered.

    **The currency is verified, not assumed.** `fiat` is sent explicitly and the response's
    `fiat_currency` has to agree, so a default changing upstream cannot turn a EUR table into a
    number this module labels as dollars. A disagreement is treated as no answer.

    **No credential.** The rate table is public, so this holds its own client with no
    `X-API-Key` header. `TmcPayClient` in `tmc_pay.py` is the authenticated half and stays
    separate; a reader that cannot leak a merchant key is worth more than a shared connection.

    Caching and failure handling follow `TaoMarketCapPriceReader` exactly — a failed refresh is
    never cached, and the last good price is served for up to `MAX_STALE_SECONDS` while every call
    retries. See that class for why that is the right trade on this path.
    """

    def __init__(
        self,
        *,
        base_url: str,
        fiat_currency: str = RATES_FIAT_CURRENCY,
        crypto_currency: str = "TAO",
        ttl_seconds: int = RATES_CACHE_SECONDS,
        timeout_seconds: float = 5.0,
        client: httpx.AsyncClient | None = None,
        now: Callable[[], dt.datetime] | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("a TMC PAY base URL is required")
        if not fiat_currency:
            raise ValueError("a fiat currency is required")
        if not crypto_currency:
            raise ValueError("a crypto currency is required")
        if ttl_seconds <= 0:
            raise ValueError("the TMC PAY rate TTL must be positive")
        if timeout_seconds <= 0:
            raise ValueError("the TMC PAY rate timeout must be positive")
        self._base_url = base_url.rstrip("/")
        self._fiat_currency = fiat_currency.upper()
        self._crypto_currency = crypto_currency.upper()
        self._ttl = dt.timedelta(seconds=ttl_seconds)
        self._now = now or (lambda: dt.datetime.now(dt.UTC))
        self._client = client or httpx.AsyncClient(
            timeout=timeout_seconds,
            headers={
                "accept": "application/json",
                "User-Agent": "conjectures-validator/0.1",
            },
        )
        self._owns_client = client is None
        self._lock = asyncio.Lock()
        self._cached: TaoUsdQuote | None = None
        self._cached_at: dt.datetime | None = None
        self._expires_at: dt.datetime | None = None

    async def tao_usd(self) -> TaoUsdQuote | None:
        now = self._now()
        if self._cached is not None and self._expires_at is not None and now < self._expires_at:
            return self._cached

        async with self._lock:
            now = self._now()
            if (
                self._cached is not None
                and self._expires_at is not None
                and now < self._expires_at
            ):
                return self._cached
            try:
                price = await self._fetch()
            except (
                httpx.HTTPError,
                InvalidOperation,
                KeyError,
                TypeError,
                ValueError,
            ) as exc:
                logger.warning("the TMC PAY %s rate is unavailable: %s", self._crypto_currency, exc)
                return self._still_usable(now)

            quote = TaoUsdQuote(price=price, source=SOURCE_TMC_PAY)
            self._cached = quote
            self._cached_at = now
            self._expires_at = now + self._ttl
            return quote

    def _still_usable(self, now: dt.datetime) -> TaoUsdQuote | None:
        """The last good price, if it is not too old to seed a quote with."""
        if self._cached is None or self._cached_at is None:
            return None
        age = (now - self._cached_at).total_seconds()
        if age > MAX_STALE_SECONDS:
            logger.warning(
                "the last TMC PAY rate is %.0fs old; treating it as unavailable", age
            )
            return None
        logger.info("serving a %.0fs-old TMC PAY rate while the upstream is unreachable", age)
        return self._cached

    async def _fetch(self) -> Decimal:
        response = await self._client.get(
            f"{self._base_url}{RATES_PATH}", params={"fiat": self._fiat_currency}
        )
        response.raise_for_status()
        if len(response.content) > MAX_RATES_BYTES:
            raise ValueError("TMC PAY returned an implausibly large rate table")
        # `parse_float=Decimal` for the same reason as the candle feed: the table may publish
        # prices as bare JSON numbers, and the default parser would make them binary floats.
        return rate_from_table(
            json.loads(response.text, parse_float=Decimal),
            fiat_currency=self._fiat_currency,
            crypto_currency=self._crypto_currency,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class TaoMarketCapPriceReader:
    """TaoMarketCap's 5-minute candles, cached to the candle boundary.

    **The cache window is aligned to the feed, not to a fixed duration.** The upstream publishes a
    new candle every five minutes on the clock, so a price fetched at 13:12 is the freshest that
    will exist until 13:15 and is cached for exactly those three minutes. A rolling five-minute TTL
    would instead hold it until 13:17 — two minutes past the point where a better number existed —
    and would drift further with every refresh.

    **A failed refresh is never cached.** `tao_usd` writes to the cache only on success, so:

    * with a previous price and a failing upstream, the last good number is served (up to
      `MAX_STALE_SECONDS`) and *every* subsequent call retries — the expiry is left in the past;
    * with no previous price, None is returned and nothing is stored, so the next call retries too.

    Storing None for a window would be the opposite behaviour, and on this path it is the wrong one:
    a purchase is a rare, deliberate action, and refusing it for five minutes because one request
    timed out is a worse trade than an extra request.
    """

    def __init__(
        self,
        *,
        base_url: str = TAOMARKETCAP_API_BASE_URL,
        timeout_seconds: float = 5.0,
        client: httpx.AsyncClient | None = None,
        now: Callable[[], dt.datetime] | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("a TaoMarketCap base URL is required")
        if timeout_seconds <= 0:
            raise ValueError("the TaoMarketCap timeout must be positive")
        self._base_url = base_url.rstrip("/")
        # Injected so the boundary arithmetic is testable without waiting for a real clock to
        # cross one. Wall clock rather than monotonic, deliberately: the cache expires at a UTC
        # instant the upstream chose, which a monotonic counter cannot name.
        self._now = now or (lambda: dt.datetime.now(dt.UTC))
        self._client = client or httpx.AsyncClient(
            timeout=timeout_seconds,
            headers={
                "accept": "application/json",
                "User-Agent": "conjectures-validator/0.1",
            },
        )
        self._owns_client = client is None
        self._lock = asyncio.Lock()
        self._cached: TaoUsdQuote | None = None
        self._cached_at: dt.datetime | None = None
        self._expires_at: dt.datetime | None = None

    async def tao_usd(self) -> TaoUsdQuote | None:
        now = self._now()
        if self._cached is not None and self._expires_at is not None and now < self._expires_at:
            return self._cached

        async with self._lock:
            # Re-checked under the lock: callers that queued behind a refresh must not each
            # trigger their own.
            now = self._now()
            if (
                self._cached is not None
                and self._expires_at is not None
                and now < self._expires_at
            ):
                return self._cached
            try:
                price = await self._fetch(now)
            except (
                httpx.HTTPError,
                InvalidOperation,
                KeyError,
                TypeError,
                ValueError,
            ) as exc:
                logger.warning("TaoMarketCap TAO/USD price is unavailable: %s", exc)
                # Nothing is written. The expiry stays in the past, so the next call retries.
                return self._still_usable(now)

            quote = TaoUsdQuote(price=price, source=SOURCE_TAOMARKETCAP)
            self._cached = quote
            self._cached_at = now
            self._expires_at = next_boundary(now, CANDLE_PERIOD_SECONDS)
            return quote

    def _still_usable(self, now: dt.datetime) -> TaoUsdQuote | None:
        """The last good price, if it is not too old to seed a quote with."""
        if self._cached is None or self._cached_at is None:
            return None
        age = (now - self._cached_at).total_seconds()
        if age > MAX_STALE_SECONDS:
            logger.warning(
                "the last TaoMarketCap price is %.0fs old; treating it as unavailable", age
            )
            return None
        logger.info("serving a %.0fs-old TaoMarketCap price while the upstream is unreachable", age)
        return self._cached

    async def _fetch(self, now: dt.datetime) -> Decimal:
        # One period back, so the request cannot land in the gap between the clock crossing a
        # boundary and the new candle existing: two candles come back and the newest is taken.
        start = floor_to_period(now, CANDLE_PERIOD_SECONDS) - dt.timedelta(
            seconds=CANDLE_PERIOD_SECONDS
        )
        response = await self._client.get(
            f"{self._base_url}{CANDLE_PATH}",
            params={
                "period": CANDLE_PERIOD,
                "from_timestamp": start.strftime(CANDLE_TIMESTAMP_FORMAT),
            },
        )
        response.raise_for_status()
        if len(response.content) > MAX_CANDLE_BYTES:
            raise ValueError("TaoMarketCap returned an implausibly large response")
        candle = newest_candle(
            # `parse_float=Decimal` is the whole reason this is not `response.json()`: the prices
            # are bare JSON numbers, and the default parser would make them binary floats.
            json.loads(response.text, parse_float=Decimal),
            now=now,
        )
        return positive_decimal(candle["close"], field="close")

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


# --- Candle parsing --------------------------------------------------------------------------
# Module-level and tested directly, because "which number is the current price" is the part that
# would be wrong silently.


def floor_to_period(moment: dt.datetime, period_seconds: int) -> dt.datetime:
    """The start of the period `moment` falls in."""
    if period_seconds <= 0:
        raise ValueError("a candle period must be positive")
    epoch = int(moment.timestamp())
    return dt.datetime.fromtimestamp(epoch - epoch % period_seconds, dt.UTC)


def next_boundary(moment: dt.datetime, period_seconds: int) -> dt.datetime:
    """The start of the next period — when a price fetched at `moment` stops being the freshest.

    At 13:12 with five-minute candles this is 13:15, so a price cached then lives three minutes and
    not five. Exactly on a boundary it returns the following one, so an expiry is never in the past.
    """
    return floor_to_period(moment, period_seconds) + dt.timedelta(seconds=period_seconds)


def newest_candle(payload: object, *, now: dt.datetime) -> Mapping[str, object]:
    """The most recent candle in an ascending list, if it is recent enough to be the current price.

    The endpoint returns candles oldest-first, ending with the one still forming, so the last
    element is the answer. Its timestamp is checked rather than trusted: a feed with a gap would
    otherwise have its last known `close` reported as the price right now.
    """
    if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes)):
        raise ValueError("TaoMarketCap returned something that is not a list of candles")
    if not payload:
        raise ValueError("TaoMarketCap returned no candles")
    candle = payload[-1]
    if not isinstance(candle, Mapping):
        raise ValueError("TaoMarketCap returned a malformed candle")
    stamp = candle.get("timestamp")
    if not isinstance(stamp, str):
        raise ValueError("TaoMarketCap candle has no timestamp")
    try:
        observed = dt.datetime.fromisoformat(stamp)
    except ValueError as exc:
        raise ValueError(f"TaoMarketCap candle timestamp {stamp!r} is not ISO-8601") from exc
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=dt.UTC)
    age = (now - observed).total_seconds()
    if age > MAX_CANDLE_AGE_SECONDS:
        raise ValueError(
            f"the newest TaoMarketCap candle is {age:.0f}s old; the feed has a gap"
        )
    return candle


def rate_from_table(
    payload: object, *, fiat_currency: str, crypto_currency: str
) -> Decimal:
    """One crypto's price from a TMC PAY rate table, in the fiat currency it says it used.

    The currency check is the point of doing this here rather than inline. The endpoint takes
    `fiat` as an *optional* query parameter, so a request that failed to apply it still answers
    200 with a perfectly well-formed table — in some other currency. Comparing what came back
    against what was asked for is the only thing standing between that and a price labelled
    dollars that is not dollars.

    A missing crypto code raises rather than returning None: the caller distinguishes "no answer"
    from "malformed answer" by catching, and both land on the same rung of the ladder.
    """
    if not isinstance(payload, Mapping):
        raise ValueError("TMC PAY returned something that is not a rate table")

    reported = payload.get("fiat_currency")
    if not isinstance(reported, str):
        raise ValueError("the TMC PAY rate table names no fiat currency")
    if reported.upper() != fiat_currency.upper():
        raise ValueError(
            f"asked TMC PAY for {fiat_currency} rates and it answered in {reported}"
        )

    rates = payload.get("rates")
    if not isinstance(rates, Mapping):
        raise ValueError("the TMC PAY rate table carries no rates")
    if crypto_currency not in rates:
        raise KeyError(f"TMC PAY quotes no {crypto_currency} rate in {fiat_currency}")

    return positive_decimal(
        rates[crypto_currency], field=f"the TMC PAY {crypto_currency}/{fiat_currency} rate"
    )


def positive_decimal(value: object, *, field: str) -> Decimal:
    """A finite, positive `Decimal` from a JSON number that was parsed as one.

    Accepts `Decimal` (what `parse_float=Decimal` produces), `int`, and `str`. Rejects `float`
    outright: a float here means the value went through the default JSON parser and has already
    lost exactness, and silently accepting it would defeat the point of the custom parse.
    """
    if isinstance(value, float):
        raise TypeError(
            f"{field} arrived as a float; parse the response with parse_float=Decimal"
        )
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, str)):
        raise TypeError(f"{field} is not a number: {value!r}")
    price = Decimal(value)
    if not price.is_finite() or price <= 0:
        raise ValueError(f"{field} is not finite and positive: {value!r}")
    return price


def build_tao_usd_reader(
    *,
    tmc_pay_base_url: str,
    taomarketcap_base_url: str,
    taostats_api_key: str,
    taostats_ttl_seconds: int,
    extra: Iterable[TaoUsdPriceReader] = (),
) -> TaoUsdPriceReader:
    """The configured chain: TMC PAY's own rates, then TaoMarketCap's candles, then TaoStats.

    TMC PAY leads because it is the rate the invoice will actually be priced from rather than
    market data about it. The two below are fallbacks for the case that matters — the rate endpoint
    being down is also the moment a purchase most needs a number — and neither needs an API key, so
    a deployment that has never configured TaoStats can still price an invoice.

    A deployment with TMC PAY switched off passes an empty base URL and simply starts one rung
    lower, which is what makes this safe to order this way.
    """
    # Imported here rather than at module scope: `taostats` imports this module for the protocol,
    # and doing it the other way at import time would be a cycle.
    from submission_api.taostats import TaoStatsTaoUsdPriceReader

    primary = (
        TmcPayRatesPriceReader(base_url=tmc_pay_base_url) if tmc_pay_base_url else None
    )
    secondary = (
        TaoMarketCapPriceReader(base_url=taomarketcap_base_url)
        if taomarketcap_base_url
        else None
    )
    tertiary = (
        TaoStatsTaoUsdPriceReader(
            api_key=taostats_api_key, ttl_seconds=taostats_ttl_seconds
        )
        if taostats_api_key
        else None
    )
    return FallbackTaoUsdPriceReader.of(primary, secondary, tertiary, *extra)


__all__ = [
    "CANDLE_PERIOD",
    "CANDLE_PERIOD_SECONDS",
    "MAX_CANDLE_AGE_SECONDS",
    "MAX_STALE_SECONDS",
    "RATES_CACHE_SECONDS",
    "SOURCE_STATIC",
    "SOURCE_TAOMARKETCAP",
    "SOURCE_TAOSTATS",
    "SOURCE_TMC_PAY",
    "TAOMARKETCAP_API_BASE_URL",
    "FallbackTaoUsdPriceReader",
    "StaticTaoUsdPriceReader",
    "TaoMarketCapPriceReader",
    "TaoUsdPriceReader",
    "TaoUsdQuote",
    "TmcPayRatesPriceReader",
    "UnavailableTaoUsdPriceReader",
    "build_tao_usd_reader",
    "floor_to_period",
    "newest_candle",
    "next_boundary",
    "positive_decimal",
    "rate_from_table",
]
