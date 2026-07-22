from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from verifier.comparator import missing_tools, resolve_tools
from verifier.repository import dependency_pin_status, formal_conjectures_pin, repository_commit


def doctor_report(project_root: Path) -> dict[str, Any]:
    repo = project_root / "vendor" / "formal-conjectures"
    expected = formal_conjectures_pin(project_root)
    actual = repository_commit(repo) if repo.is_dir() else None
    dependency_pins = dependency_pin_status(project_root)
    all_pinned = all(status["pinned"] for status in dependency_pins.values())
    tools = resolve_tools(project_root)
    absent = missing_tools(tools, enable_nanoda=False)
    commands = {name: shutil.which(name) for name in ("git", "lean", "lake", "docker")}
    linux = platform.system() == "Linux"
    unprivileged = not hasattr(os, "geteuid") or os.geteuid() != 0
    return {
        "schema_version": 1,
        "python": {
            "version": platform.python_version(),
            "supported": sys.version_info >= (3, 11),
        },
        "platform": {"system": platform.system(), "machine": platform.machine()},
        "commands": commands,
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
            "nanoda": str(tools.nanoda) if tools.nanoda is not None else None,
            "nanoda_available": tools.nanoda is not None and tools.nanoda.is_file(),
            "missing": list(absent),
        },
        "sandbox": {
            "mode": tools.sandbox_mode,
            "production_ready": linux and unprivileged and not absent and tools.sandbox_mode == "landrun",
            "network_disabled_by_default": linux,
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
        ),
    }
