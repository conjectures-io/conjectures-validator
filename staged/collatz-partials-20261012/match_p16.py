"""Match the published Goldberg construction to the frozen candidate's exact box.

Pass the downloaded supplementary-materials directory and FrozenCircleData.lean.
This checks rational interval containment, not a Lean proof of the source theorem.
"""

from pathlib import Path
from fractions import Fraction
import importlib.util
import json
import re
import sys

supplement, frozen = map(Path, sys.argv[1:])
p = supplement / "n16/certify_construction16.py"
spec = importlib.util.spec_from_file_location("circle16", p)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
Interval, Q = module.I, module.Q
b = Interval(module.BL, module.BH)
_, points = module.construction(b)
z = [
    1 / b,
    2 * (b.sq() - 1).sqrt() / b.sq(),
    points[6][1] / b,
    points[10][0] / b,
    points[10][1] / b,
    points[11][1] / b,
]
source = frozen.read_text().split("def goldbergCenter", 1)[1].split("def goldbergHalfWidth", 1)[0]
centers = [Fraction(int(a), int(d)) for a, d in re.findall(r"(-?\d+) / (\d+)", source)]
assert len(centers) == 6
width = Fraction(1, 10**25)
for interval, center in zip(z, centers):
    assert center - width <= Fraction(interval.l, Q) <= Fraction(interval.h, Q) <= center + width
print(
    json.dumps(
        {
            "passed": True,
            "coordinates_checked": 6,
            "map": ["h=1/b", "s=sin(2 eta)", "u=A_y/b", "v=M_x/b", "w=M_y/b", "t=Z_y/b"],
            "radius_identity": "h/(1+h) = 1/(1+b)",
            "reason": "Published construction contacts give the six frozen polynomial equalities; its entire rational root interval maps inside the candidate isolating box. Existing candidate uniqueness identifies the same root.",
            "lean_equivalence_replayed": False,
        },
        indent=2,
    )
)
