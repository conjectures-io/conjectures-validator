"""Live, linear-age bounty pricing with submission-time amount locks.

Catalog prices remain live estimates. Once a paid submission is accepted, its quote is immutable:
later balance and age changes affect new submissions only. Several proofs may still compete for
one target, so the promise is conditional on verification and reward eligibility rather than an
exclusive claim merely for arriving first.

For every currently open reward target ``i`` the capped policy is::

    t_i = min(age_i_seconds, ramp_seconds)
    b_i = floor(B * (c + (m - c) * t_i / ramp_seconds))

``B`` is the live treasury balance minus outstanding locked exposure. The starting share ``c``
defaults to ``1/10``, the maximum share ``m`` to ``1/6``, and the ramp to 15 elapsed days.
Only the target's own age affects its share; catalog size and other targets' ages do not.
Arithmetic is integer-only, with one final floor to base units. Catalog timestamps retain their
minute precision, so quotes progress within each day without daily weight jumps. Legacy age
weights remain available as descriptive API metadata, but never enter this pricing formula.

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
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast

import bittensor as bt
from sqlalchemy import exists, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from conjectures_subnet.db.models import (
    BountyTask,
    ManualReviewState,
    PayoutState,
    RewardEvent,
    RewardState,
    Submission,
    VerificationState,
)

# Submission writers serialize only the short quote-and-insert transaction. Public catalog reads
# do not take this lock. The integer is stable across processes and deployments.
BOUNTY_RESERVATION_ADVISORY_LOCK = 0x434F4E4A425459
LINEAR_BOUNTY_POLICY_VERSION = "linear-age-v3-locked"
DEFAULT_RAMP_SECONDS = 15 * 86_400


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
    ramp_seconds: int
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

    async def lock_quote(
        self,
        session: AsyncSession,
        *,
        reward_target_id: str,
    ) -> LiveBounty:
        """Serialize a fresh quote until the caller commits its new submission."""
        ...


@dataclass(frozen=True)
class DynamicBountyPricer:
    balance_reader: BalanceReader
    balance_coldkey: str
    balance_hotkey: str
    balance_netuid: int
    reward_target_ids: tuple[str, ...]
    policy_version: str = LINEAR_BOUNTY_POLICY_VERSION
    constant_numerator: int = 1
    constant_denominator: int = 10
    ramp_seconds: int = DEFAULT_RAMP_SECONDS
    age_period_seconds: int = 86_400
    max_age_weight: int = 60
    max_bounty_share_numerator: int = 1
    max_bounty_share_denominator: int = 6
    confirmed_payout_grace_seconds: int = 60
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)

    def __post_init__(self) -> None:
        if not self.balance_coldkey or not self.balance_hotkey:
            raise ValueError("bounty balance coldkey and hotkey must not be empty")
        if self.balance_netuid <= 0:
            raise ValueError("bounty balance netuid must be positive")
        if self.constant_numerator <= 0 or self.constant_denominator <= 0:
            raise ValueError("bounty constant must be a positive rational")
        if self.ramp_seconds <= 0:
            raise ValueError("bounty ramp must be positive")
        if self.policy_version.startswith("dynamic-age-"):
            raise ValueError("linear pricing requires a new bounty policy version")
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
        if (
            self.constant_numerator * self.max_bounty_share_denominator
            > self.max_bounty_share_numerator * self.constant_denominator
        ):
            raise ValueError("starting bounty share cannot exceed the maximum share")
        if self.confirmed_payout_grace_seconds <= 0:
            raise ValueError("confirmed payout grace period must be positive")
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

    async def lock_quote(
        self,
        session: AsyncSession,
        *,
        reward_target_id: str,
    ) -> LiveBounty:
        """Take the pool's transaction lock and price the amount a submission will retain.

        The lock lasts until the API commits or rolls back. Concurrent submissions therefore see
        the earlier lock in their committed exposure rather than both spending the same remainder.
        """
        await session.execute(
            select(func.pg_advisory_xact_lock(BOUNTY_RESERVATION_ADVISORY_LOCK))
        )
        return await self.quote(session, reward_target_id=reward_target_id)

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
        # the linear age policy and keeps otherwise identical reads cacheable, while a
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
        gross_balance = await self.balance_reader.balance_rao()
        if gross_balance < 0:
            raise RuntimeError("treasury balance cannot be negative")

        # Competing submissions for one target are mutually exclusive, so that target's exposure
        # is the largest still-live locked amount, not the sum of every attempt. Rejected proofs
        # release their lock. Confirmed payouts are already reflected by the finalized chain
        # balance and must not be subtracted a second time.
        settled_payout = exists(
            select(RewardEvent.id).where(
                RewardEvent.submission_id == Submission.id,
                RewardEvent.status == PayoutState.CONFIRMED,
                RewardEvent.chain_observed.is_(True),
                # Keep the commitment until any cached pre-transfer chain balance has expired.
                # Once both facts are current, excluding it avoids subtracting a paid reward
                # twice: once here and once from the now-lower on-chain balance.
                RewardEvent.confirmed_at
                <= now - timedelta(seconds=self.confirmed_payout_grace_seconds),
            )
        ).correlate(Submission)
        reservations = (
            await session.execute(
                select(
                    Submission.reward_target_id,
                    func.max(Submission.bounty_amount_rao),
                )
                .where(
                    Submission.bounty_locked_at.is_not(None),
                    Submission.verification_status != VerificationState.REJECTED,
                    Submission.manual_review_status != ManualReviewState.REJECTED,
                    ~settled_payout,
                )
                .group_by(Submission.reward_target_id)
            )
        ).all()
        committed = sum(int(row[1]) for row in reservations)
        balance = max(0, gross_balance - committed)

        quotes: dict[str, LiveBounty] = {}
        for target in requested:
            base_inputs: dict[str, int | str] = {
                "balance_rao": balance,
                "treasury_balance_rao": gross_balance,
                "committed_bounty_rao": committed,
                "available_balance_rao": balance,
                "balance_asset": "alpha",
                "balance_coldkey": self.balance_coldkey,
                "balance_hotkey": self.balance_hotkey,
                "balance_netuid": self.balance_netuid,
                "constant_denominator": self.constant_denominator,
                "constant_numerator": self.constant_numerator,
                "ramp_seconds": self.ramp_seconds,
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
            age_seconds = max(0, int((now - opened_at[target]).total_seconds()))
            amount = calculate_bounty_rao(
                balance_rao=balance,
                age_seconds=age_seconds,
                ramp_seconds=self.ramp_seconds,
                constant_numerator=self.constant_numerator,
                constant_denominator=self.constant_denominator,
                max_bounty_share_numerator=self.max_bounty_share_numerator,
                max_bounty_share_denominator=self.max_bounty_share_denominator,
            )
            quotes[target] = LiveBounty(
                amount_rao=amount,
                policy_version=self.policy_version,
                available=amount > 0,
                reason=(
                    "TREASURY_RESERVED"
                    if amount <= 0
                    else ("OPEN" if holder is None else "CLAIM_HELD")
                ),
                as_of=now,
                inputs={
                    **base_inputs,
                    "age_weight": weight,
                    "age_seconds": age_seconds,
                    "opened_at": opened_at[target].isoformat(),
                },
            )

        return BountyPoolSnapshot(
            quotes=quotes,
            policy_version=self.policy_version,
            balance_rao=gross_balance,
            wallet_coldkey=self.balance_coldkey,
            wallet_hotkey=self.balance_hotkey,
            netuid=self.balance_netuid,
            asset="alpha",
            open_targets=len(open_targets),
            total_age_weight=total_weight,
            as_of=now,
            constant_numerator=self.constant_numerator,
            constant_denominator=self.constant_denominator,
            ramp_seconds=self.ramp_seconds,
            max_age_weight=self.max_age_weight,
            max_bounty_share_numerator=self.max_bounty_share_numerator,
            max_bounty_share_denominator=self.max_bounty_share_denominator,
        )


def calculate_bounty_rao(
    *,
    balance_rao: int,
    age_seconds: int,
    ramp_seconds: int = DEFAULT_RAMP_SECONDS,
    constant_numerator: int = 1,
    constant_denominator: int = 10,
    max_bounty_share_numerator: int = 1,
    max_bounty_share_denominator: int = 6,
) -> int:
    """Interpolate from the starting share to the cap, with one final integer floor."""
    values = (
        ramp_seconds,
        constant_numerator,
        constant_denominator,
        max_bounty_share_numerator,
        max_bounty_share_denominator,
    )
    if balance_rao < 0 or age_seconds < 0 or any(value <= 0 for value in values):
        raise ValueError("balance and age must be non-negative and pricing inputs positive")
    if max_bounty_share_numerator > max_bounty_share_denominator:
        raise ValueError("maximum bounty share cannot exceed the treasury")
    start = constant_numerator * max_bounty_share_denominator
    maximum = max_bounty_share_numerator * constant_denominator
    if start > maximum:
        raise ValueError("starting bounty share cannot exceed the maximum share")
    elapsed = min(age_seconds, ramp_seconds)
    numerator = balance_rao * (start * ramp_seconds + (maximum - start) * elapsed)
    denominator = constant_denominator * max_bounty_share_denominator * ramp_seconds
    return numerator // denominator


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
