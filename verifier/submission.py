from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from verifier.errors import ReasonCode, VerifierError
from verifier.hashing import sha256_bytes


@dataclass(frozen=True)
class Submission:
    path: Path
    text: str
    raw: bytes
    sha256: str


def load_submission(path: Path, max_bytes: int) -> Submission:
    if path.suffix != ".lean":
        raise VerifierError(ReasonCode.SUBMISSION_POLICY_VIOLATION, "submission must be one .lean source file")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise VerifierError(ReasonCode.SUBMISSION_NOT_FOUND, f"submission not found: {path}") from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise VerifierError(ReasonCode.SUBMISSION_POLICY_VIOLATION, "submission must be one regular file, not a symlink")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise VerifierError(ReasonCode.SUBMISSION_NOT_FOUND, f"cannot open submission safely: {path}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise VerifierError(ReasonCode.SUBMISSION_POLICY_VIOLATION, "submission changed while opening")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(max_bytes + 1)
    finally:
        os.close(descriptor)
    if len(raw) > max_bytes:
        raise VerifierError(
            ReasonCode.SUBMISSION_TOO_LARGE,
            f"submission exceeds the {max_bytes}-byte maximum",
        )
    if b"\x00" in raw:
        raise VerifierError(ReasonCode.SUBMISSION_POLICY_VIOLATION, "submission contains a NUL byte")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise VerifierError(ReasonCode.SUBMISSION_NOT_UTF8, "submission is not valid UTF-8") from exc
    return Submission(path=path, text=text, raw=raw, sha256=sha256_bytes(raw))
