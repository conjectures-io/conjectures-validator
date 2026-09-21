"""The competition gate worker: claims submissions, runs a gate, records the verdict.

Runs on a host that holds a competition's toolchain -- bubblewrap, elan/Lean, Charon/Aeneas
and cargo -- which is why it is not a compose service like every other worker here. The gate
cannot be containerized, so the trust boundary is drawn with process and credential
separation instead: this is the only platform process on that host, it holds the competition
database URL and nothing else, and the gate subprocess it launches gets neither.
"""

from competition_worker.registry import Gate, GateUnavailable
from competition_worker.runner import GateBroken, GateRun
from competition_worker.settings import Settings, SettingsError
from competition_worker.worker import CompetitionWorker, Drain, gates_for

__all__ = [
    "CompetitionWorker",
    "Drain",
    "Gate",
    "GateBroken",
    "GateRun",
    "GateUnavailable",
    "Settings",
    "SettingsError",
    "gates_for",
]
