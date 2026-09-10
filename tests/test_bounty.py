"""Pure dynamic-bounty arithmetic and chain-read caching."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

pytest.importorskip("bittensor", reason="dynamic bounty pricing needs the subnet extra")
pytest.importorskip("sqlalchemy", reason="the pricing component owns DB task ages")

from conjectures_subnet.bounty import (
    BittensorBalanceReader,
    CachedBalanceReader,
    calculate_age_weight,
    calculate_bounty_rao,
)


@pytest.mark.parametrize(
    ("age_seconds", "expected"),
    [
        (0, 9_000_000_000),
        (5 * 86400, 11_000_000_000),
        (int(7.5 * 86400), 12_000_000_000),
        (10 * 86400, 13_000_000_000),
        (15 * 86400, 15_000_000_000),
        (365 * 86400, 15_000_000_000),
    ],
)
def test_linear_ramp_reaches_one_sixth_after_fifteen_days(age_seconds, expected):
    assert calculate_bounty_rao(balance_rao=90_000_000_000, age_seconds=age_seconds) == expected


def test_ramp_progresses_within_a_day_and_caps_at_the_exact_boundary():
    # This balance makes each second of the ramp worth one base unit.
    balance = 19_440_000
    assert calculate_bounty_rao(balance_rao=balance, age_seconds=1) == 1_944_001
    assert calculate_bounty_rao(balance_rao=balance, age_seconds=1295999) == 3_239_999
    assert calculate_bounty_rao(balance_rao=balance, age_seconds=1296000) == 3_240_000
    assert calculate_bounty_rao(balance_rao=balance, age_seconds=1296001) == 3_240_000


def test_round_only_after_combining_start_and_age_increment():
    assert calculate_bounty_rao(balance_rao=19, age_seconds=648000) == 2
    assert calculate_bounty_rao(balance_rao=19, age_seconds=1296000) == 3
    assert calculate_bounty_rao(balance_rao=0, age_seconds=1296000) == 0
    assert calculate_bounty_rao(balance_rao=5, age_seconds=1296000) == 0
    assert calculate_bounty_rao(balance_rao=2**63 - 1, age_seconds=1296000) == (2**63 - 1) // 6


def test_configured_shares_and_duration_are_used():
    assert (
        calculate_bounty_rao(
            balance_rao=1000,
            age_seconds=5,
            ramp_seconds=10,
            constant_numerator=1,
            constant_denominator=20,
            max_bounty_share_numerator=1,
            max_bounty_share_denominator=4,
        )
        == 150
    )
    assert (
        calculate_bounty_rao(
            balance_rao=1000,
            age_seconds=5,
            ramp_seconds=10,
            constant_numerator=1,
            constant_denominator=6,
        )
        == 166
    )


@pytest.mark.parametrize(
    "arguments",
    [
        {"balance_rao": -1},
        {"age_seconds": -1},
        {"ramp_seconds": 0},
        {"constant_numerator": 0},
        {"constant_denominator": 0},
        {"max_bounty_share_numerator": 0},
        {"max_bounty_share_denominator": 0},
        {"max_bounty_share_numerator": 7},
        {"constant_denominator": 4},
    ],
)
def test_invalid_pricing_inputs_are_refused(arguments):
    with pytest.raises(ValueError):
        calculate_bounty_rao(**{"balance_rao": 1000, "age_seconds": 0, **arguments})


def test_legacy_age_metadata_still_caps_at_sixty():
    opened_at = datetime(2026, 1, 1, tzinfo=UTC)
    assert (
        calculate_age_weight(
            opened_at,
            now=opened_at + timedelta(days=500),
            period_seconds=86400,
        )
        == 60
    )


def test_concurrent_quotes_share_one_balance_read():
    class Reader:
        calls = 0

        async def balance_rao(self) -> int:
            self.calls += 1
            await asyncio.sleep(0)
            return 123

    async def scenario():
        reader = Reader()
        clock = iter((0.0, 0.0, 0.0, 0.0, 0.0)).__next__
        cached = CachedBalanceReader(reader, ttl_seconds=60, monotonic=clock)
        assert await asyncio.gather(cached.balance_rao(), cached.balance_rao()) == [123, 123]
        assert reader.calls == 1

    asyncio.run(scenario())


def test_chain_balance_is_read_at_a_finalized_block(monkeypatch):
    observed: dict[str, object] = {}

    class Staking:
        async def get(self, coldkey: str, hotkey: str, netuid: int, block: int):
            observed.update(
                coldkey=coldkey,
                hotkey=hotkey,
                netuid=netuid,
                block=block,
            )
            return SimpleNamespace(rao=987_654_321)

    class Client:
        staking = Staking()

        async def blocks(self, *, finalized: bool):
            observed["finalized"] = finalized
            yield SimpleNamespace(number=42)

    class Context:
        async def __aenter__(self):
            return Client()

        async def __aexit__(self, *_):
            return None

    def subtensor(network: str):
        observed["network"] = network
        return Context()

    monkeypatch.setattr("conjectures_subnet.bounty.bt.Subtensor", subtensor)
    amount = asyncio.run(
        BittensorBalanceReader(
            network="test",
            coldkey="5" * 48,
            hotkey="6" * 48,
            netuid=66,
        ).balance_rao()
    )

    assert amount == 987_654_321
    assert observed == {
        "network": "test",
        "finalized": True,
        "coldkey": "5" * 48,
        "hotkey": "6" * 48,
        "netuid": 66,
        "block": 42,
    }
