"""Run inside the verifier image with /snapshot and /audit mounted."""
import difflib
from pathlib import Path

pinned = Path('/opt/fc-verifier/vendor/formal-conjectures')
upstream = Path('/snapshot')
paths = [
    'FormalConjectures/Wikipedia/HardyLittlewood.lean',
    'FormalConjecturesForMathlib/NumberTheory/NormalNumber.lean',
    'FormalConjectures/Wikipedia/BorsukConjecture.lean',
]
out = []
for path in paths:
    old = (pinned / path).read_text() if (pinned / path).exists() else ''
    new = (upstream / path).read_text()
    out.extend(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                  fromfile='verifier-pin/' + path,
                                  tofile='audited-upstream/' + path))
Path('/audit/evidence/definition-changes.diff').write_text(''.join(out))
