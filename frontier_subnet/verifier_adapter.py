from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from frontier_subnet.commitments import verify_proof_reveal
from frontier_subnet.protocol import ProofReveal
from verifier.hashing import is_sha256
from verifier.models import VerificationReport
from verifier.verification import verify


@dataclass(frozen=True)
class ProductionVerifierAdapter:
    """The only subnet-facing in-process seam into the production verifier."""

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

    def verify_reveal(self, *, task_dir: Path, reveal: ProofReveal) -> VerificationReport:
        if not verify_proof_reveal(reveal):
            raise ValueError("proof reveal signature, hash, or commitment is invalid")
        source = reveal.submission_bytes()
        with tempfile.TemporaryDirectory(prefix="frontier-submission-") as temporary:
            path = Path(temporary) / "Main.lean"
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
            try:
                with os.fdopen(descriptor, "wb", closefd=False) as handle:
                    handle.write(source)
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                os.close(descriptor)
            return self.verify_file(
                task_dir=task_dir,
                submission_path=path,
                expected_task_sha256=reveal.commitment.task.task_bundle_sha256,
            )
