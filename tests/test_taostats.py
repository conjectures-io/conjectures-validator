"""TaoStats bounty conversion: exact decimal arithmetic, auth, and outage caching."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

httpx = pytest.importorskip("httpx", reason="TaoStats pricing needs the service extra")

from submission_api.taostats import TaoStatsAlphaUsdPriceReader, amount_usd


def run(coroutine):
    return asyncio.run(coroutine)


def test_taostats_combines_alpha_tao_and_tao_usd_without_float_arithmetic():
    async def scenario():
        calls = []

        def handler(request):
            calls.append(request)
            assert request.headers["authorization"] == "test-api-key"
            if request.url.path == "/api/price/latest/v1":
                assert request.url.params["asset"] == "tao"
                return httpx.Response(200, json={"data": [{"price": "200.00"}]})
            assert request.url.path == "/api/dtao/pool/latest/v1"
            assert request.url.params["netuid"] == "66"
            assert request.url.params["limit"] == "1"
            return httpx.Response(200, json={"data": [{"price": "0.25"}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            reader = TaoStatsAlphaUsdPriceReader(
                api_key="test-api-key",
                netuid=66,
                ttl_seconds=60,
                client=client,
            )
            rate = await reader.alpha_usd()
            assert rate == Decimal("50.0000")
            assert amount_usd(2_000_000_000, alpha_usd=rate) == "100.00"
            # The cached read makes no second pair of upstream calls.
            assert await reader.alpha_usd() == rate
            assert len(calls) == 2
            await reader.aclose()

    run(scenario())


def test_an_unavailable_taostats_result_is_null_and_cached():
    async def scenario():
        calls = 0
        clock = [10.0]

        def handler(request):
            nonlocal calls
            calls += 1
            return httpx.Response(200, json={"data": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            reader = TaoStatsAlphaUsdPriceReader(
                api_key="test-api-key",
                netuid=66,
                ttl_seconds=60,
                client=client,
                monotonic=lambda: clock[0],
            )
            assert await reader.alpha_usd() is None
            assert await reader.alpha_usd() is None
            assert calls == 2

            clock[0] = 71.0
            assert await reader.alpha_usd() is None
            assert calls == 4

    run(scenario())


def test_usd_amount_rounds_to_cents_and_tracks_unavailable_bounties():
    assert amount_usd(1, alpha_usd=Decimal("5000000")) == "0.01"
    assert amount_usd(None, alpha_usd=Decimal("50")) is None
    assert amount_usd(1_000_000_000, alpha_usd=None) is None
