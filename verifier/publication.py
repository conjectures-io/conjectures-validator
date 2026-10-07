"""Committing one publication to a tasks checkout: serialized, journaled, recoverable.

A publication changes two files that readers use together: `task-versions.json` and
`allowlist.json`. They cannot be replaced in one filesystem operation, so a commit is:

1. Writers are serialized by an exclusive lock on `.publication.lock`, held from reading the
   previous registry until the commit finishes. A second publisher therefore always builds on
   the first one's history; and the on-disk registry is re-checked against the one the
   publication was derived from, so even a writer that skipped the lock cannot lose history.
2. Both new files are written under unique names, flushed, and their digests recorded in a
   journal (`.publication-journal.json`), which is flushed before anything visible changes.
3. The allowlist, then the registry, are renamed into place; the journal is removed last.

A crash at any point leaves either nothing visible or a journal. The next writer rolls a
journal forward from the staged files it names, after checking their digests; it never guesses.

Readers take no lock and write nothing, so they work on read-only mounts. `read_coherently`
reads the registry and allowlist bytes once each, hands only those bytes to the caller, and
afterwards re-checks that no journal exists and that both files still hold exactly those bytes.
A commit that began while the reader was reading - whatever step it reached, or died at -
leaves a journal, which is refused; one that completed in between changed the bytes, so the
read is repeated. Independently, readers refuse any registry whose current publication does not
name the allowlist's exact digest.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import stat
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from verifier.errors import ReasonCode, VerifierError
from verifier.hashing import sha256_bytes

LOCK_NAME = ".publication.lock"
JOURNAL_NAME = ".publication-journal.json"
REGISTRY_NAME = "task-versions.json"
ALLOWLIST_NAME = "allowlist.json"
STAGED_PREFIX = ".publication-"


class PublicationError(VerifierError):
    def __init__(self, message: str) -> None:
        super().__init__(ReasonCode.INVALID_MANIFEST, f"publication: {message}")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_durably(directory: Path, prefix: str, content: bytes) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=prefix, dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    Path(name).chmod(0o644)
    return Path(name)


def _digest(path: Path) -> str | None:
    return sha256_bytes(path.read_bytes()) if path.is_file() and not path.is_symlink() else None


def assert_no_pending_publication(tasks_root: Path) -> None:
    """A journal means a commit did not finish. Alone this proves nothing about a later read:
    readers use `read_coherently`, which checks again after reading."""
    if os.path.lexists(tasks_root / JOURNAL_NAME):
        raise PublicationError(
            "an unfinished publication is pending in this tasks checkout; refusing to read it"
        )


MAX_METADATA_BYTES = 256 * 1024 * 1024
READ_ATTEMPTS = 3
T = TypeVar("T")


@dataclass(frozen=True)
class CheckoutSnapshot:
    """The registry and allowlist bytes of one committed state of a tasks checkout.

    None means the file does not exist (a release that predates the registry has none).
    """

    registry: bytes | None
    allowlist: bytes | None


def _read_once(path: Path) -> bytes | None:
    """One read of a regular file through one descriptor, never following a symlink."""
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PublicationError(f"cannot read {path.name}: {exc}") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise PublicationError(f"{path.name} is not a regular file")
        with os.fdopen(os.dup(descriptor), "rb") as handle:
            content = handle.read(MAX_METADATA_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(content) > MAX_METADATA_BYTES:
        raise PublicationError(f"{path.name} is too large")
    return content


def read_coherently(
    tasks_root: Path,
    use: Callable[[CheckoutSnapshot], T],
    *,
    allowlist_path: Path | None = None,
) -> T:
    """Run `use` on one committed state of the checkout, or refuse.

    `use` must read the registry and allowlist only from the snapshot it is given; anything
    else it reads (bundles) is content-addressed and checked against those bytes. A commit
    that is pending before, during or after the read is refused; one that completed during
    the read makes the read start over, at most `READ_ATTEMPTS` times. An error raised by `use`
    on a snapshot that is still current is the checkout's own fault and is raised as is.
    """
    registry_path = tasks_root / REGISTRY_NAME

    def capture() -> CheckoutSnapshot:
        return CheckoutSnapshot(
            _read_once(registry_path), _read_once(allowlist_path) if allowlist_path is not None else None
        )

    for _attempt in range(READ_ATTEMPTS):
        assert_no_pending_publication(tasks_root)
        snapshot = capture()
        try:
            result = use(snapshot)
        except Exception:
            # A failure on bytes that a commit was replacing says nothing about the checkout.
            assert_no_pending_publication(tasks_root)
            if capture() != snapshot:
                continue
            raise
        assert_no_pending_publication(tasks_root)
        if capture() == snapshot:
            return result
    raise PublicationError("the tasks checkout kept changing while it was read; refusing to use it")


def _recover(tasks_root: Path) -> str | None:
    journal_path = tasks_root / JOURNAL_NAME
    if not os.path.lexists(journal_path):
        return None
    try:
        journal = json.loads(journal_path.read_bytes())
        steps = [(step["staged"], step["target"], step["sha256"]) for step in journal["steps"]]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PublicationError(f"the pending publication journal is unreadable: {exc}") from exc
    if not steps or steps[-1][1] != REGISTRY_NAME:
        raise PublicationError("the pending publication journal does not end with the registry")
    for staged_name, target_name, digest in steps:
        if (
            target_name not in {ALLOWLIST_NAME, REGISTRY_NAME}
            or not isinstance(staged_name, str)
            or not staged_name.startswith(STAGED_PREFIX)
            or "/" in staged_name
        ):
            raise PublicationError("the pending publication journal names unexpected files")
        staged, target = tasks_root / staged_name, tasks_root / target_name
        if _digest(staged) == digest:
            os.replace(staged, target)
        elif _digest(target) != digest:
            # Neither the staged bytes nor the committed bytes are the journal's: stop.
            raise PublicationError(
                f"cannot complete the pending publication: {target_name} matches neither its "
                "staged nor its committed digest; restore the checkout from version control"
            )
    _fsync_directory(tasks_root)
    journal_path.unlink()
    _fsync_directory(tasks_root)
    return steps[-1][2]


@contextlib.contextmanager
def checkout_writer(tasks_root: Path) -> Iterator[str | None]:
    """Hold the checkout's publication lock; finish any interrupted commit first.

    Yields the digest of a rolled-forward publication, or None when nothing was pending.
    """
    descriptor = os.open(
        tasks_root / LOCK_NAME,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield _recover(tasks_root)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def commit_publication(
    tasks_root: Path,
    *,
    previous_registry_sha256: str | None,
    registry: bytes,
    allowlist: bytes | None,
    after_step: Callable[[str], None] = lambda step: None,
) -> None:
    """Make one publication visible. Call only inside `checkout_writer`.

    `previous_registry_sha256` is the digest of the registry the publication was derived from
    (None when there was none). If the checkout no longer holds exactly that, the commit is
    refused: some other writer published in between, and this one would discard its history.
    `after_step` exists so tests can interrupt the commit between its steps.
    """
    registry_path = tasks_root / REGISTRY_NAME
    if _digest(registry_path) != previous_registry_sha256 or (
        previous_registry_sha256 is None and os.path.lexists(registry_path)
    ):
        raise PublicationError(
            "the registry changed since this publication read it; another publisher committed "
            "in between, so this publication would drop history"
        )
    staged_registry = _write_durably(tasks_root, f"{STAGED_PREFIX}registry-", registry)
    staged_allowlist = (
        _write_durably(tasks_root, f"{STAGED_PREFIX}allowlist-", allowlist) if allowlist is not None else None
    )
    # The allowlist first, the registry last: the registry names the allowlist's digest, so a
    # reader that sees the new registry with the old allowlist (or the reverse) refuses both.
    steps = [
        *(
            [{"staged": staged_allowlist.name, "target": ALLOWLIST_NAME, "sha256": sha256_bytes(allowlist)}]
            if staged_allowlist is not None and allowlist is not None
            else []
        ),
        {"staged": staged_registry.name, "target": REGISTRY_NAME, "sha256": sha256_bytes(registry)},
    ]
    journal = {"steps": steps}
    _write_durably(tasks_root, f"{STAGED_PREFIX}journal-", json.dumps(journal, sort_keys=True).encode()).rename(
        tasks_root / JOURNAL_NAME
    )
    _fsync_directory(tasks_root)
    after_step("journal")
    if staged_allowlist is not None:
        os.replace(staged_allowlist, tasks_root / ALLOWLIST_NAME)
        after_step("allowlist")
    os.replace(staged_registry, registry_path)
    after_step("registry")
    _fsync_directory(tasks_root)
    (tasks_root / JOURNAL_NAME).unlink()
    _fsync_directory(tasks_root)


__all__ = [
    "JOURNAL_NAME",
    "LOCK_NAME",
    "CheckoutSnapshot",
    "PublicationError",
    "assert_no_pending_publication",
    "checkout_writer",
    "commit_publication",
    "read_coherently",
]
