"""Docker health probe: a live PID is not evidence of recorded registrations.

The deposit watcher's probe reads its cursor row. This watcher has no cursor -- it re-reads
the whole subnet each pass and appends only what changed -- and registrations are too rare to
be a liveness signal, so the evidence is the heartbeat `RegistrationWatcher` touches after
every pass that completes. Its age goes stale exactly when passes stop succeeding.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from registration_watcher.settings import DEFAULT_HEARTBEAT

# Twenty-five one-block polls. Long enough to ride out a node restart and a reconnect,
# short enough that a watcher failing every pass is unhealthy within minutes.
MAX_AGE_SECONDS = 300


def check(path: Path, *, max_age: float = MAX_AGE_SECONDS, now: float | None = None) -> bool:
    try:
        age = (time.time() if now is None else now) - path.stat().st_mtime
    except FileNotFoundError:
        print("no registration pass has completed yet")
        return False
    print(f"last registration pass completed {age:.0f}s ago")
    return 0 <= age <= max_age


def main() -> int:
    path = Path(os.environ.get("REGISTRATION_WATCH_HEARTBEAT", "").strip() or DEFAULT_HEARTBEAT)
    return 0 if check(path) else 1


if __name__ == "__main__":
    sys.exit(main())
