"""Static guards on the production worker deployment boundary."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / "deploy/worker/conjectures-verification-worker.service"
ENVIRONMENT = ROOT / "deploy/worker/verification-worker.env.example"
RUNBOOK = ROOT / "deploy/worker/README.md"
DOCKERIGNORE = ROOT / ".dockerignore"
INSTALLER = ROOT / "scripts/install_worker.sh"


def test_production_environment_is_explicit_and_has_no_insecure_escape_hatch():
    value = ENVIRONMENT.read_text(encoding="utf-8")
    assert "APP_MODE=PROD" in value
    assert "VERIFICATION_RUNNER=container" in value
    assert "VERIFIER_CONTAINER_DIGEST=sha256:<64-lowercase-hex-digits>" in value
    assert "DATABASE_URL=" in value
    assert "VERIFICATION_ALLOW_INSECURE_SANDBOX" not in value


def test_the_installer_derives_every_field_that_describes_the_image():
    """The tag, the digest and the version all describe one image, so all three are derived.

    Deriving only two of them was a real failure: `VERIFIER_IMAGE_TAG=…:local just install-worker`
    resolved the local image and wrote its digest, while VERIFIER_IMAGE kept the template's
    `:release`. The image check passed and the preflight then failed on a tag nobody had asked
    for, with the environment file at odds with itself.
    """
    value = INSTALLER.read_text(encoding="utf-8")
    for key in ("VERIFIER_CONTAINER_DIGEST", "VERIFIER_VERSION", "VERIFIER_IMAGE"):
        assert f'"{key}":' in value, f"{key} is not derived by the installer"
    # Derived from the resolved tag, not from a second literal that could drift from it.
    assert '"VERIFIER_IMAGE": image_tag,' in value
    assert '"$IMAGE_TAG"' in value


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
    assert "PrivateIPC=true" in value
    assert "ProtectProc=invisible" in value
    assert "IPAddressDeny=any" in value
    assert "IPAddressAllow=127.0.0.0/8" in value


def test_runbook_explicitly_separates_development_compose_from_production():
    value = RUNBOOK.read_text(encoding="utf-8")
    assert "Do not adapt `docker-compose.worker.yml` for production" in value
    assert "one fresh, networkless verifier container" in value
    assert "submissions remain paused" in value


def test_verifier_image_build_refuses_dirty_source_and_excludes_secrets():
    runbook = RUNBOOK.read_text(encoding="utf-8")
    ignored = DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
    assert "git status --porcelain=v1 --untracked-files=all" in runbook
    assert ".env" in ignored
    assert ".env.*" in ignored
    assert "**/*.key" in ignored
    assert "**/*.pem" in ignored
    assert "**/.bittensor" in ignored
