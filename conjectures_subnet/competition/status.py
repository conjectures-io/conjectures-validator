"""The submission lifecycle, as the strings the API has always returned."""

from __future__ import annotations

from enum import StrEnum


class SubmissionState(StrEnum):
    # Waiting for a gate worker to claim it.
    QUEUED = "queued"
    # Claimed; verify.py is running against it.
    VERIFYING = "verifying"
    # The gate exited 0: the proof holds and the score is recorded. Consumes a
    # registration (see registrations.claim_slot) -- the only state that does.
    ACCEPTED = "accepted"
    # The gate exited 1: the miner's submission was refused, and may be retried
    # for free with a corrected one.
    REJECTED = "rejected"
    # The gate exited 2 or did not exit: the validator is misconfigured, not the
    # submission. Requeued, never charged.
    ERROR = "error"


# The states a worker may claim from, and the ones it leaves behind.
PENDING = (SubmissionState.QUEUED, SubmissionState.VERIFYING)
TERMINAL = (SubmissionState.ACCEPTED, SubmissionState.REJECTED, SubmissionState.ERROR)

STATE_VALUES = tuple(s.value for s in SubmissionState)
