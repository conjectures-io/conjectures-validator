"""Which competitions this deployment serves.

The routes are `/v1/competitions/{slug}/...` because more competitions are coming --
`conjectures-rust-competition` and `conjectures-rust` are already gates of the same shape --
and a URL that has to change when the second arrives is a migration nobody schedules.

**This deployment serves exactly one.** That is not a placeholder for missing code: the
competition schema has no slug column anywhere. `submissions`, `weight_sets` and
`score_snapshots` all describe one competition, and adding a second needs a migration *and*
an answer to a question this layer cannot invent -- whether one subnet registration buys one
accepted submission per competition or one across all of them. Both are defensible, they pay
miners differently, and guessing would be worse than waiting. So the slug is real, it is
checked, and an unknown one is a 404; what is deferred is the schema, not the interface.

`registry.only()` is deliberately awkward to call for more than one, so the day a second
competition is configured, whoever does it finds this comment rather than a silent mixing of
two competitions' rows in one set of tables.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

# Matches the task-id convention in `routers/web_submissions.py`: lowercase, digits, hyphens,
# starting on an alphanumeric. It is also a path segment, so nothing here can escape one.
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

DEFAULT_SLUG: Final = "miniz-oxide"
DEFAULT_NAME: Final = "miniz_oxide DEFLATE"
# A submission that beats the incumbent's bytes but is slower than this multiple of the
# incumbent's time is refused by the gate, not here. The API only reports it, so a miner can
# see the bar before spending an hour of validator time finding out.
DEFAULT_SPEED_FLOOR: Final = 8.0


class UnknownCompetition(LookupError):
    """No competition with that slug is served here."""


@dataclass(frozen=True, slots=True)
class Competition:
    slug: str
    name: str
    speed_floor: float

    def __post_init__(self) -> None:
        if not SLUG.match(self.slug):
            raise ValueError(f"not a competition slug: {self.slug!r}")


@dataclass(frozen=True, slots=True)
class CompetitionRegistry:
    """The competitions this process serves, by slug."""

    competitions: tuple[Competition, ...] = ()

    @classmethod
    def of(cls, competition: Competition) -> CompetitionRegistry:
        return cls((competition,))

    @classmethod
    def empty(cls) -> CompetitionRegistry:
        """No competitions, which is what a deployment that did not enable them serves."""
        return cls(())

    def get(self, slug: str) -> Competition:
        for competition in self.competitions:
            if competition.slug == slug:
                return competition
        raise UnknownCompetition(slug)

    def only(self) -> Competition:
        """The single competition this deployment serves.

        Raises rather than picking one, because every caller of this is code that will need
        rewriting when a second competition exists -- and a silent `[0]` is how a second
        competition's submissions end up in the first competition's tables.
        """
        if len(self.competitions) != 1:
            raise RuntimeError(
                f"this deployment serves {len(self.competitions)} competitions; "
                "the competition schema carries no slug column, so serving more than one "
                "needs a migration and an entitlement rule first"
            )
        return self.competitions[0]

    def __len__(self) -> int:
        return len(self.competitions)
