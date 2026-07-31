"""The asynchronous verification worker.

Claims paid, unverified submissions off the queue, has each proof verified in its own trust
domain, and records the immutable report. Step 4 of the sequence in `docs/SUBNET.md`.

    python -m verification_worker            # poll until stopped
    python -m verification_worker --once     # drain the queue and exit

The queue is not a table: a submission is queued by having
`verification_status = 'UNVERIFIED'`. What this package adds around that is a lease, because the
verifier runs for up to an hour and a transaction here may not stay open for one minute.

* `settings` — fail-closed configuration, refusing the in-process runner in production;
* `runner` — the trust boundary: one fresh hardened container per proof, report read from stdout;
* `outcomes` — whether a reason code judges the proof or reports our own failure;
* `tasks` — which task a submission is about, resolved fail-closed against the audited allowlist;
* `worker` — the loop.
"""

from __future__ import annotations

from verification_worker.outcomes import Outcome, classify
from verification_worker.runner import (
    ContainerVerifierRunner,
    InProcessVerifierRunner,
    RunnerFailure,
    VerifierRun,
    VerifierRunner,
    build_runner,
)
from verification_worker.settings import SettingsError, WorkerSettings
from verification_worker.tasks import PoolTaskResolver, ResolvedTask, TaskResolver
from verification_worker.worker import Processed, VerificationWorker

__all__ = [
    "ContainerVerifierRunner",
    "InProcessVerifierRunner",
    "Outcome",
    "PoolTaskResolver",
    "Processed",
    "ResolvedTask",
    "RunnerFailure",
    "SettingsError",
    "TaskResolver",
    "VerificationWorker",
    "VerifierRun",
    "VerifierRunner",
    "WorkerSettings",
    "build_runner",
    "classify",
]
