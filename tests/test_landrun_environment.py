"""Exercise the wrapper's environment boundary with an environment-clearing backend."""

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(sys.platform != "linux", reason="production wrapper requires Linux")
@pytest.mark.parametrize("inherited_threads", [None, "32"])
@pytest.mark.parametrize("env_pass", [("PATH", "HOME", "LEAN_ABORT_ON_PANIC"),
                                     ("PATH", "HOME", "LEAN_PATH", "LEAN_ABORT_ON_PANIC")])
def test_sandboxed_build_and_export_receive_trusted_thread_limit(tmp_path, inherited_threads, env_pass):
    """The upstream build/export allowlists must not lose the runtime thread limit."""
    security = tmp_path / "security"
    security.mkdir()
    wrapper = security / "hardened-landrun.sh"
    shutil.copyfile(ROOT / "security/hardened-landrun.sh", wrapper)
    launcher = tmp_path / ".tools" / "seccomp-launcher"
    launcher.parent.mkdir()
    launcher.write_text('#!/bin/sh\ntest "$1" = --check-landlock\n')
    launcher.chmod(0o755)
    landrun = tmp_path / "vendor" / "landrun" / "bin" / "landrun"
    landrun.parent.mkdir(parents=True)
    # Model Landrun's cleared child environment, not merely the wrapper argv.
    landrun.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "args = sys.argv[1:]\n"
        "env = {}\n"
        "while args and args[0] == '--env':\n"
        "    key = args[1]\n"
        "    if key in os.environ: env[key] = os.environ[key]\n"
        "    args = args[2:]\n"
        "os.execve(args[0], args, env)\n"
    )
    landrun.chmod(0o755)
    environment = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
                   "LEAN_ABORT_ON_PANIC": "1", "LEAN_PATH": "/trusted/lean", "UNTRUSTED": "discard"}
    if inherited_threads is not None:
        environment["LEAN_NUM_THREADS"] = inherited_threads
    command = ["/bin/bash", str(wrapper)]
    for key in env_pass:
        command.extend(("--env", key))
    command.extend((sys.executable, "-c", "import json, os; print(json.dumps(dict(os.environ)))"))
    result = subprocess.run(command, env=environment, capture_output=True, text=True, check=True)
    child_environment = json.loads(result.stdout)
    assert child_environment["LEAN_NUM_THREADS"] == "1"
    assert child_environment["LEAN_ABORT_ON_PANIC"] == "1"
    assert "UNTRUSTED" not in child_environment
    assert ("LEAN_PATH" in child_environment) == ("LEAN_PATH" in env_pass)
