from __future__ import annotations

import os
import platform
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from verifier.errors import ReasonCode
from verifier.models import ProcessResult, TaskManifest
from verifier.process import run_process
from verifier.workspace import WorkspacePaths

COMPARATOR_MEMORY_BYTES = 64 * 1024 * 1024 * 1024
RESOURCE_FAILURE_MARKERS = (
    "resource exhausted",
    "resource temporarily unavailable",
    "failed to create thread",
    "cannot allocate memory",
    "out of memory",
    "std::bad_alloc",
)


# The only isolation a production verdict may be produced under. Named here because both
# `resolve_tools` and the checks that gate on it have to agree on the spelling.
PRODUCTION_SANDBOX_MODE = "landrun+seccomp"
DEVELOPMENT_SANDBOX_MODE = "development-fake-landrun"


@dataclass(frozen=True)
class ComparatorTools:
    comparator: Path
    lean4export: Path
    landrun: Path
    seccomp_launcher: Path | None
    nanoda: Path | None
    sandbox_mode: str


def resolve_tools(
    project_root: Path, *, insecure_development: bool = False
) -> ComparatorTools:
    """The isolation tools this host will use, and the honest name for what they provide.

    `insecure_development` selects the same non-sandboxing shim that non-Linux hosts already get,
    on Linux too. It exists because the real Landrun refuses to start below Landlock ABI 4 and
    exits 126, which `run_comparator` cannot distinguish from a proof the comparator rejected —
    so without this the environment's own failure is recorded as a verdict against the miner.

    The resulting `sandbox_mode` is `development-fake-landrun`, not the production name. That is
    the point: `production_sandbox_available` keys off it, so the caller must still pass
    `allow_insecure_development` to get past the gate, and every report and `verification_runs`
    row states which isolation actually ran.
    """
    root = project_root.resolve()
    comparator = root / "vendor/comparator/.lake/build/bin/comparator"
    exporter = root / "vendor/lean4export/.lake/build/bin/lean4export"
    if platform.system() == "Linux" and not insecure_development:
        landrun = root / "security/hardened-landrun.sh"
        launcher = root / ".tools/seccomp-launcher"
        mode = PRODUCTION_SANDBOX_MODE
    else:
        landrun = root / "vendor/comparator/scripts/fake-landrun.sh"
        launcher = None
        mode = DEVELOPMENT_SANDBOX_MODE
    default_nanoda = root / "vendor/nanoda/target/release/nanoda_bin"
    nanoda = default_nanoda if default_nanoda.is_file() else None
    return ComparatorTools(comparator, exporter, landrun, launcher, nanoda, mode)


def _safe_executable(path: Path | None) -> bool:
    return bool(path is not None and path.is_file() and not path.is_symlink() and os.access(path, os.X_OK))


def missing_tools(tools: ComparatorTools, enable_nanoda: bool = False) -> tuple[str, ...]:
    required = {
        "comparator": tools.comparator,
        "lean4export": tools.lean4export,
        "landrun": tools.landrun,
    }
    # Keyed off the mode rather than the platform: the development shim deliberately has no
    # seccomp launcher, and demanding one would report it as a missing tool instead.
    if tools.sandbox_mode == PRODUCTION_SANDBOX_MODE:
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
    project_root: Path | None = None,
    *,
    self_test_result: bool | None = None,
) -> bool:
    root = project_root or Path(__file__).resolve().parent.parent
    unprivileged = not hasattr(os, "geteuid") or os.geteuid() != 0
    behavior_valid = (
        sandbox_self_test(tools, root)
        if self_test_result is None
        else self_test_result
    )
    return (
        platform.system() == "Linux"
        and unprivileged
        and tools.sandbox_mode == PRODUCTION_SANDBOX_MODE
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
    insecure_development: bool = False,
) -> tuple[ProcessResult, ComparatorTools]:
    tools = resolve_tools(project_root, insecure_development=insecure_development)
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
        memory_bytes=COMPARATOR_MEMORY_BYTES,
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
    if result.signal is not None or any(marker in combined for marker in RESOURCE_FAILURE_MARKERS):
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
