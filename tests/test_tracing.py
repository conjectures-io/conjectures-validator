"""Traces: one server span per request, the work underneath it nested, nothing secret on any of it.

The contract under test is `conjectures_subnet.tracing` plus the span `AxiomRequestMiddleware` opens:

1. **Off unless asked, and harmless without the packages.** No traces dataset, or no `opentelemetry`
   at all, is a process that runs exactly as it did.
2. **A request is one trace.** Its span is named by the route template, carries the request id the
   response header carries, and parents the SQL and outbound HTTP spans the request caused.
3. **Nothing secret leaves.** No raw path, no query string, on any span.
4. **Only a request starts a trace.** A query or an HTTP call with no parent is dropped instead of
   exported as a one-span trace.
5. **Events lead to traces.** An event emitted inside a sampled span carries its ids.

Spans are collected with the SDK's in-memory exporter, through the same provider, sampler and
redaction the OTLP path uses; only the exporter differs.
"""

from __future__ import annotations

import http.server
import socketserver
import sys
import threading
from typing import Any

import pytest

pytest.importorskip("opentelemetry.sdk")

import httpx  # noqa: E402
import sqlalchemy  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)
from opentelemetry.trace import SpanKind, StatusCode  # noqa: E402

from conjectures_subnet import tracing  # noqa: E402
from conjectures_subnet.axiom import Axiom, AxiomClientNoop  # noqa: E402
from submission_api import errors  # noqa: E402
from submission_api.observability import (  # noqa: E402
    REQUEST_EVENTS_OFF,
    REQUEST_ID_HEADER,
    AxiomRequestMiddleware,
)


class Recorder(Axiom):
    """An `Axiom` that keeps its events, ambient fields included, instead of shipping them."""

    def __init__(self) -> None:
        super().__init__(AxiomClientNoop())
        self.events: list[dict[str, Any]] = []

    def emit(self, *, severity, source, event_type, **fields: Any) -> None:
        self.events.append({"event_type": event_type, **self._details(fields)})


class _Echo(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's spelling
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args: object) -> None:
        return None


@pytest.fixture
def upstream():
    """A loopback HTTP server, so httpx goes through the transport the instrumentation wraps."""
    server = socketserver.TCPServer(("127.0.0.1", 0), _Echo)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _configure(ratio: str = "1.0") -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    assert tracing.configure_tracing(
        service_name="pytest",
        environ={
            "AXIOM_TRACES_SAMPLE_RATIO": ratio,
            "AXIOM_ENVIRON": "pytest",
            "CONJECTURES_REVISION": "abc123",
        },
        exporter=exporter,
    )
    return exporter


@pytest.fixture
def spans():
    """Tracing on, with every span kept in memory. Torn down so no other test is traced."""
    exporter = _configure()
    yield exporter
    tracing.shutdown_tracing()


def finished(exporter: InMemorySpanExporter) -> list[Any]:
    assert tracing._state is not None
    tracing._state.provider.force_flush()
    return list(exporter.get_finished_spans())


@pytest.fixture
def recorder(monkeypatch) -> Recorder:
    collected = Recorder()
    monkeypatch.setattr("submission_api.observability.get_axiom", lambda: collected)
    monkeypatch.setattr("submission_api.errors.get_axiom", lambda: collected)
    return collected


def build_app(recorder: Recorder, *, upstream: str = "", mode: str = "all") -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(errors.ApiError, errors.api_error_handler)
    app.add_exception_handler(Exception, errors.unhandled_error_handler)
    # Created inside the traced process, as the API's engines are: after `configure_tracing`.
    engine = sqlalchemy.create_engine("sqlite://")

    @app.get("/v1/results/{slug}")
    def result(slug: str) -> dict[str, str]:
        with engine.connect() as connection:
            connection.execute(sqlalchemy.text("SELECT 1"))
        httpx.get(f"{upstream}/rates?api_key=hunter2&fiat=usd")
        recorder.info(source="api-results", event_type="log_record", slug=slug)
        return {"slug": slug}

    @app.get("/v1/submissions/{submission_id}")
    def submission(submission_id: str) -> dict[str, str]:
        raise RuntimeError("the store went away")

    app.add_middleware(AxiomRequestMiddleware, trusted_proxy_hops=0, mode=mode)
    return app


def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# --- off --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({"AXIOM_DATASET": "conjectures-validator"}, "conjectures-validator-traces"),
        ({"AXIOM_DATASET": " cv ", "AXIOM_TRACES_DATASET": " "}, "cv-traces"),
        ({"AXIOM_DATASET": "cv", "AXIOM_TRACES_DATASET": "spans"}, "spans"),
        ({"AXIOM_TRACES_DATASET": "spans"}, "spans"),
        ({"AXIOM_DATASET": "cv", "AXIOM_TRACES_DATASET": "off"}, ""),
        ({"AXIOM_DATASET": "cv", "AXIOM_TRACES_DATASET": "OFF"}, ""),
        ({}, ""),
    ],
)
def test_traces_follow_the_events_dataset_unless_told_otherwise(environ, expected):
    assert tracing.traces_dataset(environ) == expected


def test_without_axiom_configured_nothing_is_configured():
    assert not tracing.configure_tracing(service_name="pytest", environ={"AXIOM_TOKEN": "xaat-x"})
    assert not tracing.configure_tracing(
        service_name="pytest", environ={"AXIOM_TRACES_DATASET": "traces"}
    )
    assert not tracing.configure_tracing(
        service_name="pytest",
        environ={"AXIOM_TOKEN": "xaat-x", "AXIOM_DATASET": "cv", "AXIOM_TRACES_DATASET": "off"},
    )
    assert not tracing.enabled()
    assert tracing.current_trace_ids() == {}
    with tracing.server_span("GET") as span:
        assert span is None


def test_without_the_packages_tracing_is_a_no_op(monkeypatch):
    # `None` in sys.modules makes the import raise ImportError, which is what a process built
    # without the `tracing` extra sees.
    monkeypatch.setitem(sys.modules, "opentelemetry.instrumentation.httpx", None)
    assert not tracing.configure_tracing(
        service_name="pytest",
        environ={"AXIOM_TOKEN": "xaat-x", "AXIOM_DATASET": "cv"},
    )
    assert not tracing.enabled()


def test_with_tracing_off_the_middleware_is_unchanged(recorder):
    response = client(build_app(recorder, mode=REQUEST_EVENTS_OFF)).get(
        "/v1/submissions/1"
    )
    assert response.status_code == 500
    assert REQUEST_ID_HEADER not in response.headers


@pytest.mark.parametrize(
    ("raw", "expected"), [("", 1.0), ("0.25", 0.25), ("7", 1.0), ("-1", 0.0), ("most", 1.0)]
)
def test_the_sample_ratio_is_clamped_and_never_fatal(raw, expected):
    assert tracing._ratio(raw) == expected


# --- a request is one trace -------------------------------------------------------------------


def test_a_request_is_one_trace_named_by_its_route(spans, recorder, upstream):
    response = client(build_app(recorder, upstream=upstream)).get("/v1/results/erdos-1?x=1")
    assert response.status_code == 200

    collected = finished(spans)
    (server,) = [span for span in collected if span.kind == SpanKind.SERVER]
    assert server.name == "GET /v1/results/{slug}"
    assert server.attributes["http.route"] == "/v1/results/{slug}"
    assert server.attributes["http.response.status_code"] == 200
    assert server.attributes[tracing.REQUEST_ID_ATTRIBUTE] == response.headers[REQUEST_ID_HEADER]
    assert server.resource.attributes["service.version"] == "abc123"

    children = [span for span in collected if span is not server]
    assert {span.kind for span in children} == {SpanKind.CLIENT}
    assert len(children) >= 2, [span.name for span in children]  # one SQL, one HTTP
    for span in children:
        assert span.context.trace_id == server.context.trace_id


def test_no_span_carries_the_path_or_a_query_string(spans, recorder, upstream):
    client(build_app(recorder, upstream=upstream)).get("/v1/results/erdos-1?token=secret")

    for span in finished(spans):
        rendered = repr(dict(span.attributes)) + span.name
        assert "erdos-1" not in rendered
        assert "secret" not in rendered
        assert "hunter2" not in rendered
        assert "api_key" not in rendered


def test_a_5xx_marks_the_span_failed(spans, recorder):
    response = client(build_app(recorder)).get("/v1/submissions/1")
    assert response.status_code == 500

    (server,) = [span for span in finished(spans) if span.kind == SpanKind.SERVER]
    assert server.name == "GET /v1/submissions/{submission_id}"
    assert server.status.status_code == StatusCode.ERROR
    assert server.attributes["http.response.status_code"] == 500


def test_with_request_events_off_the_request_is_still_traced(spans, recorder):
    response = client(build_app(recorder, mode=REQUEST_EVENTS_OFF)).get("/v1/submissions/1")

    assert REQUEST_ID_HEADER not in response.headers
    assert not [item for item in recorder.events if item["event_type"] == "request_completed"]
    (server,) = [span for span in finished(spans) if span.kind == SpanKind.SERVER]
    assert tracing.REQUEST_ID_ATTRIBUTE not in server.attributes


# --- only a request starts a trace ------------------------------------------------------------


def test_a_query_or_a_call_with_no_parent_is_not_exported(spans, upstream):
    engine = sqlalchemy.create_engine("sqlite://")
    with engine.connect() as connection:
        connection.execute(sqlalchemy.text("SELECT 1"))
    httpx.get(f"{upstream}/poll")

    assert finished(spans) == []


def test_a_unit_of_work_may_start_a_trace(spans):
    assert tracing._state is not None
    with tracing._state.tracer.start_as_current_span(
        "pass", attributes={tracing.WORK_ATTRIBUTE: True}
    ):
        pass

    assert [span.name for span in finished(spans)] == ["pass"]


def test_a_ratio_of_zero_keeps_no_trace(recorder):
    exporter = _configure(ratio="0")
    try:
        client(build_app(recorder)).get("/v1/submissions/1")
        assert finished(exporter) == []
        # An unsampled trace is never exported, so no event may point at it.
        assert all("trace_id" not in item for item in recorder.events)
    finally:
        tracing.shutdown_tracing()


# --- events lead to traces --------------------------------------------------------------------


def test_events_inside_a_request_carry_its_trace(spans, recorder, upstream):
    response = client(build_app(recorder, upstream=upstream)).get("/v1/results/erdos-1")

    (server,) = [span for span in finished(spans) if span.kind == SpanKind.SERVER]
    trace_id = f"{server.context.trace_id:032x}"
    in_handler = [item for item in recorder.events if item["event_type"] == "log_record"]
    request = [item for item in recorder.events if item["event_type"] == "request_completed"]
    assert in_handler and request
    for item in in_handler + request:
        assert item["trace_id"] == trace_id
        assert item["request_id"] == response.headers[REQUEST_ID_HEADER]


def test_shutdown_turns_everything_back_off(upstream):
    exporter = _configure()
    tracing.shutdown_tracing()
    tracing.shutdown_tracing()  # idempotent

    assert not tracing.enabled()
    engine = sqlalchemy.create_engine("sqlite://")
    with engine.connect() as connection:
        connection.execute(sqlalchemy.text("SELECT 1"))
    assert list(exporter.get_finished_spans()) == []
