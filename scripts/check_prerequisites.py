#!/usr/bin/env python3
"""Refuse to start a build this host cannot finish.

Every check here is behavioural: it does the thing rather than asking a package manager whether
the thing is installed. `python3-venv` is the case that forced the rule — `dpkg -l` is
distro-specific and `import venv` succeeds on a host whose ensurepip is missing, which is the
failure that actually happens. Nothing is ever installed; a failing check prints the command.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GIGABYTE = 1024**3
REQUIRED_DISK_BYTES = 20 * GIGABYTE
ADVISED_MEMORY_BYTES = 8 * GIGABYTE
# `--filter=blob:none`, which pin_dependencies.sh uses for every checkout.
MINIMUM_GIT_VERSION = (2, 19)
SUPPORTED_PYTHON = ((3, 11), (3, 14))

# Reached during a build, in this order: the clones, pip's own build dependencies, the Lean
# toolchain release assets, and Mathlib's build cache. Worth checking together, because a network
# that allows github.com but blocks Azure blob storage — common on corporate networks and some VPS
# providers — fails twenty minutes into `lake exe cache get` with an error that points nowhere.
EGRESS_URLS = (
    "https://github.com",
    "https://objects.githubusercontent.com",
    "https://pypi.org",
    "https://files.pythonhosted.org",
    "https://releases.lean-lang.org",
    "https://mathlib4.lean-cache.cloud",
    "https://lakecache.blob.core.windows.net",
)

APT_VENV = "sudo apt install python3-venv"


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    remedy: str = ""
    # A failed advisory check is reported and does not stop the build.
    advisory: bool = False


def _run(
    *command: str, stdin: bytes | None = None, timeout: float = 60
) -> subprocess.CompletedProcess[bytes] | None:
    """None when the command could not be run at all, which callers treat the same as failing."""
    try:
        return subprocess.run(
            command, input=stdin, capture_output=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _last_line(result: subprocess.CompletedProcess[bytes] | None) -> str:
    if result is None:
        return "could not be run"
    output = (result.stderr or result.stdout).decode("utf-8", "replace").strip()
    return output.splitlines()[-1] if output else f"exited {result.returncode}"


def check_platform() -> Check:
    system, machine = platform.system(), platform.machine()
    if system != "Linux":
        return Check(
            "platform",
            False,
            f"{system} {machine}",
            "Linux only for now. On Windows, run this inside WSL2.",
        )
    if machine not in ("x86_64", "aarch64", "arm64"):
        return Check(
            "platform", False, f"{system} {machine}", "no Lean toolchain targets this architecture"
        )
    return Check("platform", True, f"{system} {machine}")


def check_libc() -> Check:
    """The Lean toolchain ships dynamically glibc-linked binaries, so musl hosts cannot run it."""
    name, version = platform.libc_ver()
    if name == "glibc":
        return Check("glibc", True, version or "present")
    return Check(
        "glibc",
        False,
        name or "not glibc",
        "Alpine and other musl distributions cannot run the Lean toolchain",
    )


def check_python() -> Check:
    (low, high) = SUPPORTED_PYTHON
    if low <= sys.version_info[:2] < high:
        return Check("python", True, platform.python_version())
    return Check(
        "python",
        False,
        platform.python_version(),
        f"the verifier requires >={low[0]}.{low[1]},<{high[0]}.{high[1]}",
    )


def check_git() -> Check:
    """The Git the verifier itself will run, not whichever one `PATH` happens to name first.

    `assert_dependency_pins` shells out to it for every proof, so a host that resolves nothing
    here builds for an hour and then fails its first verification with REPOSITORY_NOT_FOUND.
    """
    # Imported here rather than at the top because `verifier.errors` needs StrEnum: on a Python
    # this project does not support, the version row above is the only answer worth printing, and
    # an ImportError traceback would bury it.
    try:
        from verifier.errors import VerifierError
        from verifier.repository import git_executable
    except ImportError as error:
        return Check(
            "git", False, f"verifier is not importable ({error})", "resolve the python row first"
        )
    try:
        executable = git_executable()
    except VerifierError as error:
        return Check("git", False, str(error), "sudo apt install git")
    result = _run(str(executable), "--version")
    if result is None or result.returncode != 0:
        return Check("git", False, f"{executable}: {_last_line(result)}", "sudo apt install git")
    reported = result.stdout.decode("utf-8", "replace").split()
    version = reported[2] if len(reported) > 2 else ""
    try:
        parsed = tuple(int(part) for part in version.split(".")[:2])
    except ValueError:
        parsed = ()
    if parsed < MINIMUM_GIT_VERSION:
        wanted = ".".join(str(part) for part in MINIMUM_GIT_VERSION)
        return Check(
            "git",
            False,
            f"{executable} is {version or 'an unreadable version'}",
            f"partial clones need Git >= {wanted}",
        )
    return Check("git", True, f"{executable} {version}")


def check_venv() -> Check:
    """Creating one is the only trustworthy test: `import venv` succeeds without ensurepip."""
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "probe"
        created = _run(sys.executable, "-m", "venv", str(target), timeout=300)
        if created is None or created.returncode != 0:
            return Check("python venv", False, _last_line(created), APT_VENV)
        checked = _run(str(target / "bin" / "pip"), "--version", timeout=120)
        if checked is None or checked.returncode != 0:
            return Check("python venv", False, "the virtualenv has no working pip", APT_VENV)
    return Check("python venv", True, "creates a virtualenv with a working pip")


def check_command(name: str, *version_args: str, remedy: str) -> Check:
    path = shutil.which(name)
    if path is None:
        return Check(name, False, "not on PATH", remedy)
    result = _run(path, *version_args)
    if result is None or result.returncode != 0:
        return Check(name, False, f"{path}: {_last_line(result)}", remedy)
    return Check(name, True, path)


def check_zstd() -> Check:
    """Mathlib's cache arrives zstd-compressed, so a broken zstd only surfaces after the download."""
    remedy = "sudo apt install zstd"
    path = shutil.which("zstd")
    if path is None:
        return Check("zstd", False, "not on PATH", remedy)
    probe = b"conjectures"
    compressed = _run(path, "-q", "-c", stdin=probe)
    if compressed is None or compressed.returncode != 0:
        return Check("zstd", False, _last_line(compressed), remedy)
    restored = _run(path, "-q", "-d", "-c", stdin=compressed.stdout)
    if restored is None or restored.stdout != probe:
        return Check("zstd", False, "does not round-trip", remedy)
    return Check("zstd", True, path)


def check_disk(root: Path) -> Check:
    free = shutil.disk_usage(root).free
    detail = f"{free / GIGABYTE:.1f} GB free on {root}"
    if free >= REQUIRED_DISK_BYTES:
        return Check("disk", True, detail)
    return Check(
        "disk", False, detail, f"the build needs about {REQUIRED_DISK_BYTES // GIGABYTE} GB"
    )


def check_memory() -> Check:
    try:
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (OSError, ValueError):
        return Check("memory", True, "unknown", advisory=True)
    detail = f"{total / GIGABYTE:.1f} GB"
    if total >= ADVISED_MEMORY_BYTES:
        return Check("memory", True, detail)
    advised = ADVISED_MEMORY_BYTES // GIGABYTE
    return Check(
        "memory",
        False,
        detail,
        f"Lean has been seen to run out of memory below {advised} GB; the build may not finish",
        advisory=True,
    )


def check_network(urls: tuple[str, ...]) -> Check:
    """Reachability, not status: a 403 from a bucket root still proves egress and TLS work."""
    curl = shutil.which("curl")
    if curl is None:
        return Check("network", False, "curl is not on PATH", "sudo apt install curl")
    unreachable = []
    for url in urls:
        probe = _run(curl, "-sS", "-I", "-o", os.devnull, "--max-time", "20", url, timeout=30)
        if probe is None or probe.returncode != 0:
            unreachable.append(url)
    if unreachable:
        return Check(
            "network",
            False,
            "cannot reach " + ", ".join(unreachable),
            "the build downloads from every one of these, and a missing ca-certificates looks "
            "the same as a blocked host",
        )
    return Check("network", True, f"{len(urls)} hosts reachable")


def collect(root: Path, *, miner: bool, offline: bool) -> list[Check]:
    checks = [check_platform()]
    if platform.system() == "Linux":
        checks.append(check_libc())
    checks += [
        check_python(),
        check_git(),
        check_venv(),
        check_command("curl", "--version", remedy="sudo apt install curl ca-certificates"),
        check_command("tar", "--version", remedy="sudo apt install tar"),
        check_command("sha256sum", "--version", remedy="sudo apt install coreutils"),
        check_zstd(),
        check_disk(root),
        check_memory(),
    ]
    if not miner and platform.system() == "Linux":
        # Only this path compiles landrun and the seccomp launcher. A `--miner` build runs the
        # development sandbox, which uses neither, so neither toolchain is worth demanding.
        checks.append(check_command("go", "version", remedy="sudo apt install golang-go"))
        checks.append(check_command("cc", "--version", remedy="sudo apt install build-essential"))
    if not offline:
        checks.append(check_network(EGRESS_URLS))
    return checks


def render(checks: list[Check]) -> str:
    lines = []
    for check in checks:
        status = "ok" if check.ok else "warn" if check.advisory else "FAIL"
        lines.append(f"  {status:<5} {check.name:<12} {check.detail}")
        if not check.ok and check.remedy:
            lines.append(f"  {'':<5} {'':<12} -> {check.remedy}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts/check_prerequisites.py")
    parser.add_argument("--miner", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    checks = collect(ROOT, miner=args.miner, offline=args.offline)
    failed = [check for check in checks if not check.ok and not check.advisory]
    if args.json:
        print(json.dumps({"ready": not failed, "checks": [asdict(c) for c in checks]}, indent=2))
        return 1 if failed else 0
    print(f"prerequisites for a {'miner' if args.miner else 'validator'} build\n")
    print(render(checks))
    if not failed:
        print("\nready")
        return 0
    print(f"\n{len(failed)} check(s) failed. Nothing was installed; run the commands above first.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
