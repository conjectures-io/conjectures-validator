"""Which reason codes are judgements about the proof, and which are the validator failing.

`verifier.errors.CONFIGURATION_REASONS` looks like this list and is not it: that set exists to
choose a CLI exit code, and it groups `INELIGIBLE_TASK` — a real decision about the task — with
`INTERNAL_ERROR` and `WORKSPACE_ERROR`, which mean the validator is broken. Reusing it would
make the worker record a rejection whenever its own sandbox failed to start.

That distinction is about money. A recorded rejection is terminal, and the miner has already
paid, so charging them for our dead container is a refund we would owe and never notice. When
in doubt a reason belongs in `OPERATOR`: the cost of being wrong there is a delay, and the cost
of being wrong in `VERDICT` is somebody's fee.

Every member of `ReasonCode` appears in exactly one set, and `classify` refuses an unknown one,
so adding a reason code to the verifier forces this decision rather than defaulting to a
rejection.
"""

from __future__ import annotations

import enum

from verifier.errors import ReasonCode


class Outcome(enum.StrEnum):
    VERDICT = "VERDICT"  # the verifier judged the proof; record it and move on
    OPERATOR = "OPERATOR"  # we failed; leave the submission UNVERIFIED and raise an alarm


# A statement about the submitted proof. Recording one of these ends the submission.
VERDICT_REASONS = frozenset(
    {
        ReasonCode.VERIFIED,
        # The proof itself is inadmissible: too large, not UTF-8, or caught by the static
        # hostile-input policy. All three are properties of the bytes the miner sent.
        ReasonCode.SUBMISSION_TOO_LARGE,
        ReasonCode.SUBMISSION_NOT_UTF8,
        ReasonCode.SUBMISSION_POLICY_VIOLATION,
        # The proof failed to prove the committed statement.
        ReasonCode.SOLUTION_BUILD_FAILED,
        ReasonCode.STATEMENT_MISMATCH,
        ReasonCode.UNPERMITTED_AXIOM,
        ReasonCode.LEAN_KERNEL_REJECTED,
        ReasonCode.NANODA_REJECTED,
        ReasonCode.COMPARATOR_REJECTED,
        # The task's own declared timeout_seconds is published in its manifest, so failing to
        # compile inside it is part of the contract the miner submitted against.
        ReasonCode.TIMEOUT,
    }
)

# The validator's problem, not the miner's. Retryable in principle; the attempt cap decides
# when to stop retrying and page somebody instead.
OPERATOR_REASONS = frozenset(
    {
        # The sandbox is absent or did not deny what it must. Never a verdict: it says nothing
        # about the proof, and an accept in this state would be unsound.
        ReasonCode.INSECURE_SANDBOX,
        # Our workspace, our container, our bug.
        ReasonCode.WORKSPACE_ERROR,
        ReasonCode.INTERNAL_ERROR,
        ReasonCode.INVALID_ARGUMENT,
        ReasonCode.SUBMISSION_NOT_FOUND,
        # The challenge is ours to build. A miner cannot make it fail.
        ReasonCode.CHALLENGE_BUILD_FAILED,
        # Killed by a resource limit rather than judged. Retry; the cap stops the loop.
        ReasonCode.RESOURCE_LIMIT,
        # The task, the repository pin or the trusted bytes disagree with what we deployed.
        # The submission is fine; the validator is serving the wrong thing, and these are the
        # cases where a refund is likely owed.
        ReasonCode.TASK_COMMITMENT_MISMATCH,
        ReasonCode.REPOSITORY_COMMIT_MISMATCH,
        ReasonCode.REPOSITORY_NOT_FOUND,
        ReasonCode.TRUSTED_FILE_MODIFIED,
        ReasonCode.SOURCE_TYPE_CHANGED,
        ReasonCode.INELIGIBLE_TASK,
        ReasonCode.INVALID_MANIFEST,
        ReasonCode.INVALID_TASK_MODE,
        ReasonCode.UNSUPPORTED_DECLARATION,
        ReasonCode.ADAPTER_REQUIRED,
        ReasonCode.THEOREM_NOT_FOUND,
        ReasonCode.CATALOG_NOT_FOUND,
        # Bundle-level reasons belong to intake, which already refused the request before a
        # submission existed. Reaching one here means the worker was handed something the API
        # should never have stored.
        ReasonCode.BUNDLE_TOO_LARGE,
        ReasonCode.BUNDLE_MALFORMED,
        ReasonCode.BUNDLE_POLICY_VIOLATION,
        ReasonCode.BUNDLE_MANIFEST_INVALID,
        ReasonCode.BUNDLE_DIGEST_MISMATCH,
    }
)


def classify(reason: ReasonCode) -> Outcome:
    """Whether this reason code may be written to the database as a verdict."""
    if reason in VERDICT_REASONS:
        return Outcome.VERDICT
    if reason in OPERATOR_REASONS:
        return Outcome.OPERATOR
    raise ValueError(
        f"{reason} is not classified; decide in verification_worker.outcomes whether it "
        "judges the proof or reports our own failure before the worker can act on it"
    )


__all__ = ["OPERATOR_REASONS", "VERDICT_REASONS", "Outcome", "classify"]
