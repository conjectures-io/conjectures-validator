"""Finite plausibility screen, not a proof of the asymptotic research statement."""

from math import log, sqrt, erf
import json

checkpoints = {1000, 10000, 100000, 1000000}
zs = [-2, -1, 0, 1, 2]
counts = [0] * len(zs)
missing = 0
max_steps = 0
rows = []
d = log(4 / 3)
for n in range(1, max(checkpoints) + 1):
    x = n
    k = 0
    while x * x > n and k < 10000:
        x = x // 2 if x % 2 == 0 else (3 * x + 1) // 2
        k += 1
    if x * x > n:
        missing += 1
    else:
        max_steps = max(max_steps, k)
        mu = log(n) / d
        sd = sqrt(log(3) ** 2 * log(n) / d**3)
        for j, z in enumerate(zs):
            counts[j] += k <= mu + z * sd
    if n in checkpoints:
        rows.append(
            {
                "N": n,
                "cdf": dict(zip(map(str, zs), [v / n for v in counts])),
                "unresolved_at_10000_steps": missing,
                "maximum_observed_first_passage": max_steps,
            }
        )
print(
    json.dumps(
        {
            "standard_normal_cdf": dict(
                zip(map(str, zs), [(1 + erf(z / sqrt(2))) / 2 for z in zs])
            ),
            "checkpoints": rows,
            "integer_trajectory_arithmetic": "exact Python integers; only normalizing logarithms and CDF comparisons use floating point",
            "interpretation": "Finite plausibility data only; cannot prove a CLT, exclude an asymptotic counterexample, or establish novelty.",
        },
        indent=2,
    )
)
