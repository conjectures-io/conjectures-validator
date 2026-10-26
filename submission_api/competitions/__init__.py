"""Which competitions this deployment serves, and the database each one owns.

The routes are `/v1/competitions/{slug}/...` because there is more than one competition, and a
URL that has to change when another arrives is a migration nobody schedules.

**Each competition owns its database.** None of the competition schemas carries a slug column:
DEFLATE's `submissions`, `weight_sets` and `score_snapshots` describe one competition, and so do
FloatLib's. Two competitions therefore never share a database -- `build_services` opens one
engine per competition and refuses a configuration that points two of them, or one of them and
the proofs database, at the same URL. Which database a request reads is decided by the slug in
its path (`dependencies.get_competition_session`), never by a default.

**Each competition owns its routes.** A competition is a `kind`, and the kind decides which
router answers its slug: DEFLATE's are `routers/competition_reads.py`, `competitions.py` and
`competitions_admin.py`; FloatLib's are `routers/floatlib.py`. They are not one generic router
because the two publish different things -- a Pareto frontier with snapshots and admission
records on one side, a king-of-the-hill board per slot on the other -- and the website reads
each in its own shape. A slug of one kind is a 404 on the other kind's routes
(`routers.competitions.resolve_competition`).

FloatLib is one competition with eight boards: its own slug is the paid full entry, and each
single-kernel practice board is `floatlib-<slot>`, all in the one FloatLib database. The registry
resolves every one of those slugs to the FloatLib entry, and `board` says which slot.

Configuration (see `Settings.from_env`):

    COMPETITIONS_ENABLED=1
    COMPETITIONS=deflate,floatlib
    COMPETITION_DEFLATE_DATABASE_URL=postgresql+psycopg://...
    COMPETITION_FLOATLIB_DATABASE_URL=postgresql+psycopg://...

Without `COMPETITIONS`, the deployment serves `COMPETITION_SLUG` alone from
`COMPETITION_DATABASE_URL`, exactly as it did when only one competition existed.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Final, Literal

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

# Matches the task-id convention in `routers/web_submissions.py`: lowercase, digits, hyphens,
# starting on an alphanumeric. It is also a path segment, so nothing here can escape one.
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

DEFAULT_SLUG: Final = "deflate"
DEFAULT_NAME: Final = "DEFLATE"
# A submission that beats the incumbent's bytes but is slower than this multiple of the
# incumbent's time is refused by the gate, not here. The API only reports it, so a miner can
# see the bar before spending an hour of validator time finding out.
DEFAULT_SPEED_FLOOR: Final = 8.0

Kind = Literal["deflate", "floatlib", "torchlean"]
DEFLATE: Final = "deflate"
FLOATLIB: Final = "floatlib"
TORCHLEAN: Final = "torchlean"
TORCHLEAN_NAME: Final = "TorchLean kernels"
KINDS: Final = (DEFLATE, FLOATLIB, TORCHLEAN)

# FloatLib's slug and boards, as `conjectures-optimisation-float-kernels` names them
# (`validator/service/sig.py`): the competition's own slug is the paid full entry, each
# single-kernel practice board `floatlib-<slot>`. A miner signs the slug, so these are wire format.
FLOATLIB_SLUG: Final = "floatlib"
FLOATLIB_NAME: Final = "FloatLib"
FLOATLIB_MAIN_SLOT: Final = "full"
FLOATLIB_PRACTICE_SLOTS: Final = ("add", "mul", "fma", "div", "sqrt", "sum", "bmm")
FLOATLIB_SLOTS: Final = (FLOATLIB_MAIN_SLOT, *FLOATLIB_PRACTICE_SLOTS)


def floatlib_slug(slot: str) -> str:
    """The slug a FloatLib board is served under."""
    return FLOATLIB_SLUG if slot == FLOATLIB_MAIN_SLOT else f"{FLOATLIB_SLUG}-{slot}"


class UnknownCompetition(LookupError):
    """No competition with that slug is served here."""


@dataclass(frozen=True, slots=True)
class Competition:
    slug: str
    name: str
    # DEFLATE's speed floor, which its summary reports. Meaningless for FloatLib.
    speed_floor: float
    kind: Kind = DEFLATE
    # The competition's own database. None where a test injects sessions through the request
    # dependency instead; `dependencies.get_competition_session` then falls back to
    # `Services.competition_sessions`, which such a test sets.
    engine: AsyncEngine | None = field(default=None, compare=False, repr=False)
    sessions: async_sessionmaker[AsyncSession] | None = field(
        default=None, compare=False, repr=False
    )

    def __post_init__(self) -> None:
        if not SLUG.match(self.slug):
            raise ValueError(f"not a competition slug: {self.slug!r}")
        if self.kind not in KINDS:
            raise ValueError(f"not a competition kind: {self.kind!r}")
        if self.kind == TORCHLEAN and self.slug != TORCHLEAN:
            raise ValueError("TorchLean is served as 'torchlean'")
        if self.kind == FLOATLIB and self.slug != FLOATLIB_SLUG:
            # Its practice boards' slugs are derived from this one, and miners sign them.
            raise ValueError(f"FloatLib is served as {FLOATLIB_SLUG!r}, not {self.slug!r}")

    @property
    def boards(self) -> dict[str, str | None]:
        """Every slug this competition answers, and the board each names (None: the only one)."""
        if self.kind == FLOATLIB:
            return {floatlib_slug(slot): slot for slot in FLOATLIB_SLOTS}
        return {self.slug: None}


@dataclass(frozen=True, slots=True)
class CompetitionRegistry:
    """The competitions this process serves, by slug."""

    competitions: tuple[Competition, ...] = ()

    def __post_init__(self) -> None:
        # At construction -- which is to say at startup -- rather than on a request path: two
        # entries answering one slug would make which database a request reads depend on order.
        seen: dict[str, str] = {}
        for competition in self.competitions:
            for slug in competition.boards:
                if (other := seen.get(slug)) is not None:
                    raise ValueError(
                        f"competition slug {slug!r} is served by both {other!r} and "
                        f"{competition.slug!r}"
                    )
                seen[slug] = competition.slug

    @classmethod
    def of(cls, *competitions: Competition) -> CompetitionRegistry:
        return cls(tuple(competitions))

    @classmethod
    def empty(cls) -> CompetitionRegistry:
        """No competitions, which is what a deployment that did not enable them serves."""
        return cls(())

    def board(self, slug: str) -> tuple[Competition, str | None]:
        """The competition a slug names, and which of its boards."""
        for competition in self.competitions:
            if slug in (boards := competition.boards):
                return competition, boards[slug]
        raise UnknownCompetition(slug)

    def get(self, slug: str) -> Competition:
        return self.board(slug)[0]

    def engines(self) -> tuple[AsyncEngine, ...]:
        """Every competition database's engine, once each."""
        return tuple(c.engine for c in self.competitions if c.engine is not None)

    def __iter__(self) -> Iterator[Competition]:
        return iter(self.competitions)

    def __len__(self) -> int:
        return len(self.competitions)
