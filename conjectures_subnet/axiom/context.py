"""The correlation id that ties a request's log records to the request event.

A request produces one `request_completed` event and, usually, several log records from the
handlers and the database layer underneath it. Without a shared key those are separate rows that
happen to have similar timestamps, and reconstructing "what did *this* call do" means guessing.

`AxiomRequestMiddleware` sets the id for the duration of the request and `AxiomLogHandler` reads
it, so every record emitted anywhere below the middleware carries it without a single call site
having to pass it down. A `ContextVar` rather than a thread local because the API is async: one
thread interleaves many requests, and a thread local would attribute records to whichever request
last ran on that thread.

Background workers can use the same variable for a unit of work — see `work_context` — so a
verification pass and everything it logs share one key too.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

# Unset outside a request or a worker pass, which is a normal state: startup and shutdown records
# belong to no unit of work.
request_id: ContextVar[str | None] = ContextVar("axiom_request_id", default=None)


def new_correlation_id() -> str:
    """A short, unique-enough key for one request or one unit of work.

    Half a UUID4. Not a security boundary — it identifies a request in a log, it does not
    authorise anything — and 64 bits of randomness makes a collision within a retention window
    something that does not happen in practice.
    """
    return uuid.uuid4().hex[:16]


def current_correlation_id() -> str | None:
    return request_id.get()


@contextmanager
def work_context(correlation_id: str | None = None) -> Iterator[str]:
    """Tag everything logged inside the block with one correlation id.

    Resets rather than clears on exit, so a nested block restores the outer id instead of
    dropping records that follow it on the floor.
    """
    resolved = correlation_id or new_correlation_id()
    token = request_id.set(resolved)
    try:
        yield resolved
    finally:
        request_id.reset(token)


__all__ = [
    "current_correlation_id",
    "new_correlation_id",
    "request_id",
    "work_context",
]
