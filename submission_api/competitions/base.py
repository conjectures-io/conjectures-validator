"""The contract between the API and one competition.

The API serves every competition through one router (`routers/competitions.py`) and knows
nothing about any of them beyond what this module defines. A competition is an *adapter*: a
class that answers the questions below against the competition's own database, through an
async session the registry opened on that database's own engine.

What the API owns, and no adapter repeats:

* the URL shape, `/v1/competitions/{slug}/...`, and the response envelopes;
* authentication -- the hotkey signature and the browser session -- and the pause switch;
* paging, cursor signing and the rate limit;
* which database each competition lives in: one engine per competition, never the proofs one.

What each adapter owns, because only the competition knows it:

* its schema, which lives and migrates in the competition's own repository. The adapter maps
  only the columns it reads or writes, and a contract test checks them against a migrated
  copy of the real schema;
* what a submission is made of (`files`) and what makes two of them the same (`digest`);
* who may submit and how often: registrations and entitlements are competition rules;
* how the board is ranked, and which measurements are worth publishing (`metrics`).

Everything crossing this boundary is a plain frozen dataclass, never an ORM row, so the router
cannot reach into a competition's schema by accident and an adapter's schema can change without
the router noticing.

Optional capabilities -- published sources, the latest score snapshot, the operator queue --
default to raising `Unsupported`, which the router answers with a 404. A competition that
lacks one simply does not override it.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession


class SubmissionState(StrEnum):
    """The lifecycle every competition reports, whatever its own column holds.

    Adapters translate into these. Five states because every proof-gated competition so far has
    the same shape -- a queue, a gate run, a verdict, and a validator-side failure that is not
    the miner's fault -- and a client should be able to render any competition's feed without
    learning its vocabulary.
    """

    QUEUED = "queued"
    VERIFYING = "verifying"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    ERROR = "error"


STATE_VALUES: tuple[str, ...] = tuple(state.value for state in SubmissionState)


class Unsupported(Exception):
    """This competition does not offer the capability that was asked for."""


class NotRegistered(Exception):
    """The hotkey has never been registered where this competition admits submitters."""


class NoEntitlement(Exception):
    """The hotkey is registered but has nothing left to spend on another submission."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# ── descriptors: what a client needs to render a competition it has never seen ─────────────


@dataclass(frozen=True, slots=True)
class FileSpec:
    """One file a submission must carry, as a multipart part of exactly this name."""

    name: str
    max_bytes: int
    description: str = ""


@dataclass(frozen=True, slots=True)
class Metric:
    """One published measurement. `key` is what appears in every `metrics` mapping."""

    key: str
    label: str
    unit: str = ""
    # Which direction is an improvement, so a client can colour a delta without knowing
    # what the number means.
    better: Literal["lower", "higher"] = "lower"


@dataclass(frozen=True, slots=True)
class CompetitionInfo:
    slug: str
    name: str
    description: str
    files: tuple[FileSpec, ...]
    metrics: tuple[Metric, ...]
    # The metric the leaderboard is ordered by. Must be one of `metrics`.
    ranked_by: str

    def __post_init__(self) -> None:
        if not self.files:
            raise ValueError(f"{self.slug}: a competition must declare at least one file")
        names = [f.name for f in self.files]
        if len(set(names)) != len(names):
            raise ValueError(f"{self.slug}: file names must be unique")
        if self.ranked_by not in {m.key for m in self.metrics}:
            raise ValueError(f"{self.slug}: ranked_by {self.ranked_by!r} is not a metric")


# ── records: what an adapter returns ──────────────────────────────────────────────────────

MetricValue = int | float | None
# Opaque, adapter-chosen keyset position. The router signs it into a cursor and hands it back
# unchanged; only the adapter that issued it interprets it. Each part must match
# `[A-Za-z0-9_-]+` (see `submission_api.pagination.encode_parts`).
Position = tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Submission:
    id: str
    hotkey: str | None
    digest: str
    state: SubmissionState
    submitted_at: datetime
    finished_at: datetime | None
    metrics: Mapping[str, MetricValue]
    # Where the next page of a newest-first feed starts if this is the last row of one.
    position: Position


@dataclass(frozen=True, slots=True)
class Standing:
    """One row of the board: a hotkey's best accepted submission."""

    hotkey: str
    submission_id: str
    submitted_at: datetime
    metrics: Mapping[str, MetricValue]
    position: Position


@dataclass(frozen=True, slots=True)
class Report:
    id: str
    state: SubmissionState
    exit_code: int | None
    text: str | None


@dataclass(frozen=True, slots=True)
class Eligibility:
    registered: bool
    # How many more this hotkey could queue right now. None when the competition does not
    # ration submissions.
    slots_remaining: int | None
    pending: int


@dataclass(frozen=True, slots=True)
class Queued:
    submission: Submission
    # False when these exact files from this hotkey were already queued, and this is that row.
    created: bool
    eligibility: Eligibility


@dataclass(frozen=True, slots=True)
class Stats:
    by_state: Mapping[str, int]
    competitors: int
    last_accepted_at: datetime | None
    best: Mapping[str, MetricValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ScoreEntry:
    hotkey: str | None
    submission_id: str | None
    weight: float
    metrics: Mapping[str, MetricValue]
    note: str | None = None


@dataclass(frozen=True, slots=True)
class Scores:
    """The competition's most recent scoring pass, as it recorded it."""

    computed_at: datetime
    block: int | None
    dry_run: bool
    accepted: bool
    summary: str | None
    entries: tuple[ScoreEntry, ...]


@dataclass(frozen=True, slots=True)
class OperatorView:
    submission: Submission
    worker_id: str | None
    claimed_at: datetime | None
    exit_code: int | None
    has_files: bool
    report: str | None


# ── the adapter ───────────────────────────────────────────────────────────────────────────


class CompetitionAdapter(ABC):
    """One competition, as the API sees it. See the module docstring."""

    info: CompetitionInfo

    # -- identity ------------------------------------------------------------------------

    def digest(self, files: Mapping[str, bytes]) -> str:
        """What a submission *is*: sha256 over the declared files, in declared order.

        Also its identity -- the same files from the same hotkey are the same submission, which
        is what makes a retry idempotent. Override only if the competition's own store already
        defines the digest differently; the hotkey signs this value, so it is wire format.
        """
        hasher = hashlib.sha256()
        for spec in self.info.files:
            hasher.update(files[spec.name])
        return hasher.hexdigest()

    def parse_id(self, raw: str) -> str | None:
        """Normalise a submission id from a URL, or None if it cannot name a submission here."""
        return raw if raw else None

    # -- public reads --------------------------------------------------------------------

    @abstractmethod
    async def headline(self, session: AsyncSession) -> Mapping[str, MetricValue]:
        """The numbers a competition's card shows: the bar to beat, the floor to clear."""

    @abstractmethod
    async def queue_depth(self, session: AsyncSession) -> int: ...

    @abstractmethod
    async def leaderboard(
        self, session: AsyncSession, *, after: Position | None, limit: int
    ) -> Sequence[Standing]: ...

    @abstractmethod
    async def rank_before(self, session: AsyncSession, after: Position) -> int:
        """How many standings sort ahead of and including `after`: the offset of the next page."""

    @abstractmethod
    async def submissions(
        self,
        session: AsyncSession,
        *,
        after: Position | None,
        limit: int,
        state: SubmissionState | None = None,
        hotkeys: Sequence[str] | None = None,
    ) -> Sequence[Submission]:
        """Newest first. `hotkeys`, when given, restricts to those submitters (possibly none)."""

    @abstractmethod
    async def submission(self, session: AsyncSession, submission_id: str) -> Submission | None: ...

    @abstractmethod
    async def report(self, session: AsyncSession, submission_id: str) -> Report | None: ...

    @abstractmethod
    async def stats(self, session: AsyncSession) -> Stats: ...

    @abstractmethod
    async def best_for(self, session: AsyncSession, hotkey: str) -> Standing | None: ...

    # -- who may submit ------------------------------------------------------------------

    @abstractmethod
    async def eligibility(self, session: AsyncSession, hotkey: str) -> Eligibility: ...

    @abstractmethod
    async def registered_by(self, session: AsyncSession, *, hotkey: str, coldkey: str) -> bool:
        """Whether `coldkey` is on record as having registered `hotkey`."""

    @abstractmethod
    async def hotkeys_of(self, session: AsyncSession, coldkey: str) -> Sequence[str]:
        """Every hotkey `coldkey` is on record as having registered."""

    # -- writing -------------------------------------------------------------------------

    @abstractmethod
    async def queue(
        self,
        session: AsyncSession,
        *,
        hotkey: str,
        digest: str,
        files: Mapping[str, bytes],
    ) -> Queued:
        """Queue a submission, or return the one these files already are.

        Idempotency first, entitlement second: a retry of a submission that is already queued
        must return it rather than be told its own slot is spent. Raises `NotRegistered` or
        `NoEntitlement`. Commits.
        """

    # -- optional capabilities -----------------------------------------------------------

    async def files(self, session: AsyncSession, submission_id: str) -> Mapping[str, bytes]:
        """An accepted submission's files, by declared name. Raise `LookupError` if absent."""
        raise Unsupported("this competition does not publish sources")

    async def scores(self, session: AsyncSession) -> Scores | None:
        raise Unsupported("this competition does not publish scores")

    async def stuck(
        self,
        session: AsyncSession,
        *,
        claimed_before: datetime,
        after: Position | None,
        limit: int,
    ) -> Sequence[OperatorView]:
        raise Unsupported("this competition has no operator queue")

    async def operator_view(
        self, session: AsyncSession, submission_id: str
    ) -> OperatorView | None:
        raise Unsupported("this competition has no operator queue")

    async def requeue(self, session: AsyncSession, submission_id: str) -> bool:
        """Put a stuck submission back in the queue. False if its state does not permit it."""
        raise Unsupported("this competition has no operator queue")


__all__ = [
    "STATE_VALUES",
    "CompetitionAdapter",
    "CompetitionInfo",
    "Eligibility",
    "FileSpec",
    "Metric",
    "MetricValue",
    "NoEntitlement",
    "NotRegistered",
    "OperatorView",
    "Position",
    "Queued",
    "Report",
    "ScoreEntry",
    "Scores",
    "Standing",
    "Stats",
    "Submission",
    "SubmissionState",
    "Unsupported",
]
