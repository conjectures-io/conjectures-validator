from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from verifier.errors import ReasonCode, VerifierError


SYSTEM_PATH = ("/usr/local/bin", "/usr/bin", "/bin")


def _toolchain_directory_name(toolchain: str) -> str:
    return toolchain.replace("/", "--").replace(":", "---")


def target_toolchain_bin(project_root: Path) -> Path:
    try:
        toolchain = (project_root / "lean-toolchain").read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise VerifierError(ReasonCode.INTERNAL_ERROR, f"cannot read verifier toolchain: {exc}") from exc
    directory = project_root / ".elan" / "toolchains" / _toolchain_directory_name(toolchain) / "bin"
    if not directory.is_dir():
        raise VerifierError(ReasonCode.INTERNAL_ERROR, f"pinned Lean toolchain is missing: {directory}")
    return directory


def tool_path(project_root: Path, name: str) -> Path:
    path = target_toolchain_bin(project_root) / name
    if not path.is_file() or path.is_symlink() or not os.access(path, os.X_OK):
        raise VerifierError(ReasonCode.INTERNAL_ERROR, f"trusted executable is missing or unsafe: {path}")
    return path


def trusted_environment(project_root: Path, home: Path) -> Mapping[str, str]:
    toolchain_bin = target_toolchain_bin(project_root)
    elan_bin = project_root / ".elan" / "bin"
    return {
        "ELAN_HOME": str((project_root / ".elan").resolve()),
        "HOME": str(home.resolve()),
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": os.pathsep.join((str(toolchain_bin.resolve()), str(elan_bin.resolve()), *SYSTEM_PATH)),
        "LEAN_ABORT_ON_PANIC": "1",
        "TMPDIR": str((home / ".tmp").resolve()),
    }
