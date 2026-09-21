"""The one clock the store reads, so tests can order events without sleeping."""

from __future__ import annotations

from datetime import datetime, timezone


def now() -> datetime:
    # UTC now. Monkeypatched in tests to make submission ordering deterministic.
    return datetime.now(timezone.utc)


def iso(t: datetime) -> str:
    # The canonical timestamp form shown to miners, unchanged since the competition's
    # first service -- miners parse it, so it is a wire format, not a display choice.
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
