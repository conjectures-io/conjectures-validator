"""What the image's own readiness verdict may and may not depend on."""

from __future__ import annotations

import platform
from pathlib import Path

import pytest

from verifier.comparator import (
    DEVELOPMENT_SANDBOX_MODE,
    PRODUCTION_SANDBOX_MODE,
    missing_tools,
    resolve_tools,
)
from verifier.doctor import image_pins_satisfied

ROOT = Path(__file__).resolve().parents[1]


def _pins(**overrides: bool) -> dict[str, dict[str, bool]]:
    status = {"formal_conjectures": True, "mathlib": True, "lean": True, "tasks": True}
    status.update(overrides)
    return {name: {"pinned": pinned} for name, pinned in status.items()}


def test_readiness_ignores_the_task_pool_pin():
    """A container's doctor run mounts nothing, so requiring this pin made `ready` unreachable.

    This is the whole production container runner: `assert_container_ready` refuses to start a
    worker whose image reports `ready: false`, and every image reports the task pool as unpinned
    because the pool is mounted one task at a time, per verification, and never for the doctor.
    """
    assert image_pins_satisfied(_pins(tasks=False)) is True


def test_readiness_still_requires_every_pin_inside_the_image():
    """The exclusion is the task pool alone, not a general softening of the pin check."""
    assert image_pins_satisfied(_pins()) is True
    assert image_pins_satisfied(_pins(mathlib=False)) is False
    assert image_pins_satisfied(_pins(formal_conjectures=False)) is False
    assert image_pins_satisfied(_pins(lean=False)) is False


def test_verification_and_readiness_agree_on_which_pins_matter():
    """They disagreed once, and the verifier then failed every proof at LOAD_TASK.

    `assert_dependency_pins` runs inside the container on the verification path; `ready` runs on
    the doctor path. Both must exclude the task pool, or one gate passes and the other rejects
    every submission for a reason that has nothing to do with the submission.
    """
    from verifier.repository import image_pin_statuses

    statuses = _pins(tasks=False)
    assert "tasks" not in image_pin_statuses(statuses)
    assert set(image_pin_statuses(statuses)) == {"formal_conjectures", "mathlib", "lean"}
    assert image_pins_satisfied(statuses) is True


@pytest.mark.skipif(platform.system() != "Linux", reason="production mode is Linux-only")
def test_the_development_sandbox_needs_no_seccomp_launcher():
    """A host that will never run production isolation must not be judged against its tooling.

    `doctor` resolved production tools unconditionally, so a checkout without the compiled seccomp
    launcher reported `ready: false` — over a binary it had no use for, since the caller passing
    `--allow-insecure-development` runs the shim instead.
    """
    production = missing_tools(resolve_tools(ROOT))
    development = missing_tools(resolve_tools(ROOT, insecure_development=True))

    assert "seccomp_launcher" not in development
    assert set(development) <= set(production) | {"seccomp_launcher"}


def test_the_development_shim_is_the_landrun_the_development_mode_names():
    """`landrun` is a wrapper script in both modes, never the Go binary, so Go is not required.

    The distinction matters for what a host has to install: production names
    `security/hardened-landrun.sh`, which execs the compiled binary, while development names the
    comparator's checked-in shim. Only the first needs a Go toolchain, and `missing_tools` inspects
    the wrapper either way.
    """
    development = resolve_tools(ROOT, insecure_development=True)

    assert development.sandbox_mode == DEVELOPMENT_SANDBOX_MODE
    assert development.landrun.name == "fake-landrun.sh"
    assert development.seccomp_launcher is None
    if platform.system() == "Linux":
        assert resolve_tools(ROOT).sandbox_mode == PRODUCTION_SANDBOX_MODE
