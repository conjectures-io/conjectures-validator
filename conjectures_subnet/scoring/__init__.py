"""Scoring a round: 60% by Pareto position, 40% by recent improvement.

    from scoring import ScoringConfig, score, to_vector

The two components are separate modules and both are pure; `pareto.py` is the library
scripts/pareto-weights.py was extracted into, and docs/SCORING.md is the argument for
why the default weight function is the one it is.
"""

from __future__ import annotations

from .combine import HotkeyScore, Scoring, score
from .config import ScoringConfig
from .frontier import FrontierScore, boundaries_for, score_frontier, to_points
from .improvement import Improvement, decay_shares, improvement_events, score_improvements
from .pareto import DEFAULT_METHOD, METHODS, Boundaries, Point, pareto_front, weigh

__all__ = [
    "DEFAULT_METHOD",
    "METHODS",
    "Boundaries",
    "FrontierScore",
    "HotkeyScore",
    "Improvement",
    "Point",
    "Scoring",
    "ScoringConfig",
    "boundaries_for",
    "decay_shares",
    "improvement_events",
    "pareto_front",
    "score",
    "score_frontier",
    "score_improvements",
    "to_points",
    "weigh",
]
