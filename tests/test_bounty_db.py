"""The new age curve against persisted task dates and changing catalogs."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("bittensor")
pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from conftest_api import harness, postgres_dsn
from conjectures_subnet.bounty import StaticBalanceReader
from conjectures_subnet.db.models import BountyTask

pytestmark = pytest.mark.skipif(postgres_dsn() is None, reason="test PostgreSQL unavailable")


def test_catalog_changes_and_repins_do_not_change_a_targets_share_or_opening_date():
    async def scenario():
        kit = await harness(BOUNTY_POOL_BALANCE_RAO="90000000000").setup()
        try:
            opened = datetime(2026, 1, 1, tzinfo=UTC)
            original = replace(kit.services.pricing, clock=lambda: opened)
            target = original.reward_target_ids[0]
            async with kit.session() as session:
                assert (
                    await original.quote(session, reward_target_id=target)
                ).amount_rao == 2_250_000_000
                await session.commit()

            later = opened + timedelta(days=5, hours=12)
            aged = replace(original, clock=lambda: later)
            expanded = replace(
                aged,
                reward_target_ids=(*original.reward_target_ids, *(f"new-{i}" for i in range(20))),
            )
            async with kit.session() as session:
                before = await aged.quote(session, reward_target_id=target)
                after = await expanded.quote(session, reward_target_id=target)
                assert before.amount_rao == after.amount_rao == 5_550_000_000
                assert after.inputs["age_seconds"] == 475200
                assert after.inputs["ramp_seconds"] == 1296000
                assert before.inputs["open_targets"] != after.inputs["open_targets"]
                assert before.inputs["total_age_weight"] != after.inputs["total_age_weight"]
                assert after.inputs["opened_at"] == opened.isoformat()
                # A new pricer/catalog reuses the stable target's first opening date.
                persisted = await session.get(BountyTask, target)
                assert persisted.opened_at == opened
                await session.commit()

            # Removing the added targets or doubling the treasury has the expected isolated effect.
            async with kit.session() as session:
                assert (
                    await aged.quote(session, reward_target_id=target)
                ).amount_rao == 5_550_000_000
                funded = replace(aged, balance_reader=StaticBalanceReader(180_000_000_000))
                assert (
                    await funded.quote(session, reward_target_id=target)
                ).amount_rao == 11_100_000_000
                mature = replace(aged, clock=lambda: opened + timedelta(days=15))
                assert (
                    await mature.quote(session, reward_target_id=target)
                ).amount_rao == 11_250_000_000
        finally:
            await kit.teardown()

    asyncio.run(scenario())


def test_a_future_opening_date_uses_the_starting_share():
    async def scenario():
        kit = await harness().setup()
        try:
            now = datetime(2026, 1, 1, tzinfo=UTC)
            pricer = replace(kit.services.pricing, clock=lambda: now)
            target = pricer.reward_target_ids[0]
            async with kit.session() as session:
                session.add(BountyTask(reward_target_id=target, opened_at=now + timedelta(days=1), pricing_policy="legacy"))
                await session.commit()
                quote = await pricer.quote(session, reward_target_id=target)
                assert quote.amount_rao == 200_000_000
                assert quote.inputs["age_seconds"] == 0
        finally:
            await kit.teardown()

    asyncio.run(scenario())


def test_a_target_keeps_the_starting_share_it_opened_with():
    async def scenario():
        kit = await harness(BOUNTY_POOL_BALANCE_RAO="80000000000").setup()
        try:
            now = datetime(2026, 1, 1, tzinfo=UTC)
            pricer = replace(kit.services.pricing, clock=lambda: now)
            existing = pricer.reward_target_ids[0]
            new = "fc-target:Fixture.new"
            async with kit.session() as session:
                # A target already open before V044 carries the 1/20 backfill.
                session.add(BountyTask(reward_target_id=existing, opened_at=now))
                await session.commit()
                expanded = replace(pricer, reward_target_ids=(existing, new))
                snapshot = await expanded.quote_many(session, reward_target_ids=(existing, new))
                assert snapshot.quotes[existing].amount_rao == 4_000_000_000
                assert snapshot.quotes[existing].inputs["constant_denominator"] == 20
                assert snapshot.quotes[new].amount_rao == 2_000_000_000
                assert snapshot.quotes[new].inputs["constant_denominator"] == 40
                await session.commit()

                # A later configuration change applies only to targets opened after it.
                changed = replace(expanded, constant_denominator=10)
                assert (await changed.quote(session, reward_target_id=new)).amount_rao == 2_000_000_000
                persisted = await session.get(BountyTask, new)
                assert (persisted.start_share_numerator, persisted.start_share_denominator) == (1, 40)
        finally:
            await kit.teardown()

    asyncio.run(scenario())
