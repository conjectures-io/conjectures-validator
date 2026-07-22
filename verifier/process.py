from __future__ import annotations

import os
import resource
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import BinaryIO, Callable, Mapping, Sequence

from verifier.models import ProcessResult


DEFAULT_CAPTURE_BYTES = 8 * 1024 * 1024


def _resource_limiter(memory_bytes: int | None, cpu_seconds: int | None) -> Callable[[], None]:
    def limit() -> None:
        os.setsid()
        if memory_bytes is not None and sys.platform.startswith("linux") and hasattr(resource, "RLIMIT_AS"):
            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        if cpu_seconds is not None and sys.platform.startswith("linux"):
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))

    return limit


def run_process(
    args: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: int,
    env: Mapping[str, str] | None = None,
    memory_bytes: int | None = None,
    cpu_seconds: int | None = None,
    max_output_bytes: int | None = DEFAULT_CAPTURE_BYTES,
) -> ProcessResult:
    command = tuple(str(x) for x in args)
    started = time.monotonic_ns()
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=dict(env) if env is not None else None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            preexec_fn=_resource_limiter(memory_bytes, cpu_seconds) if os.name == "posix" else None,
        )
        stdout_buffer = bytearray()
        stderr_buffer = bytearray()
        threads = (
            threading.Thread(
                target=_drain_pipe,
                args=(process.stdout, stdout_buffer, max_output_bytes),
                daemon=True,
            ),
            threading.Thread(
                target=_drain_pipe,
                args=(process.stderr, stderr_buffer, max_output_bytes),
                daemon=True,
            ),
        )
        for thread in threads:
            thread.start()
        timed_out = False
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait()
        for thread in threads:
            thread.join()
        stdout = stdout_buffer.decode("utf-8", errors="replace")
        stderr = stderr_buffer.decode("utf-8", errors="replace")
        return ProcessResult(
            args=command,
            exit_code=None if timed_out else process.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_ms=(time.monotonic_ns() - started) // 1_000_000,
            timed_out=timed_out,
            signal=(signal.SIGKILL if timed_out else (-process.returncode if process.returncode < 0 else None)),
        )
    except BaseException:
        if process is not None and process.poll() is None:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait()
        raise


def _drain_pipe(
    stream: BinaryIO | None,
    destination: bytearray,
    limit: int | None,
) -> None:
    if stream is None:
        return
    while chunk := stream.read(64 * 1024):
        destination.extend(chunk)
        if limit is not None and len(destination) > limit:
            del destination[: len(destination) - limit]
