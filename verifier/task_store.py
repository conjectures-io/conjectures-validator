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
from collections.abc import Callable, Iterator
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

    def publish(
        self,
        staged: Path,
        task_id: str,
        *,
        after_step: Callable[[str], None] = lambda step: None,
    ) -> TaskBundle:
        """Move a staged, validated bundle into place; idempotent for identical bytes.

        Durability comes before visibility: every staged file and the staged directory are
        flushed before the rename, the store directory after it. The entry is then sealed
        read-only. A crash between rename and seal leaves a complete, valid but unsealed entry;
        the next publisher of that version validates it in full and seals it. An entry whose
        bytes do not validate is never sealed, reused or replaced. `after_step` lets tests
        interrupt publication between its steps.
        """
        staging_root = (self.root / STAGING).resolve()
        if not _real_directory(staged) or staged.resolve().parent.parent != staging_root:
            raise VerifierError(ReasonCode.WORKSPACE_ERROR, "only bundles staged in this store can be published")
        bundle = load_task_bundle(staged)
        if bundle.manifest.task_id != task_id:
            raise VerifierError(ReasonCode.INVALID_MANIFEST, "staged bundle has a different task ID")
        for path in sorted(staged.iterdir()):
            _fsync_path(path)
        _fsync_path(staged)
        after_step("staged-synced")
        destination = self.path_for(task_id)
        with self._locked():
            if os.path.lexists(destination):
                # A previous publication of this ID, possibly interrupted before sealing. It is
                # accepted only if its bytes validate and equal ours; then it is (re)sealed.
                existing = self.load(task_id)
                assert existing is not None
                if existing.sha256 != bundle.sha256:
                    raise VerifierError(
                        ReasonCode.TASK_COMMITMENT_MISMATCH,
                        f"store already holds different bytes for {task_id}; refusing to replace them",
                    )
                _seal(destination)
                shutil.rmtree(staged.parent, ignore_errors=True)
                return existing
            # Rename first: moving a directory to a new parent rewrites its `..` entry, which an
            # unprivileged process may only do while the directory is still writable.
            os.rename(staged, destination)
            _fsync_path(self.root)
            after_step("renamed")
            _seal(destination)
            after_step("sealed")
            shutil.rmtree(staged.parent, ignore_errors=True)
        published = self.load(task_id, expected_sha256=bundle.sha256)
        assert published is not None
        return published


def _fsync_path(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    if path.is_dir():
        flags |= getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _seal(entry: Path) -> None:
    """Read-only files in a read-only directory, flushed. Idempotent."""
    for path in sorted(entry.iterdir()):
        if path.is_symlink() or not path.is_file():
            raise VerifierError(ReasonCode.TRUSTED_FILE_MODIFIED, f"store entry contains a non-file: {path.name}")
        if stat.S_IMODE(path.lstat().st_mode) != 0o444:
            path.chmod(0o444)
    if stat.S_IMODE(entry.lstat().st_mode) != 0o555:
        entry.chmod(0o555)
    _fsync_path(entry)
    _fsync_path(entry.parent)


def is_sealed(entry: Path) -> bool:
    return stat.S_IMODE(entry.lstat().st_mode) == 0o555 and all(
        stat.S_IMODE(path.lstat().st_mode) == 0o444 for path in entry.iterdir()
    )


__all__ = ["TaskVersionStore", "is_sealed"]
