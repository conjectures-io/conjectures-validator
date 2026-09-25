"""The gate seam: what the worker hands a gate, and what it does with what comes back.

Every test here drives a *stub* gate -- a small Python script standing in for `verify.py` --
because the real one needs bubblewrap, elan/Lean, Charon/Aeneas and cargo on the host, which
CI does not have and a unit test should not want. What is under test is the contract between
the two, and a stub exercises that contract exactly: the same argv, the same exit codes, the
same results file.

The tests that matter most are the ones about what the gate does *not* get. It compiles and
runs miner-authored Rust for forty-five minutes, so the environment it is handed is an
allowlist, and a test that proves the database URL does not cross is worth more than any
number of happy-path runs.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from competition_worker.registry import Gate, GateUnavailable
from competition_worker.registry import check as check_gate
from competition_worker.registry import load as load_registry
from competition_worker.runner import PASSTHROUGH_ENV, measurements
from competition_worker.runner import run as run_gate
from competition_worker.settings import Settings, SettingsError

RUST, LEAN = b"fn parse() {}", b"theorem ok : True := trivial"

RESULTS = {
    "raw_bytes": 1000,
    "candidates": ["submission"],
    "methods": {
        "submission": {"output_bytes": 400, "parse_s": 1.5, "slowdown": 1.2},
        "incumbent": {"output_bytes": 500, "parse_s": 1.25},
    },
}


def _gate(tmp_path: Path, body: str, *, commit: str = "0" * 40) -> Gate:
    """A checkout shaped like a competition repo, with a stub `verify.py`."""
    root = tmp_path / "gate"
    verifier = root / "validator/verifier"
    verifier.mkdir(parents=True)
    (verifier / "verify.py").write_text(body)
    (verifier / "PINS.json").write_text('{"pinned": {}}')
    import hashlib

    digest = hashlib.sha256((verifier / "PINS.json").read_bytes()).hexdigest()
    return Gate(
        slug="lz77",
        root=root,
        commit=commit,
        pins_sha256=digest,
        timeout_seconds=30.0,
    )


ACCEPTS = """
import json, sys
results = sys.argv[sys.argv.index("--results") + 1]
json.dump(%s, open(results, "w"))
print("TOTAL 1000 500 400")
sys.exit(0)
""" % json.dumps(RESULTS)

REJECTS = """
import sys
print("REJECTED: the proof does not typecheck")
sys.exit(1)
"""

BREAKS = """
import sys
print("stage 3 could not start", file=sys.stderr)
sys.exit(2)
"""


# ── what comes back ────────────────────────────────────────────────────────


def test_an_accepted_submission_carries_its_measurements(tmp_path):
    run = run_gate(_gate(tmp_path, ACCEPTS), parse_source=RUST, proof_source=LEAN)
    assert run.accepted and not run.broken
    assert run.measured["bytes"] == 400
    assert run.measured["incumbent_bytes"] == 500
    assert run.measured["raw_bytes"] == 1000
    # The gate computed the ratio its floor was applied to; the worker does not recompute it.
    assert run.measured["time_ratio"] == 1.2


def test_a_rejection_is_a_verdict_not_a_failure(tmp_path):
    run = run_gate(_gate(tmp_path, REJECTS), parse_source=RUST, proof_source=LEAN)
    assert run.rejected and not run.broken
    assert "does not typecheck" in run.report
    assert run.measured == {}


@pytest.mark.parametrize("code", [2, 3, 127])
def test_any_undefined_exit_code_is_the_validator_not_the_miner(tmp_path, code):
    run = run_gate(
        _gate(tmp_path, f"import sys\nsys.exit({code})"),
        parse_source=RUST,
        proof_source=LEAN,
    )
    assert run.broken
    assert not run.accepted and not run.rejected


def test_a_gate_that_times_out_rejects_rather_than_breaking(tmp_path):
    """The validator declining to spend more is a verdict the miner can act on."""
    gate = _gate(tmp_path, "import time\ntime.sleep(30)")
    gate = Gate(
        slug=gate.slug,
        root=gate.root,
        commit=gate.commit,
        pins_sha256=gate.pins_sha256,
        timeout_seconds=1.0,
    )
    run = run_gate(gate, parse_source=RUST, proof_source=LEAN)
    assert run.rejected
    assert "did not finish within" in run.report


def test_an_accept_with_no_usable_score_yields_nothing_rather_than_zeroes(tmp_path):
    """A half-measured point on the frontier is worse than no point."""
    body = (
        'import json, sys\n'
        'results = sys.argv[sys.argv.index("--results") + 1]\n'
        'json.dump({"candidates": ["submission"], "methods": '
        '{"submission": {"output_bytes": 400}}}, open(results, "w"))\n'
        "sys.exit(0)\n"
    )
    run = run_gate(_gate(tmp_path, body), parse_source=RUST, proof_source=LEAN)
    assert run.accepted
    assert run.measured == {}


def test_a_missing_or_unreadable_results_file_scores_nothing(tmp_path):
    assert measurements(tmp_path / "absent.json") == {}
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert measurements(broken) == {}


def test_a_missing_gate_is_broken_not_a_rejection(tmp_path):
    """A gate that is not there cannot have judged anything.

    The interpreter is still runnable, so this is not an OSError -- Python exits 2 on a
    script it cannot open, which lands in the same "undefined exit code" bucket as any other
    way the gate can fail to render a verdict. The submission is requeued uncharged.
    `registry.check` catches this at startup anyway; this proves the runner is safe if it
    somehow does not.
    """
    gate = _gate(tmp_path, "unused")
    gate.verify.unlink()
    run = run_gate(gate, parse_source=RUST, proof_source=LEAN)
    assert run.broken
    assert not run.rejected


# ── what the gate is handed ────────────────────────────────────────────────


def test_the_submission_is_materialised_from_the_bytes_it_is_given(tmp_path):
    """No shared filesystem: the API is in a container, the gate is on bare metal."""
    body = (
        "import sys, pathlib\n"
        "d = pathlib.Path(sys.argv[1])\n"
        'print((d / "parse.rs").read_text())\n'
        'print((d / "Parse.lean").read_text())\n'
        "sys.exit(1)\n"
    )
    run = run_gate(
        _gate(tmp_path, body), parse_source=b"MARKER_RUST", proof_source=b"MARKER_LEAN"
    )
    assert "MARKER_RUST" in run.report
    assert "MARKER_LEAN" in run.report


def test_the_workspace_does_not_outlive_the_run(tmp_path):
    body = (
        "import sys, pathlib\n"
        'pathlib.Path("/tmp/competition-workspace-probe").write_text(sys.argv[1])\n'
        "sys.exit(1)\n"
    )
    run_gate(_gate(tmp_path, body), parse_source=RUST, proof_source=LEAN)
    probe = Path("/tmp/competition-workspace-probe")
    try:
        workspace = Path(probe.read_text())
        assert not workspace.exists(), "the submission workspace survived the run"
    finally:
        probe.unlink(missing_ok=True)


def test_no_secret_reaches_the_subprocess(tmp_path, monkeypatch):
    """The one test to keep if only one survives.

    This subprocess compiles and runs miner-authored Rust. Handing it `os.environ` would
    hand it the competition database URL, the Axiom token, and the wallet path.
    """
    for name, value in {
        "COMPETITION_DATABASE_URL": "postgresql://secret@host/db",
        "DATABASE_URL": "postgresql://also-secret@host/db",
        "AXIOM_TOKEN": "xaat-secret",
        "BITTENSOR_WALLET_PATH": "/home/validator/.bittensor",
        "AWS_SECRET_ACCESS_KEY": "nope",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("VERIFY_SANDBOX", "bwrap")

    body = (
        "import json, os, sys\n"
        "print(json.dumps(dict(os.environ)))\n"
        "sys.exit(1)\n"
    )
    run = run_gate(_gate(tmp_path, body), parse_source=RUST, proof_source=LEAN)
    passed = json.loads(run.report.strip().splitlines()[0])

    for leaked in (
        "COMPETITION_DATABASE_URL",
        "DATABASE_URL",
        "AXIOM_TOKEN",
        "BITTENSOR_WALLET_PATH",
        "AWS_SECRET_ACCESS_KEY",
    ):
        assert leaked not in passed, f"{leaked} reached the gate subprocess"
    # What it does need still crosses.
    assert passed.get("VERIFY_SANDBOX") == "bwrap"
    assert set(passed) <= set(PASSTHROUGH_ENV)


# ── the integrity preflight ────────────────────────────────────────────────


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _committed_gate(tmp_path: Path, body: str = ACCEPTS) -> Gate:
    gate = _gate(tmp_path, body)
    _git(gate.root, "init", "-q")
    _git(gate.root, "config", "user.email", "t@example.com")
    _git(gate.root, "config", "user.name", "t")
    _git(gate.root, "add", "-A")
    _git(gate.root, "commit", "-qm", "gate")
    head = subprocess.run(
        ["git", "-C", str(gate.root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return Gate(
        slug=gate.slug,
        root=gate.root,
        commit=head,
        pins_sha256=gate.pins_sha256,
        timeout_seconds=gate.timeout_seconds,
    )


def test_a_clean_checkout_at_the_pinned_commit_passes(tmp_path):
    check_gate(_committed_gate(tmp_path))


def test_a_modified_checkout_refuses_to_serve(tmp_path):
    gate = _committed_gate(tmp_path)
    gate.verify.write_text("import sys\nsys.exit(0)  # backdoor\n")
    with pytest.raises(GateUnavailable, match="uncommitted changes"):
        check_gate(gate)


def test_a_checkout_at_another_commit_refuses_to_serve(tmp_path):
    gate = _committed_gate(tmp_path)
    wrong = Gate(
        slug=gate.slug,
        root=gate.root,
        commit="b" * 40,
        pins_sha256=gate.pins_sha256,
        timeout_seconds=gate.timeout_seconds,
    )
    with pytest.raises(GateUnavailable, match="registry pins"):
        check_gate(wrong)


def test_a_rewritten_pin_set_refuses_to_serve(tmp_path):
    """PINS.json pins the gate's files; this is what pins PINS.json."""
    gate = _committed_gate(tmp_path)
    wrong = Gate(
        slug=gate.slug,
        root=gate.root,
        commit=gate.commit,
        pins_sha256="c" * 64,
        timeout_seconds=gate.timeout_seconds,
    )
    with pytest.raises(GateUnavailable, match="PINS.json"):
        check_gate(wrong)


def test_a_missing_gate_refuses_to_serve(tmp_path):
    gate = _committed_gate(tmp_path)
    gate.verify.unlink()
    with pytest.raises(GateUnavailable, match="no gate at"):
        check_gate(gate)


def test_the_registry_file_is_validated_when_it_is_read(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text('{"not": "a list"}')
    with pytest.raises(GateUnavailable, match="list of gates"):
        load_registry(path)
    path.write_text('[{"slug": "x"}]')
    with pytest.raises(GateUnavailable, match="bad gate entry"):
        load_registry(path)


# ── configuration ──────────────────────────────────────────────────────────


def test_production_refuses_an_unsandboxed_gate():
    with pytest.raises(SettingsError, match="VERIFY_SANDBOX"):
        Settings.from_env(
            {
                "APP_MODE": "PROD",
                "COMPETITION_DATABASE_URL": "postgresql+psycopg://u:p@h/competition",
                "VERIFY_SANDBOX": "off",
            }
        )


def test_production_refuses_an_implicit_database():
    with pytest.raises(SettingsError, match="COMPETITION_DATABASE_URL"):
        Settings.from_env({"APP_MODE": "PROD", "VERIFY_SANDBOX": "bwrap"})


def test_development_may_run_unsandboxed():
    settings = Settings.from_env({"APP_MODE": "DEV", "VERIFY_SANDBOX": "off"})
    assert not settings.production
    assert settings.worker_id  # host:pid, so a stale claim names the machine that left it


def test_a_host_may_be_restricted_to_the_competitions_it_has_toolchains_for():
    settings = Settings.from_env(
        {"APP_MODE": "DEV", "COMPETITION_WORKER_COMPETITIONS": "lz77, rust-comp"}
    )
    assert settings.competitions == ("lz77", "rust-comp")


# ── the loop, against a real queue ─────────────────────────────────────────

from conftest import COMPETITION_SKIP_REASON, competition_dsn  # noqa: E402

from competition_worker.worker import BACKOFF_TURNS, CompetitionWorker  # noqa: E402
from conjectures_subnet.competition import SubmissionState, connect  # noqa: E402

needs_db = pytest.mark.skipif(competition_dsn() is None, reason=COMPETITION_SKIP_REASON)


@pytest.fixture
def store():
    from sqlalchemy import text

    opened = connect(competition_dsn())
    with opened.engine.begin() as conn:
        for table in ("entitlement_claims", "submissions", "registrations"):
            conn.execute(text(f"DELETE FROM {table}"))
    try:
        yield opened
    finally:
        opened.close()


def _queued(store, *, hotkey: str = "5" * 48, slots: int = 1, digest: str = "a" * 64) -> int:
    from sqlalchemy import text

    with store.engine.begin() as conn:
        for uid in range(slots):
            conn.execute(
                text(
                    "INSERT INTO registrations (uid, ss58_hot, ss58_cold, block, block_date)"
                    " VALUES (:uid, :hot, '5Cold', 100, now())"
                ),
                {"uid": uid, "hot": hotkey},
            )
    sub_id, _ = store.submissions.add(
        hotkey, digest, parse_source=RUST, proof_source=LEAN
    )
    return sub_id


def _worker(store, gate, **overrides):
    env = {"APP_MODE": "DEV", "VERIFY_SANDBOX": "off", **overrides}
    return CompetitionWorker(store, Settings.from_env(env), {gate.slug: gate})


@needs_db
def test_an_accepted_submission_spends_a_registration_and_records_its_score(store, tmp_path):
    sub_id = _queued(store)
    result = _worker(store, _gate(tmp_path, ACCEPTS)).drain()
    assert result.verified == 1
    row = store.submissions.get(sub_id)
    assert row.state == SubmissionState.ACCEPTED.value
    assert row.bytes == 400
    assert row.time_ratio == 1.2
    # The entitlement is spent on acceptance and only then.
    assert store.registrations.available_slots(row.hotkey) == 0


@needs_db
def test_a_rejected_submission_costs_the_miner_nothing(store, tmp_path):
    """A failed attempt at a hard problem must not be punished."""
    sub_id = _queued(store)
    _worker(store, _gate(tmp_path, REJECTS)).drain()
    row = store.submissions.get(sub_id)
    assert row.state == SubmissionState.REJECTED.value
    assert store.registrations.available_slots(row.hotkey) == 1


@needs_db
def test_a_broken_gate_requeues_uncharged_and_backs_that_competition_off(store, tmp_path):
    sub_id = _queued(store)
    worker = _worker(store, _gate(tmp_path, BREAKS))
    result = worker.drain()
    assert result.errored == 1
    row = store.submissions.get(sub_id)
    assert row.state == SubmissionState.QUEUED.value
    assert row.attempts == 1
    assert store.registrations.available_slots(row.hotkey) == 1
    # And it is not retried on the very next turn.
    assert worker.drain().verified == 0


@needs_db
def test_a_submission_that_always_breaks_the_gate_is_eventually_left_alone(store, tmp_path):
    """One poisonous row must not block the queue behind it forever."""
    sub_id = _queued(store)
    worker = _worker(store, _gate(tmp_path, BREAKS), COMPETITION_MAX_ATTEMPTS="2")
    for _ in range(4):
        worker._backoff.clear()  # skip the wait; the backoff is tested above
        worker.drain()
    row = store.submissions.get(sub_id)
    assert row.state == SubmissionState.ERROR.value
    assert "an operator has been asked to look" in row.report
    # Still uncharged: nothing here is a statement about the miner's submission.
    assert store.registrations.available_slots(row.hotkey) == 1


@needs_db
def test_the_backoff_expires_so_a_fixed_gate_needs_no_restart(store, tmp_path):
    """Backing off is a pause, not a latch.

    Asserted by the retry actually happening rather than by the key disappearing: once the
    backoff lapses the competition is tried again immediately, and this gate always breaks,
    so it re-arms in the same turn. The observable property is the second attempt.
    """
    sub_id = _queued(store)
    worker = _worker(store, _gate(tmp_path, BREAKS), COMPETITION_MAX_ATTEMPTS="99")
    worker.drain()
    assert worker._backoff["lz77"] == BACKOFF_TURNS
    assert store.submissions.get(sub_id).attempts == 1

    # Nothing is retried while the backoff stands.
    for _ in range(BACKOFF_TURNS - 1):
        worker.tick()
    assert store.submissions.get(sub_id).attempts == 1

    # The turn it lapses, the queue is drained again.
    worker.tick()
    assert store.submissions.get(sub_id).attempts == 2


@needs_db
def test_a_submission_with_no_files_is_an_error_not_a_rejection(store, tmp_path):
    """A row that predates the file columns has nothing for the gate to run."""
    sub_id, _ = store.submissions.add("5" * 48, "b" * 64)
    _worker(store, _gate(tmp_path, ACCEPTS)).drain()
    row = store.submissions.get(sub_id)
    assert row.state == SubmissionState.ERROR.value


@needs_db
def test_an_accept_the_gate_could_not_score_is_not_put_on_the_leaderboard(store, tmp_path):
    body = (
        'import json, sys\n'
        'json.dump({"candidates": ["submission"], "methods": {}}, '
        'open(sys.argv[sys.argv.index("--results") + 1], "w"))\n'
        "sys.exit(0)\n"
    )
    sub_id = _queued(store)
    _worker(store, _gate(tmp_path, body)).drain()
    row = store.submissions.get(sub_id)
    assert row.state != SubmissionState.ACCEPTED.value
    assert store.registrations.available_slots(row.hotkey) == 1


@needs_db
def test_a_claim_abandoned_mid_gate_is_reclaimed(store, tmp_path):
    from sqlalchemy import text

    sub_id = _queued(store)
    store.submissions.claim_next("worker-that-died")
    with store.engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE submissions SET claimed_at = now() - interval '10 hours' "
                "WHERE id = :i"
            ),
            {"i": sub_id},
        )
    _worker(store, _gate(tmp_path, ACCEPTS)).sweep()
    assert store.submissions.get(sub_id).state == SubmissionState.QUEUED.value


@needs_db
def test_two_workers_never_claim_the_same_submission(store, tmp_path):
    """SKIP LOCKED is what lets several gate hosts drain one queue."""
    _queued(store, slots=2)
    store.submissions.add("5" * 48, "c" * 64, parse_source=RUST, proof_source=LEAN)
    first = store.submissions.claim_next("worker-a")
    second = store.submissions.claim_next("worker-b")
    assert first is not None and second is not None
    assert first.id != second.id
