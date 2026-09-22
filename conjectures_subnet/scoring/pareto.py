"""Weighting a Pareto frontier by how much each point actually buys.

Lifted out of scripts/pareto-weights.py, which spent eight competing weight functions
working out what "buys" should mean. The script is still the place to compare them --
it keeps the synthetic scenarios and the plots -- but the functions live here so the
validator can score a real round with the same code that was argued over, rather than
with a reimplementation of it.

One thing changed in the lift. The script kept its boundaries in a module global that
`--run` reassigned per corpus; every weight function read it as a constant. That is fine
for a script processing one corpus at a time and wrong for a service, so `Boundaries` is
passed in instead. The script builds one and passes it, so its output is unchanged.

The boundaries are the *enforced* edges of the legal region -- the worst a submission
can be and still be accepted -- never the observed spread of whoever happened to submit
this round. A point's normalized position must not move because someone else entered.
"""

from __future__ import annotations

import dataclasses as dc
from collections.abc import Callable, Sequence

# The gate rejects anything slower than this multiple of the incumbent, so the time
# boundary for a round is this times the incumbent's measured time on the scoring corpus.
SPEED_FLOOR = 8.0
# The worst ratio a submission could have: no compression at all.
BOUNDARY_RATIO_PCT = 100.0
# Only used when there is no incumbent time to derive a boundary from.
FALLBACK_BOUNDARY_TIME_S = 120.0


@dc.dataclass(frozen=True, slots=True)
class Point:
    """One submission's position; both fields are "lower is better"."""

    name: str
    time_s: float
    ratio_pct: float  # compressed bytes as % of raw


@dc.dataclass(frozen=True, slots=True)
class Boundaries:
    """The edges of the legal region: the worst a submission may be and still count.

    `time_s` is the speed floor in absolute terms -- the multiple of the incumbent's
    measured time that the gate rejects past. Anchoring to it rather than to a fixed
    number of seconds matters: against a 120s placeholder every real candidate (0.2-2.2s)
    collapses into the corner and every normalized method degenerates into noise.
    """

    time_s: float = FALLBACK_BOUNDARY_TIME_S
    ratio_pct: float = BOUNDARY_RATIO_PCT

    @classmethod
    def from_incumbent(cls, incumbent_seconds: float, speed_floor: float = SPEED_FLOOR):
        if incumbent_seconds <= 0:
            return cls()
        return cls(time_s=speed_floor * incumbent_seconds)

    def normalize(self, time_s: float, ratio_pct: float) -> tuple[float, float]:
        # Both axes to [0, 1]. 0 is the utopia corner (instant, perfect); 1 is the
        # boundary itself, the worst a legal submission can be.
        return time_s / self.time_s, ratio_pct / self.ratio_pct


Weights = dict[str, float]
WeightFn = Callable[[Sequence[Point], Boundaries], Weights]


def pareto_front(points: Sequence[Point]) -> list[Point]:
    # Minimize (time_s, ratio_pct): standard 2D skyline, sweep by ascending time, keep
    # only strict ratio improvements.
    ordered = sorted(points, key=lambda p: (p.time_s, p.ratio_pct))
    front: list[Point] = []
    best_ratio = float("inf")
    for p in ordered:
        if p.ratio_pct < best_ratio:
            front.append(p)
            best_ratio = p.ratio_pct
    return front


def default_reference(ordered: Sequence[Point]) -> tuple[float, float]:
    # The "do nothing" baseline: no compression at all, at the slowest time anyone took.
    return (ordered[-1].time_s * 1.05, 100.0)


# ── The eight methods ─────────────────────────────────────────────────────


def hypervolume_weights(front, bounds=None, reference=None) -> Weights:
    """Each frontier point's share of the dominated area, normalized to sum to 1.

    Sorted by time, the frontier tiles the region between it and `reference` into
    disjoint rectangles -- one per point, from its own time to the next point's (or to
    the reference, for the last). Weight is that rectangle's area over the total.

    Its flaw, found by running it: a point right before a big empty gap inherits that
    whole gap's width even when it barely improves on its own neighbours. On the real
    frontier that handed `lazy` 81% of the weight for sitting in front of a 1.7s hole.
    Kept because the comparison is the point.
    """
    del bounds  # this method never normalizes; the signature is shared
    if not front:
        return {}
    ordered = sorted(front, key=lambda p: p.time_s)
    ref_time, ref_ratio = reference or default_reference(ordered)
    areas = {}
    for i, p in enumerate(ordered):
        next_time = ordered[i + 1].time_s if i + 1 < len(ordered) else ref_time
        width = max(next_time - p.time_s, 0.0)
        height = max(ref_ratio - p.ratio_pct, 0.0)
        areas[p.name] = width * height
    total = sum(areas.values()) or 1.0
    return {name: area / total for name, area in areas.items()}


def normalized_hypervolume_weights(front, bounds: Boundaries) -> Weights:
    """The same tiling on axes rescaled to comparable [0, 1] units first.

    Fixes the seconds-versus-percent mismatch, but keeps the "credit the gap to your
    left" structure and so keeps the same flaw. That the winner flips when only the
    units change is itself the finding: a scoring function whose answer inverts under a
    change of units is not measuring what it claims to.
    """
    if not front:
        return {}
    ordered = sorted(front, key=lambda p: p.time_s)
    ref_t, ref_r = 1.0, 1.0
    areas = {}
    for i, p in enumerate(ordered):
        nt_i, nr_i = bounds.normalize(p.time_s, p.ratio_pct)
        nt_next = bounds.normalize(ordered[i + 1].time_s, 0.0)[0] if i + 1 < len(ordered) else ref_t
        areas[p.name] = max(nt_next - nt_i, 0.0) * max(ref_r - nr_i, 0.0)
    total = sum(areas.values()) or 1.0
    return {name: area / total for name, area in areas.items()}


def neighbor_improvement_weights(front, bounds: Boundaries) -> Weights:
    """Weight = the ratio a point recovers over the previous frontier point.

    Time never enters the formula: a point that buys a big ratio drop over its neighbour
    is valuable however long it took, and one that adds a little gets a little even if it
    sits right before a big empty gap. The fastest point is compared against the enforced
    ratio ceiling, which is also its weakness -- nothing real is near 100%, so on the real
    frontier it handed the template 92% for beating a bound nobody was near.
    """
    if not front:
        return {}
    ordered = sorted(front, key=lambda p: p.time_s)
    prev_ratio = bounds.ratio_pct
    improvements = {}
    for p in ordered:
        improvements[p.name] = max(prev_ratio - p.ratio_pct, 0.0)
        prev_ratio = p.ratio_pct
    total = sum(improvements.values()) or 1.0
    return {name: v / total for name, v in improvements.items()}


def elbow_sweetspot_weights(front, bounds: Boundaries) -> Weights:
    """Reward points where the trade-off curve genuinely bends. The default.

    In normalized [0, 1] space each point has a rate coming in (ratio recovered per unit
    time, arriving from the previous frontier point, or from the boundary corner for the
    first) and a rate going out (continuing to the next, or to the boundary for the last).
    Score = incoming minus outgoing, clipped at zero: a point scores only if arriving
    there paid off distinctly better than continuing past it did.

    A steady, even trade-off scores everyone near zero alike. Not literally zero, though:
    the slowest frontier point's outgoing rate is zero by construction -- it is compared
    against the boundary at its own ratio -- so it always keeps something, and an even
    staircase stays a real distribution. All-zero takes a degenerate frontier (a lone
    point at the ratio ceiling, recovering nothing against the boundary either), and
    there the share is split equally rather than thrown away: being on the frontier is
    always worth something.
    """
    if not front:
        return {}
    ordered = sorted(front, key=lambda p: p.time_s)
    eps = 1e-9
    n = len(ordered)
    # The boundary treated as a literal neighbour at each end -- never a hardcoded
    # already-normalized constant (which would collide with the first point's own time,
    # always the normalized origin, and divide by ~0) and never derived from the data
    # (which would let who else submitted this round shift everyone's score).
    boundary_start = bounds.normalize(0.0, bounds.ratio_pct)
    scores = {}
    for i, p in enumerate(ordered):
        nt_i, nr_i = bounds.normalize(p.time_s, p.ratio_pct)
        nt_prev, nr_prev = (
            boundary_start
            if i == 0
            else bounds.normalize(ordered[i - 1].time_s, ordered[i - 1].ratio_pct)
        )
        nt_next, nr_next = (
            bounds.normalize(bounds.time_s, p.ratio_pct)
            if i == n - 1
            else bounds.normalize(ordered[i + 1].time_s, ordered[i + 1].ratio_pct)
        )
        rate_in = (nr_prev - nr_i) / max(nt_i - nt_prev, eps)
        rate_out = (nr_i - nr_next) / max(nt_next - nt_i, eps)
        scores[p.name] = max(rate_in - rate_out, 0.0)
    total = sum(scores.values())
    if total <= 0:
        return {p.name: 1.0 / n for p in ordered}
    return {name: s / total for name, s in scores.items()}


def diagonal_sweep_weights(front, bounds: Boundaries, k: float = 1.0) -> Weights:
    """Rank the frontier by a swept diagonal line; weight by that rank alone.

    In normalized space a family of parallel lines `nr = -k*nt + c` sweeps up from the
    utopia corner; a point is crossed at `c = nr + k*nt`, so sorting by that is the order
    the sweep meets the frontier. `k=1` weighs both axes equally, below 1 favours ratio,
    above 1 favours time.

    Unlike every other method here only the order matters, never the size of the gaps:
    two points a hair apart and two at opposite ends score identically as rank 1 and 2.
    """
    if not front:
        return {}

    def score(p: Point) -> float:
        nt, nr = bounds.normalize(p.time_s, p.ratio_pct)
        return nr + k * nt

    ranked = sorted(front, key=lambda p: (score(p), p.time_s))
    n = len(ranked)
    denom = n * (n + 1) / 2  # sum of 1..n
    return {p.name: (n - rank) / denom for rank, p in enumerate(ranked)}


def local_global_weights(front, bounds=None, coef_max: float = 2.0, coef_min: float = 0.0):
    """Two multiplicative coefficients: how a point sits locally, and globally.

    Take any two frontier points A (faster, worse ratio) and B (slower, better ratio) and
    normalize the box they bound to a unit square: A at (0, 1), B at (1, 0), the straight
    line between them exactly `nt + nr = 1`. A third point between them falls on that line
    only if it is a plain linear trade-off; to the utopia side when it is a better deal
    than that, to the worst side when it is a worse one.

    `local` applies this with the point's own immediate neighbours as A and B -- the
    false-elbow test as a formula. `global` applies it with the frontier's two extremes
    for every point. Weight = local x global.

    This method compares a point only to others on the frontier and has no external limit
    in its formula, so it takes no boundaries: normalizing it against a competition
    constant would be noise.
    """
    del bounds  # deliberately unused; see the docstring
    if not front:
        return {}
    ordered = sorted(front, key=lambda p: p.time_s)
    if len(ordered) == 1:
        return {ordered[0].name: 1.0}
    local = local_coefficients(ordered, coef_max, coef_min)
    glob = global_coefficients(ordered, coef_max, coef_min)
    raw = {p.name: local[p.name] * glob[p.name] for p in ordered}
    total = sum(raw.values()) or 1.0
    return {name: v / total for name, v in raw.items()}


def local_coefficients(ordered, coef_max, coef_min):
    # 1.0 for the two points with no local neighbour pair to sit between.
    n = len(ordered)
    coefs = {ordered[0].name: 1.0, ordered[-1].name: 1.0}
    for i in range(1, n - 1):
        coefs[ordered[i].name] = corner_coefficient(
            ordered[i - 1], ordered[i + 1], ordered[i], coef_max, coef_min
        )
    return coefs


def global_coefficients(ordered, coef_max, coef_min):
    # Every point measured against the same pair: the frontier's own two extremes. They
    # plug in as A and B themselves, so they land exactly on the line and come out 1.0.
    a, b = ordered[0], ordered[-1]
    return {p.name: corner_coefficient(a, b, p, coef_max, coef_min) for p in ordered}


def corner_coefficient(a, b, m, coef_max, coef_min):
    # a = the faster, worse-ratio boundary point; b = the slower, better-ratio one;
    # m = the point being scored.
    nt = (m.time_s - a.time_s) / (b.time_s - a.time_s) if b.time_s > a.time_s else 0.0
    nr = (
        (m.ratio_pct - b.ratio_pct) / (a.ratio_pct - b.ratio_pct)
        if a.ratio_pct > b.ratio_pct
        else 0.0
    )
    d = nt + nr - 1.0  # < 0 utopia side, > 0 worst side, 0 exactly on the line
    if d <= 0:
        return 1.0 - d * (coef_max - 1.0)
    return 1.0 - d * (1.0 - coef_min)


# ── The registry ──────────────────────────────────────────────────────────

# Every method, by the name the reports and TAU_SCORING_METHOD use. The validator's
# default is elbow-sweetspot: on the real frontier it and the diagonal sweeps agree on
# the defensible answer (the knee), while hypervolume inverts under a change of units and
# neighbor-improvement anchors against a bound nothing is near.
METHODS: dict[str, WeightFn] = {
    "hypervolume": hypervolume_weights,
    "hypervolume-normalized": normalized_hypervolume_weights,
    "neighbor-improvement": neighbor_improvement_weights,
    "elbow-sweetspot": elbow_sweetspot_weights,
    "diagonal-sweep-k1.0": lambda front, bounds: diagonal_sweep_weights(front, bounds, k=1.0),
    "diagonal-sweep-k0.33": lambda front, bounds: diagonal_sweep_weights(front, bounds, k=1 / 3),
    "diagonal-sweep-k3.0": lambda front, bounds: diagonal_sweep_weights(front, bounds, k=3.0),
    "local-global": local_global_weights,
}

DEFAULT_METHOD = "elbow-sweetspot"


def weigh(front, bounds: Boundaries, method: str = DEFAULT_METHOD) -> Weights:
    # Apply one named method. An unknown name is a configuration error, not a fallback:
    # silently scoring a round with the wrong function is worse than refusing to start.
    try:
        fn = METHODS[method]
    except KeyError:
        raise ValueError(f"unknown scoring method {method!r}; one of {sorted(METHODS)}") from None
    return fn(front, bounds)
