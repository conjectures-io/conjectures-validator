"""Content-addressed store of immutable v2 task versions.

`<root>/<task_id>/` holds exactly one bundle. The store is a cache, not a trust anchor: every
read re-validates the bundle completely (`load_task_bundle` recomputes the task ID from the
bytes), and reuse additionally requires the digest the version registry published for that ID.
A corrupt, poisoned or swapped entry therefore fails closed; it is never rebuilt over or used.

Writes are atomic: a bundle is staged under `<root>/.staging/` on the same filesystem, fully
validated, and renamed into place under an exclusive lock. Two writers of the same version
converge on one directory; two different bundles for one ID are a hard conflict.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import shutil
import stat
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from verifier.errors import ReasonCode, VerifierError
from verifier.task_loader import TaskBundle, load_task_bundle
from verifier.task_versions import is_v2_task_id

STAGING = ".staging"
LOCK = ".lock"


def _real_directory(path: Path) -> bool:
    try:
        return stat.S_ISDIR(path.lstat().st_mode)
    except OSError:
        return False


@dataclass(frozen=True)
class TaskVersionStore:
    root: Path

    def __post_init__(self) -> None:
        if not _real_directory(self.root):
            raise VerifierError(ReasonCode.INVALID_ARGUMENT, f"task version store is not a real directory: {self.root}")

    def path_for(self, task_id: str) -> Path:
        if not is_v2_task_id(task_id):
            raise VerifierError(ReasonCode.INVALID_ARGUMENT, f"not a v2 task ID: {task_id!r}")
        return self.root / task_id

    def load(self, task_id: str, *, expected_sha256: str | None = None) -> TaskBundle | None:
        """The stored version, None when absent. Raises on anything but an exact valid bundle."""
        path = self.path_for(task_id)
        if not os.path.lexists(path):
            return None
        if not _real_directory(path):
            raise VerifierError(
                ReasonCode.TRUSTED_FILE_MODIFIED, f"store entry is not a real directory: {task_id}"
            )
        bundle = load_task_bundle(path)
        if bundle.manifest.task_id != task_id:
            raise VerifierError(
                ReasonCode.TRUSTED_FILE_MODIFIED, f"store entry {task_id} holds {bundle.manifest.task_id}"
            )
        if expected_sha256 is not None and bundle.sha256 != expected_sha256:
            raise VerifierError(
                ReasonCode.TRUSTED_FILE_MODIFIED,
                f"store entry {task_id} has digest {bundle.sha256}, the registry publishes {expected_sha256}",
            )
        return bundle

    def staging_directory(self, task_id: str) -> Path:
        """A fresh parent for building one version, on the store's filesystem."""
        self.path_for(task_id)
        staging = self.root / STAGING
        staging.mkdir(mode=0o700, exist_ok=True)
        if not _real_directory(staging):
            raise VerifierError(ReasonCode.WORKSPACE_ERROR, "store staging area is not a real directory")
        return Path(tempfile.mkdtemp(prefix=f"{task_id}.", dir=staging))

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        descriptor = os.open(
            self.root / LOCK,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def publish(self, staged: Path, task_id: str) -> TaskBundle:
        """Move a staged, validated bundle into place; idempotent for identical bytes."""
        staging_root = (self.root / STAGING).resolve()
        if not _real_directory(staged) or staged.resolve().parent.parent != staging_root:
            raise VerifierError(ReasonCode.WORKSPACE_ERROR, "only bundles staged in this store can be published")
        bundle = load_task_bundle(staged)
        if bundle.manifest.task_id != task_id:
            raise VerifierError(ReasonCode.INVALID_MANIFEST, "staged bundle has a different task ID")
        destination = self.path_for(task_id)
        with self._locked():
            if os.path.lexists(destination):
                existing = self.load(task_id)
                assert existing is not None
                if existing.sha256 != bundle.sha256:
                    raise VerifierError(
                        ReasonCode.TASK_COMMITMENT_MISMATCH,
                        f"store already holds different bytes for {task_id}; refusing to replace them",
                    )
                shutil.rmtree(staged.parent, ignore_errors=True)
                return existing
            for path in staged.iterdir():
                path.chmod(0o444)
            staged.chmod(0o555)
            os.rename(staged, destination)
            descriptor = os.open(self.root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            shutil.rmtree(staged.parent, ignore_errors=True)
        published = self.load(task_id, expected_sha256=bundle.sha256)
        assert published is not None
        return published


__all__ = ["TaskVersionStore"]
