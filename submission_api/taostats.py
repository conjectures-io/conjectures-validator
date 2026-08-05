"""TaoStats-backed USD display prices for Subnet Alpha bounties.

``amount_rao`` on a bounty is nano-Alpha, not TAO rao. Turning it into dollars therefore
needs two independently moving prices: the configured subnet's Alpha price in TAO and TAO's
price in USD. TaoStats publishes both as decimal strings. They stay decimals here too; this
module never turns a financial value into a binary float.

USD is display metadata, not part of the bounty policy. A failed external request therefore
returns no USD quote instead of making an otherwise valid catalog or submission response fail.
The result (including an unavailable result) is cached so a TaoStats outage cannot turn public
catalog traffic into an outbound request storm.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Protocol

import httpx

RAO_PER_ALPHA = Decimal(1_000_000_000)
USD_CENT = Decimal("0.01")

TAOSTATS_TAO_PRICE_URL = "https://api.taostats.io/api/price/latest/v1"
TAOSTATS_SUBNET_POOL_URL = "https://api.taostats.io/api/dtao/pool/latest/v1"

logger = logging.getLogger(__name__)


class AlphaUsdPriceReader(Protocol):
    """A cached source for the current USD price of one whole Subnet Alpha."""

    async def alpha_usd(self) -> Decimal | None:
        """Return dollars per Alpha, or none when no trustworthy quote is available."""
        ...

    async def aclose(self) -> None:
        """Release any network resources held by the reader."""
        ...


@dataclass(frozen=True)
class UnavailableAlphaUsdPriceReader:
    """The explicit result when no TaoStats API key has been configured."""

    async def alpha_usd(self) -> None:
        return None

    async def aclose(self) -> None:
        return None


@dataclass(frozen=True)
class StaticAlphaUsdPriceReader:
    """A deterministic rate source for tests and local callers."""

    price: Decimal

    async def alpha_usd(self) -> Decimal:
        return self.price

    async def aclose(self) -> None:
        return None


def amount_usd(amount_rao: int | None, *, alpha_usd: Decimal | None) -> str | None:
    """Render nano-Alpha at ``alpha_usd`` to the nearest cent without a float."""
    if amount_rao is None or alpha_usd is None:
        return None
    if amount_rao < 0:
        raise ValueError("a bounty amount cannot be negative")
    if not alpha_usd.is_finite() or alpha_usd <= 0:
        raise ValueError("the Alpha USD price must be finite and positive")
    dollars = Decimal(amount_rao) * alpha_usd / RAO_PER_ALPHA
    return f"{dollars.quantize(USD_CENT, rounding=ROUND_HALF_UP):.2f}"


class TaoStatsAlphaUsdPriceReader:
    """Combine TaoStats' Subnet Alpha/TAO and TAO/USD feeds into one cached rate."""

    def __init__(
        self,
        *,
        api_key: str,
        netuid: int,
        ttl_seconds: int,
        timeout_seconds: float = 5.0,
        client: httpx.AsyncClient | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not api_key:
            raise ValueError("a TaoStats API key is required")
        if netuid <= 0:
            raise ValueError("the bounty netuid must be positive")
        if ttl_seconds <= 0:
            raise ValueError("the TaoStats cache duration must be positive")
        if timeout_seconds <= 0:
            raise ValueError("the TaoStats timeout must be positive")

        self._api_key = api_key
        self._netuid = netuid
        self._ttl_seconds = ttl_seconds
        self._monotonic = monotonic
        self._client = client or httpx.AsyncClient(
            timeout=timeout_seconds,
            headers={
                "accept": "application/json",
                "User-Agent": "conjectures-validator/0.1",
            },
        )
        self._owns_client = client is None
        self._lock = asyncio.Lock()
        self._cached: Decimal | None = None
        self._has_cached = False
        self._expires_at = 0.0

    async def alpha_usd(self) -> Decimal | None:
        now = self._monotonic()
        if self._has_cached and now < self._expires_at:
            return self._cached

        # One outbound pair per process and cache window even when a page of bounties arrives
        # concurrently. The second check matters for callers that waited on the lock.
        async with self._lock:
            now = self._monotonic()
            if self._has_cached and now < self._expires_at:
                return self._cached
            try:
                price = await self._fetch()
            except (httpx.HTTPError, InvalidOperation, KeyError, TypeError, ValueError) as exc:
                logger.warning(
                    "TaoStats USD bounty price is unavailable: %s",
                    exc,
                    extra={"netuid": self._netuid},
                )
                price = None
            self._cached = price
            self._has_cached = True
            self._expires_at = self._monotonic() + self._ttl_seconds
            return price

    async def _fetch(self) -> Decimal:
        headers = {"Authorization": self._api_key}
        tao_response, pool_response = await asyncio.gather(
            self._client.get(
                TAOSTATS_TAO_PRICE_URL,
                params={"asset": "tao"},
                headers=headers,
            ),
            self._client.get(
                TAOSTATS_SUBNET_POOL_URL,
                params={"netuid": self._netuid, "limit": 1},
                headers=headers,
            ),
        )
        tao_response.raise_for_status()
        pool_response.raise_for_status()

        tao_usd = _positive_decimal(_one_record(tao_response)["price"], field="TAO price")
        alpha_tao = _positive_decimal(
            _one_record(pool_response)["price"], field="Subnet Alpha price"
        )
        return alpha_tao * tao_usd

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _one_record(response: httpx.Response) -> Mapping[str, object]:
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise ValueError("TaoStats returned a non-object response")
    records = payload.get("data")
    if not isinstance(records, list) or len(records) != 1:
        raise ValueError("TaoStats returned no unique price record")
    record = records[0]
    if not isinstance(record, Mapping):
        raise ValueError("TaoStats returned a malformed price record")
    return record


def _positive_decimal(value: object, *, field: str) -> Decimal:
    price = Decimal(str(value))
    if not price.is_finite() or price <= 0:
        raise ValueError(f"TaoStats {field} is not finite and positive")
    return price


__all__ = [
    "AlphaUsdPriceReader",
    "StaticAlphaUsdPriceReader",
    "TaoStatsAlphaUsdPriceReader",
    "UnavailableAlphaUsdPriceReader",
    "amount_usd",
]
