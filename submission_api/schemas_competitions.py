"""Response models for the competition surface.

Separate from `schemas.py` (the miner's proof-submission surface) and `schemas_public.py`
(the website's read surface) for the same reason those two are separate from each other:
they are three contracts with three audiences, and one module would let a field added for
one of them appear in the others.

Timestamps are ISO-8601 with a `Z`, matching what the competition service has always
returned. Miners parse these, so it is a wire format rather than a display choice.
"""

from __future__ import annotations

from submission_api.schemas import Model


class CompetitionSummary(Model):
    """One competition, as the index and the detail page describe it."""

    slug: str
    name: str
    # The multiple of the incumbent's parse time a winning submission may not exceed. The
    # gate enforces it; this is here so a miner can see the bar before spending an hour of
    # validator time discovering it.
    speed_floor: float
    # Fewest bytes anyone has to beat, as the most recent report measured it. None before
    # the first accepted submission.
    incumbent_bytes: int | None = None
    # Queued plus verifying, across every hotkey: how long a new submission waits.
    queued: int


class CompetitionIndex(Model):
    competitions: list[CompetitionSummary]


class SubmissionAccepted(Model):
    """What a successful submit returns."""

    competition: str
    submission: int
    state: str
    digest: str
    # What is left after this one. A miner who sees 0 knows the next submission needs
    # another registration without having to read the refusal first.
    slots_remaining: int


class SubmissionView(Model):
    competition: str
    id: int
    hotkey: str
    digest: str
    submitted_at: str
    state: str
    exit_code: int | None = None
    bytes: int | None = None
    incumbent_bytes: int | None = None
    time_ratio: float | None = None


class SubmissionReport(Model):
    """The gate's stage-by-stage output: how a miner finds out what refused them.

    Its own endpoint rather than a field on `SubmissionView`, because it is unbounded text
    and every list of submissions would otherwise carry a copy of all of it.
    """

    competition: str
    id: int
    state: str
    exit_code: int | None = None
    report: str | None = None


class Ranking(Model):
    rank: int
    submission: int
    hotkey: str
    bytes: int
    # This submission's bytes as a fraction of the incumbent's. Below 1.0 is an improvement.
    vs_incumbent: float | None = None
    time_ratio: float | None = None
    submitted_at: str


class Leaderboard(Model):
    competition: str
    incumbent_bytes: int | None = None
    speed_floor: float
    ranking: list[Ranking]


class AccountSubmissions(Model):
    """One account's own submissions, across the competition surface.

    `account_id` is set only on submissions made through a signed-in session; a
    hotkey-signed submit leaves it null and therefore never appears here.
    """

    submissions: list[SubmissionView]
