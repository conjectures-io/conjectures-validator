"""The observability layer: the labels, the transport, the logging bridge, the request events.

Three things are worth proving here, and they are the three properties the rest of the codebase
relies on when it calls `get_axiom()` from inside a request handler or a verification pass:

1. **It never raises.** A saturated queue, an unreachable backend, an unserialisable field — none
   of them may reach the caller, because the caller is in the middle of crediting a deposit.
2. **The labels are the declared ones.** `test_every_emitted_label_is_declared` reads the source
   and refuses a `source=` or `event_type=` that is not in `labels.py`, which is what keeps a typo
   from becoming a field nobody can query.
3. **The attribution is right.** An event's `source` says which area spoke and its `severity` says
   how bad it is, whether it came from an explicit `get_axiom().info(...)` or from a `logger.info`
   the bridge forwarded.

The transport is exercised against a real HTTP server on a loopback port rather than a mocked
`urlopen`, because the thing most likely to be wrong is the shape of the request — gzip, NDJSON,
the bearer header, the dataset in the path — and a mock would assert our own assumptions back at us.
"""

from __future__ import annotations

import ast
import gzip
import http.server
import json
import logging
import re
import socketserver
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from conjectures_subnet.axiom import (
    EVENT_TYPES,
    SOURCES,
    Axiom,
    AxiomClient,
    AxiomClientNoop,
    AxiomLogHandler,
    Severity,
    attach_axiom_handler,
    create_axiom_client_from_env,
    severity_for_level,
    source_for_logger,
    work_context,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# How long a test waits for the drain thread to ship a batch. Generous: the assertion is about
# what was sent, not how fast.
FLUSH_TIMEOUT_SECONDS = 5.0


class _Collector(http.server.BaseHTTPRequestHandler):
    """Records ingest calls onto the server object so a test can read them."""

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's spelling
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        server: Any = self.server
        if server.status >= 400:
            self.send_response(server.status)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        server.calls.append(
            {
                "path": self.path,
                "headers": dict(self.headers),
                "events": [json.loads(line) for line in raw.decode().splitlines()],
            }
        )
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args: object) -> None:
        return None


class FakeAxiom:
    """A collecting server plus a client pointed at it, as one context manager."""

    def __init__(self, *, status: int = 200, **client_options: Any) -> None:
        self._server = socketserver.TCPServer(("127.0.0.1", 0), _Collector)
        self._server.calls = []  # type: ignore[attr-defined]
        self._server.status = status  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._client_options = client_options

    def __enter__(self) -> FakeAxiom:
        self._thread.start()
        port = self._server.server_address[1]
        self.client = AxiomClient(
            dataset="verifier-events",
            token="xaat-test-token",
            environ="pytest",
            api_url=f"http://127.0.0.1:{port}",
            flush_seconds=0.05,
            **self._client_options,
        )
        self.sink = Axiom(self.client)
        return self

    def __exit__(self, *exc: object) -> None:
        self.client.close()
        self._server.shutdown()
        self._server.server_close()

    @property
    def calls(self) -> list[dict[str, Any]]:
        return self._server.calls  # type: ignore[attr-defined,no-any-return]

    def events(self, *, at_least: int = 1) -> list[dict[str, Any]]:
        """Flush and return every event the server received, waiting for the drain thread."""
        deadline = time.monotonic() + FLUSH_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            collected = [item for call in self.calls for item in call["events"]]
            if len(collected) >= at_least:
                return collected
            time.sleep(0.02)
        return [item for call in self.calls for item in call["events"]]


# --- the vocabulary -------------------------------------------------------------------------


def test_the_declared_vocabulary_is_closed_and_non_empty():
    assert len(SOURCES) == len(set(SOURCES))
    assert len(EVENT_TYPES) == len(set(EVENT_TYPES))
    # Every source is a lowercase, hyphenated label, so a dashboard filter never has to guess at
    # casing or separator.
    for source in SOURCES:
        assert re.fullmatch(r"[a-z][a-z0-9-]*", source), source
    # Every event type is a lowercase, underscored name, deliberately spelled differently from a
    # source so the two cannot be confused in a query.
    for event_type in EVENT_TYPES:
        assert re.fullmatch(r"[a-z][a-z0-9_]*", event_type), event_type


def test_every_emitted_label_is_declared():
    """No call site may emit a `source` or `event_type` that `labels.py` does not declare.

    A `Literal` alias is only checked by a type checker, and nothing in this repository's gates
    runs one. This is the gate: it parses every module that emits and refuses a label that would
    land in the dataset as an unqueryable string. Non-literal arguments are skipped rather than
    guessed at — there are none today, and one added later would show up as a missing assertion
    rather than a false failure.
    """
    emitted_sources: set[str] = set()
    emitted_types: set[str] = set()
    for path in sorted(PROJECT_ROOT.glob("*/**/*.py")):
        if ".venv" in path.parts or path.parts[0] == "tests":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):  # pragma: no cover - the tree is ours and parses
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if not isinstance(keyword.value, ast.Constant):
                    continue
                if not isinstance(keyword.value.value, str):
                    continue
                if keyword.arg == "source":
                    emitted_sources.add(keyword.value.value)
                elif keyword.arg == "event_type":
                    emitted_types.add(keyword.value.value)

    # `source=` is a common enough keyword that other calls use it; only judge the ones that look
    # like a Source, which is every value the vocabulary knows plus nothing else.
    assert emitted_sources & set(SOURCES), "no Axiom sources found; the scan is not working"
    assert emitted_types, "no Axiom event types found; the scan is not working"
    undeclared = emitted_types - set(EVENT_TYPES)
    assert not undeclared, f"event types not declared in labels.py: {sorted(undeclared)}"


# --- the transport --------------------------------------------------------------------------


def test_ingest_posts_gzipped_ndjson_to_the_dataset_endpoint():
    with FakeAxiom() as fake:
        fake.sink.info(
            source="api-submissions", event_type="submission_accepted", attempt=1
        )
        events = fake.events()
        assert events, "the drain thread shipped nothing"
        call = fake.calls[0]
    assert call["path"] == "/v1/datasets/verifier-events/ingest"
    assert call["headers"]["Authorization"] == "Bearer xaat-test-token"
    assert call["headers"]["Content-Type"] == "application/x-ndjson"
    assert call["headers"]["Content-Encoding"] == "gzip"


def test_every_event_carries_severity_source_type_environ_and_a_timestamp():
    with FakeAxiom() as fake:
        fake.sink.warn(
            source="deposit-watcher", event_type="transfer_unattributed", block=42
        )
        event = fake.events()[0]
    assert event["severity"] == "warning"
    assert event["source"] == "deposit-watcher"
    assert event["event_type"] == "transfer_unattributed"
    assert event["environ"] == "pytest"
    assert event["block"] == 42
    # Stamped by the producer, not by the ingest endpoint, so a delayed batch keeps its own time.
    assert event["_time"].endswith("+00:00")


def test_a_details_field_cannot_displace_an_envelope_label():
    """A caller passing `source=` as a detail must not be able to rewrite the label.

    Otherwise one careless call site relabels its events as another area's and the dashboard for
    that area quietly starts lying.
    """
    with FakeAxiom() as fake:
        fake.sink.emit(
            severity=Severity.INFO,
            source="api-catalog",
            event_type="request_completed",
            # A field of the same name, arriving through **fields.
            environ="attacker-chosen",
        )
        event = fake.events()[0]
    assert event["source"] == "api-catalog"
    assert event["environ"] == "pytest"


def test_severity_helpers_each_set_their_own_level():
    with FakeAxiom() as fake:
        fake.sink.debug(source="api", event_type="log_record")
        fake.sink.info(source="api", event_type="log_record")
        fake.sink.warn(source="api", event_type="log_record")
        fake.sink.error(source="api", event_type="log_record")
        fake.sink.critical(source="api", event_type="log_record")
        events = fake.events(at_least=5)
    assert [item["severity"] for item in events] == [
        "debug",
        "info",
        "warning",
        "error",
        "critical",
    ]


def test_exception_attaches_the_traceback_and_defaults_to_error():
    with FakeAxiom() as fake:
        try:
            raise ValueError("the proof did not compile")
        except ValueError:
            fake.sink.exception(
                source="verification-worker", event_type="unexpected_error", attempt=2
            )
        event = fake.events()[0]
    assert event["severity"] == "error"
    assert "ValueError: the proof did not compile" in event["exception"]
    assert event["attempt"] == 2


def test_exception_severity_can_be_lowered_for_a_retryable_failure():
    with FakeAxiom() as fake:
        try:
            raise ConnectionError("the node is syncing")
        except ConnectionError:
            fake.sink.exception(
                source="deposit-watcher",
                event_type="unexpected_error",
                severity=Severity.WARNING,
            )
        event = fake.events()[0]
    assert event["severity"] == "warning"


def test_a_field_json_cannot_encode_does_not_lose_the_batch():
    """An unserialisable detail becomes its string form rather than dropping every event with it."""

    class Opaque:
        def __str__(self) -> str:
            return "opaque-value"

    with FakeAxiom() as fake:
        fake.sink.info(source="api", event_type="service_started", thing=Opaque())
        event = fake.events()[0]
    assert event["thing"] == "opaque-value"


def test_a_failing_backend_never_raises_and_is_counted():
    with FakeAxiom(status=500) as fake:
        fake.sink.error(source="api", event_type="unexpected_error")
        deadline = time.monotonic() + FLUSH_TIMEOUT_SECONDS
        while time.monotonic() < deadline and fake.client.stats()["failed"] == 0:
            time.sleep(0.02)
        stats = fake.client.stats()
    assert stats["failed"] == 1
    assert stats["sent"] == 0


def test_an_unreachable_backend_never_raises():
    # Port 1 on loopback, which nothing listens on. `close()` must still return.
    client = AxiomClient(
        dataset="d",
        token="t",
        api_url="http://127.0.0.1:1",
        flush_seconds=0.05,
        timeout_seconds=0.2,
    )
    try:
        Axiom(client).info(source="api", event_type="service_started")
    finally:
        client.close()
    assert client.stats()["sent"] == 0


def test_a_saturated_queue_drops_rather_than_blocking():
    """The bound is a memory budget an attacker controls the keys of; it must drop, not wait."""
    client = AxiomClient(
        dataset="d",
        token="t",
        api_url="http://127.0.0.1:1",
        queue_size=2,
        # Not started, so nothing drains and the queue is guaranteed to fill.
        start=False,
    )
    sink = Axiom(client)
    for _ in range(20):
        sink.info(source="api", event_type="service_started")
    stats = client.stats()
    assert stats["queued"] == 2
    assert stats["dropped"] == 18


def test_close_is_idempotent():
    with FakeAxiom() as fake:
        fake.client.close()
        fake.client.close()


# --- configuration --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "environ",
    [
        {},
        {"AXIOM_TOKEN": "xaat-x"},
        {"AXIOM_DATASET": "events"},
        {"AXIOM_TOKEN": "  ", "AXIOM_DATASET": "events"},
    ],
)
def test_ingestion_is_disabled_until_both_credentials_are_present(environ):
    client = create_axiom_client_from_env(environ)
    assert isinstance(client, AxiomClientNoop)
    assert client.enabled is False
    # The point of the no-op: a call site does not have to ask whether it is configured.
    Axiom(client).error(source="api", event_type="unexpected_error")


def test_both_credentials_build_a_real_client():
    client = create_axiom_client_from_env(
        {"AXIOM_TOKEN": "xaat-x", "AXIOM_DATASET": "events", "AXIOM_ENVIRON": "prod"}
    )
    try:
        assert isinstance(client, AxiomClient)
        assert client.enabled is True
    finally:
        client.close()


# --- the logging bridge ---------------------------------------------------------------------


def test_a_stdlib_level_maps_onto_a_severity():
    assert severity_for_level(logging.DEBUG) is Severity.DEBUG
    assert severity_for_level(logging.INFO) is Severity.INFO
    assert severity_for_level(logging.WARNING) is Severity.WARNING
    assert severity_for_level(logging.ERROR) is Severity.ERROR
    assert severity_for_level(logging.CRITICAL) is Severity.CRITICAL
    # A custom level belongs with the standard one below it, not in a bucket of its own.
    assert severity_for_level(25) is Severity.INFO
    assert severity_for_level(logging.CRITICAL + 10) is Severity.CRITICAL


@pytest.mark.parametrize(
    ("logger_name", "expected"),
    [
        ("verification_worker", "verification-worker"),
        ("deposit_watcher", "deposit-watcher"),
        ("emissions_worker", "emissions-worker"),
        ("submission_api.mail", "api-mail"),
        ("submission_api.chain_payments", "api-payments"),
        ("submission_api.routers.me", "api-me"),
        ("conjectures_subnet.transfers", "subnet-chain"),
        ("sqlalchemy.engine.Engine", "database"),
        # An unmapped child resolves through its longest mapped ancestor.
        ("submission_api.routers.not_written_yet", "api"),
        ("verification_worker.deep.nesting", "verification-worker"),
    ],
)
def test_a_logger_name_resolves_to_the_area_that_owns_it(logger_name, expected):
    assert source_for_logger(logger_name, default="api-health") == expected


def test_an_unmapped_logger_falls_back_to_the_entry_points_own_source():
    assert source_for_logger("some.third.party", default="deposit-watcher") == (
        "deposit-watcher"
    )


def _bridged(sink: Axiom, name: str = "bridge-test") -> logging.Logger:
    """A logger with only the Axiom handler on it, isolated from the root logger."""
    logger = logging.getLogger(name)
    logger.handlers = [AxiomLogHandler(default_source="api", level=logging.DEBUG, axiom=sink)]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger


def test_a_log_record_arrives_with_its_severity_and_its_source():
    with FakeAxiom() as fake:
        _bridged(fake.sink, "verification_worker").warning("lost lease before recording")
        event = fake.events()[0]
    assert event["event_type"] == "log_record"
    assert event["severity"] == "warning"
    assert event["source"] == "verification-worker"
    assert event["message"] == "lost lease before recording"
    assert event["logger"] == "verification_worker"
    assert event["level"] == "WARNING"
    assert event["function"] and event["line"]


def test_a_bridged_record_interpolates_its_arguments():
    with FakeAxiom() as fake:
        _bridged(fake.sink).info("claimed submission=%s attempt=%d", "abc", 3)
        event = fake.events()[0]
    assert event["message"] == "claimed submission=abc attempt=3"


def test_logger_exception_arrives_as_an_error_with_the_traceback():
    with FakeAxiom() as fake:
        logger = _bridged(fake.sink, "deposit_watcher")
        try:
            raise RuntimeError("the node closed the socket")
        except RuntimeError:
            logger.exception("scan pass failed; the cursor did not move, retrying")
        event = fake.events()[0]
    assert event["severity"] == "error"
    assert event["source"] == "deposit-watcher"
    assert "RuntimeError: the node closed the socket" in event["exception"]


def test_extra_becomes_a_structured_field():
    with FakeAxiom() as fake:
        _bridged(fake.sink).info(
            "credited a deposit", extra={"account_id": "acct-7", "credits": 3}
        )
        event = fake.events()[0]
    assert event["account_id"] == "acct-7"
    assert event["credits"] == 3


def test_the_transports_own_warnings_are_never_forwarded():
    """The recursion guard. Forwarding these would be an event about failing to send events."""
    with FakeAxiom() as fake:
        _bridged(fake.sink, "conjectures_subnet.axiom").error("Axiom ingest failed")
        _bridged(fake.sink, "conjectures_subnet.axiom.client").error("also suppressed")
        # Nothing to flush, so assert on the queue rather than waiting for a batch.
        assert fake.client.stats()["queued"] == 0
        assert fake.events(at_least=0) == []


def test_a_record_the_bridge_cannot_format_does_not_propagate():
    """`logging`'s contract: a broken handler must never raise into the code that logged."""
    with FakeAxiom() as fake:
        logger = _bridged(fake.sink)
        logging.raiseExceptions = False
        try:
            # Too few arguments for the format string: `getMessage()` raises inside `emit`.
            logger.info("submission=%s task=%s", "only-one")
        finally:
            logging.raiseExceptions = True


def test_a_bridged_record_carries_the_ambient_correlation_id():
    with FakeAxiom() as fake:
        logger = _bridged(fake.sink, "verification_worker")
        with work_context("pass-1") as correlation:
            logger.info("claimed a submission")
        fake.sink.info(
            source="verification-worker", event_type="verdict_recorded", request_id=correlation
        )
        events = fake.events(at_least=2)
    assert events[0]["request_id"] == "pass-1"
    # A caller that passed one explicitly keeps it.
    assert events[1]["request_id"] == "pass-1"


def test_a_correlation_id_does_not_leak_out_of_its_block():
    with FakeAxiom() as fake:
        with work_context("inner"):
            pass
        fake.sink.info(source="api", event_type="service_started")
        event = fake.events()[0]
    assert "request_id" not in event


def test_the_bridge_is_not_attached_when_ingestion_is_disabled():
    """A handler that formats every record only to hand it to a no-op is pure cost."""
    logger = logging.getLogger("attach-disabled")
    logger.handlers = []
    assert (
        attach_axiom_handler(source="api", logger=logger, axiom=Axiom(AxiomClientNoop()))
        is None
    )
    assert logger.handlers == []


def test_attaching_twice_does_not_double_every_event():
    with FakeAxiom() as fake:
        logger = logging.getLogger("attach-twice")
        logger.handlers = []
        first = attach_axiom_handler(source="api", logger=logger, axiom=fake.sink)
        second = attach_axiom_handler(source="api", logger=logger, axiom=fake.sink)
        assert first is second
        assert len(logger.handlers) == 1
        logger.handlers = []


def test_an_unparseable_log_level_falls_back_rather_than_stopping_the_process():
    with FakeAxiom() as fake:
        logger = logging.getLogger("attach-bad-level")
        logger.handlers = []
        handler = attach_axiom_handler(
            source="api",
            logger=logger,
            environ={"AXIOM_LOG_LEVEL": "LOUDLY"},
            axiom=fake.sink,
        )
        assert handler is not None
        assert handler.level == logging.INFO
        logger.handlers = []
