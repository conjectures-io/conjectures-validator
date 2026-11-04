"""The two lock files, pyproject.toml and every install that ships agree with one another.

`scripts/lock_dependencies.sh` writes both locks from one resolve, but nothing stops a hand edit
to one of them, a pin bumped in pyproject.toml without regenerating, or a new Dockerfile that
installs without `--require-hashes`. Each of those silently gives up what the lock is for, so each
is checked here, offline, in the ordinary test run.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "requirements-service.lock"
HASHES = ROOT / "requirements-service.hashes"

# Every file that installs runtime packages from PyPI into something that ships.
INSTALLERS = (
    "deploy/api/Dockerfile",
    "Dockerfile.worker",
    "Dockerfile.watcher",
    "Dockerfile.emissions",
    "Dockerfile.payout-notifier",
    "deploy/migrate/competition/Dockerfile",
    "scripts/install_worker.sh",
)

_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)")


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _PIN.match(line)
        if match:
            name = _normalise(match.group(1))
            assert name not in pins, f"{path.name} pins {name} twice"
            pins[name] = match.group(2)
    return pins


def _hashed_entries(path: Path) -> dict[str, int]:
    """Each requirement in the hashed lock, with how many hashes follow it."""
    counts: dict[str, int] = {}
    current: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _PIN.match(line)
        if match:
            current = _normalise(match.group(1))
            counts[current] = 0
        elif current is not None and line.strip().startswith("--hash=sha256:"):
            counts[current] += 1
    return counts


def _commands(path: Path) -> list[str]:
    """Every `pip install` command in a Dockerfile or shell script, continuations joined."""
    joined = path.read_text(encoding="utf-8").replace("\\\n", " ")
    commands: list[str] = []
    for line in joined.splitlines():
        if line.lstrip().startswith("#"):
            continue
        for part in re.split(r"&&|;", line):
            if re.search(r"\bpip install\b", part):
                commands.append(" ".join(part.split()))
    return commands


def test_both_locks_carry_the_same_pins():
    assert _pins(LOCK) == _pins(HASHES), (
        "requirements-service.lock and .hashes disagree; regenerate both with "
        "scripts/lock_dependencies.sh"
    )


def test_every_hashed_requirement_has_a_hash():
    unhashed = [name for name, count in _hashed_entries(HASHES).items() if count == 0]
    assert not unhashed, unhashed


def test_every_exact_pin_in_pyproject_is_the_locked_version():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    locked = _pins(LOCK)
    for extra, requirements in project["optional-dependencies"].items():
        if extra == "dev":  # resolved fresh in CI, never shipped, deliberately not locked
            continue
        for requirement in requirements:
            match = re.match(r"^([A-Za-z0-9._-]+)(?:\[[^\]]*\])?==(\S+)$", requirement)
            if not match:
                continue
            name = _normalise(match.group(1))
            assert locked.get(name) == match.group(2), (
                f"pyproject.toml [{extra}] pins {name}=={match.group(2)} but the lock has "
                f"{locked.get(name)}; regenerate with scripts/lock_dependencies.sh"
            )


@pytest.mark.parametrize("installer", INSTALLERS)
def test_every_shipped_install_is_hash_checked_against_the_lock(installer):
    commands = _commands(ROOT / installer)
    assert commands, f"{installer} installs nothing; drop it from INSTALLERS"
    locked = _pins(LOCK)
    for command in commands:
        # The two installs that are not from the lock: pip bootstrapping itself to an exact
        # version, and the project's own source with its dependencies already in place.
        if "--upgrade pip==" in command or re.search(r"--no-deps \.(\s|$)", command):
            continue
        assert "--require-hashes" in command, f"{installer}: {command}"
        assert "requirements-service.hashes" in command, f"{installer}: {command}"
        # Every package an image names has to be one the lock pins, or pip refuses it in
        # hash-checking mode -- at build time, on the server. Better here.
        tail = command.split("pip install", 1)[1]
        for token in tail.split():
            token = token.strip("'\"")
            if token.startswith("-") or "requirements-service" in token or "/" in token:
                continue
            name = _normalise(token.split("[", 1)[0].split("==", 1)[0])
            assert name in locked, f"{installer} installs {token}, which the lock does not pin"
