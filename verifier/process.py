from __future__ import annotations

import os
import resource
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import BinaryIO, Callable, Mapping, Sequence

from verifier.models import ProcessResult


DEFAULT_CAPTURE_BYTES = 8 * 1024 * 1024


def _resource_limiter(
    memory_bytes: int | None,
    cpu_seconds: int | None,
    file_bytes: int | None,
    open_files: int | None,
    processes: int | None,
) -> Callable[[], None]:
    def limit() -> None:
        os.setsid()
        if hasattr(resource, "RLIMIT_CORE"):
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        if memory_bytes is not None and sys.platform.startswith("linux") and hasattr(resource, "RLIMIT_AS"):
            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        if cpu_seconds is not None and sys.platform.startswith("linux"):
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        if file_bytes is not None and sys.platform.startswith("linux") and hasattr(resource, "RLIMIT_FSIZE"):
            resource.setrlimit(resource.RLIMIT_FSIZE, (file_bytes, file_bytes))
        if open_files is not None and hasattr(resource, "RLIMIT_NOFILE"):
            resource.setrlimit(resource.RLIMIT_NOFILE, (open_files, open_files))
        if processes is not None and sys.platform.startswith("linux") and hasattr(resource, "RLIMIT_NPROC"):
            resource.setrlimit(resource.RLIMIT_NPROC, (processes, processes))

    return limit


def run_process(
    args: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: int,
    env: Mapping[str, str] | None = None,
    memory_bytes: int | None = None,
    cpu_seconds: int | None = None,
    file_bytes: int | None = None,
    open_files: int | None = None,
    processes: int | None = None,
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
            preexec_fn=(
                _resource_limiter(memory_bytes, cpu_seconds, file_bytes, open_files, processes)
                if os.name == "posix"
                else None
            ),
        )
        stdout_chunks: deque[bytes] = deque()
        stderr_chunks: deque[bytes] = deque()
        stdout_size = [0]
        stderr_size = [0]
        threads = (
            threading.Thread(
                target=_drain_pipe,
                args=(process.stdout, stdout_chunks, stdout_size, max_output_bytes),
                daemon=True,
            ),
            threading.Thread(
                target=_drain_pipe,
                args=(process.stderr, stderr_chunks, stderr_size, max_output_bytes),
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
            thread.join(timeout=2)
        pipes_held_open = any(thread.is_alive() for thread in threads)
        if pipes_held_open:
            timed_out = True
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            for thread in threads:
                thread.join(timeout=1)
        stdout = b"".join(stdout_chunks).decode("utf-8", errors="replace")
        stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace")
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
    destination: deque[bytes],
    size: list[int],
    limit: int | None,
) -> None:
    if stream is None:
        return
    try:
        while chunk := stream.read(64 * 1024):
            destination.append(chunk)
            size[0] += len(chunk)
            if limit is not None:
                while destination and size[0] - len(destination[0]) >= limit:
                    size[0] -= len(destination.popleft())
                if destination and size[0] > limit:
                    excess = size[0] - limit
                    destination[0] = destination[0][excess:]
                    size[0] = limit
    except (OSError, ValueError):
        return
