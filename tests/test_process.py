import sys
from pathlib import Path

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
