"""Where Subnet 66's emissions go, as code rather than configuration.

The split between the treasury and the competitions is a policy constant, in the same
spirit as `NETUID` and `TREASURY_UID` in `worker.py`: changing where emissions go should
require a reviewed code change, not an environment edit on a running validator. An operator
who can move a competition's share by exporting a variable can move it at three in the
morning with nobody reading the diff.

That applies to the split *between* competitions and the treasury. It does not apply to the
shape of a competition's own vector -- how its share is divided between Pareto position and
recent improvement is tuned by `SCORING_*` and may stay that way, because redistributing a
competition's own share among its own miners cannot move value out of the subnet.

Basis points, not floats: `8_000 + 2_000 == 10_000` is checkable by eye and by assertion,
and `0.8 + 0.2 == 1.0` is not reliably true in binary floating point.
"""

from __future__ import annotations

from typing import Final, Mapping

BASIS_POINTS: Final = 10_000

# The treasury's share. Everything not allocated to a competition below.
TREASURY_BPS: Final = 8_000

# Each competition's share of the subnet's emissions, by slug. A competition absent from
# this mapping is paid nothing regardless of what it scores, which is the correct direction
# to fail: a competition that has not been given a share has not been reviewed for one.
COMPETITION_BPS: Final[Mapping[str, int]] = {
    "lz77": 2_000,
}

# Checked at import, so a bad edit cannot reach an epoch. There is no runtime path that
# recovers from a split which does not total 100%: paying out less would burn the remainder
# silently, and paying out more is not possible.
assert TREASURY_BPS + sum(COMPETITION_BPS.values()) == BASIS_POINTS, (
    f"emissions must total {BASIS_POINTS} basis points, got "
    f"{TREASURY_BPS + sum(COMPETITION_BPS.values())}"
)


def share(slug: str) -> float:
    """A competition's share of emissions, as a fraction. Zero if it has none."""
    return COMPETITION_BPS.get(slug, 0) / BASIS_POINTS


def treasury_share() -> float:
    return TREASURY_BPS / BASIS_POINTS
