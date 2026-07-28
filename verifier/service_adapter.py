from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from verifier.hashing import is_sha256
from verifier.models import VerificationReport
from verifier.task_generator import MAX_SUBMISSION_BYTES
from verifier.verification import verify


@dataclass(frozen=True)
class ProductionVerifierAdapter:
    """Narrow service-facing handoff into the production proof verifier."""

    project_root: Path

    def verify_file(
        self,
        *,
        task_dir: Path,
        submission_path: Path,
        expected_task_sha256: str,
    ) -> VerificationReport:
        if not is_sha256(expected_task_sha256):
            raise ValueError("expected task digest is not a lowercase SHA-256 commitment")
        return verify(
            task_dir=task_dir,
            submission_path=submission_path,
            project_root=self.project_root,
            expected_task_sha256=expected_task_sha256,
        )

    def verify_bytes(
        self,
        *,
        task_dir: Path,
        submission: bytes,
        expected_task_sha256: str,
    ) -> VerificationReport:
        if not isinstance(submission, bytes):
            raise TypeError("proof submission must be bytes")
        if len(submission) > MAX_SUBMISSION_BYTES:
            raise ValueError("proof submission exceeds verifier policy")
        with tempfile.TemporaryDirectory(prefix="conjectures-proof-") as temporary:
            path = Path(temporary) / "Main.lean"
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
            try:
                with os.fdopen(descriptor, "wb", closefd=False) as handle:
                    handle.write(submission)
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                os.close(descriptor)
            return self.verify_file(
                task_dir=task_dir,
                submission_path=path,
                expected_task_sha256=expected_task_sha256,
            )
