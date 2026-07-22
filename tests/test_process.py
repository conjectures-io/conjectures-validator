import os
import sys
import time
from pathlib import Path

import pytest

from verifier.process import run_process


def test_process_capture_retains_only_configured_tail():
    result = run_process(
        (sys.executable, "-c", 'print("x" * 1000)'),
        cwd=Path.cwd(),
        timeout_seconds=10,
        max_output_bytes=100,
    )
    assert result.exit_code == 0
    assert len(result.stdout.encode("utf-8")) == 100
    assert result.stdout.endswith("\n")


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_process_kills_descendant_that_holds_output_pipe_open():
    code = (
        "import os,time; "
        "pid=os.fork(); "
        "time.sleep(60) if pid == 0 else os._exit(0)"
    )
    started = time.monotonic()
    result = run_process(
        (sys.executable, "-c", code),
        cwd=Path.cwd(),
        timeout_seconds=10,
    )
    assert result.timed_out
    assert time.monotonic() - started < 6
