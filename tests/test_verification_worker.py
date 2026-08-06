"""The worker's decisions that do not need a database: classification, container flags, config.

The important property here is the split in `outcomes`. A recorded rejection is terminal and the
miner has already paid, so a reason code that describes the validator failing must never become
one — these tests are the guard on that, and on the exhaustiveness that stops a new reason code
from silently defaulting either way.
"""

from __future__ import annotations

import json
import subprocess
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
    MAX_CONTAINER_STDERR_BYTES,
    MAX_CONTAINER_STDOUT_BYTES,
    ContainerVerifierRunner,
    RunnerFailure,
    VerifierRun,
    _read_bounded,
    _report_from,
    assert_container_ready,
    assert_production_report,
    build_runner,
)
from verification_worker.settings import SettingsError, WorkerSettings
from verification_worker.tasks import PoolTaskResolver, TaskNotAllowed
from verifier.errors import ReasonCode
from verifier.hashing import canonical_json_bytes
from verifier.models import DEFAULT_CHECKS
from verifier.repository import tasks_repository_root
from verifier.task_pool import DEFAULT_TIER_TASK_COUNT

ROOT = Path(__file__).resolve().parents[1]
TASKS_ROOT = tasks_repository_root(ROOT)

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


def production_report(**overrides) -> dict:
    checks = {key: True for key in DEFAULT_CHECKS}
    checks["nanoda_enabled"] = False
    checks["nanoda_passed"] = False
    payload = {
        "schema_version": 2,
        "problem_id": "fc-fixture-problem",
        "task_id": "fc-fixture-task",
        "repository_commit": "ab" * 20,
        "source_theorem": "Fixture.target",
        "task_mode": "formalized",
        "task_bundle_sha256": TASK_DIGEST,
        "submission_sha256": "sha256:" + "ef" * 32,
        "accepted": True,
        "stage": "COMPLETED",
        "reason_code": "VERIFIED",
        "checks": checks,
        "theorem_names": ["Bounty.target"],
        "permitted_axioms": ["propext", "Quot.sound", "Classical.choice"],
        "duration_ms": 1,
        "comparator_exit_code": 0,
        "stdout_tail": "",
        "stderr_tail": "",
        "workspace_retained": False,
        "sandbox_mode": "landrun+seccomp",
    }
    payload.update(overrides)
    return payload


def verifier_run(payload: dict) -> VerifierRun:
    return VerifierRun(
        report=payload,
        report_bytes=canonical_json_bytes(payload),
        container_digest="sha256:" + "cd" * 32,
        verifier_version="test",
    )


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


def test_hostile_cli_error_cannot_inject_multiline_worker_logs():
    payload = json.dumps(
        {
            "accepted": False,
            "reason_code": "INTERNAL_ERROR",
            "error": "miner-controlled\nforged-log-line",
        }
    ).encode()
    with pytest.raises(RunnerFailure) as failure:
        _report_from(payload)
    assert "miner-controlled\\nforged-log-line" in str(failure.value)
    assert "miner-controlled\nforged-log-line" not in str(failure.value)


def test_output_that_is_not_json_is_a_runner_failure():
    with pytest.raises(RunnerFailure, match="no JSON report"):
        _report_from(b"Traceback (most recent call last):")


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"accepted": 1}, "accepted must be a JSON boolean"),
        ({"checks": {"lean_kernel_passed": "true"}}, "JSON booleans"),
    ],
)
def test_report_type_confusion_is_refused(overrides, message):
    with pytest.raises(RunnerFailure, match=message):
        _report_from(json.dumps(report(**overrides)).encode())


def test_production_acceptance_is_bound_to_the_exact_task_and_proof():
    run = verifier_run(production_report())
    assert_production_report(
        run,
        expected_task_id="fc-fixture-task",
        expected_task_sha256=TASK_DIGEST,
        expected_submission_sha256="sha256:" + "ef" * 32,
        expected_nanoda_enabled=False,
    )

    with pytest.raises(RunnerFailure, match="proof digest"):
        assert_production_report(
            run,
            expected_task_id="fc-fixture-task",
            expected_task_sha256=TASK_DIGEST,
            expected_submission_sha256="sha256:" + "11" * 32,
            expected_nanoda_enabled=False,
        )


def test_production_acceptance_requires_every_security_check():
    checks = dict(production_report()["checks"])
    checks["axioms_permitted"] = False
    run = verifier_run(production_report(checks=checks))

    with pytest.raises(RunnerFailure, match="axioms_permitted"):
        assert_production_report(
            run,
            expected_task_id="fc-fixture-task",
            expected_task_sha256=TASK_DIGEST,
            expected_submission_sha256="sha256:" + "ef" * 32,
            expected_nanoda_enabled=False,
        )


def test_production_rejects_retained_hostile_workspaces():
    run = verifier_run(production_report(workspace_retained=True))
    with pytest.raises(RunnerFailure, match="workspace was retained"):
        assert_production_report(
            run,
            expected_task_id="fc-fixture-task",
            expected_task_sha256=TASK_DIGEST,
            expected_submission_sha256="sha256:" + "ef" * 32,
            expected_nanoda_enabled=False,
        )


def test_production_report_cannot_disable_the_tasks_second_kernel():
    run = verifier_run(production_report())
    with pytest.raises(RunnerFailure, match="Nanoda policy"):
        assert_production_report(
            run,
            expected_task_id="fc-fixture-task",
            expected_task_sha256=TASK_DIGEST,
            expected_submission_sha256="sha256:" + "ef" * 32,
            expected_nanoda_enabled=True,
        )


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
    assert "--log-driver none" in joined
    assert "--ipc none" in joined
    assert "core=0:0" in argv
    assert argv[argv.index("--memory-swap") + 1] == container_runner().memory

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


def test_the_doctor_uses_the_same_hardened_container_profile():
    argv = container_runner().doctor_argv(name="conjectures-verifier-doctor-test")
    joined = " ".join(argv)

    assert "--network none" in joined
    assert "--read-only" in argv
    assert "--user 10001:10001" in joined
    assert "--security-opt no-new-privileges:true" in joined
    assert "--cap-drop ALL" in joined
    assert argv[-2:] == ("formal-conjectures-verifier:local", "doctor")


def test_production_preflight_requires_the_live_sandbox_probe(monkeypatch):
    payload = {"ready": True, "sandbox": {"production_ready": False}}
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout=json.dumps(payload).encode(), stderr=b""
        ),
    )

    with pytest.raises(RunnerFailure, match="sandbox_production_ready=False"):
        assert_container_ready(container_runner())


def test_production_preflight_accepts_a_ready_hardened_image(monkeypatch):
    payload = {"ready": True, "sandbox": {"production_ready": True}}
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout=json.dumps(payload).encode(), stderr=b""
        ),
    )

    assert_container_ready(container_runner())


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


def test_container_output_is_drained_but_never_buffered_without_bound():
    import asyncio

    async def scenario():
        stream = asyncio.StreamReader()
        stream.feed_data(b"a" * 17)
        stream.feed_eof()
        return await _read_bounded(stream, 8)

    value, overflow = asyncio.run(scenario())
    assert value == b"a" * 8
    assert overflow
    assert MAX_CONTAINER_STDOUT_BYTES <= 1024 * 1024
    assert MAX_CONTAINER_STDERR_BYTES <= 256 * 1024


def test_host_runner_refuses_a_container_that_floods_stdout():
    import asyncio
    import sys

    with pytest.raises(RunnerFailure, match="output exceeded"):
        asyncio.run(
            container_runner()._communicate(
                (
                    sys.executable,
                    "-c",
                    f"import sys; sys.stdout.write('x' * {MAX_CONTAINER_STDOUT_BYTES + 1})",
                ),
                "not-a-container",
                5,
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


def production_env(**overrides: str) -> dict[str, str]:
    values = {
        "APP_MODE": "PROD",
        "DATABASE_URL": "postgresql+psycopg://worker:test@127.0.0.1/conjectures",
        "VERIFICATION_RUNNER": "container",
        "VERIFIER_VERSION": "release-test",
        "VERIFIER_IMAGE": "formal-conjectures-verifier:release",
        "VERIFIER_CONTAINER_DIGEST": "sha256:" + "cd" * 32,
    }
    values.update(overrides)
    return values


def test_production_requires_an_explicit_container_digest():
    values = production_env()
    del values["VERIFIER_CONTAINER_DIGEST"]
    with pytest.raises(SettingsError, match="VERIFIER_CONTAINER_DIGEST"):
        WorkerSettings.from_env(values)


def test_production_requires_an_explicit_database_url():
    values = production_env()
    del values["DATABASE_URL"]
    with pytest.raises(SettingsError, match="DATABASE_URL"):
        WorkerSettings.from_env(values)


def test_production_preflight_refuses_development_mode(monkeypatch):
    import argparse
    import asyncio

    from verification_worker.__main__ import _run

    monkeypatch.delenv("APP_MODE", raising=False)
    with pytest.raises(SettingsError, match="requires APP_MODE=PROD"):
        asyncio.run(_run(argparse.Namespace(check=True, once=False, limit=None)))


def test_the_worker_runs_the_inspected_image_id_not_its_mutable_tag(monkeypatch):
    from verification_worker import runner as runner_module

    digest = "sha256:" + "cd" * 32
    checked = []
    monkeypatch.setattr(runner_module, "resolve_container_digest", lambda *_: digest)
    monkeypatch.setattr(runner_module, "assert_container_ready", checked.append)

    runner = build_runner(WorkerSettings.from_env(production_env()))

    assert isinstance(runner, ContainerVerifierRunner)
    assert runner.image == digest
    assert runner.container_digest == digest
    assert checked == [runner]


def test_production_refuses_an_image_that_does_not_match_the_pin(monkeypatch):
    from verification_worker import runner as runner_module

    actual = "sha256:" + "ef" * 32
    monkeypatch.setattr(runner_module, "resolve_container_digest", lambda *_: actual)

    with pytest.raises(RunnerFailure, match="not configured VERIFIER_CONTAINER_DIGEST"):
        build_runner(WorkerSettings.from_env(production_env()))


def test_development_needs_no_configuration_at_all():
    settings = WorkerSettings.from_env({})
    assert not settings.production
    assert settings.runner == "in-process"
    assert settings.owner  # host/pid, so a lease can be traced to a process
    # The isolation override is opt-in. A deployment that says nothing gets the real sandbox.
    assert settings.allow_insecure_sandbox is False


def test_each_worker_process_gets_a_unique_lease_owner_token():
    first = WorkerSettings.from_env({"VERIFICATION_WORKER_ID": "validator-a"})
    second = WorkerSettings.from_env({"VERIFICATION_WORKER_ID": "validator-a"})
    assert first.owner.startswith("validator-a/")
    assert second.owner.startswith("validator-a/")
    assert first.owner != second.owner


def test_worker_label_cannot_overflow_the_database_lease_field():
    with pytest.raises(SettingsError, match="too long"):
        WorkerSettings.from_env({"VERIFICATION_WORKER_ID": "x" * 128})


def test_task_repository_root_configures_both_worker_pool_paths(tmp_path: Path):
    settings = WorkerSettings.from_env(
        {"CONJECTURES_TASKS_ROOT": str(tmp_path / "task-repository")}
    )
    assert settings.task_allowlist_path == tmp_path / "task-repository/allowlist.json"
    assert settings.task_pool_root == tmp_path / "task-repository/pool"


def test_production_refuses_an_insecure_sandbox():
    # An accept produced without the real isolation says nothing sound about the proof, so it must
    # never be reachable in the deployment that pays out.
    with pytest.raises(SettingsError, match="VERIFICATION_ALLOW_INSECURE_SANDBOX"):
        WorkerSettings.from_env(
            {
                "APP_MODE": "PROD",
                "VERIFICATION_RUNNER": "container",
                "VERIFIER_VERSION": "v1",
                "VERIFICATION_ALLOW_INSECURE_SANDBOX": "1",
            }
        )


def test_an_insecure_sandbox_is_refused_where_it_would_do_nothing():
    # The container runner invokes the published verifier CLI without this flag, so honouring it
    # there would be a lie. Silently ignoring a security setting is worse than not having one.
    with pytest.raises(SettingsError, match="only affects the in-process runner"):
        WorkerSettings.from_env(
            {
                "VERIFICATION_RUNNER": "container",
                "VERIFICATION_ALLOW_INSECURE_SANDBOX": "true",
            }
        )


def test_the_insecure_sandbox_flag_rejects_a_value_it_cannot_read():
    with pytest.raises(SettingsError, match="must be a boolean"):
        WorkerSettings.from_env({"VERIFICATION_ALLOW_INSECURE_SANDBOX": "maybe"})


def test_the_lease_covers_the_container_which_covers_the_task_deadline():
    settings = WorkerSettings.from_env({})
    # A production task declares an hour. Each bound must strictly contain the one inside it,
    # or a container gets killed before the verifier's own deadline fires and a live verdict
    # gets written under an expired lease.
    assert settings.container_timeout(3600) > 3600
    assert settings.lease_seconds(3600) > settings.container_timeout(3600)


def test_final_verdict_query_requires_a_live_owned_lease_and_row_lock():
    import asyncio
    import uuid

    from sqlalchemy.dialects import postgresql

    from conjectures_subnet.db import verification as queue

    captured = {}

    class EmptyResult:
        def scalar_one_or_none(self):
            return None

    class Session:
        async def execute(self, statement):
            captured["sql"] = str(statement.compile(dialect=postgresql.dialect()))
            return EmptyResult()

    result = asyncio.run(
        queue.lock_owned_for_recording(
            Session(),  # type: ignore[arg-type]
            uuid.uuid4(),
            owner="worker/session-token",
        )
    )
    assert result is None
    sql = captured["sql"]
    assert "verification_status" in sql
    assert "verification_lease_owner" in sql
    assert "verification_lease_until >= now()" in sql
    assert "FOR UPDATE" in sql


def test_the_resolver_loads_the_checked_out_pool_by_manifest_task_id():
    """The other worker tests build a resolver directly, leaving `load` uncovered.

    The task repository names its directories for humans and renames them freely, so a task is
    the task ID in its manifest and the directory name is only a label. This asserts the two
    genuinely differ in the checked-out pool, so a resolver that rebuilt the path from the task
    ID fails here rather than when a paid submission is already claimed.
    """
    resolver = PoolTaskResolver.load(
        allowlist_path=TASKS_ROOT / "allowlist.json",
        pool_root=TASKS_ROOT / "pool",
    )

    tasks = tuple(resolver.tasks.values())
    assert len(tasks) == DEFAULT_TIER_TASK_COUNT
    assert all(task.task_dir.is_dir() for task in tasks)
    assert all(task.task_dir.name != task.task_id for task in tasks)
    # The worker sizes its lease from this, so a manifest that declares nothing usable would
    # silently become an unbounded container.
    assert all(task.timeout_seconds > 0 for task in tasks)


def test_the_resolver_refuses_a_pool_missing_an_allowlisted_task(tmp_path: Path):
    """A claimed submission whose task has no bytes would be released and retried forever."""
    complete = PoolTaskResolver.load(
        allowlist_path=TASKS_ROOT / "allowlist.json",
        pool_root=TASKS_ROOT / "pool",
    )
    kept = min(complete.tasks.values(), key=lambda task: task.task_id)
    for tier in {task.tier for task in complete.tasks.values()}:
        (tmp_path / tier).mkdir(parents=True)
    destination = tmp_path / kept.tier / kept.task_dir.name
    destination.mkdir()
    for source in kept.task_dir.iterdir():
        if source.is_file():
            (destination / source.name).write_bytes(source.read_bytes())

    with pytest.raises(TaskNotAllowed, match="missing from the pool"):
        PoolTaskResolver.load(
            allowlist_path=TASKS_ROOT / "allowlist.json", pool_root=tmp_path
        )


def test_a_rejection_decided_before_the_submission_was_read_is_not_a_digest_mismatch():
    """The verifier hashes the submission only once it loads it, and reports "" until then.

    A task that fails to load, or a file over the size cap, is a rejection the verifier reached
    honestly. Refusing it for naming no proof turned every such verdict into INTERNAL_ERROR and
    parked paid submissions at the attempt limit instead of recording the reason.
    """
    run = verifier_run(
        production_report(
            accepted=False,
            reason_code="REPOSITORY_COMMIT_MISMATCH",
            stage="LOAD_TASK",
            submission_sha256="",
            comparator_exit_code=None,
            sandbox_mode="not-started",
        )
    )
    assert_production_report(
        run,
        expected_task_id="fc-fixture-task",
        expected_task_sha256=TASK_DIGEST,
        expected_submission_sha256="sha256:" + "ef" * 32,
        expected_nanoda_enabled=False,
    )


def test_an_accept_must_still_name_the_exact_proof():
    """The empty-digest allowance is for rejections only; an accept is what a payout rests on."""
    run = verifier_run(production_report(submission_sha256=""))
    with pytest.raises(RunnerFailure, match="proof digest"):
        assert_production_report(
            run,
            expected_task_id="fc-fixture-task",
            expected_task_sha256=TASK_DIGEST,
            expected_submission_sha256="sha256:" + "ef" * 32,
            expected_nanoda_enabled=False,
        )


def test_a_rejection_about_a_different_proof_is_still_refused():
    """Only an absent digest is tolerated. A present one that disagrees is a real mismatch."""
    run = verifier_run(
        production_report(
            accepted=False,
            reason_code="LEAN_KERNEL_FAILED",
            stage="COMPARATOR",
            submission_sha256="sha256:" + "11" * 32,
        )
    )
    with pytest.raises(RunnerFailure, match="proof digest"):
        assert_production_report(
            run,
            expected_task_id="fc-fixture-task",
            expected_task_sha256=TASK_DIGEST,
            expected_submission_sha256="sha256:" + "ef" * 32,
            expected_nanoda_enabled=False,
        )
