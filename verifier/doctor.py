from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from verifier.comparator import (
    landlock_available,
    missing_tools,
    production_sandbox_available,
    resolve_tools,
    sandbox_self_test,
)
from verifier.environment import tool_path
from verifier.repository import (
    dependency_pin_status,
    formal_conjectures_pin,
    image_pin_statuses,
    load_pins,
    repository_commit,
)


def _version_output(path: Path) -> str:
    try:
        return subprocess.run(
            (str(path), "--version"),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            shell=False,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def image_pins_satisfied(dependency_pins: Mapping[str, Mapping[str, Any]]) -> bool:
    """Whether every pin carried inside this image matches. The task pool is not one of them.

    `ready` answers one question: is THIS image fit to verify a proof. Which pins that covers is
    `image_pin_statuses`, shared with `assert_dependency_pins` so readiness and verification cannot
    disagree about it — they did, and the result was an image that reported itself unready and,
    once past that, failed every verification at LOAD_TASK for the same reason.
    """
    return all(status["pinned"] for status in image_pin_statuses(dependency_pins).values())


def doctor_report(project_root: Path) -> dict[str, Any]:
    repo = project_root / "vendor" / "formal-conjectures"
    pins = load_pins(project_root)
    expected = formal_conjectures_pin(project_root)
    actual = repository_commit(repo) if repo.is_dir() else None
    dependency_pins = dependency_pin_status(project_root)
    all_pinned = image_pins_satisfied(dependency_pins)
    tools = resolve_tools(project_root)
    sandbox_probe = sandbox_self_test(tools, project_root)
    absent = missing_tools(tools, enable_nanoda=False)
    commands = {name: shutil.which(name) for name in ("git", "lean", "lake", "docker")}
    lean_version = _version_output(tool_path(project_root, "lean"))
    elan_version = _version_output(project_root / ".elan" / "bin" / "elan")
    lean_identity_valid = str(pins["lean"]["commit"]) in lean_version
    elan_identity_valid = f"elan {pins['elan']['version']} " in elan_version
    unprivileged = not hasattr(os, "geteuid") or os.geteuid() != 0
    return {
        "schema_version": 1,
        "python": {
            "version": platform.python_version(),
            "supported": sys.version_info >= (3, 11),
        },
        "platform": {"system": platform.system(), "machine": platform.machine()},
        "commands": commands,
        "toolchain_identity": {
            "lean_version": lean_version,
            "lean_commit_valid": lean_identity_valid,
            "elan_version": elan_version,
            "elan_version_valid": elan_identity_valid,
        },
        "formal_conjectures": {
            "path": str(repo),
            "expected_commit": expected,
            "actual_commit": actual,
            "pinned": actual == expected,
        },
        "dependency_pins": dependency_pins,
        "comparator": {
            "path": str(tools.comparator),
            "lean4export": str(tools.lean4export),
            "landrun": str(tools.landrun),
            "seccomp_launcher": str(tools.seccomp_launcher) if tools.seccomp_launcher is not None else None,
            "nanoda": str(tools.nanoda) if tools.nanoda is not None else None,
            "nanoda_available": tools.nanoda is not None and tools.nanoda.is_file(),
            "missing": list(absent),
        },
        "sandbox": {
            "mode": tools.sandbox_mode,
            "production_ready": unprivileged
            and production_sandbox_available(
                tools,
                project_root,
                self_test_result=sandbox_probe,
            ),
            "landlock_abi_4_or_newer": landlock_available(tools),
            "behavioral_self_test": sandbox_probe,
            "network_isolation_required": True,
            "unprivileged": unprivileged,
        },
        "ready": (
            sys.version_info >= (3, 11)
            and actual == expected
            and all_pinned
            and commands["git"] is not None
            and commands["lean"] is not None
            and commands["lake"] is not None
            and not absent
            and lean_identity_valid
            and elan_identity_valid
        ),
    }
