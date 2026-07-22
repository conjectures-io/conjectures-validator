from __future__ import annotations

import os
import platform
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
    nanoda: Path | None
    sandbox_mode: str


def _configured_path(env: Mapping[str, str], name: str, default: Path) -> Path:
    return Path(env.get(name, str(default))).expanduser().resolve()


def resolve_tools(project_root: Path, env: Mapping[str, str] | None = None) -> ComparatorTools:
    values = dict(os.environ if env is None else env)
    comparator = _configured_path(
        values, "VERIFIER_COMPARATOR", project_root / "vendor/comparator/.lake/build/bin/comparator"
    )
    exporter = _configured_path(
        values, "COMPARATOR_LEAN4EXPORT", project_root / "vendor/lean4export/.lake/build/bin/lean4export"
    )
    if platform.system() == "Linux":
        landrun = _configured_path(values, "COMPARATOR_LANDRUN", project_root / "vendor/landrun/bin/landrun")
        mode = "landrun"
    else:
        landrun = _configured_path(
            values, "COMPARATOR_LANDRUN", project_root / "vendor/comparator/scripts/fake-landrun.sh"
        )
        mode = "development-fake-landrun"
    default_nanoda = project_root / "vendor/nanoda/target/release/nanoda_bin"
    nanoda_value = values.get("COMPARATOR_NANODA")
    nanoda = Path(nanoda_value).resolve() if nanoda_value else (default_nanoda if default_nanoda.is_file() else None)
    return ComparatorTools(comparator, exporter, landrun, nanoda, mode)


def missing_tools(tools: ComparatorTools, enable_nanoda: bool) -> tuple[str, ...]:
    required = {
        "comparator": tools.comparator,
        "lean4export": tools.lean4export,
        "landrun": tools.landrun,
    }
    if enable_nanoda:
        required["nanoda"] = tools.nanoda
    return tuple(name for name, path in required.items() if path is None or not path.is_file())


def run_comparator(
    *,
    paths: WorkspacePaths,
    manifest: TaskManifest,
    project_root: Path,
    env: Mapping[str, str] | None = None,
) -> tuple[ProcessResult, ComparatorTools]:
    tools = resolve_tools(project_root, env)
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
    effective_env = dict(os.environ if env is None else env)
    effective_env.update(
        {
            "COMPARATOR_LANDRUN": str(tools.landrun),
            "COMPARATOR_LEAN4EXPORT": str(tools.lean4export),
            "HOME": str(paths.root / ".home"),
        }
    )
    target_toolchains = tuple((project_root / ".elan" / "toolchains").glob("*v4.27.0"))
    if target_toolchains:
        effective_env["PATH"] = f"{target_toolchains[0] / 'bin'}{os.pathsep}{effective_env.get('PATH', '')}"
    if tools.nanoda is not None:
        effective_env["COMPARATOR_NANODA"] = str(tools.nanoda)
    (paths.root / ".home").mkdir(mode=0o700)
    result = run_process(
        ("lake", "env", str(tools.comparator), str(paths.config)),
        cwd=paths.root,
        timeout_seconds=manifest.timeout_seconds,
        env=effective_env,
        memory_bytes=8 * 1024 * 1024 * 1024,
        cpu_seconds=manifest.timeout_seconds,
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
