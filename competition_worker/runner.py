"""Running one competition gate over one submission.

The seam, in full:

    <python> <root>/validator/verifier/verify.py <submission-dir> --results <file>
      exit 0  accepted
      exit 1  rejected
      exit 2  the validator is broken, not the submission
      anything else  also the validator, never a rejection

Standardising on `--results <json>` rather than parsing stdout is what keeps the gate's
report a human document: it can be reworded without silently changing what a validator
stores. The competition's own gate already writes it; `conjectures-rust-competition` has the
same CLI and needs `--results` adding, which costs a `just repin` because `PINS.json` hashes
`verify.py` itself.

**The environment is an allowlist.** This subprocess compiles miner-authored Rust and runs it
for forty-five minutes; handing it `os.environ` would hand it the competition database URL,
the Axiom token and whatever else the unit happens to carry. Only what the toolchain needs
crosses, in the same spirit as `PASSTHROUGH_ENV` in `verification_worker/runner.py`.

**The submission is materialised from the row**, not read off a shared disk. The API runs in
a container and the gate runs on bare metal; there is no shared filesystem, and a writable
mount from one to the other would point the wrong way. The bytes travel in the database and
land in a temp directory that exists for one run.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from competition_worker.registry import Gate

# What the gate legitimately needs and nothing else: where its toolchain lives, and its own
# knobs. No database URL, no wallet, no observability token.
PASSTHROUGH_ENV = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "ELAN_HOME",
    "CARGO_HOME",
    "RUSTUP_HOME",
    "VERIFY_SANDBOX",
    "VERIFY_LEAN_TIMEOUT",
    "VERIFY_LEAN_MEMORY_MB",
    "VERIFY_CORPUS",
)

# Bounded so one submission's output cannot fill a column or a log. The gate's report is a
# stage summary, not a build log; anything approaching this is already pathological.
MAX_REPORT_BYTES = 256 * 1024

# Everything scoring needs, or nothing. A row carrying bytes but no timing puts a
# half-measured point on the frontier, and the scorer filters those out anyway -- so
# recording one only hides the problem until somebody asks why a submission never scored.
MEASURED = ("raw_bytes", "bytes", "incumbent_bytes", "parse_seconds", "incumbent_seconds")

ACCEPTED, REJECTED = 0, 1


class GateBroken(RuntimeError):
    """The gate could not render a verdict. Never a statement about the submission."""


@dataclass(frozen=True, slots=True)
class GateRun:
    """One completed gate invocation."""

    exit_code: int
    report: str
    stderr: str
    measured: dict[str, float]
    gate_commit: str

    @property
    def accepted(self) -> bool:
        return self.exit_code == ACCEPTED

    @property
    def rejected(self) -> bool:
        return self.exit_code == REJECTED

    @property
    def broken(self) -> bool:
        """Anything the gate does not define is the validator's problem, not the miner's."""
        return self.exit_code not in (ACCEPTED, REJECTED)


def _environment() -> dict[str, str]:
    return {name: os.environ[name] for name in PASSTHROUGH_ENV if name in os.environ}


def _tail(text: str) -> str:
    if len(text) <= MAX_REPORT_BYTES:
        return text
    return text[-MAX_REPORT_BYTES:]


def measurements(results: Path) -> dict[str, float]:
    """The score, from the JSON the gate wrote.

    `candidates` names the submission's own method, so nothing here depends on what the
    caller happened to call it. Returns `{}` when the file is absent or incomplete, which
    the caller treats as "not scored" rather than as zero.
    """
    if not results.is_file():
        return {}
    try:
        summary = cast("dict[str, Any]", json.loads(results.read_text()))
    except (OSError, json.JSONDecodeError):
        return {}
    names = cast("list[str]", summary.get("candidates") or [])
    methods = cast("dict[str, dict[str, Any]]", summary.get("methods") or {})
    if not names or names[0] not in methods:
        return {}
    mine, incumbent = methods[names[0]], methods.get("incumbent", {})

    def number(source: dict[str, Any], key: str) -> float | None:
        value = source.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    out = {
        "raw_bytes": number(summary, "raw_bytes"),
        "bytes": number(mine, "output_bytes"),
        "incumbent_bytes": number(incumbent, "output_bytes"),
        "parse_seconds": number(mine, "parse_s"),
        "incumbent_seconds": number(incumbent, "parse_s"),
    }
    if any(out[key] is None for key in MEASURED):
        return {}
    scored = {key: value for key, value in out.items() if value is not None}
    # The gate computes the ratio its speed floor was applied to. Recomputing it here would
    # be a second opinion on the one number the verdict already turned on.
    if (slowdown := number(mine, "slowdown")) is not None:
        scored["time_ratio"] = slowdown
    return scored


def run(
    gate: Gate,
    *,
    parse_source: bytes,
    proof_source: bytes,
    python: str | None = None,
) -> GateRun:
    """Materialise one submission, run the gate over it, and report what came back.

    A timeout is reported as a rejection with the reason, not as a validator error: the
    validator declining to spend more than its cap is a verdict about this submission, and
    the miner can act on it by submitting something that finishes.
    """
    with tempfile.TemporaryDirectory(prefix="competition-gate-") as workspace:
        submission = Path(workspace) / "submission"
        submission.mkdir()
        (submission / "parse.rs").write_bytes(parse_source)
        (submission / "Parse.lean").write_bytes(proof_source)
        results = Path(workspace) / "results.json"
        command = [
            python or sys.executable,
            str(gate.verify),
            str(submission),
            "--results",
            str(results),
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                env=_environment(),
                cwd=gate.root,
                timeout=gate.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            partial = exc.stdout or ""
            if isinstance(partial, bytes):
                partial = partial.decode(errors="replace")
            return GateRun(
                exit_code=REJECTED,
                report=_tail(
                    partial
                    + f"\nREJECTED: the gate did not finish within "
                    f"{gate.timeout_seconds:.0f}s\n"
                ),
                stderr="",
                measured={},
                gate_commit=gate.commit,
            )
        except OSError as exc:  # the gate is not runnable at all
            raise GateBroken(f"{gate.slug}: could not run {gate.verify}: {exc}") from exc
        measured = measurements(results) if completed.returncode == ACCEPTED else {}
    return GateRun(
        exit_code=completed.returncode,
        report=_tail(completed.stdout),
        stderr=_tail(completed.stderr),
        measured=measured,
        gate_commit=gate.commit,
    )
