"""The 60% share: turn accepted submissions into a frontier, and weigh it.

One competitor is one hotkey, represented by their best accepted submission. Everything
below is pure -- it takes the rows the store already read and returns numbers -- so the
rule can be exercised without a database or a chain.
"""

from __future__ import annotations

import dataclasses as dc
from collections.abc import Sequence

from conjectures_subnet.competition.scoring import ScoredSubmission

from .config import ScoringConfig
from .pareto import Boundaries, Point, pareto_front, weigh


@dc.dataclass(frozen=True, slots=True)
class FrontierScore:
    """What the Pareto component decided, and enough to explain it afterwards."""

    # hotkey -> its share of emission, already scaled by pareto_share.
    weights: dict[str, float]
    # The points as the scorer saw them, by hotkey: what goes into score_snapshots.
    points: dict[str, Point]
    frontier: tuple[str, ...]
    bounds: Boundaries

    def on_frontier(self, hotkey: str) -> bool:
        return hotkey in self.frontier


def to_points(submissions: Sequence[ScoredSubmission]) -> dict[str, Point]:
    # One point per hotkey. Both axes are "lower is better": seconds, and compressed
    # bytes as a percentage of raw. The percentage rather than the bytes because the
    # corpus changes between rounds and absolute bytes are not comparable across it.
    return {
        s.hotkey: Point(name=s.hotkey, time_s=s.time_s, ratio_pct=s.ratio_pct) for s in submissions
    }


def boundaries_for(submissions: Sequence[ScoredSubmission], speed_floor: float) -> Boundaries:
    """The enforced edge of the legal region, from the incumbent's measured time.

    Every accepted submission carries the incumbent's time as measured on the same run,
    so they should agree; they can differ when the operator promoted a new incumbent
    mid-round. The most recent measurement is the one that describes the current gate, so
    that is the one used.
    """
    if not submissions:
        return Boundaries()
    newest = max(submissions, key=lambda s: (s.submitted_at, s.submission_id))
    return Boundaries.from_incumbent(newest.incumbent_seconds, speed_floor)


def score_frontier(submissions: Sequence[ScoredSubmission], config: ScoringConfig) -> FrontierScore:
    """Weigh the frontier, scaled to the Pareto share of emission.

    A hotkey not on the frontier gets nothing here: it is dominated, meaning some other
    submission is both faster and smaller, and there is no sense in which it bought
    anything. It can still earn from the improvement share, which is the point of having
    two components.
    """
    points = to_points(submissions)
    bounds = boundaries_for(submissions, config.speed_floor)
    front = pareto_front(list(points.values()))
    raw = weigh(front, bounds, config.method)
    weights = {hotkey: 0.0 for hotkey in points}
    for hotkey, share in raw.items():
        weights[hotkey] = share * config.pareto_share
    return FrontierScore(
        weights=weights,
        points=points,
        frontier=tuple(p.name for p in front),
        bounds=bounds,
    )
