"""What setup refuses to start over, and what it merely mentions.

The gate is the difference between a miner learning in ten seconds that `python3-venv` is missing
and learning it forty minutes into a Lean build. Two properties carry that: it reports everything
wrong at once, and it demands only what the profile in front of it actually builds.
"""

from __future__ import annotations

import importlib.util
import platform
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _module():
    spec = importlib.util.spec_from_file_location(
        "check_prerequisites", ROOT / "scripts" / "check_prerequisites.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registered before execution because @dataclass resolves annotations through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


prerequisites = _module()


def _names(checks) -> set[str]:
    return {check.name for check in checks}


@pytest.mark.skipif(platform.system() != "Linux", reason="the sandbox tooling is Linux-only")
def test_a_miner_is_not_asked_for_a_go_or_c_toolchain():
    """`--miner` builds neither landrun nor the seccomp launcher, so neither compiler is needed.

    Demanding them anyway would put `golang-go` and `build-essential` back on the install list for
    a host that will never invoke either.
    """
    miner = _names(prerequisites.collect(ROOT, miner=True, offline=True))
    validator = _names(prerequisites.collect(ROOT, miner=False, offline=True))

    assert {"go", "cc"} <= validator
    assert not {"go", "cc"} & miner
    assert miner < validator


def test_the_network_check_can_be_left_out(monkeypatch):
    """Everything else has to remain answerable on a host that is deliberately offline."""
    monkeypatch.setattr(
        prerequisites, "check_network", lambda *_: prerequisites.Check("network", True, "stub")
    )

    assert "network" not in _names(prerequisites.collect(ROOT, miner=True, offline=True))
    assert "network" in _names(prerequisites.collect(ROOT, miner=True, offline=False))


def test_every_failure_is_reported_not_just_the_first(monkeypatch):
    """Otherwise a miner runs the check five times to discover five missing packages."""
    monkeypatch.setattr(prerequisites.shutil, "which", lambda *_: None)

    checks = prerequisites.collect(ROOT, miner=True, offline=True)
    failed = [check for check in checks if not check.ok]

    assert {"curl", "tar", "sha256sum", "zstd"} <= {check.name for check in failed}
    assert all(check.remedy for check in failed)


def test_a_shortfall_of_memory_is_a_warning_and_not_a_refusal(monkeypatch):
    """It slows a build down or kills it late; it is not a reason to refuse to start one."""
    monkeypatch.setattr(prerequisites.os, "sysconf", lambda *_: 1024)

    check = prerequisites.check_memory()

    assert check.ok is False
    assert check.advisory is True
