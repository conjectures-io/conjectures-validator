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
from submission_api.schemas_public import CursorPage


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
    # Whether the competition is taking submissions right now. False while
    # `SUBMISSIONS_PAUSED` is set, which both submit paths refuse on -- so a client that
    # reads this and a client that ignores it get consistent answers. See
    # `/v1/system/status`, which reports the same flag for the proofs surface.
    submissions_open: bool = True


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
    """One page of the standings.

    Not a bare `CursorPage[Ranking]`, because the board is only readable next to the two
    numbers it is measured against: `incumbent_bytes` is what every row's `vs_incumbent` is
    a fraction of, and `speed_floor` is the bar a row had to clear to be here at all. A page
    of ranks without them is a list of numbers with no scale.

    `rank` is absolute across pages, so page two starts where page one stopped.
    """

    competition: str
    incumbent_bytes: int | None = None
    speed_floor: float
    ranking: list[Ranking]
    # Null exactly when the board is exhausted, like every other feed on this API.
    next_cursor: str | None = None


class WeightVector(Model):
    """The competition's per-hotkey scores, as the emissions worker reads them.

    Scores, not final weights: they are proportional to each other and say nothing about
    this competition's share of the subnet. That share is a code constant in
    `emissions_worker/allocation.py`, and keeping it out of this payload is what stops a
    competition deciding how much of the subnet it is worth.

    `computed_at` is load-bearing rather than informational. A scorer that died an hour ago
    leaves this endpoint answering with a leaderboard that has moved, and paying that is
    worse than paying nothing because it looks like it worked -- so the reader checks the
    age and falls back to the treasury.
    """

    competition: str
    computed_at: str
    # hotkey -> score. Empty when nothing is scorable yet, which the reader treats as
    # "pay the treasury" rather than as an error.
    weights: dict[str, float]
    scored_submissions: int


# --- Feeds -----------------------------------------------------------------------------
#
# `CursorPage` is the platform's, imported rather than redefined. The envelope is the one
# thing every feed on this API should agree about: a client that has learned to loop until
# `next_cursor` is null should not have to learn a second way to do it because the rows came
# from the other database.

SubmissionPage = CursorPage[SubmissionView]
RankingPage = CursorPage[Ranking]


class CompetitionStats(Model):
    """The numbers a competition's landing page is made of.

    Separate from `CompetitionSummary` rather than folded into it, because the index
    endpoint renders every competition and these are four aggregates each. One competition
    today makes that free; the endpoint that will not be free later is the one to keep cheap
    now, since the shape is what a second competition inherits.
    """

    competition: str
    # Every state the schema defines, including the ones nothing has reached -- filled in
    # from the state enum rather than from the query, so a client can render a fixed set of
    # counters instead of discovering which keys exist today.
    submissions: dict[str, int]
    total_submissions: int
    # Hotkeys holding an accepted, scored submission: the size of the field, not the number
    # of people who have tried.
    competitors: int
    # The fewest bytes anyone has reached. `incumbent_bytes` on the summary is the bar;
    # this is the best answer to it.
    best_bytes: int | None = None
    last_accepted_at: str | None = None


class CompetitorView(Model):
    """One hotkey's standing in the competition, and what it may still do.

    `slots_remaining` is the reason this endpoint exists. Before it, a miner could only
    learn how many submissions their registrations still entitle them to by making one and
    reading the refusal -- which, for a surface whose whole scarcity model is "one
    registration buys one accepted submission", is the single fact they most need in
    advance.
    """

    competition: str
    hotkey: str
    registered: bool
    # Unclaimed registrations minus what is already in the queue: how many more could be
    # queued right now, the same number a submit response reports.
    slots_remaining: int
    # Queued or verifying for this hotkey.
    pending: int
    submissions: int
    accepted: int
    # Their best accepted submission, if they have one. The leaderboard row, in other words.
    best: Ranking | None = None


class SubmissionSource(Model):
    """The two files a submission is made of, as uploaded.

    Published only for accepted submissions, mirroring `/v1/results/{id}/solution` on the
    proofs side: what an accept means is that the gate proved this parser correct and
    measured it, and a competition whose winning entries cannot be read is one nobody can
    build on. A queued or rejected submission is the miner's unproven work and stays theirs
    -- there is no verdict yet that the rest of the subnet has a claim on.

    UTF-8 text rather than base64: both files are source the gate compiled, so anything that
    is not decodable never got as far as being accepted.
    """

    competition: str
    id: int
    hotkey: str
    digest: str
    parse_rs: str
    proof_lean: str


# --- The operator surface --------------------------------------------------------------


class OperatorSubmission(Model):
    """A submission with the queue bookkeeping the public view omits.

    Everything here is about the validator rather than about the miner: which worker holds
    the row, how many times it has been claimed, and when. It is behind ADMIN for that
    reason and not because the underlying attempt is secret -- the same submission is public
    at `/v1/competitions/{slug}/submissions/{id}`.
    """

    competition: str
    id: int
    hotkey: str
    digest: str
    submitted_at: str
    state: str
    exit_code: int | None = None
    attempts: int
    worker_id: str | None = None
    claimed_at: str | None = None
    finished_at: str | None = None
    # The account behind a session submission, or null when a hotkey signed for itself.
    account_id: str | None = None
    # Whether the uploaded files are still on the row. A submission whose sources are gone
    # cannot be requeued into a gate run, so an operator needs to see it before trying.
    has_sources: bool
    report: str | None = None


OperatorSubmissionPage = CursorPage[OperatorSubmission]


class Requeued(Model):
    """What a requeue did. `requeued` is false when the row was not one to requeue."""

    competition: str
    id: int
    requeued: bool
    state: str
