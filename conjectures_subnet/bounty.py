"""Live, age-weighted bounty pricing.

Prices are estimates until a payout instruction is created.  In particular, accepting a paid
submission does not reserve either a reward target or an amount: another proof may establish the
same target first, and the wallet balance and task ages continue to move while verification is in
flight.

For every currently open reward target ``i`` the capped policy is::

    w_i = min(w_max, 1 + floor(age_i / age_period))
    b_i = min(c * B * N * w_i / W, m * B)

where ``B`` is the live treasury balance, ``N`` is the number of open reward targets, ``w_i`` is
the target's age weight, ``W`` is the sum of all open weights, and ``c`` defaults to ``1/4``.  This
is the integer form of ``c * B * w_i / w_avg``. ``w_max`` defaults to ``60`` and ``m`` defaults
to ``33/100``, so no one target can quote more than 33% of the treasury. Integer division rounds
down, so pricing never creates a fractional base unit or exceeds either cap.

Task age is database-owned.  The first API process to see a reward target inserts it into
``bounty_tasks``; later catalog repins reuse that original ``opened_at`` through the stable
``reward_target_id``.  A target stops participating as soon as a submission holds its unique reward
claim (``reward_status <> INELIGIBLE``).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, cast

import bittensor as bt
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from conjectures_subnet.db.models import BountyTask, RewardState, Submission


@dataclass(frozen=True)
class LiveBounty:
    """One live answer from the pricing policy."""

    amount_rao: int | None
    policy_version: str
    available: bool
    reason: str
    as_of: datetime
    inputs: dict[str, int | str]


@dataclass(frozen=True)
class BountyPoolSnapshot:
    """A consistent set of quotes produced from one balance and one clock reading."""

    quotes: Mapping[str, LiveBounty]
    policy_version: str
    balance_rao: int
    wallet_coldkey: str
    wallet_hotkey: str
    netuid: int
    asset: str
    open_targets: int
    total_age_weight: int
    as_of: datetime
    constant_numerator: int
    constant_denominator: int
    max_age_weight: int
    max_bounty_share_numerator: int
    max_bounty_share_denominator: int


class BalanceReader(Protocol):
    async def balance_rao(self) -> int:
        """Return the configured bounty pool balance in integer base units."""
        ...


@dataclass(frozen=True)
class StaticBalanceReader:
    """A deterministic balance source for development and tests."""

    amount_rao: int

    async def balance_rao(self) -> int:
        return self.amount_rao


@dataclass(frozen=True)
class BittensorBalanceReader:
    """Read one wallet's finalized subnet-Alpha stake without loading signing keys."""

    network: str
    coldkey: str
    hotkey: str
    netuid: int

    async def balance_rao(self) -> int:
        async with bt.Subtensor(self.network) as client:
            finalized_blocks = cast(
                AsyncGenerator[bt.BlockHeader, None], client.blocks(finalized=True)
            )
            try:
                block = (await anext(finalized_blocks)).number
            finally:
                await finalized_blocks.aclose()
            balance = await client.staking.get(
                self.coldkey,
                self.hotkey,
                self.netuid,
                block=block,
            )
        return int(balance.rao)


class CachedBalanceReader:
    """Bound public-request traffic to at most one chain read per cache window."""

    def __init__(
        self,
        reader: BalanceReader,
        *,
        ttl_seconds: int,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("balance cache duration must be positive")
        self._reader = reader
        self._ttl_seconds = ttl_seconds
        self._monotonic = monotonic
        self._cached: int | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def balance_rao(self) -> int:
        now = self._monotonic()
        if self._cached is not None and now < self._expires_at:
            return self._cached
        async with self._lock:
            now = self._monotonic()
            if self._cached is not None and now < self._expires_at:
                return self._cached
            amount = await self._reader.balance_rao()
            if amount < 0:
                raise RuntimeError("treasury balance cannot be negative")
            self._cached = amount
            self._expires_at = now + self._ttl_seconds
            return amount


class BountyPricer(Protocol):
    async def quote(
        self,
        session: AsyncSession,
        *,
        reward_target_id: str,
        claimant_id: uuid.UUID | None = None,
    ) -> LiveBounty:
        """Price one target now; ``claimant_id`` keeps its own won target visible."""
        ...

    async def quote_many(
        self,
        session: AsyncSession,
        *,
        reward_target_ids: Sequence[str],
        claimants: Mapping[str, uuid.UUID] | None = None,
    ) -> BountyPoolSnapshot:
        """Price several targets against one consistent pool snapshot."""
        ...


@dataclass(frozen=True)
class DynamicBountyPricer:
    balance_reader: BalanceReader
    balance_coldkey: str
    balance_hotkey: str
    balance_netuid: int
    reward_target_ids: tuple[str, ...]
    policy_version: str = "dynamic-age-v2-capped"
    constant_numerator: int = 1
    constant_denominator: int = 4
    age_period_seconds: int = 86_400
    max_age_weight: int = 60
    max_bounty_share_numerator: int = 33
    max_bounty_share_denominator: int = 100
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)

    def __post_init__(self) -> None:
        if not self.balance_coldkey or not self.balance_hotkey:
            raise ValueError("bounty balance coldkey and hotkey must not be empty")
        if self.balance_netuid <= 0:
            raise ValueError("bounty balance netuid must be positive")
        if self.constant_numerator <= 0 or self.constant_denominator <= 0:
            raise ValueError("bounty constant must be a positive rational")
        if self.age_period_seconds <= 0:
            raise ValueError("age period must be positive")
        if self.max_age_weight <= 0:
            raise ValueError("maximum age weight must be positive")
        if (
            self.max_bounty_share_numerator <= 0
            or self.max_bounty_share_denominator <= 0
            or self.max_bounty_share_numerator > self.max_bounty_share_denominator
        ):
            raise ValueError("maximum bounty share must be in the interval (0, 1]")
        if not self.reward_target_ids:
            raise ValueError("dynamic pricing requires at least one reward target")
        if len(set(self.reward_target_ids)) != len(self.reward_target_ids):
            raise ValueError("reward targets must be unique")

    async def quote(
        self,
        session: AsyncSession,
        *,
        reward_target_id: str,
        claimant_id: uuid.UUID | None = None,
    ) -> LiveBounty:
        claimants = (
            {reward_target_id: claimant_id} if claimant_id is not None else None
        )
        snapshot = await self.quote_many(
            session,
            reward_target_ids=(reward_target_id,),
            claimants=claimants,
        )
        return snapshot.quotes[reward_target_id]

    async def quote_many(
        self,
        session: AsyncSession,
        *,
        reward_target_ids: Sequence[str],
        claimants: Mapping[str, uuid.UUID] | None = None,
    ) -> BountyPoolSnapshot:
        requested = tuple(dict.fromkeys(reward_target_ids))
        configured = set(self.reward_target_ids)
        now = self.clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        # Public catalog responses carry a strong ETag. Minute precision is enough to explain
        # a daily age-weight policy and keeps otherwise identical reads cacheable, while a
        # changed balance or solved-target set still changes the response immediately.
        now = now.astimezone(UTC).replace(second=0, microsecond=0)

        await session.execute(
            pg_insert(BountyTask)
            .values(
                [
                    {"reward_target_id": target, "opened_at": now}
                    for target in self.reward_target_ids
                ]
            )
            .on_conflict_do_nothing(index_elements=[BountyTask.reward_target_id])
        )
        opened_rows = (
            await session.execute(
                select(BountyTask.reward_target_id, BountyTask.opened_at).where(
                    BountyTask.reward_target_id.in_(self.reward_target_ids)
                )
            )
        ).all()
        opened_at = {str(row[0]): _utc(row[1]) for row in opened_rows}

        holder_rows = (
            await session.execute(
                select(Submission.reward_target_id, Submission.id).where(
                    Submission.reward_target_id.in_(self.reward_target_ids),
                    Submission.reward_status != RewardState.INELIGIBLE,
                )
            )
        ).all()
        holders = {str(row[0]): row[1] for row in holder_rows}
        claimant_map = dict(claimants or {})

        open_targets = [
            target
            for target in self.reward_target_ids
            if target not in holders or claimant_map.get(target) == holders[target]
        ]
        weights = {
            target: calculate_age_weight(
                opened_at[target],
                now=now,
                period_seconds=self.age_period_seconds,
                max_age_weight=self.max_age_weight,
            )
            for target in open_targets
        }
        total_weight = sum(weights.values())
        balance = await self.balance_reader.balance_rao()
        if balance < 0:
            raise RuntimeError("treasury balance cannot be negative")

        quotes: dict[str, LiveBounty] = {}
        for target in requested:
            base_inputs: dict[str, int | str] = {
                "balance_rao": balance,
                "balance_asset": "alpha",
                "balance_coldkey": self.balance_coldkey,
                "balance_hotkey": self.balance_hotkey,
                "balance_netuid": self.balance_netuid,
                "constant_denominator": self.constant_denominator,
                "constant_numerator": self.constant_numerator,
                "max_age_weight": self.max_age_weight,
                "max_bounty_share_denominator": self.max_bounty_share_denominator,
                "max_bounty_share_numerator": self.max_bounty_share_numerator,
                "max_bounty_rao": (
                    self.max_bounty_share_numerator
                    * balance
                    // self.max_bounty_share_denominator
                ),
                "open_targets": len(open_targets),
                "total_age_weight": total_weight,
            }
            if target not in configured:
                quotes[target] = LiveBounty(
                    amount_rao=None,
                    policy_version=self.policy_version,
                    available=False,
                    reason="NOT_IN_BOUNTY_POOL",
                    as_of=now,
                    inputs=base_inputs,
                )
                continue
            holder = holders.get(target)
            if holder is not None and claimant_map.get(target) != holder:
                quotes[target] = LiveBounty(
                    amount_rao=None,
                    policy_version=self.policy_version,
                    available=False,
                    reason="ALREADY_SOLVED",
                    as_of=now,
                    inputs=base_inputs,
                )
                continue

            weight = weights[target]
            amount = calculate_bounty_rao(
                balance_rao=balance,
                open_targets=len(open_targets),
                task_age_weight=weight,
                total_age_weight=total_weight,
                constant_numerator=self.constant_numerator,
                constant_denominator=self.constant_denominator,
                max_bounty_share_numerator=self.max_bounty_share_numerator,
                max_bounty_share_denominator=self.max_bounty_share_denominator,
            )
            quotes[target] = LiveBounty(
                amount_rao=amount,
                policy_version=self.policy_version,
                available=True,
                reason="OPEN" if holder is None else "CLAIM_HELD",
                as_of=now,
                inputs={
                    **base_inputs,
                    "age_weight": weight,
                    "opened_at": opened_at[target].isoformat(),
                },
            )

        return BountyPoolSnapshot(
            quotes=quotes,
            policy_version=self.policy_version,
            balance_rao=balance,
            wallet_coldkey=self.balance_coldkey,
            wallet_hotkey=self.balance_hotkey,
            netuid=self.balance_netuid,
            asset="alpha",
            open_targets=len(open_targets),
            total_age_weight=total_weight,
            as_of=now,
            constant_numerator=self.constant_numerator,
            constant_denominator=self.constant_denominator,
            max_age_weight=self.max_age_weight,
            max_bounty_share_numerator=self.max_bounty_share_numerator,
            max_bounty_share_denominator=self.max_bounty_share_denominator,
        )


def calculate_bounty_rao(
    *,
    balance_rao: int,
    open_targets: int,
    task_age_weight: int,
    total_age_weight: int,
    constant_numerator: int = 1,
    constant_denominator: int = 4,
    max_bounty_share_numerator: int = 33,
    max_bounty_share_denominator: int = 100,
) -> int:
    """Evaluate the weighted quote and treasury-share cap, rounding down to base units."""
    values = (
        balance_rao,
        open_targets,
        task_age_weight,
        total_age_weight,
        constant_numerator,
        constant_denominator,
        max_bounty_share_numerator,
        max_bounty_share_denominator,
    )
    if balance_rao < 0 or any(value <= 0 for value in values[1:]):
        raise ValueError("balance must be non-negative and pricing inputs positive")
    if max_bounty_share_numerator > max_bounty_share_denominator:
        raise ValueError("maximum bounty share cannot exceed the treasury")
    numerator = constant_numerator * balance_rao * open_targets * task_age_weight
    denominator = constant_denominator * total_age_weight
    weighted_amount = numerator // denominator
    maximum_amount = (
        max_bounty_share_numerator * balance_rao // max_bounty_share_denominator
    )
    return min(weighted_amount, maximum_amount)


def calculate_age_weight(
    opened_at: datetime,
    *,
    now: datetime,
    period_seconds: int,
    max_age_weight: int = 60,
) -> int:
    """Return the linear age weight, capped at the policy maximum."""
    if period_seconds <= 0 or max_age_weight <= 0:
        raise ValueError("age period and maximum age weight must be positive")
    age_seconds = max(0, int((now - _utc(opened_at)).total_seconds()))
    return min(max_age_weight, 1 + age_seconds // period_seconds)


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


__all__ = [
    "BalanceReader",
    "BittensorBalanceReader",
    "BountyPoolSnapshot",
    "BountyPricer",
    "CachedBalanceReader",
    "DynamicBountyPricer",
    "LiveBounty",
    "StaticBalanceReader",
    "calculate_age_weight",
    "calculate_bounty_rao",
]
