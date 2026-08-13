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


def test_an_average_age_task_is_not_divided_by_the_number_of_tasks():
    for tasks in (1, 4, 74, 10_000):
        assert calculate_bounty_rao(
            balance_rao=4_000_000_000,
            open_targets=tasks,
            task_age_weight=7,
            total_age_weight=7 * tasks,
        ) == 1_000_000_000


def test_bounties_follow_the_ratio_to_average_age_weight():
    # Weights 1, 2, 3 average to 2. With c*B = 1 Alpha, prices are .5, 1, 1.5 Alpha.
    assert [
        calculate_bounty_rao(
            balance_rao=4_000_000_000,
            open_targets=3,
            task_age_weight=weight,
            total_age_weight=6,
            max_bounty_share_numerator=1,
            max_bounty_share_denominator=1,
        )
        for weight in (1, 2, 3)
    ] == [500_000_000, 1_000_000_000, 1_500_000_000]


def test_one_bounty_is_capped_at_33_percent_of_the_treasury():
    assert calculate_bounty_rao(
        balance_rao=4_000_000_000,
        open_targets=3,
        task_age_weight=3,
        total_age_weight=6,
    ) == 1_320_000_000


def test_age_weight_is_capped_at_60():
    opened_at = datetime(2026, 1, 1, tzinfo=UTC)
    assert calculate_age_weight(
        opened_at,
        now=opened_at + timedelta(days=58),
        period_seconds=86_400,
    ) == 59
    assert calculate_age_weight(
        opened_at,
        now=opened_at + timedelta(days=500),
        period_seconds=86_400,
    ) == 60


def test_fractional_base_units_round_down():
    assert calculate_bounty_rao(
        balance_rao=11,
        open_targets=3,
        task_age_weight=2,
        total_age_weight=7,
    ) == 2


@pytest.mark.parametrize(
    "arguments",
    [
        {"balance_rao": -1, "open_targets": 1, "task_age_weight": 1, "total_age_weight": 1},
        {"balance_rao": 1, "open_targets": 0, "task_age_weight": 1, "total_age_weight": 1},
        {"balance_rao": 1, "open_targets": 1, "task_age_weight": 0, "total_age_weight": 1},
        {"balance_rao": 1, "open_targets": 1, "task_age_weight": 1, "total_age_weight": 0},
    ],
)
def test_invalid_pool_inputs_are_refused(arguments):
    with pytest.raises(ValueError):
        calculate_bounty_rao(**arguments)


def test_a_bounty_share_above_the_whole_treasury_is_refused():
    with pytest.raises(ValueError, match="cannot exceed"):
        calculate_bounty_rao(
            balance_rao=1,
            open_targets=1,
            task_age_weight=1,
            total_age_weight=1,
            max_bounty_share_numerator=101,
            max_bounty_share_denominator=100,
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
