from __future__ import annotations

import ctypes
import errno
import mmap
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Callable


def _denied(action: Callable[[], object]) -> bool:
    try:
        action()
    except PermissionError:
        return True
    except OSError as exc:
        return exc.errno in {errno.EPERM, errno.EACCES, errno.EXDEV}
    return False


def _allowed(action: Callable[[], object]) -> bool:
    try:
        action()
    except OSError:
        return False
    return True


def _open_socket() -> socket.socket:
    return socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)


def _map_executable(path: Path) -> None:
    with path.open("rb") as stream:
        mapping = mmap.mmap(
            stream.fileno(),
            0,
            prot=mmap.PROT_READ | mmap.PROT_EXEC,
        )
        mapping.close()


def _add_execute_permission() -> None:
    mapping = mmap.mmap(
        -1,
        4096,
        prot=mmap.PROT_READ | mmap.PROT_WRITE,
    )
    try:
        address = ctypes.addressof(ctypes.c_char.from_buffer(mapping))
        libc = ctypes.CDLL(None, use_errno=True)
        result = libc.mprotect(
            ctypes.c_void_p(address),
            ctypes.c_size_t(len(mapping)),
            mmap.PROT_READ | mmap.PROT_EXEC,
        )
        if result != 0:
            error_number = ctypes.get_errno()
            raise OSError(error_number, os.strerror(error_number))
    finally:
        mapping.close()


def main(arguments: list[str]) -> int:
    if len(arguments) != 3:
        return 64
    allowed, denied, readable = map(Path, arguments)
    denied_file = denied / "existing"
    alias = allowed / "alias"
    checks = (
        _allowed(lambda: (allowed / "created").write_bytes(b"ok")),
        _allowed(lambda: readable.read_bytes()),
        _allowed(lambda: Path("/dev/null").read_bytes()),
        _denied(lambda: subprocess.run((str(allowed / "executable"),), check=True)),
        _denied(lambda: _map_executable(allowed / "executable")),
        _denied(
            lambda: mmap.mmap(
                -1,
                4096,
                prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC,
            )
        ),
        _denied(_add_execute_permission),
        _denied(lambda: (denied / "created").write_bytes(b"no")),
        _denied(lambda: os.link(denied_file, allowed / "linked")),
        _denied(lambda: os.rename(denied_file, allowed / "moved")),
        _allowed(lambda: alias.symlink_to(denied_file)),
        _denied(lambda: alias.write_bytes(b"no")),
        _denied(lambda: os.utime(denied_file, None)),
        _denied(lambda: denied_file.chmod(0o700)),
        _denied(lambda: Path("/proc/self/status").read_bytes()),
        _denied(lambda: Path("/etc/passwd").read_bytes()),
        _denied(_open_socket),
    )
    return 0 if all(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
