"""Authoritative payout-time pricing for fixed-USD review awards."""

from __future__ import annotations

import datetime as dt
import json
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

RAO_PER_ALPHA = Decimal(1_000_000_000)
FORMALIZATION_DEFECT_AWARD_USD = Decimal("750.00")
FORMALIZATION_DEFECT_POLICY_VERSION = "formalization-defect-usd-v1"

TAOSTATS_TAO_PRICE_URL = "https://api.taostats.io/api/price/latest/v1"
TAOSTATS_SUBNET_POOL_URL = "https://api.taostats.io/api/dtao/pool/latest/v1"


@dataclass(frozen=True)
class DefectAwardQuote:
    """The integer payout and the complete inputs needed to reproduce it."""

    amount_rao: int
    pricing_inputs: dict[str, object]


def quote_formalization_defect_award(
    *,
    api_key: str,
    netuid: int,
    timeout_seconds: float = 10.0,
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> DefectAwardQuote:
    """Convert the fixed $750 award with the bounty system's TaoStats feeds.

    TaoStats publishes Alpha/TAO and TAO/USD as decimal strings. Keeping the complete
    calculation in ``Decimal`` makes the integer rao amount reproducible and matches the
    rounding convention already present on historical defect-award payout records.
    """
    if not api_key:
        raise ValueError("a TaoStats API key is required for a defect award")
    if netuid <= 0:
        raise ValueError("the bounty netuid must be positive")
    if timeout_seconds <= 0:
        raise ValueError("the TaoStats timeout must be positive")

    tao = _get_one_record(
        TAOSTATS_TAO_PRICE_URL,
        params={"asset": "tao"},
        api_key=api_key,
        timeout_seconds=timeout_seconds,
    )
    pool = _get_one_record(
        TAOSTATS_SUBNET_POOL_URL,
        params={"netuid": netuid, "limit": 1},
        api_key=api_key,
        timeout_seconds=timeout_seconds,
    )
    tao_usd = _positive_decimal(tao["price"], field="TAO price")
    alpha_tao = _positive_decimal(pool["price"], field="Subnet Alpha price")
    alpha_usd = alpha_tao * tao_usd
    amount_rao = int(
        (FORMALIZATION_DEFECT_AWARD_USD * RAO_PER_ALPHA / alpha_usd).quantize(
            Decimal(1), rounding=ROUND_HALF_UP
        )
    )
    observed_at = now().astimezone(dt.UTC).isoformat()
    return DefectAwardQuote(
        amount_rao=amount_rao,
        pricing_inputs={
            "award_usd": f"{FORMALIZATION_DEFECT_AWARD_USD:.2f}",
            "alpha_usd": str(alpha_usd),
            "price_source": "TaoStats Alpha/TAO multiplied by TaoStats TAO/USD",
            "price_source_urls": [
                TAOSTATS_SUBNET_POOL_URL,
                TAOSTATS_TAO_PRICE_URL,
            ],
            "price_observed_at": observed_at,
            "netuid": netuid,
            "calculation": "750 * 1000000000 / alpha_usd",
            "rounding": "ROUND_HALF_UP to nearest integer Alpha rao",
        },
    )


def _get_one_record(
    url: str,
    *,
    params: Mapping[str, object],
    api_key: str,
    timeout_seconds: float,
) -> Mapping[str, object]:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"{url}?{query}",
        headers={
            "accept": "application/json",
            "Authorization": api_key,
            "User-Agent": "conjectures-payout-notifier/1",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read())
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
    try:
        price = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"TaoStats {field} is not a decimal") from exc
    if not price.is_finite() or price <= 0:
        raise ValueError(f"TaoStats {field} is not finite and positive")
    return price


__all__ = [
    "DefectAwardQuote",
    "FORMALIZATION_DEFECT_POLICY_VERSION",
    "quote_formalization_defect_award",
]
