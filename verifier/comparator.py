from __future__ import annotations

import os
import platform
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from verifier.errors import ReasonCode
from verifier.models import ProcessResult, TaskManifest
from verifier.process import run_process
from verifier.workspace import WorkspacePaths


@dataclass(frozen=True)
class ComparatorTools:
    comparator: Path
    lean4export: Path
    landrun: Path
    seccomp_launcher: Path | None
    nanoda: Path | None
    sandbox_mode: str


def resolve_tools(project_root: Path) -> ComparatorTools:
    root = project_root.resolve()
    comparator = root / "vendor/comparator/.lake/build/bin/comparator"
    exporter = root / "vendor/lean4export/.lake/build/bin/lean4export"
    if platform.system() == "Linux":
        landrun = root / "security/hardened-landrun.sh"
        launcher = root / ".tools/seccomp-launcher"
        mode = "landrun+seccomp"
    else:
        landrun = root / "vendor/comparator/scripts/fake-landrun.sh"
        launcher = None
        mode = "development-fake-landrun"
    default_nanoda = root / "vendor/nanoda/target/release/nanoda_bin"
    nanoda = default_nanoda if default_nanoda.is_file() else None
    return ComparatorTools(comparator, exporter, landrun, launcher, nanoda, mode)


def _safe_executable(path: Path | None) -> bool:
    return bool(path is not None and path.is_file() and not path.is_symlink() and os.access(path, os.X_OK))


def missing_tools(tools: ComparatorTools, enable_nanoda: bool) -> tuple[str, ...]:
    required = {
        "comparator": tools.comparator,
        "lean4export": tools.lean4export,
        "landrun": tools.landrun,
    }
    if platform.system() == "Linux":
        required["seccomp_launcher"] = tools.seccomp_launcher
    if enable_nanoda:
        required["nanoda"] = tools.nanoda
    return tuple(name for name, path in required.items() if not _safe_executable(path))


def sandbox_self_test(tools: ComparatorTools, project_root: Path) -> bool:
    if platform.system() != "Linux" or not _safe_executable(tools.seccomp_launcher):
        return False
    probe = project_root / "security" / "sandbox-probe.py"
    parent = project_root / ".work"
    if not probe.is_file() or probe.is_symlink() or not parent.is_dir():
        return False
    try:
        with tempfile.TemporaryDirectory(prefix="sandbox-probe-", dir=parent) as temporary:
            root = Path(temporary)
            allowed = root / "allowed"
            denied = root / "denied"
            readable = project_root / "pins.lock.json"
            allowed.mkdir()
            denied.mkdir()
            (denied / "existing").write_bytes(b"unchanged")
            executable = allowed / "executable"
            executable.write_bytes(Path("/usr/bin/true").read_bytes())
            executable.chmod(0o700)
            command = (
                str(tools.seccomp_launcher),
                str(tools.landrun),
                "--best-effort",
                "--ro",
                "/",
                "--rw",
                "/dev",
                "-ldd",
                "-add-exec",
                "--ro",
                str(probe),
                "--ro",
                str(readable),
                "--rwx",
                str(allowed),
                "/usr/bin/python3",
                str(probe),
                str(allowed),
                str(denied),
                str(readable),
            )
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
                check=False,
                timeout=10,
            )
            return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def production_sandbox_available(
    tools: ComparatorTools,
    project_root: Path,
    *,
    self_test_result: bool | None = None,
) -> bool:
    unprivileged = not hasattr(os, "geteuid") or os.geteuid() != 0
    behavior_valid = (
        sandbox_self_test(tools, project_root)
        if self_test_result is None
        else self_test_result
    )
    return (
        platform.system() == "Linux"
        and unprivileged
        and tools.sandbox_mode == "landrun+seccomp"
        and not missing_tools(tools, False)
        and landlock_available(tools)
        and behavior_valid
    )


def landlock_available(tools: ComparatorTools) -> bool:
    if platform.system() != "Linux" or not _safe_executable(tools.seccomp_launcher):
        return False
    try:
        result = subprocess.run(
            (str(tools.seccomp_launcher), "--check-landlock"),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def run_comparator(
    *,
    paths: WorkspacePaths,
    manifest: TaskManifest,
    project_root: Path,
    lake: Path,
    env: Mapping[str, str],
    timeout_seconds: int,
) -> tuple[ProcessResult, ComparatorTools]:
    tools = resolve_tools(project_root)
    absent = missing_tools(tools, manifest.enable_nanoda)
    if absent:
        detail = ", ".join(absent)
        return (
            ProcessResult(
                args=(),
                exit_code=127,
                stdout="",
                stderr=f"missing verification tools: {detail}",
                duration_ms=0,
            ),
            tools,
        )
    effective_env = {
        **dict(env),
        "COMPARATOR_LANDRUN": str(tools.landrun),
        "COMPARATOR_LEAN4EXPORT": str(tools.lean4export),
        "HOME": str(paths.root / ".home"),
    }
    if tools.nanoda is not None:
        effective_env["COMPARATOR_NANODA"] = str(tools.nanoda)
    command = (str(lake), "env", str(tools.comparator), str(paths.config))
    if tools.seccomp_launcher is not None:
        command = (str(tools.seccomp_launcher), *command)
    result = run_process(
        command,
        cwd=paths.root,
        timeout_seconds=timeout_seconds,
        env=effective_env,
        memory_bytes=12 * 1024 * 1024 * 1024,
        cpu_seconds=timeout_seconds,
        file_bytes=2 * 1024 * 1024 * 1024,
        open_files=1024,
        processes=512,
    )
    return result, tools


def rejection_reason(result: ProcessResult, enable_nanoda: bool) -> ReasonCode:
    if result.timed_out:
        return ReasonCode.TIMEOUT
    combined = f"{result.stdout}\n{result.stderr}".lower()
    if result.exit_code == 127 or "missing verification tools" in combined:
        return ReasonCode.INTERNAL_ERROR
    if result.signal is not None:
        return ReasonCode.RESOURCE_LIMIT
    if combined.rfind("building solution") > combined.rfind("exporting"):
        return ReasonCode.SOLUTION_BUILD_FAILED
    if "illegal axiom" in combined or (
        "axiom" in combined and ("not permitted" in combined or "unpermitted" in combined)
    ):
        return ReasonCode.UNPERMITTED_AXIOM
    if (
        "different" in combined
        or "mismatch" in combined
        or "does not match" in combined
        or "do not match" in combined
        or "same statement" in combined
    ):
        return ReasonCode.STATEMENT_MISMATCH
    if enable_nanoda and "nanoda" in combined:
        return ReasonCode.NANODA_REJECTED
    if "kernel" in combined:
        return ReasonCode.LEAN_KERNEL_REJECTED
    return ReasonCode.COMPARATOR_REJECTED
