"""What the scorer is allowed to be told, and what it refuses to start without."""

from __future__ import annotations

import dataclasses as dc
import os
from collections.abc import Mapping

from .pareto import DEFAULT_METHOD, METHODS, SPEED_FLOOR


def _float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{key}={raw!r} is not a number") from None


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{key}={raw!r} is not an integer") from None


@dc.dataclass(frozen=True, slots=True)
class ScoringConfig:
    """The 60/40 rule and its knobs.

    Sixty per cent goes to the Pareto frontier, weighted by how much each point actually
    buys. Forty goes to whoever has moved the record recently, newest most. The split is
    the whole design: the frontier alone pays a field that has stopped improving, and
    recency alone pays whoever shipped last however marginal it was.
    """

    # Which of the eight weight functions scores the frontier. The default is the one
    # that, on the real frontier, finds the knee rather than inverting under a change of
    # units -- see docs/SCORING.md.
    method: str = DEFAULT_METHOD
    # Emission split. They need not sum to 1: whatever is left over burns, which is how
    # an operator dials the whole competition down without changing anything else.
    pareto_share: float = 0.60
    improvement_share: float = 0.40

    # How many recent improvements are paid at all. Past this, an improvement has been
    # superseded often enough that it is the frontier's job to reward it, not recency's.
    improvement_window: int = 10
    # How much better than the record a submission must be to count as an improvement,
    # relative. 0.0025 is a quarter of a percent -- above measurement noise, below what
    # any real algorithmic step buys.
    improvement_threshold: float = 0.0025
    # Geometric decay across the window, newest first. At 0.6 the newest improvement
    # takes about 40% of the improvement share and the tenth about 0.4%.
    improvement_decay: float = 0.6

    # The multiple of the incumbent's time the gate rejects past; it is also the time
    # boundary every normalized weight function measures against.
    speed_floor: float = SPEED_FLOOR

    def __post_init__(self) -> None:
        if self.method not in METHODS:
            raise ValueError(f"unknown scoring method {self.method!r}; one of {sorted(METHODS)}")
        if not 0.0 <= self.pareto_share <= 1.0:
            raise ValueError("pareto_share must be between 0 and 1")
        if not 0.0 <= self.improvement_share <= 1.0:
            raise ValueError("improvement_share must be between 0 and 1")
        if self.pareto_share + self.improvement_share > 1.0 + 1e-9:
            raise ValueError("pareto_share + improvement_share must not exceed 1")
        if self.improvement_window < 0:
            raise ValueError("improvement_window must be >= 0")
        if not 0.0 <= self.improvement_threshold < 1.0:
            raise ValueError("improvement_threshold is a relative fraction in [0, 1)")
        if not 0.0 < self.improvement_decay <= 1.0:
            raise ValueError("improvement_decay must be in (0, 1]")
        if self.speed_floor <= 0:
            raise ValueError("speed_floor must be positive")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> ScoringConfig:
        # Fails closed: a misspelt method or an unparseable number stops the worker at
        # startup rather than quietly paying a round with the wrong function.
        env = os.environ if environ is None else environ
        d = cls()
        return cls(
            method=env.get("SCORING_METHOD") or d.method,
            pareto_share=_float(env, "SCORING_PARETO_SHARE", d.pareto_share),
            improvement_share=_float(env, "SCORING_IMPROVEMENT_SHARE", d.improvement_share),
            improvement_window=_int(env, "SCORING_IMPROVEMENT_WINDOW", d.improvement_window),
            improvement_threshold=_float(
                env, "SCORING_IMPROVEMENT_THRESHOLD", d.improvement_threshold
            ),
            improvement_decay=_float(env, "SCORING_IMPROVEMENT_DECAY", d.improvement_decay),
            speed_floor=_float(env, "SCORING_SPEED_FLOOR", d.speed_floor),
        )

    @property
    def burn_share(self) -> float:
        # Whatever neither component claims. Zero at the defaults.
        return max(0.0, 1.0 - self.pareto_share - self.improvement_share)
