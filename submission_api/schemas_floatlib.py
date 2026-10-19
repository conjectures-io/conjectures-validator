"""FloatLib's read contract, as the website reads it.

The shared FloatLib wire contract served centrally, including: per-kernel times and promotion dates on the board, the champions' history, and
the rules a page states. Timestamps are ISO-8601 to the second with a `Z`, as that API writes
them.
"""

from typing import Literal

from pydantic import BaseModel

Admission = Literal["leader", "qualifies", "not-significant", "above-bar", "no-evidence"]


class AdmissionRule(BaseModel):
    # The bootstrap behind promotion: resamples drawn, and the one-sided quantile of the gain
    # that must be above zero.
    draws: int
    quantile: float


class Health(BaseModel):
    # The FloatLib host's queue and the rules its latest published scoring applied; the rules are
    # null before the first publication. On `/health`, `queued` and the rules are also null while
    # FloatLib's database cannot be read, since that answer must not fail with it.
    ok: bool
    queued: int | None = None
    promotion_bar: float
    # The competition's share of the validator's emission.
    competition_share: float | None = None
    admission: AdmissionRule | None = None
    # Each reward rank's fraction of the slot winner share, newest champion first.
    rank_shares: list[float] | None = None


class SubmissionView(BaseModel):
    id: int
    hotkey: str
    slot: str
    digest: str
    submitted_at: str
    state: str
    exit_code: int | None = None
    stage: str | None = None
    reason: str | None = None
    ratio: float | None = None
    # Per input type: incumbent_ns, submission_ns, ratio and both spreads.
    classes: dict[str, dict[str, float]] | None = None
    seed: int | None = None
    incumbent_sha256: str | None = None
    # The champion's Kernel.lean this was timed against.
    champion_sha256: str | None = None
    verifier_fingerprint: str | None = None
    # Whether an accepted verdict came from the verifier of the newest verdict; null otherwise.
    verification_current: bool | None = None


class Report(BaseModel):
    report: str | None = None


class Ranking(BaseModel):
    rank: int
    submission: int
    hotkey: str
    ratio: float
    # At or below the promotion bar.
    qualifies: bool
    # As the latest published scoring recorded it; null on a board it does not score.
    admission: Admission | None = None
    submitted_at: str
    # Nanoseconds per operation for each kernel -- add, mul, sum and bmm on the full entry, the
    # board's own kernel on a practice board -- the sum of its timed input types.
    kernels: dict[str, float] | None = None
    # Promotion date: normally only the leader; historical boards also identify promoted challengers.
    promoted_at: str | None = None


class Leaderboard(BaseModel):
    slot: str
    incumbent_sha256: str | None = None
    # The champion this board's ratios are measured against.
    champion_sha256: str | None = None
    # The verifier of the newest verdict.
    verifier_fingerprint: str | None = None
    promotion_bar: float
    # The champion's submission id; null while the shipped champion holds.
    leader: int | None = None
    # Whether this board pays its recent champions' authors: the full entry does, the practice boards
    # do not.
    pays: bool
    ranking: list[Ranking]


class Champion(BaseModel):
    # One submission the board promoted. A newer promotion supersedes it without demoting it,
    # and a demotion hands the board back to the newest one before it not demoted, so `current`
    # rather than `demoted_at` says who holds the board.
    submission: int
    hotkey: str
    # Against the champion it took over from: its total time over that champion's, lower faster.
    ratio: float | None = None
    # Shipped kernels' time over this champion's, higher faster: 1 / the product of the ratios
    # along the chain of champions each was timed against. Null when that chain is broken. Each
    # link is its own bench run, so this is an estimate. See `floatlib_store.speedups`.
    speedup_vs_shipped: float | None = None
    promoted_at: str
    # Set only when its own bench failed too often and it was demoted.
    demoted_at: str | None = None
    current: bool
    # Reward rank, independent of leaderboard position; null on unpaid or older champions.
    rank: int | None = None
    rank_share: float | None = None


class Accepted(BaseModel):
    competition: str = "floatlib"
    submission: int
    slot: str = "full"
    state: str = "queued"
    digest: str
    created: bool = True
    slots_remaining: int = 0
