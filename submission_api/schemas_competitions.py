"""Response models for the competition surface.

Separate from `schemas.py` (the miner's proof-submission surface) and `schemas_public.py` (the
website's read surface) for the same reason those two are separate from each other: three
contracts with three audiences, and one module would let a field added for one appear in the
others.

Generic across competitions. Nothing here names a competition's measurements: a competition
publishes them as `metrics`, keyed by the `key`s it declares in `CompetitionSummary.metrics`,
so a client renders a competition it has never seen from its own description -- labels, units
and which direction is better -- and a new competition needs no change to this file.

Timestamps are ISO-8601 with a `Z`. Submission ids are strings, because an id is whatever the
competition's own store uses and a client should treat it as opaque.
"""

from __future__ import annotations

from typing import Generic, Literal, TypeVar

from submission_api.schemas import Model

ItemT = TypeVar("ItemT")
MetricValue = int | float | None


class FileSpec(Model):
    """One file a submission carries: a multipart part of exactly this name."""

    name: str
    max_bytes: int
    description: str


class Metric(Model):
    key: str
    label: str
    unit: str
    better: Literal["lower", "higher"]


class CompetitionSummary(Model):
    """One competition, described well enough to render and to submit to."""

    slug: str
    name: str
    description: str
    files: list[FileSpec]
    metrics: list[Metric]
    # The metric key the leaderboard is ordered by.
    ranked_by: str
    # The numbers a card shows: for a compression competition, the incumbent to beat and the
    # speed floor to clear. Competition-defined keys.
    headline: dict[str, MetricValue]
    # Queued plus verifying: how long a new submission waits.
    queued: int
    # False while `SUBMISSIONS_PAUSED` is set, which both submit paths refuse on.
    submissions_open: bool


class CompetitionIndex(Model):
    competitions: list[CompetitionSummary]


class SubmissionView(Model):
    competition: str
    id: str
    # Null only for rows a competition keeps for itself; never on the public feeds.
    hotkey: str | None
    digest: str
    state: str
    submitted_at: str
    finished_at: str | None = None
    metrics: dict[str, MetricValue]


class SubmissionAccepted(Model):
    """What a successful submit returns."""

    competition: str
    submission: str
    state: str
    digest: str
    # False when these exact files from this hotkey were already queued: a retry returns the
    # submission it is retrying rather than a second place in the queue.
    created: bool
    # How many more this hotkey could queue right now, after this one. Null when the
    # competition does not ration submissions.
    slots_remaining: int | None


class SubmissionReport(Model):
    """The gate's own output: how a miner finds out which stage refused them.

    Its own endpoint because it is unbounded text that no list should carry.
    """

    competition: str
    id: str
    state: str
    exit_code: int | None = None
    report: str | None = None


class Ranking(Model):
    # Absolute across pages.
    rank: int
    submission: str
    hotkey: str
    submitted_at: str
    metrics: dict[str, MetricValue]


class Leaderboard(Model):
    """One page of the standings, with the numbers they are measured against."""

    competition: str
    ranked_by: str
    headline: dict[str, MetricValue]
    ranking: list[Ranking]
    next_cursor: str | None = None


class CursorPage(Model, Generic[ItemT]):
    """One page of a competition feed. Same wire shape as the proofs feeds, separately owned.

    `next_cursor` is opaque, signed and null exactly when the feed is exhausted. No total: a
    count of a growing feed on every page read is a scan an anonymous caller should not get.
    """

    items: tuple[ItemT, ...]
    next_cursor: str | None = None


SubmissionPage = CursorPage[SubmissionView]


class CompetitionStats(Model):
    competition: str
    # Every generic state, including ones nothing has reached, so a client renders a fixed set
    # of counters.
    submissions: dict[str, int]
    total_submissions: int
    # Hotkeys holding an accepted, ranked submission: the size of the field.
    competitors: int
    # The best value reached for competition-defined metrics.
    best: dict[str, MetricValue]
    last_accepted_at: str | None = None


class CompetitorView(Model):
    """One hotkey's standing, and what it may still submit.

    `slots_remaining` is why this exists: without it a miner learns how many submissions their
    registrations still buy only by making one and reading the refusal.
    """

    competition: str
    hotkey: str
    registered: bool
    slots_remaining: int | None
    pending: int
    best: Ranking | None = None


class SubmissionSource(Model):
    """An accepted submission's files, as uploaded, keyed by declared file name.

    Accepted only: an accept means the gate proved and measured it, and a winning entry nobody
    can read is one nobody can build on. Anything else is the miner's unproven work.
    """

    competition: str
    id: str
    hotkey: str | None
    digest: str
    files: dict[str, str]


class ScoreEntry(Model):
    hotkey: str | None
    submission: str | None
    # This entry's share of the competition's emission, as the competition recorded it.
    weight: float
    metrics: dict[str, MetricValue]
    # Why an entry is paid nothing, when it is not: a baseline, a deregistered hotkey.
    note: str | None = None


class Scores(Model):
    """The competition's most recent scoring pass, exactly as it recorded it."""

    competition: str
    computed_at: str
    block: int | None
    # True when the pass computed a vector but did not submit it to the chain.
    dry_run: bool
    # Whether the chain accepted the vector.
    accepted: bool
    summary: str | None = None
    entries: list[ScoreEntry]


# --- The operator surface --------------------------------------------------------------


class OperatorSubmission(Model):
    """A submission plus the queue bookkeeping the public view omits.

    Behind ADMIN because it is about the validator -- which worker holds it, since when --
    not because the attempt is secret: the same submission is public.
    """

    competition: str
    submission: SubmissionView
    worker_id: str | None = None
    claimed_at: str | None = None
    exit_code: int | None = None
    # Whether the uploaded files are in the competition's database. A submission queued
    # through a path that kept them elsewhere can only be re-run where they are.
    has_files: bool
    report: str | None = None


OperatorSubmissionPage = CursorPage[OperatorSubmission]


class Requeued(Model):
    """What a requeue did. `requeued` is false when the row was not one to requeue."""

    competition: str
    id: str
    requeued: bool
    state: str
