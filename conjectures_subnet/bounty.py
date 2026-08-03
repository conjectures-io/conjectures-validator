"""Quote a reward once at paid-submission intake.

The API writes the quote and the manual payout instruction copies exactly that frozen value.
Pricing is therefore shared infrastructure rather than an API or wallet detail.
The current implementation is deliberately flat; a future dynamic policy can
read treasury state through the supplied session without changing the durable
submission contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class FrozenBounty:
    """One direct-TAO payout quote, expressed in integer rao."""

    amount_rao: int
    policy_version: str
    inputs: dict[str, Any] | None = None


class BountyPricer(Protocol):
    async def quote(self, session: AsyncSession, *, task_id: str) -> FrozenBounty:
        """Return the quote to freeze on one new submission."""


@dataclass(frozen=True)
class FlatBountyPricer:
    """A configured constant until a treasury-aware pricing policy replaces it."""

    amount_rao: int
    policy_version: str

    async def quote(self, session: AsyncSession, *, task_id: str) -> FrozenBounty:
        del session, task_id
        return FrozenBounty(
            amount_rao=self.amount_rao,
            policy_version=self.policy_version,
        )


__all__ = ["BountyPricer", "FlatBountyPricer", "FrozenBounty"]
