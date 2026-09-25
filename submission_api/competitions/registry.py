"""Which competitions this deployment serves, and the database engine each one owns.

One engine per competition, built at startup beside the proofs engine and disposed by the same
lifespan. Never shared, never the proofs one: each competition's schema belongs to the
competition's own repository and migrates there, and nothing joins across the two. A handler
that needs both -- the session submit, which checks an account in the proofs database and then
writes a submission into the competition's -- does two units of work, and is written that way.

Configuration is two environment variables per competition:

    COMPETITIONS=lz77
    COMPETITION_LZ77_DATABASE_URL=postgresql+psycopg://...

and the slug must name an adapter in `catalog.ADAPTERS`. Adding a competition is an adapter
module and one line there; removing one is deleting the line. Nothing in the router, the
schemas or the proofs database changes either way.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from submission_api.competitions.base import CompetitionAdapter, CompetitionInfo

SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


class UnknownCompetition(LookupError):
    """No competition with this slug is served here."""


class CompetitionConfigError(ValueError):
    """The competition configuration cannot be served as given."""


def database_url_variable(slug: str) -> str:
    """The environment variable holding `slug`'s database URL: `rust-comp` -> `..._RUST_COMP_...`."""
    return f"COMPETITION_{slug.upper().replace('-', '_')}_DATABASE_URL"


@dataclass(frozen=True, slots=True)
class CompetitionConfig:
    slug: str
    database_url: str

    def __post_init__(self) -> None:
        if not SLUG.match(self.slug):
            raise CompetitionConfigError(f"not a competition slug: {self.slug!r}")
        if not self.database_url:
            raise CompetitionConfigError(
                f"{database_url_variable(self.slug)} is required to serve {self.slug!r}"
            )


@dataclass(frozen=True, slots=True)
class Competition:
    adapter: CompetitionAdapter
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]

    @property
    def slug(self) -> str:
        return self.adapter.info.slug

    @property
    def info(self) -> CompetitionInfo:
        return self.adapter.info

    async def ping(self) -> bool:
        async with self.engine.connect() as conn:
            return (await conn.execute(text("SELECT 1"))).scalar_one() == 1


class CompetitionRegistry:
    """The competitions served here, in configured order."""

    def __init__(self, competitions: Iterable[Competition] = ()) -> None:
        self._by_slug: dict[str, Competition] = {}
        for competition in competitions:
            if competition.slug in self._by_slug:
                raise CompetitionConfigError(f"competition {competition.slug!r} is listed twice")
            self._by_slug[competition.slug] = competition

    @classmethod
    def empty(cls) -> CompetitionRegistry:
        return cls()

    def get(self, slug: str) -> Competition:
        try:
            return self._by_slug[slug]
        except KeyError:
            raise UnknownCompetition(slug) from None

    def __iter__(self) -> Iterator[Competition]:
        return iter(self._by_slug.values())

    def __len__(self) -> int:
        return len(self._by_slug)

    async def dispose(self) -> None:
        for competition in self:
            await competition.engine.dispose()


def build_registry(
    configs: Iterable[CompetitionConfig],
    *,
    proofs_database_url: str,
    adapters: dict[str, Callable[[], CompetitionAdapter]],
    create_engine: Callable[[str], AsyncEngine],
    session_factory: Callable[[AsyncEngine], async_sessionmaker[AsyncSession]],
) -> CompetitionRegistry:
    """Open one engine per configured competition, refusing anything that would share one.

    Two refusals keep "each competition owns its database" true at runtime rather than by
    convention: a competition URL equal to the proofs one would put a competition's writes into
    the database Flyway owns, and two competitions on one URL would mix two schemas' rows in
    tables that carry no competition column.
    """
    configs = tuple(configs)
    seen: dict[str, str] = {}
    for config in configs:
        if config.slug not in adapters:
            raise CompetitionConfigError(
                f"no adapter for competition {config.slug!r}; known: {', '.join(sorted(adapters))}"
            )
        if config.database_url == proofs_database_url:
            raise CompetitionConfigError(
                f"{database_url_variable(config.slug)} resolves to the proofs database; "
                "each competition must have its own"
            )
        if (other := seen.get(config.database_url)) is not None:
            raise CompetitionConfigError(
                f"{config.slug!r} and {other!r} are configured with the same database"
            )
        seen[config.database_url] = config.slug

    competitions = []
    for config in configs:
        adapter = adapters[config.slug]()
        if adapter.info.slug != config.slug:
            raise CompetitionConfigError(
                f"adapter for {config.slug!r} describes itself as {adapter.info.slug!r}"
            )
        engine = create_engine(config.database_url)
        competitions.append(
            Competition(adapter=adapter, engine=engine, sessions=session_factory(engine))
        )
    return CompetitionRegistry(competitions)


__all__ = [
    "SLUG",
    "Competition",
    "CompetitionConfig",
    "CompetitionConfigError",
    "CompetitionRegistry",
    "UnknownCompetition",
    "build_registry",
    "database_url_variable",
]
