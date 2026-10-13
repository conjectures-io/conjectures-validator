"""Contract tests for the supported Taostats feeds; no live service or credentials."""

import io
import json
from decimal import Decimal, ROUND_HALF_UP
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest

from payout_notifier.pricing import quote_formalization_defect_award


def test_supported_feeds_preserve_fixed_usd_rounding_and_audit_urls(monkeypatch):
    calls = []

    def urlopen(request, *, timeout):
        url = urlsplit(request.full_url)
        calls.append(url.path)
        assert request.get_header("Authorization") == "fixture-key"
        assert request.get_header("User-agent")
        assert timeout == 7
        if url.path == "/v1/price":
            assert parse_qs(url.query) == {"asset": ["tao"]}
            row = {"symbol": "TAO", "price": "281.050562235749"}
        else:
            assert url.path == "/v1/subnets/pools"
            assert parse_qs(url.query) == {"netuid": ["66"], "limit": ["1"]}
            row = {"netuid": 66, "price": "0.002932651"}
        return io.BytesIO(json.dumps({"data": [row], "pagination": {"next_page": None}}).encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    quote = quote_formalization_defect_award(api_key="fixture-key", netuid=66, timeout_seconds=7)
    expected = Decimal("750") * 10**9 / (Decimal("281.050562235749") * Decimal("0.002932651"))
    assert quote.amount_rao == int(expected.quantize(Decimal(1), rounding=ROUND_HALF_UP))
    assert calls == ["/v1/price", "/v1/subnets/pools"]
    assert quote.pricing_inputs["price_source_urls"] == [
        "https://api.taostats.io/v1/subnets/pools", "https://api.taostats.io/v1/price"
    ]


@pytest.mark.parametrize("payload", [
    {}, [], {"data": []}, {"data": {}}, {"data": [{}, {}]}, {"data": [None]},
    {"data": [{}]}, *({"data": [{"price": value}]} for value in
                      [None, "bad", "NaN", "Infinity", "0", "-1"]),
])
@pytest.mark.parametrize("failed_leg", [0, 1])
def test_invalid_quote_never_produces_an_award(monkeypatch, payload, failed_leg):
    responses = [{"data": [{"price": "200"}]}, {"data": [{"price": "0.003"}]}]
    responses[failed_leg] = payload
    iterator = iter(responses)
    monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k:
                        io.BytesIO(json.dumps(next(iterator)).encode()))
    with pytest.raises((ValueError, KeyError)):
        quote_formalization_defect_award(api_key="fixture-key", netuid=66)


@pytest.mark.parametrize("error", [
    TimeoutError(), URLError("unavailable"),
    HTTPError("https://api.taostats.io/v1/price", 429, "rate limited", {}, None),
    HTTPError("https://api.taostats.io/v1/price", 503, "unavailable", {}, None),
])
def test_transport_failures_propagate_to_worker_boundary(monkeypatch, error):
    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr("urllib.request.urlopen", fail)
    with pytest.raises(type(error)):
        quote_formalization_defect_award(api_key="fixture-key", netuid=66)
