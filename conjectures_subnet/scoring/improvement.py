"""The 40% share: who has moved the record lately, newest most.

The frontier alone would keep paying a field that has stopped improving -- a knee stays
a knee forever. This component is the part of emission that only exists while the
competition is still moving, and it decays, so yesterday's breakthrough fades rather
than becoming an annuity.

An improvement is measured on bytes, the competition's headline metric: lower wins, and
the leaderboard has always ranked on it. A submission that is merely faster at the same
size does not move the record -- it may well be a new frontier point, and the other 60%
is where it is paid for that.
"""

from __future__ import annotations

import dataclasses as dc
import datetime as dt
from collections.abc import Sequence

from conjectures_subnet.competition.scoring import ScoredSubmission

from .config import ScoringConfig


@dc.dataclass(frozen=True, slots=True)
class Improvement:
    """One accepted submission that beat the record by more than the threshold."""

    submission_id: int
    hotkey: str
    bytes: int
    # The record it beat, and by how much, relative. Kept so a snapshot can say why a
    # submission counted.
    previous_best: int
    relative_gain: float
    at: dt.datetime


def improvement_events(history: Sequence[ScoredSubmission], threshold: float) -> list[Improvement]:
    """Walk the accepted submissions oldest-first and pick out the ones that moved it.

    The record starts at the incumbent's size, not at infinity: beating nothing is not an
    improvement, and a first submission worse than the baseline every miner starts from
    has not advanced anything. The record then follows whichever is smaller, the best
    accepted submission so far or the incumbent -- so promoting a new incumbent
    mid-round raises the bar rather than handing out a free improvement to whoever
    submits next.
    """
    events: list[Improvement] = []
    best: float = float("inf")
    for s in sorted(history, key=lambda s: (s.submitted_at, s.submission_id)):
        # The incumbent is the floor the round starts from and re-floors on promotion.
        best = min(best, float(s.incumbent_bytes)) if s.incumbent_bytes else best
        if best == float("inf"):
            best = float(s.bytes)
        if s.bytes <= best * (1.0 - threshold):
            events.append(
                Improvement(
                    submission_id=s.submission_id,
                    hotkey=s.hotkey,
                    bytes=s.bytes,
                    previous_best=int(best),
                    relative_gain=(best - s.bytes) / best,
                    at=s.submitted_at,
                )
            )
            best = float(s.bytes)
    return events


def decay_shares(count: int, decay: float) -> list[float]:
    """`count` shares summing to 1, geometrically decaying, newest first.

    Geometric rather than linear so the newest improvement is distinctly the largest
    without the tail going to zero: at decay 0.6 over ten slots the newest takes ~40%
    and the tenth ~0.4%, which is small but not nothing.
    """
    if count <= 0:
        return []
    raw = [decay**i for i in range(count)]
    total = sum(raw)
    return [r / total for r in raw]


def score_improvements(
    history: Sequence[ScoredSubmission], config: ScoringConfig
) -> tuple[dict[str, float], list[Improvement]]:
    """Weigh the last `improvement_window` improvements, scaled to the improvement share.

    Returns the per-hotkey weights and the events they came from. A hotkey holding
    several recent improvements accumulates their shares: shipping three of the last ten
    advances is worth three slots, not one.

    With no improvements at all -- an empty round, or one where nothing has beaten the
    incumbent yet -- this pays nobody and the share burns. That is deliberate: there is
    no recent progress to reward, and spreading it over the frontier would quietly turn
    the 60/40 split into something else.
    """
    events = improvement_events(history, config.improvement_threshold)
    recent = list(reversed(events))[: config.improvement_window]
    shares = decay_shares(len(recent), config.improvement_decay)
    weights: dict[str, float] = {}
    for event, share in zip(recent, shares, strict=True):
        weights[event.hotkey] = weights.get(event.hotkey, 0.0) + share * config.improvement_share
    return weights, recent
