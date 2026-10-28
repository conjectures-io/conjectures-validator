"""Distributed traces to Axiom over OpenTelemetry, for the processes that carry the packages.

Events (`conjectures_subnet.axiom`) say *what happened*. A trace says *where the time went*: one
request as a tree of spans — the route, every SQL statement it ran, every outbound HTTP call it
waited on — which is what Axiom's trace viewer, and its critical-path analysis, are built on. The
two are joined: an event emitted inside a sampled span carries `trace_id` and `span_id`, so a row
in the events dataset leads to its trace.

**Optional twice over**, in the same spirit as `axiom/env_factory.py`:

* The packages are the `tracing` extra, which only the API image installs. Nothing here imports
  `opentelemetry` at module level, so the verification worker — whose environment deliberately
  stays small, because it runs next to hostile Lean — imports this module and gets a no-op.
* It is on wherever events are: `AXIOM_TOKEN` plus `AXIOM_DATASET` turn both on. Without them, or
  with a value that cannot be used, every function here is a cheap no-op and the process runs
  exactly as it did. Tracing
  decides whether anyone can see where time goes, never whether the validator is correct, so it
  never stops a process from starting.

**What a span may carry.** Same rule as the request events: the route *template*, never the
requested path, and never a query string. Every span's URL attributes are stripped of their query
before export (`_Redact`), whichever instrumentation set them, and no inbound or outbound trace
context is propagated — a client-supplied `traceparent` is ignored for the reason a client-supplied
request id is, and third parties we call are not handed our trace ids.

**Which spans may start a trace.** Only a server span (one per request) or a span marked as a unit of
work. A database query or an HTTP call with no parent — a background poll, a startup check — is
dropped rather than exported as a one-span trace that says nothing; see `_RootSampler`.

Environment:

* `AXIOM_TRACES_DATASET`      — the dataset spans land in. Default `<AXIOM_DATASET>-traces`, so an
                                operator who has events configured only creates that dataset;
                                `off` turns tracing off while events stay on. A dataset of its
                                own: traces and events have different shapes, and the trace
                                viewer reads one. Deployments sharing an events dataset share
                                its traces dataset too, told apart by `AXIOM_ENVIRON`, as events
                                are.
* `AXIOM_TRACES_SAMPLE_RATIO` — the share of traces kept, 0.0 to 1.0. Default 1.0.
* `AXIOM_TOKEN`, `AXIOM_DATASET`, `AXIOM_URL`, `AXIOM_ENVIRON`, `CONJECTURES_REVISION` — as for
  events. `AXIOM_ENVIRON` becomes the `deployment.environment.name` resource attribute.
"""

from __future__ import annotations

import contextlib
import logging
import os
import urllib.parse
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

# The transport's own namespace, which the logging bridge refuses to forward; see `axiom.handler`.
log = logging.getLogger("conjectures_subnet.axiom.tracing")

DEFAULT_API_URL: Final = "https://api.axiom.co"
DEFAULT_SAMPLE_RATIO: Final = 1.0
TRACES_DATASET_SUFFIX: Final = "-traces"
_OFF: Final = frozenset({"off", "none", "false", "0"})
EXPORT_TIMEOUT_SECONDS: Final = 10.0
THROTTLE_SECONDS: Final = 60.0

# Set on a span that may start a trace without being a server span: a worker's pass, a poll.
WORK_ATTRIBUTE: Final = "conjectures.unit_of_work"
REQUEST_ID_ATTRIBUTE: Final = "conjectures.request_id"

# Attributes that hold a URL, across the old and the stable HTTP semantic conventions.
_URL_ATTRIBUTES: Final = ("url.full", "http.url")
_TARGET_ATTRIBUTES: Final = ("http.target",)
_QUERY_ATTRIBUTES: Final = ("url.query",)


@dataclass
class _State:
    """Everything a configured process holds. One per process; None until configured."""

    provider: Any
    tracer: Any
    get_current_span: Any
    span_kind_server: Any
    status_error: Any
    instrumentors: list[Any]


_state: _State | None = None


def enabled() -> bool:
    """Whether this process exports traces."""
    return _state is not None


def configure_tracing(
    *,
    service_name: str,
    environ: Mapping[str, str] | None = None,
    exporter: Any = None,
) -> bool:
    """Start exporting traces when the environment asks for it. Returns whether it did.

    Idempotent: a second call in the same process keeps the first configuration. `exporter` is for
    tests, which pass an in-memory one; production always builds the OTLP/HTTP exporter.

    Global instrumentation is installed here — SQLAlchemy's engine constructors and httpx's
    transports — so call it before the process creates its engines and clients.
    """
    global _state
    if _state is not None:
        return True
    env = os.environ if environ is None else environ
    token = env.get("AXIOM_TOKEN", "").strip()
    dataset = traces_dataset(env)
    if exporter is None and (not token or not dataset):
        log.debug("Axiom is not configured or AXIOM_TRACES_DATASET is off; tracing is disabled")
        return False
    try:
        _state = _build(service_name=service_name, env=env, token=token, dataset=dataset,
                        exporter=exporter)
    except ImportError:
        log.info("the tracing extra is not installed; tracing is disabled")
        return False
    except Exception:  # noqa: BLE001 — see the module docstring on why this is not fatal
        log.warning("tracing could not be configured; it is disabled", exc_info=True)
        return False
    log.info("tracing enabled (service=%s dataset=%s)", service_name, dataset or "-")
    return True


def traces_dataset(env: Mapping[str, str]) -> str:
    """The dataset spans go to, or "" for none: the override, else the events dataset's sibling.

    Derived rather than required so tracing needs no setting events do not already have. Unlike
    the guess `env_factory` refuses to make, this name follows from one the operator chose, and
    it is documented: a missing dataset costs rejected exports (logged, throttled), never events.
    """
    explicit = env.get("AXIOM_TRACES_DATASET", "").strip()
    if explicit.lower() in _OFF:
        return ""
    if explicit:
        return explicit
    events = env.get("AXIOM_DATASET", "").strip()
    return f"{events}{TRACES_DATASET_SUFFIX}" if events else ""


def _build(
    *, service_name: str, env: Mapping[str, str], token: str, dataset: str, exporter: Any
) -> _State:
    from opentelemetry import propagate, trace
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    from opentelemetry.propagators.composite import CompositePropagator
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

    attributes = {
        "service.name": service_name,
        "deployment.environment.name": env.get("AXIOM_ENVIRON", "").strip() or "default",
    }
    revision = env.get("CONJECTURES_REVISION", "").strip()
    if revision and revision != "unknown":
        attributes["service.version"] = revision

    ratio = _ratio(env.get("AXIOM_TRACES_SAMPLE_RATIO", ""))
    provider = TracerProvider(
        resource=Resource.create(attributes),
        sampler=ParentBased(root=_root_sampler(TraceIdRatioBased(ratio))),
        # Our own provider, never the global one: OpenTelemetry allows the global to be set once
        # per process, and everything here passes this provider explicitly instead.
        shutdown_on_exit=True,
    )
    if exporter is None:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        api_url = env.get("AXIOM_URL", "").strip() or DEFAULT_API_URL
        exporter = OTLPSpanExporter(
            endpoint=f"{api_url.rstrip('/')}/v1/traces",
            headers={"Authorization": f"Bearer {token}", "X-Axiom-Dataset": dataset},
            timeout=EXPORT_TIMEOUT_SECONDS,
        )
    # Redaction first, so no processor after it -- and so no exporter -- sees a query string.
    provider.add_span_processor(_redactor())
    # Batched on its own thread with a bounded queue, like the event transport: a full queue
    # drops spans, it never blocks a request.
    provider.add_span_processor(BatchSpanProcessor(exporter))

    # No context crosses a process boundary in either direction. See the module docstring.
    propagate.set_global_textmap(CompositePropagator([]))

    # The exporter logs every failed batch, and the logging bridge would forward each one to the
    # events dataset: a revoked token would bury it in one error per flush. One a minute is
    # enough to know it is happening. A logger's filters do not apply to its children, so the
    # throttle goes on each OpenTelemetry logger, all of which exist once the imports above ran.
    throttle = _Throttle(THROTTLE_SECONDS)
    for name in list(logging.root.manager.loggerDict):
        if name == "opentelemetry" or name.startswith("opentelemetry."):
            otel_logger = logging.getLogger(name)
            if not any(isinstance(item, _Throttle) for item in otel_logger.filters):
                otel_logger.addFilter(throttle)

    instrumentors: list[Any] = []
    sqlalchemy = SQLAlchemyInstrumentor()
    sqlalchemy.instrument(tracer_provider=provider)
    instrumentors.append(sqlalchemy)
    httpx = HTTPXClientInstrumentor()
    httpx.instrument(tracer_provider=provider)
    instrumentors.append(httpx)

    return _State(
        provider=provider,
        tracer=provider.get_tracer("conjectures"),
        get_current_span=trace.get_current_span,
        span_kind_server=trace.SpanKind.SERVER,
        status_error=trace.Status(trace.StatusCode.ERROR),
        instrumentors=instrumentors,
    )


class _Throttle(logging.Filter):
    """Pass at most one record per `seconds` for each logger at or below the one it is on."""

    def __init__(self, seconds: float) -> None:
        super().__init__()
        self._seconds = seconds
        self._last: dict[str, float] = {}

    def filter(self, record: logging.LogRecord) -> bool:
        last = self._last.get(record.name)
        if last is not None and record.created - last < self._seconds:
            return False
        self._last[record.name] = record.created
        return True


def _ratio(raw: str) -> float:
    """`AXIOM_TRACES_SAMPLE_RATIO`, clamped to [0, 1]. Unparseable falls back to the default."""
    raw = raw.strip()
    if not raw:
        return DEFAULT_SAMPLE_RATIO
    try:
        value = float(raw)
    except ValueError:
        log.warning("AXIOM_TRACES_SAMPLE_RATIO=%r is not a number; using %s", raw,
                    DEFAULT_SAMPLE_RATIO)
        return DEFAULT_SAMPLE_RATIO
    if value != value:  # NaN
        return DEFAULT_SAMPLE_RATIO
    return min(1.0, max(0.0, value))


def _root_sampler(delegate: Any) -> Any:
    """Starts a trace only for a server span or a marked unit of work; defers to `delegate` then."""
    from opentelemetry.sdk.trace.sampling import Decision, Sampler, SamplingResult
    from opentelemetry.trace import SpanKind

    class _RootSampler(Sampler):
        def should_sample(
            self,
            parent_context: Any,
            trace_id: int,
            name: str,
            kind: Any = None,
            attributes: Any = None,
            links: Sequence[Any] | None = None,
            trace_state: Any = None,
        ) -> SamplingResult:
            if kind == SpanKind.SERVER or (attributes and attributes.get(WORK_ATTRIBUTE)):
                return delegate.should_sample(
                    parent_context, trace_id, name, kind, attributes, links, trace_state
                )
            return SamplingResult(Decision.DROP)

        def get_description(self) -> str:
            return f"RootSampler{{{delegate.get_description()}}}"

    return _RootSampler()


def _redactor() -> Any:
    """A span processor that strips query strings from URL attributes as each span starts.

    `on_start` rather than at export, because the SDK hands processors a mutable span only then,
    and the instrumentations set their URL attributes when the span is created.
    """
    from opentelemetry.sdk.trace import SpanProcessor

    class _Redact(SpanProcessor):
        def on_start(self, span: Any, parent_context: Any = None) -> None:
            attributes = span.attributes or {}
            for key in _URL_ATTRIBUTES:
                value = attributes.get(key)
                if isinstance(value, str):
                    span.set_attribute(key, _without_query(value))
            for key in _TARGET_ATTRIBUTES:
                value = attributes.get(key)
                if isinstance(value, str):
                    span.set_attribute(key, value.split("?", 1)[0])
            for key in _QUERY_ATTRIBUTES:
                if key in attributes:
                    span.set_attribute(key, "")

    return _Redact()


def _without_query(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    # Credentials in the authority (`https://user:pass@host`) go too.
    netloc = parts.netloc.rsplit("@", 1)[-1]
    return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, "", ""))


@contextlib.contextmanager
def server_span(method: str) -> Iterator[Any]:
    """The span for one HTTP request, current for everything the request does. None when off.

    Named by method alone until `finish_server_span` knows the route: the template is only set
    once routing has run, which is inside this block.
    """
    state = _state
    if state is None:
        yield None
        return
    with state.tracer.start_as_current_span(
        method or "HTTP",
        kind=state.span_kind_server,
        attributes={"http.request.method": method},
        # The request event and `unexpected_error` already carry the traceback; the span records
        # that it failed, and the status, without a second copy of it.
        record_exception=False,
        set_status_on_exception=True,
    ) as span:
        yield span


def finish_server_span(
    span: Any, *, method: str, route: str | None, status: int | None, request_id: str | None
) -> None:
    """Name the request span after its route and record the outcome. A no-op for None."""
    state = _state
    if span is None or state is None:
        return
    try:
        if route:
            span.update_name(f"{method} {route}")
            span.set_attribute("http.route", route)
        if status is not None:
            span.set_attribute("http.response.status_code", status)
            if status >= 500:
                span.set_status(state.status_error)
        if request_id is not None:
            span.set_attribute(REQUEST_ID_ATTRIBUTE, request_id)
    except Exception:  # noqa: BLE001 — a telemetry bug must not surface as a request failure
        log.debug("could not finish the request span", exc_info=True)


def current_trace_ids() -> dict[str, str]:
    """`trace_id` and `span_id` of the current sampled span, for an event; empty otherwise.

    Unsampled spans are left out: their trace is never exported, and an id that leads nowhere is
    worse than none.
    """
    state = _state
    if state is None:
        return {}
    context = state.get_current_span().get_span_context()
    if not context.is_valid or not context.trace_flags.sampled:
        return {}
    return {"trace_id": f"{context.trace_id:032x}", "span_id": f"{context.span_id:016x}"}


def shutdown_tracing() -> None:
    """Flush what is queued, uninstrument, and stop. Idempotent. Bounded by the export timeout."""
    global _state
    state, _state = _state, None
    if state is None:
        return
    for instrumentor in state.instrumentors:
        with contextlib.suppress(Exception):
            instrumentor.uninstrument()
    with contextlib.suppress(Exception):
        state.provider.shutdown()


__all__ = [
    "REQUEST_ID_ATTRIBUTE",
    "WORK_ATTRIBUTE",
    "configure_tracing",
    "current_trace_ids",
    "enabled",
    "finish_server_span",
    "server_span",
    "shutdown_tracing",
    "traces_dataset",
]
