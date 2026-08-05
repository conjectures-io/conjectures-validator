"""The transport: events to Axiom's ingest endpoint, off the hot path, never raising.

Two rules shape every decision in this module, and both come from the same premise — telemetry
that can break or stall the thing it observes is worse than no telemetry:

1. **`ingest` never blocks.** It puts the event on a bounded queue and returns. A background
   thread batches and POSTs. The API is async, and a synchronous HTTPS round trip inside a
   request handler would park the whole event loop on Axiom's latency; a worker's verification
   pass would pay the same cost per log line.
2. **`ingest` never raises.** A full queue drops the event and counts it. A failed POST drops the
   batch and logs one line. Neither is allowed to reach the caller, because the caller is in the
   middle of crediting a deposit or recording a verdict.

The dropped-event counter is deliberate rather than silent: `AxiomClient.stats()` reports it, and
a dataset with a gap is only trustworthy if something says how big the gap was.

**Written against Axiom's REST API with nothing but the standard library**, rather than against
`axiom-py`. The ingest contract is one gzipped NDJSON POST — see `_post` — and implementing it
here keeps `requirements-service.lock` unchanged, which matters most for the verification worker:
its image is the one that runs next to hostile Lean, and it does not grow a JSON parser, an HTTP
session library and a case-conversion library so that a log line can be shipped. The interface is
the seam — swapping in `axiom-py` later means writing one more `AxiomClientInterface`.
"""

from __future__ import annotations

import atexit
import gzip
import json
import logging
import queue
import threading
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any, Final

from conjectures_subnet.axiom.labels import Details, EventType, Severity, Source

# Deliberately this module's own name, and deliberately outside every prefix
# `handler.source_for_logger` maps: the bridge refuses records from this namespace, so a failing
# ingest logs to stderr and cannot enqueue an event about failing to enqueue events.
log = logging.getLogger("conjectures_subnet.axiom")

AXIOM_API_URL: Final = "https://api.axiom.co"

# Bounded because the queue is a memory budget under load the process does not control. Ten
# thousand events is seconds of a busy API; past that, dropping is the correct answer and the
# counter records that it happened.
DEFAULT_QUEUE_SIZE: Final = 10_000
# Batched because one POST per event would spend more time in TLS than in work.
DEFAULT_BATCH_EVENTS: Final = 100
# How long a partial batch waits for company before being sent anyway. Short enough that a quiet
# process still reports promptly.
DEFAULT_FLUSH_SECONDS: Final = 2.0
DEFAULT_TIMEOUT_SECONDS: Final = 10.0
# How long `close()` waits for the queue to drain at shutdown. Bounded: a validator must not hang
# on SIGTERM because a telemetry backend is unreachable.
DEFAULT_SHUTDOWN_SECONDS: Final = 5.0

# A dead Axiom would otherwise write a warning per flush interval forever, burying the process's
# real output. The first failure of a streak is logged, then one in this many.
_FAILURE_LOG_INTERVAL: Final = 50

USER_AGENT: Final = "conjectures-validator-axiom/1"

# Sentinel pushed by `close()` to wake the drain thread immediately rather than waiting out the
# flush interval.
_SHUTDOWN: Final = object()


class AxiomClientInterface(ABC):
    """What the `Axiom` sink needs from a transport, and nothing more."""

    @abstractmethod
    def ingest(
        self,
        *,
        severity: Severity,
        source: Source,
        event_type: EventType,
        details: Details,
    ) -> None:
        """Record one event. Must not block meaningfully, and must not raise."""

    def close(self) -> None:
        """Flush what is queued and stop. Idempotent; safe to call on a no-op client."""

    @property
    def enabled(self) -> bool:
        """Whether events actually go anywhere. False for the no-op client."""
        return True


class AxiomClientNoop(AxiomClientInterface):
    """What every process gets until `AXIOM_TOKEN` and `AXIOM_DATASET` are both set.

    Not an error state. A validator running without Axiom configured is a supported deployment —
    the stderr logs are still there — so the absence of credentials disables ingestion instead of
    refusing to start.
    """

    def ingest(
        self,
        *,
        severity: Severity,
        source: Source,
        event_type: EventType,
        details: Details,
    ) -> None:
        return None

    @property
    def enabled(self) -> bool:
        return False


class AxiomClient(AxiomClientInterface):
    """Batches events onto a daemon thread and POSTs them as gzipped NDJSON.

    The thread is a daemon and `close()` is registered with `atexit`, which together give the
    behaviour a validator wants at shutdown: a clean exit flushes, and an exit that cannot flush
    within `shutdown_seconds` proceeds anyway rather than hanging the process.
    """

    def __init__(
        self,
        *,
        dataset: str,
        token: str,
        environ: str = "default",
        api_url: str = AXIOM_API_URL,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        batch_events: int = DEFAULT_BATCH_EVENTS,
        flush_seconds: float = DEFAULT_FLUSH_SECONDS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        shutdown_seconds: float = DEFAULT_SHUTDOWN_SECONDS,
        start: bool = True,
    ) -> None:
        if not dataset:
            raise ValueError("dataset is required")
        if not token:
            raise ValueError("token is required")
        self._dataset = dataset
        self._token = token
        self._environ = environ
        self._url = (
            f"{api_url.rstrip('/')}/v1/datasets/"
            f"{urllib.parse.quote(dataset, safe='')}/ingest"
        )
        self._batch_events = max(1, batch_events)
        self._flush_seconds = max(0.05, flush_seconds)
        self._timeout_seconds = timeout_seconds
        self._shutdown_seconds = shutdown_seconds

        self._queue: queue.Queue[Any] = queue.Queue(maxsize=max(1, queue_size))
        self._stop = threading.Event()
        self._closed = False
        # Counters, read by `stats()`. Only the drain thread writes `_sent`/`_failed`, only
        # callers write `_dropped`, and `int` increments are atomic under the GIL, so no lock.
        self._sent = 0
        self._dropped = 0
        self._failed = 0
        self._failure_streak = 0

        self._thread = threading.Thread(
            target=self._drain, name="axiom-ingest", daemon=True
        )
        if start:
            self._thread.start()
            atexit.register(self.close)

    # --- producer side ----------------------------------------------------------------------

    def ingest(
        self,
        *,
        severity: Severity,
        source: Source,
        event_type: EventType,
        details: Details,
    ) -> None:
        """Queue one event. Returns immediately; drops rather than blocks when saturated."""
        try:
            event = self._envelope(
                severity=severity,
                source=source,
                event_type=event_type,
                details=details,
            )
            self._queue.put_nowait(event)
        except queue.Full:
            self._dropped += 1
        except Exception:  # noqa: BLE001 — a telemetry bug must not surface as a request failure
            self._dropped += 1
            log.debug("Axiom event could not be queued", exc_info=True)

    def _envelope(
        self,
        *,
        severity: Severity,
        source: Source,
        event_type: EventType,
        details: Details,
    ) -> dict[str, Any]:
        """The event as Axiom will store it.

        `_time` is stamped here rather than left to the ingest endpoint, because a batch can sit
        on the queue for a flush interval and be delayed further by a retry — timestamping at
        arrival would compress an incident's timeline into whenever the backend recovered.

        The envelope fields are written last so a details key called `source` or `severity`
        cannot displace the label the query language depends on.
        """
        return {
            **details,
            "_time": datetime.now(UTC).isoformat(),
            "severity": str(severity),
            "source": source,
            "event_type": event_type,
            "environ": self._environ,
        }

    # --- consumer side ----------------------------------------------------------------------

    def _drain(self) -> None:
        """Accumulate events and POST them: when the batch fills, when it goes quiet, or on exit."""
        pending: list[dict[str, Any]] = []
        while True:
            try:
                item: Any = self._queue.get(timeout=self._flush_seconds)
            except queue.Empty:
                item = None
            else:
                if item is not _SHUTDOWN:
                    pending.append(item)

            stopping = item is _SHUTDOWN or self._stop.is_set()
            if stopping:
                # Take whatever else is already queued, so a clean shutdown loses nothing that
                # was accepted before `close()` was called.
                while True:
                    try:
                        queued = self._queue.get_nowait()
                    except queue.Empty:
                        break
                    if queued is not _SHUTDOWN:
                        pending.append(queued)

            idle = item is None
            if pending and (stopping or idle or len(pending) >= self._batch_events):
                self._post(pending)
                pending = []
            if stopping:
                return

    def _post(self, events: list[dict[str, Any]]) -> None:
        """One ingest call. Swallows everything: this runs on the drain thread, which must not die.

        No retry. A retry here would hold the batch while newer events pile onto a bounded queue,
        turning one failed flush into a wave of drops — and the next flush is two seconds away
        regardless. Losing a batch is the cheaper failure.
        """
        try:
            body = gzip.compress(
                "\n".join(
                    json.dumps(event, default=_fallback) for event in events
                ).encode("utf-8")
            )
            request = urllib.request.Request(
                self._url,
                data=body,
                method="POST",
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/x-ndjson",
                    "Content-Encoding": "gzip",
                    "User-Agent": USER_AGENT,
                },
            )
            with urllib.request.urlopen(
                request, timeout=self._timeout_seconds
            ) as response:
                # Read and discard: leaving the body unread would leak the connection.
                response.read()
        except Exception as exc:  # noqa: BLE001 — see the module docstring
            self._failed += len(events)
            self._failure_streak += 1
            if (
                self._failure_streak == 1
                or self._failure_streak % _FAILURE_LOG_INTERVAL == 0
            ):
                log.warning(
                    "Axiom ingest failed (%d consecutive), dropped %d event(s): %s",
                    self._failure_streak,
                    len(events),
                    exc,
                )
        else:
            self._sent += len(events)
            self._failure_streak = 0

    # --- lifecycle --------------------------------------------------------------------------

    def close(self) -> None:
        """Ask the drain thread to flush and stop, waiting at most `shutdown_seconds`."""
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        try:
            # Wakes the thread immediately instead of letting it wait out the flush interval.
            # A full queue means it is already awake and about to notice `_stop`.
            self._queue.put_nowait(_SHUTDOWN)
        except queue.Full:
            pass
        if self._thread.is_alive():
            self._thread.join(timeout=self._shutdown_seconds)

    def stats(self) -> dict[str, int]:
        """Counters for a health endpoint or a test. `dropped` and `failed` are the honest bits."""
        return {
            "sent": self._sent,
            "dropped": self._dropped,
            "failed": self._failed,
            "queued": self._queue.qsize(),
        }


def _fallback(value: Any) -> str:
    """Anything `json` cannot encode becomes its string form.

    Event details are assembled from whatever a call site had to hand — UUIDs, enum members,
    `datetime`s, SS58 addresses, `Decimal`s. Refusing to serialise one of those would drop the
    whole batch over a field nobody needed to be structured.
    """
    return str(value)


__all__ = [
    "AXIOM_API_URL",
    "DEFAULT_BATCH_EVENTS",
    "DEFAULT_FLUSH_SECONDS",
    "DEFAULT_QUEUE_SIZE",
    "AxiomClient",
    "AxiomClientInterface",
    "AxiomClientNoop",
]
