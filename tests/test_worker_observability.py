"""The worker events, and the severities they were chosen for.

Severity is a judgement about who has to do something, and these are the calls that judgement was
made in. They are asserted rather than left to review because getting one wrong is silent: an
`attempts_exhausted` recorded at `info` is a miner who paid and got nothing, sitting on a dashboard
nobody has a reason to look at.

Only the paths that need no database live here — `deposit_watcher` and the rest of the verification
worker are covered by `test_deposit_watcher.py` and `test_verification_worker.py`, which run
against the fixed test PostgreSQL. What is asserted here is the choice of label and severity, which
does not depend on a store.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest

from conjectures_subnet.axiom import Axiom, AxiomClientNoop
from conjectures_subnet.db import verification as queue
from emissions_worker.worker import NETUID, TREASURY_UID, TreasuryWeightWorker
from verification_worker.outcomes import Outcome
from verification_worker.worker import LEASE_LOST, VerificationWorker
from verifier.errors import ReasonCode


class Recorder(Axiom):
    def __init__(self) -> None:
        super().__init__(AxiomClientNoop())
        self.events: list[dict[str, Any]] = []

    def emit(self, *, severity, source, event_type, **fields: Any) -> None:
        self.events.append(
            {
                "severity": str(severity),
                "source": source,
                "event_type": event_type,
                **fields,
            }
        )

    def one(self, event_type: str) -> dict[str, Any]:
        matched = [item for item in self.events if item["event_type"] == event_type]
        assert len(matched) == 1, f"expected one {event_type}, got {self.events}"
        return matched[0]


# --- the verification worker -----------------------------------------------------------------


class Settings:
    """Only the fields the paths under test read. The rest need a real deployment."""

    owner = "worker-1"
    max_attempts = 3


def worker() -> VerificationWorker:
    # None for the collaborators these two methods never reach: `_lease_lost` writes nothing by
    # design — the lease it would clear belongs to whoever took it — and `_outcome_of` is a pure
    # classification.
    return VerificationWorker(
        settings=Settings(),  # type: ignore[arg-type]
        sessions=None,  # type: ignore[arg-type]
        runner=None,  # type: ignore[arg-type]
        tasks=None,  # type: ignore[arg-type]
    )


def claim() -> queue.ClaimedSubmission:
    return queue.ClaimedSubmission(
        submission_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        task_id="erdos-1",
        task_bundle_sha256="a" * 64,
        proof_digest="b" * 64,
        attempts=2,
        lease_until=dt.datetime(2026, 8, 5, 12, tzinfo=dt.UTC),
    )


@pytest.fixture
def recorder(monkeypatch) -> Recorder:
    collected = Recorder()
    monkeypatch.setattr("verification_worker.worker.get_axiom", lambda: collected)
    monkeypatch.setattr("emissions_worker.worker.get_axiom", lambda: collected)
    return collected


def test_a_lost_lease_is_a_warning_naming_the_stage_it_was_lost_at(recorder):
    """Two stages, one label. Losing it before recording means compiled work thrown away, so the
    stage is what distinguishes an annoyance from a wasted verification."""
    processed = worker()._lease_lost(claim(), stage="before_recording")
    assert processed.outcome is Outcome.OPERATOR
    assert processed.reason_code == LEASE_LOST

    event = recorder.one("lease_lost")
    assert event["severity"] == "warning"
    assert event["source"] == "verification-worker"
    assert event["stage"] == "before_recording"
    assert event["submission_id"] == "11111111-1111-1111-1111-111111111111"
    assert event["task_id"] == "erdos-1"
    assert event["attempt"] == 2


def test_a_submission_id_is_reported_as_a_string():
    """A UUID would serialise via the transport's `str` fallback anyway; doing it here keeps the
    field's type stable rather than dependent on which encoder ran."""
    processed = worker()._lease_lost(claim(), stage="before_start")
    assert isinstance(processed.submission_id, uuid.UUID)


def test_an_unclassified_reason_code_is_an_error_and_fails_towards_the_operator(recorder):
    """Guessing here would charge a miner for a code the worker does not understand."""
    assert worker()._outcome_of("NOT_A_REAL_REASON_CODE") is Outcome.OPERATOR
    event = recorder.one("unclassified_reason_code")
    assert event["severity"] == "error"
    assert event["reason_code"] == "NOT_A_REAL_REASON_CODE"


def test_a_classified_reason_code_emits_nothing(recorder):
    """The ordinary path stays quiet; `verdict_recorded` is what reports it."""
    assert worker()._outcome_of(ReasonCode.VERIFIED.value) is Outcome.VERDICT
    assert recorder.events == []


# --- the emissions worker --------------------------------------------------------------------


class Epoch:
    def __init__(self, block: int) -> None:
        self.block = block


class Result:
    def raise_for_failure(self) -> Result:
        return self


class Client:
    def __init__(self, *, failures: int = 0) -> None:
        self.failures = failures
        self.attempts = 0

    def wait_for_epoch(self, netuid: int, *, timeout: float | None = None) -> Epoch:
        del netuid, timeout
        return Epoch(block=8_675_309)

    def execute(self, intent: Any, wallet: Any, *, retries: int = 2) -> Result:
        del intent, wallet, retries
        self.attempts += 1
        if self.failures:
            self.failures -= 1
            raise RuntimeError("the extrinsic was rejected")
        return Result()


def test_an_epoch_and_the_weight_it_set_are_two_info_events(recorder):
    TreasuryWeightWorker(
        client=Client(), wallet=object(), sleep=lambda _seconds: None
    ).run_epoch()

    observed = recorder.one("epoch_observed")
    assert observed["severity"] == "info"
    assert observed["source"] == "emissions-worker"
    assert observed["netuid"] == NETUID
    assert observed["block"] == 8_675_309
    assert observed["treasury_uid"] == TREASURY_UID

    was_set = recorder.one("weights_set")
    assert was_set["severity"] == "info"
    assert was_set["block"] == 8_675_309
    assert was_set["attempt"] == 1


def test_a_retried_submission_is_a_warning_and_reports_the_attempt(recorder):
    """The loop does not give up, so a single rejection is not an error. A streak is, and that is a
    rate over `attempt` rather than a severity on any one event."""
    TreasuryWeightWorker(
        client=Client(failures=2), wallet=object(), sleep=lambda _seconds: None
    ).run_epoch()

    failures = [item for item in recorder.events if item["event_type"] == "weights_failed"]
    assert [item["severity"] for item in failures] == ["warning", "warning"]
    assert [item["attempt"] for item in failures] == [1, 2]
    assert "RuntimeError: the extrinsic was rejected" in failures[0]["exception"]
    # And it still succeeded, on the third attempt.
    assert recorder.one("weights_set")["attempt"] == 3


def test_an_epoch_watch_failure_is_an_error_because_the_epoch_cannot_be_reset(recorder):
    class Broken(Client):
        def __init__(self) -> None:
            super().__init__()
            self.watched = 0

        def wait_for_epoch(self, netuid: int, *, timeout: float | None = None) -> Epoch:
            self.watched += 1
            if self.watched > 1:
                raise KeyboardInterrupt
            raise ConnectionError("the websocket closed")

    TreasuryWeightWorker(
        client=Broken(), wallet=object(), sleep=lambda _seconds: None
    ).run_forever()

    event = recorder.one("weights_failed")
    assert event["severity"] == "error"
    assert event["stage"] == "epoch_watch"
    assert "ConnectionError: the websocket closed" in event["exception"]
