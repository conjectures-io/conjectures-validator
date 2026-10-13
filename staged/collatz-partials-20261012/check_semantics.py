"""Exact checks against two formulations of the published Syracuse recurrence.

These finite checks guard the statement encoding. They do not prove any target.
"""

from fractions import Fraction as Q
from math import log
import json


def push(previous, n):
    q, length = 3 ** (n + 1), 2 * 3**n
    inverse = (q + 1) // 2
    result = [Q(0)] * q
    for y, mass in enumerate(previous):
        for a in range(1, length + 1):
            r = ((3 * y + 1) * pow(inverse, a, q)) % q
            result[r] += mass * Q(2 ** (length - a), 2**length - 1)
    return result


def pull(previous, n):
    # Lemma 1.12, directly: solve 2^a*x=1 mod 3 and recover the prior residue.
    q, length = 3**n, 2 * 3**n
    return [
        sum(
            (
                Q(1, 2**a) * previous[((2**a * x - 1) // 3) % q]
                for a in range(1, length + 1)
                if (2**a * x) % 3 == 1
            ),
            Q(0),
        )
        / (1 - Q(1, 2**length))
        for x in range(3 * q)
    ]


levels = [[Q(1)]]
for n in range(4):
    masses = push(levels[-1], n)
    assert masses == pull(levels[-1], n)
    assert sum(masses) == 1 and min(masses) >= 0
    assert all(masses[r] == 0 for r in range(0, len(masses), 3))
    levels.append(masses)
    for m in range(n + 1):
        assert [sum(masses[r :: 3**m]) for r in range(3**m)] == levels[m]
assert levels[1] == [Q(0), Q(1, 3), Q(2, 3)]
assert levels[2] == [
    Q(0),
    Q(8, 63),
    Q(16, 63),
    Q(0),
    Q(11, 63),
    Q(4, 63),
    Q(0),
    Q(2, 63),
    Q(22, 63),
]

# Independent renewal calculation for the proposed shortcut first-passage scaling.
mu = (log(2) + log(2 / 3)) / 2
variance = ((log(2) - mu) ** 2 + (log(2 / 3) - mu) ** 2) / 2
d = log(4 / 3)
assert abs(0.5 / mu - 1 / d) < 1e-12
assert abs(0.5 * variance / mu**3 - log(3) ** 2 / d**3) < 1e-10
print(
    json.dumps(
        {
            "passed": True,
            "exact_recurrence_levels": 4,
            "checks": [
                "push law equals published pull recurrence",
                "normalization",
                "nonnegative mass",
                "zero mass on multiples of 3",
                "all coarse marginals",
                "published mod3 and mod9 fixtures",
                "independent shortcut renewal mean and variance",
            ],
            "research_targets_proved": False,
        },
        indent=2,
    )
)
