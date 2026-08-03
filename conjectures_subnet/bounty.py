"""What a submission will be paid, quoted once at intake.

Shared rather than API-local: the API writes the quote, and the payout component pays against
it. Both need the same idea of what a quote is, and neither may decide the amount on its own.

The quote is frozen on the submission row and returned to the miner with the submission id, so a
miner learns what the proof is worth when it is accepted rather than at payout. That makes this a
money-facing decision with two consequences:

* **Quote once, then never recompute.** The rule reads state that moves — funds collected, task
  age — so re-running it later answers a different question. `submissions.bounty_amount_rao` is
  the answer of record; nothing downstream reprices.
* **Record why.** `policy_version` names the rule and `inputs` carries what it read, because
  "why is this one 4.1 and that one 6.3" is a question the validator has to be able to answer
  for a submission accepted several rule revisions ago.

Every accepted submission accrues a liability the treasury has to be able to cover. The sum of
frozen amounts over submissions not yet paid or failed is that liability, and a rule that reads
the pool without subtracting it will over-promise. `FlatBountyPricer` cannot subtract it: a fixed
amount times an unbounded queue is unbounded, so until the real rule lands that risk sits with
the operator who sets the constant.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class FrozenBounty:
    """One quote, about to be written to the submission it belongs to."""

    amount_rao: int  # alpha base units, not the TAO rao a miner pays with
    policy_version: str
    inputs: dict[str, Any] | None = None


class BountyPricer(Protocol):
    async def quote(self, session: AsyncSession, *, task_id: str) -> FrozenBounty:
        """Price one submission for this task. Takes a session: the real rule reads state."""
        ...


@dataclass(frozen=True)
class FlatBountyPricer:
    """A configured constant, until the dynamic rule exists.

    Deliberately not a stand-in that guesses. It records its own `policy_version`, so
    submissions quoted under it stay distinguishable from ones priced by whatever replaces it,
    and the switch needs no backfill.
    """

    amount_rao: int
    policy_version: str

    async def quote(self, session: AsyncSession, *, task_id: str) -> FrozenBounty:
        del session, task_id  # a constant depends on neither
        return FrozenBounty(
            amount_rao=self.amount_rao, policy_version=self.policy_version
        )


__all__ = ["BountyPricer", "FlatBountyPricer", "FrozenBounty"]
