"""The worker's decisions that do not need a database: classification, container flags, config.

The important property here is the split in `outcomes`. A recorded rejection is terminal and the
miner has already paid, so a reason code that describes the validator failing must never become
one — these tests are the guard on that, and on the exhaustiveness that stops a new reason code
from silently defaulting either way.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from verification_worker.outcomes import (
    OPERATOR_REASONS,
    VERDICT_REASONS,
    Outcome,
    classify,
)
from verification_worker.runner import (
    CONTAINER_PROOF_PATH,
    CONTAINER_TASK_DIR,
    ContainerVerifierRunner,
    RunnerFailure,
    _report_from,
)
from verification_worker.settings import SettingsError, WorkerSettings
from verifier.errors import ReasonCode

TASK_DIGEST = "sha256:" + "ab" * 32


# --- classification -----------------------------------------------------------------


def test_every_verifier_reason_code_is_classified_exactly_once():
    # Adding a ReasonCode to the verifier must force a decision here. Without this, a new code
    # would fall through to whatever the default branch happens to be, and one of the two
    # defaults charges a miner for our bug.
    codes = set(ReasonCode)
    assert VERDICT_REASONS | OPERATOR_REASONS == codes
    assert not VERDICT_REASONS & OPERATOR_REASONS


def test_our_own_failures_are_never_verdicts():
    # Each of these says something about the validator, not about the proof.
    for reason in (
        ReasonCode.INSECURE_SANDBOX,
        ReasonCode.INTERNAL_ERROR,
        ReasonCode.WORKSPACE_ERROR,
        ReasonCode.CHALLENGE_BUILD_FAILED,
        ReasonCode.RESOURCE_LIMIT,
        ReasonCode.REPOSITORY_COMMIT_MISMATCH,
        ReasonCode.TRUSTED_FILE_MODIFIED,
        ReasonCode.TASK_COMMITMENT_MISMATCH,
        ReasonCode.INELIGIBLE_TASK,
    ):
        assert classify(reason) is Outcome.OPERATOR, reason


def test_a_proof_that_failed_is_a_verdict():
    for reason in (
        ReasonCode.VERIFIED,
        ReasonCode.LEAN_KERNEL_REJECTED,
        ReasonCode.STATEMENT_MISMATCH,
        ReasonCode.UNPERMITTED_AXIOM,
        ReasonCode.SOLUTION_BUILD_FAILED,
        ReasonCode.SUBMISSION_POLICY_VIOLATION,
        # The task publishes its own timeout_seconds, so failing to compile inside it is part
        # of the contract the miner submitted against.
        ReasonCode.TIMEOUT,
    ):
        assert classify(reason) is Outcome.VERDICT, reason


def test_an_unclassified_code_refuses_rather_than_guessing():
    with pytest.raises(ValueError, match="not classified"):
        classify("NOT_A_REASON_CODE")  # type: ignore[arg-type]


# --- reading the verifier's answer --------------------------------------------------


def report(**overrides) -> dict:
    payload = {
        "accepted": True,
        "reason_code": "VERIFIED",
        "stage": "COMPLETED",
        "checks": {"lean_kernel_passed": True},
        "sandbox_mode": "landrun+seccomp",
    }
    payload.update(overrides)
    return payload


def test_a_full_report_is_read_back():
    import json

    parsed = _report_from(json.dumps(report()).encode())
    assert parsed["reason_code"] == "VERIFIED"


def test_the_cli_error_shape_is_a_runner_failure_not_a_rejection():
    # verifier/cli.py prints this when it fails outside verify(): accepted=false, but no stage,
    # no checks, no sandbox_mode. Treating it as a report would record a rejection the proof
    # never earned.
    payload = b'{"accepted": false, "reason_code": "INTERNAL_ERROR", "error": "boom"}'
    with pytest.raises(RunnerFailure, match="error rather than a report"):
        _report_from(payload)


def test_output_that_is_not_json_is_a_runner_failure():
    with pytest.raises(RunnerFailure, match="no JSON report"):
        _report_from(b"Traceback (most recent call last):")


# --- the container invocation --------------------------------------------------------


def container_runner() -> ContainerVerifierRunner:
    return ContainerVerifierRunner(
        image="formal-conjectures-verifier:local",
        container_digest="sha256:" + "cd" * 32,
        verifier_version="test",
    )


def test_the_container_command_matches_the_hardened_profile():
    argv = container_runner().argv(
        task_dir=Path("/pool/tier-1/task"),
        proof_path=Path("/scratch/Main.lean"),
        expected_task_sha256=TASK_DIGEST,
        name="conjectures-verify-test",
    )
    joined = " ".join(argv)

    # SECURITY.md's reviewed profile, the same one docker-compose.yml declares.
    assert "--network none" in joined
    assert "--read-only" in argv
    assert "--user 10001:10001" in joined
    assert "--security-opt no-new-privileges:true" in joined
    assert "--cap-drop ALL" in joined
    assert "--rm" in argv

    # Exactly one task, read-only, rather than the whole pool.
    assert f"/pool/tier-1/task:{CONTAINER_TASK_DIR}:ro" in argv
    assert f"/scratch/Main.lean:{CONTAINER_PROOF_PATH}:ro" in argv

    # The digest is passed, so the verifier re-checks the commitment itself rather than
    # trusting that we mounted the right directory.
    assert argv[-1] == TASK_DIGEST
    assert "--expected-task-sha256" in argv


def test_the_container_is_named_so_a_timeout_can_kill_it():
    # Killing `docker run` only detaches the client; the Lean build would keep a core busy for
    # the rest of the task's hour.
    argv = container_runner().argv(
        task_dir=Path("/pool/tier-1/task"),
        proof_path=Path("/scratch/Main.lean"),
        expected_task_sha256=TASK_DIGEST,
        name="conjectures-verify-abc",
    )
    assert "--name" in argv
    assert argv[argv.index("--name") + 1] == "conjectures-verify-abc"


def test_a_bad_task_digest_never_reaches_the_container():
    import asyncio

    with pytest.raises(RunnerFailure, match="not a sha256"):
        asyncio.run(
            container_runner().run(
                task_dir=Path("/pool/tier-1/task"),
                proof=b"theorem x : True := trivial",
                expected_task_sha256="not-a-digest",
                timeout_seconds=5,
            )
        )


# --- configuration ------------------------------------------------------------------


def test_production_refuses_the_in_process_runner():
    # It would compile a hostile proof inside the process holding the database credentials.
    with pytest.raises(SettingsError, match="VERIFICATION_RUNNER=container"):
        WorkerSettings.from_env(
            {
                "APP_MODE": "PROD",
                "VERIFICATION_RUNNER": "in-process",
                "VERIFIER_VERSION": "v1",
            }
        )


def test_production_requires_an_explicit_verifier_version():
    # verification_runs.verifier_version is the record of what decided a submission; a default
    # would make every report claim the same thing.
    with pytest.raises(SettingsError, match="VERIFIER_VERSION"):
        WorkerSettings.from_env({"APP_MODE": "PROD"})


def test_development_needs_no_configuration_at_all():
    settings = WorkerSettings.from_env({})
    assert not settings.production
    assert settings.runner == "in-process"
    assert settings.owner  # host/pid, so a lease can be traced to a process


def test_the_lease_covers_the_container_which_covers_the_task_deadline():
    settings = WorkerSettings.from_env({})
    # A production task declares an hour. Each bound must strictly contain the one inside it,
    # or a container gets killed before the verifier's own deadline fires and a live verdict
    # gets written under an expired lease.
    assert settings.container_timeout(3600) > 3600
    assert settings.lease_seconds(3600) > settings.container_timeout(3600)
