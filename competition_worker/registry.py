"""Where a competition's gate lives on this host, and proof it is the reviewed one.

The gate is a checkout on disk, named in a registry file, never a runtime `git clone`. It
compiles and runs miner-authored Rust and type-checks miner-authored Lean, so *which* gate
ran is part of every verdict -- and a gate that could update itself between submissions
would make that unanswerable.

`verification_worker/runner.py` binds a Lean verdict to an immutable container digest. This
side has no image to pin, because the gate needs bubblewrap, elan, Charon/Aeneas and cargo on
the host and cannot be containerized. Three checks stand in for the digest:

1. the checkout sits at the registry's `commit` with a clean tree -- it is the reviewed code,
   unmodified;
2. the gate's own `PINS.json` still matches the files it pins, which is the competition's own
   integrity check run from the outside;
3. `sha256(PINS.json)` matches the registry, because (2) proves the pinned files match the
   pin set and nothing otherwise proves the pin set itself was not rewritten. (1) covers it
   for a clean checkout; (3) says so explicitly and survives a reviewed commit bump.

All three run once at startup, not per submission: they read the disk the gate will use, and
re-reading it before every run would be a check an attacker with write access could simply
wait out.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

# 45 minutes. The Lean stages have their own tighter caps inside the gate; this bounds the
# whole thing, including the cargo build and the scoring run.
DEFAULT_TIMEOUT_SECONDS = 2700.0


class GateUnavailable(RuntimeError):
    """The gate on this host is missing, modified, or not the reviewed revision."""


@dataclass(frozen=True, slots=True)
class Gate:
    """One competition's gate, as this host holds it."""

    slug: str
    root: Path
    commit: str
    pins_sha256: str
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    @property
    def verify(self) -> Path:
        return self.root / "validator/verifier/verify.py"

    @property
    def pins(self) -> Path:
        return self.root / "validator/verifier/PINS.json"


def load(path: Path) -> tuple[Gate, ...]:
    """Read the registry file. Shape errors raise here rather than at the first submission."""
    raw = json.loads(path.read_text())
    if not isinstance(raw, list):
        raise GateUnavailable(f"{path} must contain a list of gates")
    gates = []
    for entry in raw:
        try:
            gates.append(
                Gate(
                    slug=entry["slug"],
                    root=Path(entry["root"]).expanduser().resolve(),
                    commit=entry["commit"],
                    pins_sha256=entry["pins_sha256"],
                    timeout_seconds=float(
                        entry.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
                    ),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise GateUnavailable(f"{path}: bad gate entry {entry!r}") from exc
    return tuple(gates)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        raise GateUnavailable(f"git {' '.join(args)} in {root}: {result.stderr.strip()}")
    return result.stdout.strip()


def check(gate: Gate) -> None:
    """Refuse to start unless this gate is the reviewed one, unmodified. Raises or returns."""
    if not gate.verify.is_file():
        raise GateUnavailable(f"no gate at {gate.verify}")

    head = _git(gate.root, "rev-parse", "HEAD")
    if head != gate.commit:
        raise GateUnavailable(
            f"{gate.slug}: checkout is at {head[:12]}, registry pins {gate.commit[:12]}"
        )
    if dirty := _git(gate.root, "status", "--porcelain"):
        raise GateUnavailable(
            f"{gate.slug}: checkout has uncommitted changes:\n{dirty[:500]}"
        )

    digest = hashlib.sha256(gate.pins.read_bytes()).hexdigest()
    if digest != gate.pins_sha256:
        raise GateUnavailable(
            f"{gate.slug}: PINS.json is {digest[:12]}, registry pins {gate.pins_sha256[:12]}"
        )
