"""Both components, added, then turned into a uid-aligned weight vector.

The split is the design: 60% of emission follows the Pareto frontier -- where the
engineering is, and where it stays rewarded for as long as it stands -- and 40% follows
recent improvement, decaying, so a field that stops moving stops collecting it.

Everything here is pure. `score` takes rows the store read and returns numbers;
`to_vector` takes those numbers and a view of the metagraph and returns the vector. The
worker does the I/O around them.
"""

from __future__ import annotations

import dataclasses as dc
from collections.abc import Sequence

from conjectures_subnet.competition.scoring import ScoredSubmission

from .config import ScoringConfig
from .frontier import FrontierScore, score_frontier
from .improvement import Improvement, score_improvements


@dc.dataclass(frozen=True, slots=True)
class HotkeyScore:
    """One competitor's line in the audit: both components, and what they came to."""

    hotkey: str
    submission_id: int | None
    time_s: float | None
    ratio_pct: float | None
    on_frontier: bool
    pareto_weight: float
    improvement_weight: float

    @property
    def combined_weight(self) -> float:
        return self.pareto_weight + self.improvement_weight

    def as_snapshot(self) -> dict:
        # The shape db.scoring.record_weight_set stores.
        return {
            "hotkey": self.hotkey,
            "submission_id": self.submission_id,
            "time_s": self.time_s,
            "ratio_pct": self.ratio_pct,
            "on_frontier": self.on_frontier,
            "pareto_weight": self.pareto_weight,
            "improvement_weight": self.improvement_weight,
            "combined_weight": self.combined_weight,
        }


@dc.dataclass(frozen=True, slots=True)
class Scoring:
    scores: tuple[HotkeyScore, ...]
    frontier: FrontierScore
    improvements: tuple[Improvement, ...]

    @property
    def weights(self) -> dict[str, float]:
        return {s.hotkey: s.combined_weight for s in self.scores if s.combined_weight > 0}

    def snapshots(self) -> list[dict]:
        return [s.as_snapshot() for s in self.scores]

    def summary(self) -> str:
        top = sorted(self.scores, key=lambda s: -s.combined_weight)[:5]
        paid = ", ".join(f"{s.hotkey[:8]}…={s.combined_weight:.3f}" for s in top)
        return (
            f"frontier={len(self.frontier.frontier)} improvements={len(self.improvements)} "
            f"top=[{paid}]"
        )


def score(
    best_per_hotkey: Sequence[ScoredSubmission],
    history: Sequence[ScoredSubmission],
    config: ScoringConfig,
) -> Scoring:
    """Both components over one round's accepted submissions.

    `best_per_hotkey` is one row per competitor -- the frontier is over competitors, not
    over submissions, or a miner could crowd it by submitting many variants.
    `history` is every accepted submission in order, because an improvement is a fact
    about a moment, and superseding it later does not mean it never happened.
    """
    frontier = score_frontier(best_per_hotkey, config)
    improvement_weights, improvements = score_improvements(history, config)

    by_hotkey = {s.hotkey: s for s in best_per_hotkey}
    scores = []
    for hotkey in sorted(set(by_hotkey) | set(improvement_weights)):
        row = by_hotkey.get(hotkey)
        point = frontier.points.get(hotkey)
        scores.append(
            HotkeyScore(
                hotkey=hotkey,
                submission_id=row.submission_id if row else None,
                time_s=point.time_s if point else None,
                ratio_pct=point.ratio_pct if point else None,
                on_frontier=frontier.on_frontier(hotkey),
                pareto_weight=frontier.weights.get(hotkey, 0.0),
                improvement_weight=improvement_weights.get(hotkey, 0.0),
            )
        )
    return Scoring(scores=tuple(scores), frontier=frontier, improvements=tuple(improvements))


