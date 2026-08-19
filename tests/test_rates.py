"""TAO/USD price sources: the candle cache, and the rules about not caching a failure.

No database and no network. The clock is injected and the HTTP transport is a stub, because the two
things worth proving here are both about *timing* — when the cache expires, and what happens on the
call after it does — and neither can be observed by waiting five minutes.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from decimal import Decimal

import pytest

pytest.importorskip("httpx", reason="rate readers need the service extra")

import httpx

from submission_api import rates


def run(coroutine):
    return asyncio.run(coroutine)


def at(hour: int, minute: int, second: int = 0) -> dt.datetime:
    return dt.datetime(2026, 8, 13, hour, minute, second, tzinfo=dt.UTC)


def candle(moment: dt.datetime, close: str) -> dict:
    return {
        "timestamp": moment.isoformat(),
        "open": 200.4,
        "high": 200.5,
        "low": 200.2,
        # A JSON *string* here would be easy; the real feed sends a bare number, so the fixture
        # sends one too and `json.dumps` writes it as such.
        "close": float(close),
        "volume": 46.5879,
    }


class Feed:
    """A stub transport that answers with scripted candle bodies, or fails.

    Counts requests, because "did the cache prevent a call" is the whole question in half of these
    tests and a mock that cannot be counted would not answer it.
    """

    def __init__(self, bodies: list[object] | None = None) -> None:
        self.bodies = list(bodies or [])
        self.requests: list[httpx.Request] = []
        self.fail: Exception | None = None
        self.status = 200
        self.raw: str | None = None

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail is not None:
            raise self.fail
        if self.raw is not None:
            return httpx.Response(self.status, text=self.raw)
        body = self.bodies.pop(0) if self.bodies else []
        return httpx.Response(self.status, text=json.dumps(body))

    def query(self, index: int = -1) -> dict[str, str]:
        return dict(self.requests[index].url.params)


def reader(feed: Feed, clock) -> rates.TaoMarketCapPriceReader:
    return rates.TaoMarketCapPriceReader(client=feed.client(), now=clock)


# --- Boundary arithmetic ---------------------------------------------------------------------


def test_the_cache_expires_at_the_next_candle_not_five_minutes_later():
    """A price fetched at 13:12 is cached for three minutes, because 13:15 is when a better one
    exists. A rolling TTL would hold it until 13:17, past the point of a fresher number."""
    assert rates.next_boundary(at(13, 12), 300) == at(13, 15)
    assert rates.next_boundary(at(13, 12, 30), 300) == at(13, 15)
    assert rates.next_boundary(at(13, 14, 59), 300) == at(13, 15)
    # Exactly on a boundary gives the *next* one, so an expiry is never in the past.
    assert rates.next_boundary(at(13, 15), 300) == at(13, 20)
    assert rates.floor_to_period(at(13, 12, 30), 300) == at(13, 10)
    assert rates.floor_to_period(at(13, 15), 300) == at(13, 15)


# --- Parsing ---------------------------------------------------------------------------------


def test_the_newest_candle_wins_and_prices_never_become_floats():
    """The feed returns candles oldest-first, ending with the one still forming."""
    payload = json.loads(
        json.dumps(
            [
                candle(at(13, 5), "200.4"),
                candle(at(13, 10), "200.2"),
                candle(at(13, 15), "200.3"),
            ]
        ),
        parse_float=Decimal,
    )
    newest = rates.newest_candle(payload, now=at(13, 16))
    assert newest["timestamp"] == at(13, 15).isoformat()

    price = rates.positive_decimal(newest["close"], field="close")
    # The exact published number, not the binary float nearest to it.
    assert price == Decimal("200.3")
    assert str(price) == "200.3"


def test_a_price_that_went_through_the_default_json_parser_is_refused():
    """Belt and braces on the no-float rule: accepting a float here would silently defeat the
    custom parse, and the loss of exactness would be invisible."""
    with pytest.raises(TypeError):
        rates.positive_decimal(200.3, field="close")
    for bad in (None, True, [], {}):
        with pytest.raises(TypeError):
            rates.positive_decimal(bad, field="close")
    for bad in ("0", "-1", "nan"):
        with pytest.raises((ValueError, ArithmeticError)):
            rates.positive_decimal(bad, field="close")


def test_a_stale_or_malformed_candle_list_is_not_a_price():
    fresh = json.loads(json.dumps([candle(at(13, 15), "200.3")]), parse_float=Decimal)
    assert rates.newest_candle(fresh, now=at(13, 16)) is fresh[-1]

    # The feed has a gap: its newest candle is old, and reporting that close as "the price now"
    # would be quietly wrong.
    with pytest.raises(ValueError, match="gap"):
        rates.newest_candle(fresh, now=at(14, 30))
    for bad in ([], {}, "candles", [1], [{"close": 1}]):
        with pytest.raises(ValueError):
            rates.newest_candle(bad, now=at(13, 16))


# --- The cached reader -----------------------------------------------------------------------


def test_one_request_per_candle_window():
    async def scenario():
        feed = Feed(
            [
                [candle(at(13, 10), "200.2"), candle(at(13, 15), "200.3")],
                [candle(at(13, 15), "200.3"), candle(at(13, 20), "201.0")],
            ]
        )
        clock = at(13, 12)
        source = reader(feed, lambda: clock)

        first = await source.tao_usd()
        assert first == rates.TaoUsdQuote(Decimal("200.3"), rates.SOURCE_TAOMARKETCAP)
        assert len(feed.requests) == 1
        # Asked from one period back, so the boundary race cannot return an empty list.
        assert feed.query() == {"period": "5m", "from_timestamp": "2026-08-13T13:05:00"}

        # Still inside the window: served from memory, no second request.
        for minute in (12, 13, 14):
            clock = at(13, minute, 59)
            assert await source.tao_usd() == first
        assert len(feed.requests) == 1

        # 13:15 is the boundary the cache was set to expire on.
        clock = at(13, 15)
        assert (await source.tao_usd()).price == Decimal("201.0")
        assert len(feed.requests) == 2

    run(scenario())


def test_a_failed_refresh_never_caches_null_and_keeps_trying():
    """The rule: a failure must not overwrite a good price, and must not stop the next call trying.

    Caching the failure for a window would refuse purchases for five minutes because one request
    timed out — a far worse trade than an extra request on a rare, deliberate action.
    """

    async def scenario():
        feed = Feed([[candle(at(13, 15), "200.3")], [candle(at(13, 20), "205.0")]])
        clock = at(13, 12)
        source = reader(feed, lambda: clock)

        good = await source.tao_usd()
        assert good.price == Decimal("200.3")

        # Past the boundary, and the upstream is now down.
        clock = at(13, 16)
        feed.fail = httpx.ConnectError("down")
        for expected_calls in (2, 3, 4):
            served = await source.tao_usd()
            # The last good price, still — not None.
            assert served == good
            # And every call retries, because nothing was cached.
            assert len(feed.requests) == expected_calls

        # When it comes back, the fresh price is used and cached again.
        feed.fail = None
        assert (await source.tao_usd()).price == Decimal("205.0")
        assert len(feed.requests) == 5
        clock = at(13, 17)
        assert (await source.tao_usd()).price == Decimal("205.0")
        assert len(feed.requests) == 5

    run(scenario())


def test_with_no_price_ever_a_failure_reports_nothing_and_caches_nothing():
    async def scenario():
        feed = Feed()
        feed.fail = httpx.ConnectError("down")
        clock = at(13, 12)
        source = reader(feed, lambda: clock)

        assert await source.tao_usd() is None
        assert await source.tao_usd() is None
        # Two calls, two attempts: nothing was stored, so nothing was suppressed.
        assert len(feed.requests) == 2

    run(scenario())


def test_a_price_too_old_to_seed_with_stops_being_served():
    """Serving the last good number through an outage is right; serving one from hours ago is not —
    it would burn an invoice on every purchase instead of pricing one."""

    async def scenario():
        feed = Feed([[candle(at(13, 15), "200.3")]])
        clock = at(13, 12)
        source = reader(feed, lambda: clock)
        assert (await source.tao_usd()).price == Decimal("200.3")

        feed.fail = httpx.ConnectError("down")
        clock = at(13, 12) + dt.timedelta(seconds=rates.MAX_STALE_SECONDS - 1)
        assert (await source.tao_usd()).price == Decimal("200.3")
        clock = at(13, 12) + dt.timedelta(seconds=rates.MAX_STALE_SECONDS + 1)
        assert await source.tao_usd() is None

    run(scenario())


def test_an_http_error_or_junk_body_is_not_a_price():
    async def scenario():
        for setup in ("status", "junk", "empty"):
            feed = Feed()
            clock = at(13, 12)
            if setup == "status":
                feed.status = 503
                feed.raw = "nope"
            elif setup == "junk":
                feed.raw = "not json at all"
            else:
                feed.raw = "[]"
            source = reader(feed, lambda: clock)
            assert await source.tao_usd() is None, setup

    run(scenario())


# --- The fallback chain ----------------------------------------------------------------------


def test_the_chain_takes_the_first_source_that_answers_and_says_which():
    async def scenario():
        primary = rates.UnavailableTaoUsdPriceReader()
        secondary = rates.StaticTaoUsdPriceReader(
            Decimal("199.5"), source=rates.SOURCE_TAOSTATS
        )
        chain = rates.FallbackTaoUsdPriceReader.of(primary, secondary)
        quote = await chain.tao_usd()
        # A deployment silently running on its fallback is visible, not merely working.
        assert quote == rates.TaoUsdQuote(Decimal("199.5"), rates.SOURCE_TAOSTATS)

        leading = rates.StaticTaoUsdPriceReader(
            Decimal("200.3"), source=rates.SOURCE_TAOMARKETCAP
        )
        assert (
            await rates.FallbackTaoUsdPriceReader.of(leading, secondary).tao_usd()
        ).source == rates.SOURCE_TAOMARKETCAP

        # Arity is never the caller's problem.
        assert rates.FallbackTaoUsdPriceReader.of(None, secondary) is secondary
        assert await rates.FallbackTaoUsdPriceReader.of(None, None).tao_usd() is None
        assert await rates.FallbackTaoUsdPriceReader.of(primary, primary).tao_usd() is None

    run(scenario())


# --- TMC PAY's own rate table -----------------------------------------------------------------
# The preferred source, so the orientation of its number is the thing most worth pinning down: it
# publishes fiat per crypto unit, which is the reciprocal of an invoice's `exchange_rate`.


def rate_table(tao: str = "191.52707097907592", *, fiat: str = "USD") -> dict:
    """The shape TMC PAY answers `/api/v1/rates` with."""
    return {
        "fiat_currency": fiat,
        # Bare JSON numbers, like the real endpoint, so `parse_float=Decimal` is exercised.
        "rates": {
            "USDC": 1.0,
            "USDT": 1.0,
            "BTC": 64323.85838008977,
            "TAO": float(tao),
        },
    }


def rate_reader(feed: Feed, clock=None, **kwargs) -> rates.TmcPayRatesPriceReader:
    return rates.TmcPayRatesPriceReader(
        base_url="https://pay-api.example.com",
        client=feed.client(),
        now=clock,
        **kwargs,
    )


def test_the_table_is_read_as_dollars_per_tao():
    """191.53 is the price of one TAO, not the TAO one dollar buys.

    Getting this backwards would price a credit at about 36,000 times the intended amount, and
    every downstream check works off the inverted figure, so it is asserted on the raw number.
    """
    feed = Feed([rate_table("191.52707097907592")])
    quote = run(rate_reader(feed).tao_usd())

    assert quote.price == Decimal("191.52707097907592")
    assert quote.source == rates.SOURCE_TMC_PAY
    # A plausibility check that a flipped rate could not pass: one TAO is worth many dollars.
    assert quote.price > 1


def test_the_price_never_becomes_a_float():
    feed = Feed([rate_table("191.52707097907592")])
    quote = run(rate_reader(feed).tao_usd())

    assert isinstance(quote.price, Decimal)
    # The exact published digits, which a binary float would not preserve.
    assert str(quote.price) == "191.52707097907592"


def test_the_requested_fiat_currency_is_sent_and_no_credential_is():
    feed = Feed([rate_table()])
    run(rate_reader(feed).tao_usd())

    request = feed.requests[0]
    assert request.url.path == rates.RATES_PATH
    assert dict(request.url.params) == {"fiat": "USD"}
    # The endpoint is public. Sending the merchant key here would be exposure for nothing.
    assert "x-api-key" not in request.headers
    assert "authorization" not in request.headers


def test_a_table_in_another_currency_is_not_a_price():
    """The `fiat` parameter is optional upstream, so a 200 can still be the wrong currency."""
    feed = Feed([rate_table(fiat="EUR")])
    assert run(rate_reader(feed).tao_usd()) is None


def test_a_table_without_tao_is_not_a_price():
    body = rate_table()
    del body["rates"]["TAO"]
    assert run(rate_reader(Feed([body])).tao_usd()) is None


def test_a_malformed_or_negative_table_is_not_a_price():
    for body in (
        [],
        {"rates": {"TAO": 1.0}},
        {"fiat_currency": "USD"},
        {"fiat_currency": "USD", "rates": []},
        {"fiat_currency": "USD", "rates": {"TAO": 0}},
        {"fiat_currency": "USD", "rates": {"TAO": -5}},
        {"fiat_currency": "USD", "rates": {"TAO": "not a number"}},
    ):
        assert run(rate_reader(Feed([body])).tao_usd()) is None, body


def test_one_request_per_ttl_window():
    moment = at(13, 12)
    feed = Feed([rate_table("191.5"), rate_table("205.0")])
    reader = rate_reader(feed, clock=lambda: moment, ttl_seconds=60)

    assert run(reader.tao_usd()).price == Decimal("191.5")
    assert len(feed.requests) == 1

    # Inside the window: served from the cache, no second request.
    moment = at(13, 12, 59)
    assert run(reader.tao_usd()).price == Decimal("191.5")
    assert len(feed.requests) == 1

    # Past it: refreshed.
    moment = at(13, 13, 1)
    assert run(reader.tao_usd()).price == Decimal("205.0")
    assert len(feed.requests) == 2


def test_a_failed_refresh_serves_the_last_good_rate_and_keeps_trying():
    moment = at(13, 12)
    feed = Feed([rate_table("191.5")])
    reader = rate_reader(feed, clock=lambda: moment, ttl_seconds=60)

    assert run(reader.tao_usd()).price == Decimal("191.5")

    feed.fail = httpx.ConnectError("no route")
    moment = at(13, 20)
    assert run(reader.tao_usd()).price == Decimal("191.5")
    # Nothing was cached, so the call after it tries again rather than waiting out a window.
    before = len(feed.requests)
    assert run(reader.tao_usd()).price == Decimal("191.5")
    assert len(feed.requests) == before + 1


def test_a_rate_too_old_to_seed_with_stops_being_served():
    moment = at(13, 12)
    feed = Feed([rate_table("191.5")])
    reader = rate_reader(feed, clock=lambda: moment, ttl_seconds=60)
    assert run(reader.tao_usd()) is not None

    feed.fail = httpx.ConnectError("no route")
    moment = at(13, 12) + dt.timedelta(seconds=rates.MAX_STALE_SECONDS + 1)
    assert run(reader.tao_usd()) is None


def test_an_http_error_is_not_a_price():
    feed = Feed([rate_table()])
    feed.status = 503
    assert run(rate_reader(feed).tao_usd()) is None


def test_the_reader_refuses_impossible_construction():
    for kwargs in (
        {"base_url": ""},
        {"base_url": "https://x", "ttl_seconds": 0},
        {"base_url": "https://x", "timeout_seconds": 0},
        {"base_url": "https://x", "fiat_currency": ""},
    ):
        with pytest.raises(ValueError):
            rates.TmcPayRatesPriceReader(**kwargs)


# --- The ladder ------------------------------------------------------------------------------


def test_tmc_pay_leads_the_configured_chain():
    reader = rates.build_tao_usd_reader(
        tmc_pay_base_url="https://pay-api.example.com",
        taomarketcap_base_url=rates.TAOMARKETCAP_API_BASE_URL,
        taostats_api_key="",
        taostats_ttl_seconds=60,
    )
    assert isinstance(reader, rates.FallbackTaoUsdPriceReader)
    assert isinstance(reader.readers[0], rates.TmcPayRatesPriceReader)
    assert isinstance(reader.readers[1], rates.TaoMarketCapPriceReader)
    run(reader.aclose())


def test_an_unconfigured_tmc_pay_starts_one_rung_lower():
    reader = rates.build_tao_usd_reader(
        tmc_pay_base_url="",
        taomarketcap_base_url=rates.TAOMARKETCAP_API_BASE_URL,
        taostats_api_key="",
        taostats_ttl_seconds=60,
    )
    assert isinstance(reader, rates.TaoMarketCapPriceReader)
    run(reader.aclose())


def parsed(body: dict) -> object:
    """`body` as the reader sees it: through the parser that keeps numbers exact.

    Not the dict itself. `positive_decimal` rejects `float` on purpose — a float means the value
    went through the default JSON parser and has already lost digits — so a fixture handed over
    directly would be testing the guard rather than the parsing.
    """
    return json.loads(json.dumps(body), parse_float=Decimal)


def test_the_table_parser_is_asserted_directly():
    """Module-level, because which number is the price is what would be wrong silently."""
    table = parsed(rate_table("191.5"))
    assert rates.rate_from_table(
        table, fiat_currency="USD", crypto_currency="TAO"
    ) == Decimal("191.5")
    # Case-insensitive on the currency, because the response's casing is not ours to assume.
    assert rates.rate_from_table(
        parsed({"fiat_currency": "usd", "rates": {"TAO": 191.5}}),
        fiat_currency="USD",
        crypto_currency="TAO",
    ) == Decimal("191.5")
    with pytest.raises(ValueError, match="answered in EUR"):
        rates.rate_from_table(
            parsed(rate_table(fiat="EUR")), fiat_currency="USD", crypto_currency="TAO"
        )
    with pytest.raises(KeyError):
        rates.rate_from_table(table, fiat_currency="USD", crypto_currency="DOGE")


def test_a_rate_that_went_through_the_default_json_parser_is_refused():
    """The same guard the candle feed has: a float here has already lost digits."""
    with pytest.raises(TypeError, match="parse_float=Decimal"):
        rates.rate_from_table(
            {"fiat_currency": "USD", "rates": {"TAO": 191.5}},
            fiat_currency="USD",
            crypto_currency="TAO",
        )
