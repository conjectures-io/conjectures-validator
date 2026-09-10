"""What the image's own readiness verdict may and may not depend on."""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path

import pytest

from verifier.comparator import (
    DEVELOPMENT_SANDBOX_MODE,
    PRODUCTION_SANDBOX_MODE,
    missing_tools,
    resolve_tools,
)
from verifier.doctor import image_pins_satisfied, trusted_cache_status

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
@pytest.mark.needs_checkouts
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


def _trusted_tree(root: Path) -> Path:
    """The image's layout in miniature: a root package and one dependency with a build tree.

    `trusted_cache_status` resolves what to probe through the Lake package graph, the same way
    `create_workspace` resolves what to symlink, so a fixture has to be a real graph rather than a
    bare directory of files.
    """
    (root / "lakefile.toml").write_text('name = "root"\n', encoding="utf-8")
    (root / "lake-manifest.json").write_text(
        json.dumps(
            {
                "packages": [
                    {
                        "type": "path",
                        "name": "mathlib",
                        "dir": "vendor/mathlib",
                        "configFile": "lakefile.toml",
                        "manifestFile": "lake-manifest.json",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (root / ".lake" / "build").mkdir(parents=True)
    (root / ".lake" / "build" / "root.trace").write_text("root\n", encoding="utf-8")
    package = root / "vendor" / "mathlib"
    package.mkdir(parents=True)
    (package / "lakefile.toml").write_text('name = "mathlib"\n', encoding="utf-8")
    (package / "lake-manifest.json").write_text('{"packages": []}\n', encoding="utf-8")
    build = package / ".lake" / "build" / "lib" / "lean"
    build.mkdir(parents=True)
    cached = build / "SplitOn.trace"
    cached.write_text("cached\n", encoding="utf-8")
    return cached


def test_the_trusted_cache_probe_covers_every_package_the_workspace_symlinks(tmp_path):
    """Both build trees, resolved from the package graph rather than from a hardcoded list."""
    _trusted_tree(tmp_path)
    status = trusted_cache_status(tmp_path)

    assert status["readable"] is True
    assert status["files_probed"] == 2
    assert status["unreadable"] == []
    assert sorted(status["build_roots"]) == [
        str(tmp_path / ".lake" / "build"),
        str(tmp_path / "vendor" / "mathlib" / ".lake" / "build"),
    ]


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root reads regardless of mode, which is the state this probe exists to catch",
)
def test_readiness_fails_when_the_running_user_cannot_read_the_trusted_cache(tmp_path):
    """The gap that let a fatally broken image report `ready: true`.

    Mathlib's cache arrives through the toolchain's `leantar`, which writes 0600 whatever the
    umask. Every other check in `doctor` — pins, tool paths, version strings, the sandbox self-test
    — passes against a cache the running user cannot open, and the first thing that notices is
    `lake build Challenge` dying on a `.trace`. That surfaces as CHALLENGE_BUILD_FAILED, which the
    worker classes as its own failure, so the submission gets no verdict at all: three retries and
    the refund alarm.
    """
    cached = _trusted_tree(tmp_path)
    cached.chmod(0o000)

    unreadable = trusted_cache_status(tmp_path)
    assert unreadable["readable"] is False
    assert any(str(cached) in entry for entry in unreadable["unreadable"])

    cached.chmod(0o644)
    assert trusted_cache_status(tmp_path)["readable"] is True


def test_an_unreadable_cache_directory_is_reported_rather_than_walked_past(tmp_path):
    """A 0700 directory hides its files from the walk, so silence there is not readability."""
    _trusted_tree(tmp_path)
    build = tmp_path / "vendor" / "mathlib" / ".lake" / "build" / "lib"
    build.chmod(0o000)
    try:
        status = trusted_cache_status(tmp_path)
    finally:
        build.chmod(0o755)

    if os.geteuid() != 0:  # pragma: no branch - the suite does not run as root
        assert status["readable"] is False
        assert status["unreadable"]


def test_a_broken_package_graph_is_not_reported_as_a_readable_cache(tmp_path):
    """`trusted_build_roots` raises for a missing checkout, and unready is the safe reading."""
    (tmp_path / "lakefile.toml").write_text('name = "root"\n', encoding="utf-8")
    (tmp_path / "lake-manifest.json").write_text('{"packages": "not a list"}', encoding="utf-8")

    status = trusted_cache_status(tmp_path)

    assert status["readable"] is False
    assert status["files_probed"] == 0
