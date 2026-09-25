"""Claim a submission, run its gate over it, record what came back.

Its own process on its own host, not a thread in the API. The gate is a forty-five minute
subprocess holding a Lean toolchain and a cargo build; as a thread it pinned the API to one
uvicorn worker and took the API down whenever it died. `FOR UPDATE ... SKIP LOCKED` on the
claim is what lets several of these drain one queue, on one box or on several.

Sync SQLAlchemy, unlike `verification_worker`, and for a reason rather than by omission:
that worker awaits a container, and this one blocks on a subprocess with nothing to await.

Two departures from the service this replaces, both about one submission not being allowed
to stop everything else:

* a validator error backs that *competition* off rather than breaking the whole loop --
  one worker can serve several, and a misconfigured gate for one of them is not a reason to
  stop draining the others;
* a submission that breaks the gate `max_attempts` times is marked `error` and left alone,
  rather than requeued forever with the queue stacking up behind it.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from competition_worker.registry import Gate, GateUnavailable
from competition_worker.registry import check as check_gate
from competition_worker.runner import GateBroken, GateRun
from competition_worker.runner import run as run_gate
from competition_worker.settings import Settings
from conjectures_subnet.competition import Store, SubmissionState, models

logger = logging.getLogger(__name__)

# How long a competition sits out after its gate broke, in idle turns. Long enough that a
# misconfiguration is not retried every two seconds, short enough that fixing it does not
# need a restart.
BACKOFF_TURNS = 30


@dataclass
class Drain:
    """One pass over the queue, and what it did. Returned so `--once` can report."""

    verified: int = 0
    errored: int = 0
    abandoned: int = 0
    skipped: list[str] = field(default_factory=list)


class CompetitionWorker:
    def __init__(self, store: Store, settings: Settings, gates: dict[str, Gate]) -> None:
        self._store = store
        self._settings = settings
        self._gates = gates
        # slug -> turns remaining before this competition is tried again.
        self._backoff: dict[str, int] = {}

    # --- one submission -------------------------------------------------------
    def verify_one(self, gate: Gate, submission: models.Submission) -> str:
        """Run the gate over one claimed submission and record the outcome."""
        logger.info(
            "verifying submission %d (%s, attempt %d)",
            submission.id,
            submission.hotkey[:8],
            submission.attempts,
        )
        if not submission.parse_source or not submission.proof_source:
            # Nothing to run: the row predates the columns, or was seeded. Not the miner's
            # doing, so it is an error rather than a rejection.
            self._store.submissions.abandon(
                submission.id,
                "\nERROR: this submission carries no files for the gate to run.\n",
            )
            return SubmissionState.ERROR.value

        run = run_gate(
            gate,
            parse_source=submission.parse_source,
            proof_source=submission.proof_source,
        )
        if run.broken:
            return self._gate_failed(gate, submission, run)

        fields: dict[str, object] = {"exit_code": run.exit_code, "report": run.report}
        if run.accepted:
            if not run.measured:
                # Exit 0 with no usable score is the gate contradicting itself. Accepting it
                # would put an unscored row on the leaderboard; rejecting it would blame the
                # miner for the validator's problem.
                return self._gate_failed(gate, submission, run)
            fields |= run.measured
        state = SubmissionState.ACCEPTED if run.accepted else SubmissionState.REJECTED
        final = self._store.submissions.finish(submission.id, state, **fields)
        logger.info(
            "submission %d %s%s",
            submission.id,
            final,
            f" ({run.measured['bytes']:.0f} bytes)" if "bytes" in run.measured else "",
        )
        return final

    def _gate_failed(self, gate: Gate, submission: models.Submission, run: GateRun) -> str:
        """The gate could not render a usable verdict. Never charged to the miner."""
        logger.warning(
            "validator error on submission %d (exit %s)\nstdout: %s\nstderr: %s",
            submission.id,
            run.exit_code,
            run.report[-1500:],
            run.stderr[-1500:],
        )
        self._backoff[gate.slug] = BACKOFF_TURNS
        if submission.attempts >= self._settings.max_attempts:
            self._store.submissions.abandon(
                submission.id,
                f"\nERROR: the gate failed to produce a verdict on "
                f"{submission.attempts} attempts; an operator has been asked to look.\n",
            )
            logger.error(
                "submission %d abandoned after %d attempts",
                submission.id,
                submission.attempts,
            )
            return "abandoned"
        self._store.submissions.requeue(submission.id)
        return SubmissionState.ERROR.value

    # --- the loop -------------------------------------------------------------
    def _serving(self) -> list[str]:
        wanted = self._settings.competitions or tuple(self._gates)
        return [
            slug
            for slug in wanted
            if slug in self._gates and self._backoff.get(slug, 0) <= 0
        ]

    def drain(self, limit: int | None = None) -> Drain:
        """Verify claimable submissions until the queue is empty or `limit` is reached."""
        result = Drain()
        for slug in self._serving():
            gate = self._gates[slug]
            while limit is None or result.verified < limit:
                submission = self._store.submissions.claim_next(self._settings.worker_id)
                if submission is None:
                    break
                outcome = self.verify_one(gate, submission)
                if outcome == "abandoned":
                    result.abandoned += 1
                elif outcome == SubmissionState.ERROR.value:
                    result.errored += 1
                    break  # this competition only; see BACKOFF_TURNS
                else:
                    result.verified += 1
        result.skipped = [slug for slug, turns in self._backoff.items() if turns > 0]
        return result

    def sweep(self) -> None:
        """Reclaim what a dead worker abandoned, and drop expired rate-limit counters."""
        if requeued := self._store.submissions.requeue_stale(
            self._settings.stale_claim_seconds
        ):
            logger.info("requeued %d submission(s) abandoned mid-gate", requeued)
        self._store.rate.prune()

    def tick(self) -> Drain:
        for slug in list(self._backoff):
            self._backoff[slug] -= 1
            if self._backoff[slug] <= 0:
                del self._backoff[slug]
                logger.info("retrying %s after backoff", slug)
        return self.drain()

    def run_forever(self) -> None:
        logger.info("worker %s draining the queue", self._settings.worker_id)
        self.sweep()
        turn = 0
        while True:
            try:
                self.tick()
                turn += 1
                if turn % self._settings.sweep_every == 0:
                    self.sweep()
            except Exception:  # noqa: BLE001 - a database blip must not end the loop
                logger.exception("loop error")
            time.sleep(self._settings.idle_seconds)


def gates_for(settings: Settings) -> dict[str, Gate]:
    """Load and verify every gate this host is meant to serve.

    Checked once, at startup: these read the disk the gate will use, and re-reading them
    before every run would be a check that an attacker with write access could wait out.
    """
    from competition_worker.registry import load

    available = {gate.slug: gate for gate in load(settings.registry_path)}
    wanted = settings.competitions or tuple(available)
    missing = [slug for slug in wanted if slug not in available]
    if missing:
        raise GateUnavailable(
            f"{settings.registry_path} has no gate for {missing}; it lists "
            f"{sorted(available)}"
        )
    serving = {slug: available[slug] for slug in wanted}
    for gate in serving.values():
        check_gate(gate)
        logger.info("gate %s verified at %s", gate.slug, gate.commit[:12])
    return serving


__all__ = ["BACKOFF_TURNS", "CompetitionWorker", "Drain", "GateBroken", "gates_for"]
