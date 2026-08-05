"""The event sink every call site uses: `get_axiom().info(source=…, event_type=…, **fields)`.

One process-wide instance, built on first use, because the transport owns a thread and a queue and
there is no reason for two of them.

The helpers take primitives, not domain objects. That is what keeps this package importable from
`submission_api`, from all three workers and from `conjectures_subnet.db` without an import cycle
and without the observability layer growing an opinion about a `ChainTransfer`. The cost is that
call sites spell out the fields they want; the benefit is that the fields they spell out are the
ones a query needs, rather than whatever `__repr__` happened to produce.

Severity is chosen by the method, not passed: `info`/`warn`/`error` are what almost every call
site wants, `emit` is there for the cases that compute a severity, and `exception` attaches the
formatted traceback of the exception being handled.
"""

from __future__ import annotations

import traceback
from functools import lru_cache
from typing import Any

from conjectures_subnet.axiom.client import AxiomClientInterface
from conjectures_subnet.axiom.context import current_correlation_id
from conjectures_subnet.axiom.env_factory import create_axiom_client_from_env
from conjectures_subnet.axiom.labels import EventType, Severity, Source


class Axiom:
    """A severity-tagged, source-tagged event sink that never raises on the caller's behalf."""

    def __init__(self, client: AxiomClientInterface) -> None:
        self._client = client

    @property
    def enabled(self) -> bool:
        """Whether events reach Axiom. False when the process has no credentials configured."""
        return self._client.enabled

    def emit(
        self,
        *,
        severity: Severity,
        source: Source,
        event_type: EventType,
        **fields: Any,
    ) -> None:
        """Record one event at an explicit severity. The base the named helpers wrap."""
        self._client.ingest(
            severity=severity,
            source=source,
            event_type=event_type,
            details=self._details(fields),
        )

    def debug(self, *, source: Source, event_type: EventType, **fields: Any) -> None:
        self.emit(
            severity=Severity.DEBUG, source=source, event_type=event_type, **fields
        )

    def info(self, *, source: Source, event_type: EventType, **fields: Any) -> None:
        self.emit(
            severity=Severity.INFO, source=source, event_type=event_type, **fields
        )

    def warn(self, *, source: Source, event_type: EventType, **fields: Any) -> None:
        self.emit(
            severity=Severity.WARNING, source=source, event_type=event_type, **fields
        )

    def error(self, *, source: Source, event_type: EventType, **fields: Any) -> None:
        self.emit(
            severity=Severity.ERROR, source=source, event_type=event_type, **fields
        )

    def critical(self, *, source: Source, event_type: EventType, **fields: Any) -> None:
        self.emit(
            severity=Severity.CRITICAL, source=source, event_type=event_type, **fields
        )

    def exception(
        self,
        *,
        source: Source,
        event_type: EventType,
        severity: Severity = Severity.ERROR,
        **fields: Any,
    ) -> None:
        """Record the exception currently being handled, with its traceback.

        Defaults to `error`. Call sites that catch an expected, retryable failure — a chain read
        that will be retried on the next pass — should pass `severity=Severity.WARNING` rather
        than letting a routine retry page somebody.

        Only meaningful inside an `except` block; outside one `format_exc()` yields `NoneType:
        None`, which is a legible enough tell that the call is in the wrong place.
        """
        self.emit(
            severity=severity,
            source=source,
            event_type=event_type,
            exception=traceback.format_exc(),
            **fields,
        )

    def close(self) -> None:
        """Flush queued events. Called at exit by the transport; explicit here for tests."""
        self._client.close()

    @staticmethod
    def _details(fields: dict[str, Any]) -> dict[str, Any]:
        """Add the ambient correlation id, when there is one, without the call site knowing.

        A field the caller passed wins: a handler that knows the id it wants recorded has better
        information than the context variable.
        """
        correlation = current_correlation_id()
        if correlation is None or "request_id" in fields:
            return fields
        return {"request_id": correlation, **fields}


@lru_cache(maxsize=1)
def get_axiom() -> Axiom:
    """The process-wide sink, built on first use.

    Cached rather than constructed at import, so importing anything from this package in a test or
    a CLI does not start a thread that nothing will use.
    """
    return Axiom(create_axiom_client_from_env())


def reset_axiom() -> None:
    """Drop the cached sink after flushing it. For tests, and for nothing else.

    A process that resets mid-flight would keep whatever thread the old client started, so this
    closes it first.
    """
    if get_axiom.cache_info().currsize:
        get_axiom().close()
    get_axiom.cache_clear()


__all__ = ["Axiom", "get_axiom", "reset_axiom"]
