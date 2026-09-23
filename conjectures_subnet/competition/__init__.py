"""The competition store: a second database, one Store, four repositories.

    from conjectures_subnet.competition import connect
    store = connect()
    store.submissions.add(hotkey, digest)
    store.registrations.available_slots(hotkey)

This is the proof-gated competitions' durable state, and it lives in its own database
rather than beside `conjectures_subnet.db`. Two reasons, both structural:

* These models are the source of truth and Alembic generates the deploy path from them,
  while the proofs schema is hand-written SQL applied by Flyway with a hand-maintained ORM
  mirror. Two migration tools sharing one database each read the other's objects as drift.
* V035 retired the miner hotkey in the proofs database and enforces it with triggers, while
  a competition entitlement is inherently per-hotkey -- one subnet registration buys one
  accepted submission. A trigger does not reach across a database boundary, so neither model
  has to bend to the other.

What that costs, and what every caller must respect: PostgreSQL has no cross-database
foreign key and no cross-database transaction. A row here names an account by a plain
`account_id` that the API is responsible for, and no unit of work may assume this database
and the proofs one moved together.

`Base` is this package's own declarative base, deliberately NOT
`conjectures_subnet.db.models.Base`: sharing one would put both schemas in a single
`metadata`, and `create_all` and the drift checks would then mix two catalogs.

The connection plumbing is borrowed rather than copied -- `session_factory` and
`session_scope` come from `conjectures_subnet.db.engine`, which already has exactly the
semantics these repositories want. Only the URL differs, and that is what
`competition_database_url` is for. (`autoflush=False` there is immaterial here: every
repository is Core-style `session.execute`, with no `session.add` to flush.)
"""

from __future__ import annotations

from sqlalchemy import Engine, text

from conjectures_subnet.db.engine import (
    competition_database_url,
    create_db_engine,
    session_factory,
    session_scope,
)

from .adapters import DatabaseSnapshotSink
from .clock import iso, now
from .models import Base
from .ratelimit import RateLimiter
from .registrations import NoSlot, RegistrationsDb
from .scoring import ScoredSubmission, ScoringDb
from .status import PENDING, TERMINAL, SubmissionState
from .submissions import SubmissionsDb

__all__ = [
    "PENDING",
    "TERMINAL",
    "Base",
    "DatabaseSnapshotSink",
    "NoSlot",
    "RateLimiter",
    "RegistrationsDb",
    "ScoredSubmission",
    "ScoringDb",
    "Store",
    "SubmissionState",
    "SubmissionsDb",
    "competition_database_url",
    "connect",
    "iso",
    "now",
    "session_factory",
    "session_scope",
]

# Defaults for the write limiter; the API overrides them from its settings.
RATE_LIMIT = 10
RATE_WINDOW_SECONDS = 60


class Store:
    """One engine per process, handed around. The repositories hold only the factory."""

    def __init__(
        self,
        engine: Engine,
        *,
        rate_limit: int = RATE_LIMIT,
        rate_window_seconds: int = RATE_WINDOW_SECONDS,
    ) -> None:
        self.engine = engine
        self.sessions = session_factory(engine)
        self.submissions = SubmissionsDb(self.sessions)
        self.registrations = RegistrationsDb(self.sessions)
        self.scoring = ScoringDb(self.sessions)
        self.rate = RateLimiter(
            self.sessions, limit=rate_limit, window_seconds=rate_window_seconds
        )

    def ping(self) -> bool:
        """Is the database actually reachable? `/readyz` must fail when the process is
        alive but cannot serve."""
        with self.engine.connect() as conn:
            return conn.execute(text("SELECT 1")).scalar_one() == 1

    def close(self) -> None:
        self.engine.dispose()


def connect(url: str | None = None, *, echo: bool = False, **kwargs: object) -> Store:
    """Open the store against `url`, or the competition database the environment names.

    Note the default: `competition_database_url()`, never `database_url()`. A caller that
    passes nothing must reach the competition database, not the proofs one.
    """
    return Store(
        create_db_engine(url or competition_database_url(), echo=echo),
        **kwargs,  # pyright: ignore[reportArgumentType]
    )
