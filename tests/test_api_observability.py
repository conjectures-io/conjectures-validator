"""Per-request Axiom events: one per request, attributed to the endpoint that answered.

Built on a purpose-made app rather than the real one, deliberately. These tests are about the
middleware's contract — one event, the right severity, the route template and not the path, a
correlation id shared with the error event — and the real app needs PostgreSQL, a task pool and a
pin lock to answer anything at all. The exception handlers under test are the real ones from
`submission_api.errors`, and the layer under test is the real `AxiomRequestMiddleware`.

What the real app contributes is the wiring, and `test_the_real_app_installs_the_layer_outermost`
covers that: the layer has to be outermost or it cannot see the statuses that middleware below it
generates without a route ever running.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from conjectures_subnet.axiom import Axiom, AxiomClientNoop, Severity
from submission_api import errors
from submission_api.observability import (
    REQUEST_EVENTS_ALL,
    REQUEST_EVENTS_FAILURES,
    REQUEST_EVENTS_OFF,
    REQUEST_ID_HEADER,
    AxiomRequestMiddleware,
    request_event_mode,
    severity_for_status,
    source_for_path,
)


class Recorder(Axiom):
    """An `Axiom` that keeps its events instead of shipping them."""

    def __init__(self) -> None:
        super().__init__(AxiomClientNoop())
        self.events: list[dict[str, Any]] = []

    def emit(self, *, severity, source, event_type, **fields: Any) -> None:
        self.events.append(
            {
                "severity": str(severity),
                "source": source,
                "event_type": event_type,
                **self._details(fields),
            }
        )

    def of_type(self, event_type: str) -> list[dict[str, Any]]:
        return [item for item in self.events if item["event_type"] == event_type]


def build_app(recorder: Recorder, *, mode: str = REQUEST_EVENTS_ALL) -> FastAPI:
    """A handful of routes shaped like the real ones, behind the real middleware and handlers."""
    app = FastAPI()
    app.add_exception_handler(errors.ApiError, errors.api_error_handler)
    app.add_exception_handler(Exception, errors.unhandled_error_handler)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/results/{slug}")
    def result(slug: str) -> dict[str, str]:
        return {"slug": slug}

    @app.get("/v1/me/credits")
    def credits() -> dict[str, int]:
        raise errors.PaymentRequired("no credits held")

    @app.get("/v1/submissions/{submission_id}")
    def submission(submission_id: str) -> dict[str, str]:
        raise RuntimeError("the store went away")

    app.add_middleware(AxiomRequestMiddleware, trusted_proxy_hops=0, mode=mode)
    return app


@pytest.fixture
def recorder(monkeypatch) -> Recorder:
    collected = Recorder()
    monkeypatch.setattr(
        "submission_api.observability.get_axiom", lambda: collected
    )
    monkeypatch.setattr("submission_api.errors.get_axiom", lambda: collected)
    return collected


def client(app: FastAPI) -> TestClient:
    # `raise_server_exceptions=False` so a 500 is answered rather than re-raised into the test,
    # which is what a real deployment does.
    return TestClient(app, raise_server_exceptions=False)


# --- attribution ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/v1/submissions", "api-submissions"),
        ("/v1/submissions/1234", "api-submissions"),
        # The intent endpoints share a prefix with the extrinsic path and must not be swallowed
        # by it: the longest prefix wins.
        ("/v1/submissions/intents", "api-intents"),
        ("/v1/submissions/intents/abc/confirm", "api-intents"),
        ("/v1/submissions/preflight", "api-intents"),
        ("/v1/auth/email/verify", "api-auth"),
        ("/v1/me", "api-me"),
        ("/v1/me/credits/ledger", "api-me"),
        ("/v1/catalog/conjectures/erdos-1", "api-catalog"),
        ("/v1/results", "api-results"),
        ("/v1/tasks", "api-tasks"),
        ("/v1/system/status", "api-system"),
        ("/healthz", "api-health"),
        ("/readyz", "api-health"),
        # Not a prefix match on a partial segment: `/v1/metrics` is not the account surface.
        ("/v1/metrics", "api"),
        ("/nothing/here", "api"),
    ],
)
def test_a_path_resolves_to_the_area_of_the_api_that_owns_it(path, expected):
    assert source_for_path(path) == expected


def test_a_status_resolves_to_a_severity():
    assert severity_for_status(200) is Severity.INFO
    assert severity_for_status(304) is Severity.INFO
    # A refusal is a warning, not an error: most of them are the API working correctly.
    assert severity_for_status(402) is Severity.WARNING
    assert severity_for_status(429) is Severity.WARNING
    assert severity_for_status(500) is Severity.ERROR
    assert severity_for_status(503) is Severity.ERROR


# --- the request event ----------------------------------------------------------------------


def test_a_served_request_reports_its_endpoint_template_and_not_its_path(recorder):
    with client(build_app(recorder)) as http:
        assert http.get("/v1/results/erdos-1").status_code == 200
    (event,) = recorder.of_type("request_completed")
    # The template. A per-path breakdown of a slug-keyed route has one row per request.
    assert event["endpoint"] == "/v1/results/{slug}"
    assert "erdos-1" not in event["endpoint"]
    assert event["source"] == "api-results"
    assert event["severity"] == "info"
    assert event["status"] == 200
    assert event["method"] == "GET"
    assert event["duration_ms"] >= 0


def test_a_refusal_reports_its_reason_code_on_the_same_event(recorder):
    """The reason code is the actionable half of a 4xx, and it belongs on the request's own row."""
    with client(build_app(recorder)) as http:
        assert http.get("/v1/me/credits").status_code == 402
    (event,) = recorder.of_type("request_completed")
    assert event["severity"] == "warning"
    assert event["source"] == "api-me"
    assert event["status"] == 402
    assert event["reason_code"] == "PAYMENT_NOT_FINALIZED"


def test_an_unmatched_path_reports_the_path_it_was_asked_for(recorder):
    with client(build_app(recorder)) as http:
        assert http.get("/v1/results/erdos-1/nope").status_code == 404
    (event,) = recorder.of_type("request_completed")
    assert event["endpoint"] == "/v1/results/erdos-1/nope"
    assert event["status"] == 404


def test_a_query_string_never_reaches_an_event(recorder):
    """`?token=` on a sign-in callback is a credential. Telemetry is not where it goes."""
    with client(build_app(recorder)) as http:
        http.get("/v1/results/erdos-1?token=super-secret&page=2")
    (event,) = recorder.of_type("request_completed")
    assert "super-secret" not in str(event)
    assert "token" not in event["endpoint"]


def test_liveness_and_readiness_are_never_reported(recorder):
    """At one probe per interval these would drown out everything that matters."""
    with client(build_app(recorder)) as http:
        assert http.get("/healthz").status_code == 200
    assert recorder.events == []


# --- the failure path -----------------------------------------------------------------------


def test_a_500_reports_both_its_outcome_and_its_traceback(recorder):
    """Two events, on purpose, and each carries what the other cannot.

    `request_completed` puts the 5xx on the request stream, where a rate of them is visible.
    `unexpected_error` carries the traceback, which is the only copy a query can reach — the
    stderr one belongs to whichever container happened to serve the request.
    """
    with client(build_app(recorder)) as http:
        assert http.get("/v1/submissions/abc").status_code == 500

    (completed,) = recorder.of_type("request_completed")
    assert completed["status"] == 500
    assert completed["severity"] == "error"
    assert completed["source"] == "api-submissions"
    assert completed["endpoint"] == "/v1/submissions/{submission_id}"

    (failure,) = recorder.of_type("unexpected_error")
    assert failure["severity"] == "error"
    assert failure["reason_code"] == "INTERNAL_ERROR"
    assert failure["endpoint"] == "/v1/submissions/{submission_id}"
    assert "RuntimeError: the store went away" in failure["exception"]


def test_a_500s_two_events_share_one_request_id(recorder):
    """Without this the traceback is a row nobody can tie back to the request that caused it."""
    with client(build_app(recorder)) as http:
        response = http.get("/v1/submissions/abc")
    ids = {item["request_id"] for item in recorder.events}
    assert len(ids) == 1
    # And the client is told the id, so a failing page can quote it.
    assert response.headers[REQUEST_ID_HEADER] == ids.pop()


def test_the_response_never_leaks_internals_to_the_caller(recorder):
    """The events carry the traceback. The response still must not."""
    with client(build_app(recorder)) as http:
        response = http.get("/v1/submissions/abc")
    assert response.json() == {
        "type": "about:blank",
        "title": "Request rejected",
        "status": 500,
        "detail": "internal error",
        "reason_code": "INTERNAL_ERROR",
    }


# --- the correlation id ---------------------------------------------------------------------


def test_every_response_carries_a_unique_request_id(recorder):
    with client(build_app(recorder)) as http:
        first = http.get("/v1/results/a").headers[REQUEST_ID_HEADER]
        second = http.get("/v1/results/b").headers[REQUEST_ID_HEADER]
    assert first != second
    assert len(first) == 16
    assert [item["request_id"] for item in recorder.of_type("request_completed")] == [
        first,
        second,
    ]


def test_a_client_supplied_request_id_is_ignored(recorder):
    """A client-chosen key would let one caller collide its requests with another's."""
    with client(build_app(recorder)) as http:
        response = http.get("/v1/results/a", headers={REQUEST_ID_HEADER: "chosen-by-me"})
    assert response.headers[REQUEST_ID_HEADER] != "chosen-by-me"


# --- volume control -------------------------------------------------------------------------


def test_failures_mode_keeps_the_refusals_and_drops_the_successes(recorder):
    app = build_app(recorder, mode=REQUEST_EVENTS_FAILURES)
    with client(app) as http:
        http.get("/v1/results/erdos-1")
        http.get("/v1/me/credits")
    statuses = [item["status"] for item in recorder.of_type("request_completed")]
    assert statuses == [402]


def test_off_mode_emits_no_request_events_but_keeps_the_error_events(recorder):
    app = build_app(recorder, mode=REQUEST_EVENTS_OFF)
    with client(app) as http:
        http.get("/v1/results/erdos-1")
        assert http.get("/v1/submissions/abc").status_code == 500
    assert recorder.of_type("request_completed") == []
    # Still reported: turning the request stream off is a volume decision, not a decision to stop
    # recording that the API broke.
    assert len(recorder.of_type("unexpected_error")) == 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({}, REQUEST_EVENTS_ALL),
        ({"AXIOM_REQUEST_EVENTS": ""}, REQUEST_EVENTS_ALL),
        ({"AXIOM_REQUEST_EVENTS": "all"}, REQUEST_EVENTS_ALL),
        ({"AXIOM_REQUEST_EVENTS": "FAILURES"}, REQUEST_EVENTS_FAILURES),
        ({"AXIOM_REQUEST_EVENTS": "off"}, REQUEST_EVENTS_OFF),
        # A typo must not silently disable observability.
        ({"AXIOM_REQUEST_EVENTS": "none"}, REQUEST_EVENTS_ALL),
    ],
)
def test_the_request_event_mode_is_read_from_the_environment(value, expected):
    assert request_event_mode(value) == expected


# --- the intake refusal log -----------------------------------------------------------------
#
# `_Audit` is the only trace a refused submission leaves, because payment-gated intake creates no
# submission row. These reach into it directly: the surrounding handler needs PostgreSQL, a task
# pool and a chain payment verifier, and what is under test is that the event is emitted and that
# it survives the `api_rejection_log` write failing.


class FakeSession:
    def __init__(self) -> None:
        self.rolled_back = 0
        self.committed = 0

    async def rollback(self) -> None:
        self.rolled_back += 1

    async def commit(self) -> None:
        self.committed += 1


def audit(session: FakeSession):
    from submission_api.routers.submissions import _Audit

    return _Audit(
        session=session,  # type: ignore[arg-type]
        source_ip="203.0.113.7",
        user_agent="miner-tooling/1.0",
        hotkey_claimed="5C4hrfjw9DjXZTzV3MwzrrAr9P1MJhSrvWGWqi1eSuyUpnhM",
        idempotency_key="key-1",
        task_id="erdos-1",
        task_bundle_sha256="sha256:" + "a" * 64,
        payment_reference="0xdeadbeef",
    )


def refuse(guard, error: Exception) -> None:
    """Drive the context manager the way the handler does, minus the handler.

    `asyncio.run`, matching `tests/test_deposit_watcher.py`: the suite drives coroutines directly
    rather than depending on a pytest-asyncio plugin, and `requirements-service.lock` is a
    deliberately curated set.
    """

    async def driven() -> None:
        await guard.__aenter__()
        await guard.__aexit__(type(error), error, None)

    asyncio.run(driven())


def test_a_refused_submission_is_reported_with_its_reason_code(recorder, monkeypatch):
    logged: list[dict[str, Any]] = []

    async def log_rejection(_session, **fields: Any) -> None:
        logged.append(fields)

    monkeypatch.setattr(
        "submission_api.routers.submissions.get_axiom", lambda: recorder
    )
    monkeypatch.setattr(
        "submission_api.routers.submissions.store.log_rejection", log_rejection
    )
    session = FakeSession()
    refuse(audit(session), errors.PaymentRequired("payment is not finalized"))

    assert len(logged) == 1
    (event,) = recorder.of_type("submission_rejected")
    assert event["severity"] == "warning"
    assert event["source"] == "api-submissions"
    assert event["reason_code"] == "PAYMENT_NOT_FINALIZED"
    assert event["http_status"] == 402
    assert event["task_id"] == "erdos-1"
    assert event["payment_reference"] == "0xdeadbeef"


def test_a_refusal_is_still_reported_when_the_rejection_log_write_fails(
    recorder, monkeypatch
):
    """The case the event matters most in: the database refused the row, so it is the only trace."""

    async def log_rejection(_session, **_fields: Any) -> None:
        raise RuntimeError("the rejection log write failed")

    monkeypatch.setattr(
        "submission_api.routers.submissions.get_axiom", lambda: recorder
    )
    monkeypatch.setattr(
        "submission_api.routers.submissions.store.log_rejection", log_rejection
    )
    refuse(audit(FakeSession()), errors.UnprocessableEntity("bundle is malformed"))

    (event,) = recorder.of_type("submission_rejected")
    assert event["reason_code"] == "MALFORMED_REQUEST"
    assert event["http_status"] == 422


def test_a_disarmed_audit_reports_nothing(recorder, monkeypatch):
    """`disarm()` is the success path. A submission that was accepted is not also refused."""
    monkeypatch.setattr(
        "submission_api.routers.submissions.get_axiom", lambda: recorder
    )
    guard = audit(FakeSession())
    guard.disarm()
    refuse(guard, errors.PaymentRequired("ignored"))
    assert recorder.events == []


# --- wiring ---------------------------------------------------------------------------------


def test_the_real_app_installs_the_layer_outermost():
    """It has to be outermost: the rate limiter's 429 and the CSRF 403 never reach a route.

    Asserted on the class order rather than by driving a request, because building the real app
    needs a database, a task pool and a pin lock — and what could regress here is the order of
    `add_middleware` calls in `app.py`, which this reads directly.
    """
    import inspect

    from submission_api import app as app_module

    source = inspect.getsource(app_module.create_app)
    added = [
        line.strip()
        for line in source.splitlines()
        if "add_middleware(" in line or "Middleware," in line
    ]
    # `add_middleware` prepends, so the last one added is the outermost.
    assert "AxiomRequestMiddleware," in added[-1], added
