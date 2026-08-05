"""Static guards on the production worker deployment boundary."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / "deploy/worker/conjectures-verification-worker.service"
ENVIRONMENT = ROOT / "deploy/worker/verification-worker.env.example"
RUNBOOK = ROOT / "deploy/worker/README.md"


def test_production_environment_is_explicit_and_has_no_insecure_escape_hatch():
    value = ENVIRONMENT.read_text(encoding="utf-8")
    assert "APP_MODE=PROD" in value
    assert "VERIFICATION_RUNNER=container" in value
    assert "VERIFIER_CONTAINER_DIGEST=sha256:<64-lowercase-hex-digits>" in value
    assert "DATABASE_URL=" in value
    assert "VERIFICATION_ALLOW_INSECURE_SANDBOX" not in value


def test_systemd_runs_the_non_mutating_preflight_before_the_worker():
    value = UNIT.read_text(encoding="utf-8")
    check = "python -m verification_worker --check"
    run = "python -m verification_worker --log-level INFO"
    assert check in value
    assert run in value
    assert value.index(check) < value.index(run)
    assert "RestartPreventExitStatus=2" in value
    assert "TimeoutStopSec=75min" in value


def test_systemd_keeps_the_release_read_only_without_hiding_proof_paths_from_docker():
    value = UNIT.read_text(encoding="utf-8")
    assert "ProtectSystem=strict" in value
    assert "ReadWritePaths=/var/lib/conjectures-worker" in value
    assert "TMPDIR=/var/lib/conjectures-worker" in value
    assert "PrivateTmp=true" not in value
    assert "/var/run/docker.sock" not in value
    assert "NoNewPrivileges=true" in value
    assert "CapabilityBoundingSet=" in value


def test_runbook_explicitly_separates_development_compose_from_production():
    value = RUNBOOK.read_text(encoding="utf-8")
    assert "Do not adapt `docker-compose.worker.yml` for production" in value
    assert "one fresh, networkless verifier container" in value
    assert "submissions remain paused" in value
