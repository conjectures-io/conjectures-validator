"""Links the catalog publishes, so no client assembles them.

Two kinds, both built once at startup from what the server actually loaded rather than from a
naming convention a client would have to copy:

* **The Lean source upstream.** Not at the pinned `formal_conjectures` commit: that commit is
  derived locally by applying the audit patch (`scripts/pin_dependencies.sh`) and does not exist
  on GitHub, so a link to it 404s. The link names `base_commit`, the upstream commit the patch is
  applied to, and `patched` says which source files the patch changes there, so a reader can be
  told when the upstream file differs from the audited statement.
* **The task bundle.** A bundle's directory is where the loader found it, which is not derivable
  from its task id: a rebuild may keep an old directory name under a new id, and versioned tasks
  live under `versions/` rather than `pool/<tier>/`. So the path is taken from
  `TaskEntry.task_dir`, relative to the tasks checkout, at the pinned tasks commit.

Only GitHub repositories get links. Anything else answers None rather than a guessed URL.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from submission_api.pins import PinSet
from verifier.hashing import sha256_bytes

logger = logging.getLogger(__name__)

# Where `scripts/pin_dependencies.sh` reads the audit patch from, relative to the tasks checkout.
AUDIT_PATCH_PATH = Path("tiers/tier-1/formal-conjectures-audit-fixes.patch")
MAX_PATCH_BYTES = 4 * 1024 * 1024

_GITHUB = re.compile(r"^https://github\.com/(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?$")
# `diff --git a/<path> b/<path>`; the b-side is the file as it stands after the patch.
_DIFF_HEADER = re.compile(r"^diff --git a/(?P<old>\S+) b/(?P<new>\S+)$", re.MULTILINE)


def github_web_base(repository: str | None) -> str | None:
    """`https://github.com/<owner>/<name>` for a GitHub clone URL, else None."""
    match = _GITHUB.match(repository or "")
    return None if match is None else f"https://github.com/{match.group('repo')}"


@dataclass(frozen=True)
class PatchedFiles:
    """What one git diff does to the tree: files it changes, and the subset it creates."""

    touched: frozenset[str]
    created: frozenset[str]


def patched_paths(patch: bytes) -> PatchedFiles:
    """Every file a git diff touches, on either side of a rename, and those it creates.

    A created file exists only in the derived commit, so it has no upstream page to link to.
    """
    text = patch.decode("utf-8", errors="replace")
    headers = list(_DIFF_HEADER.finditer(text))
    touched: set[str] = set()
    created: set[str] = set()
    for index, match in enumerate(headers):
        touched.update((match.group("old"), match.group("new")))
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        block = text[match.end():end]
        if re.search(r"^new file mode ", block, re.MULTILINE):
            created.add(match.group("new"))
    return PatchedFiles(touched=frozenset(touched), created=frozenset(created))


@dataclass(frozen=True)
class CatalogLinks:
    source_base: str | None = None
    source_commit: str | None = None
    # None means "unknown": the pin names a patch that could not be read or did not match its
    # digest. Empty means the pin is unpatched.
    patched: PatchedFiles | None = PatchedFiles(frozenset(), frozenset())
    tasks_base: str | None = None
    tasks_commit: str | None = None
    tasks_root: Path | None = None

    @classmethod
    def none(cls) -> CatalogLinks:
        return cls(patched=None)

    @classmethod
    def build(cls, pins: PinSet, *, tasks_root: Path | None) -> CatalogLinks:
        source = pins.get("formal_conjectures")
        tasks = pins.get("tasks")
        patched: PatchedFiles | None = PatchedFiles(frozenset(), frozenset())
        if source is not None and source.patch_sha256:
            patched = _read_patch(tasks_root, source.patch_sha256)
        return cls(
            source_base=None if source is None else github_web_base(source.repository),
            source_commit=None if source is None else (source.base_commit or source.commit),
            patched=patched,
            tasks_base=None if tasks is None else github_web_base(tasks.repository),
            tasks_commit=None if tasks is None else tasks.commit,
            tasks_root=tasks_root,
        )

    def source_url(self, source_path: str) -> str | None:
        """The file upstream, or None when there is no upstream page for it.

        None for a file the audit patch creates, and for any file while the patch is unknown:
        then it cannot be ruled out that the file was created, and a link that 404s is worse
        than none.
        """
        path = source_path.lstrip("/")
        if not self.source_base or not self.source_commit or not path:
            return None
        if self.patched is None or path in self.patched.created:
            return None
        return f"{self.source_base}/blob/{self.source_commit}/{path}"

    def source_patched(self, source_path: str) -> bool | None:
        if self.patched is None:
            return None
        return source_path.lstrip("/") in self.patched.touched

    def bundle_url(self, task_dir: Path) -> str | None:
        if not self.tasks_base or not self.tasks_commit or self.tasks_root is None:
            return None
        try:
            relative = task_dir.resolve().relative_to(self.tasks_root.resolve())
        except ValueError:
            return None
        return f"{self.tasks_base}/tree/{self.tasks_commit}/{relative.as_posix()}"


def _read_patch(tasks_root: Path | None, expected_sha256: str) -> PatchedFiles | None:
    """The files the pinned audit patch changes, or None when it cannot be vouched for."""
    if tasks_root is None:
        return None
    path = tasks_root / AUDIT_PATCH_PATH
    try:
        if path.stat().st_size > MAX_PATCH_BYTES:
            logger.warning("audit patch at %s is implausibly large; source_patched is unknown", path)
            return None
        raw = path.read_bytes()
    except OSError as exc:
        logger.warning("cannot read the audit patch at %s: %s; source_patched is unknown", path, exc)
        return None
    if sha256_bytes(raw).removeprefix("sha256:") != expected_sha256.removeprefix("sha256:"):
        logger.warning(
            "audit patch at %s does not match pins.lock.json; source_patched is unknown", path
        )
        return None
    return patched_paths(raw)


__all__ = [
    "AUDIT_PATCH_PATH",
    "CatalogLinks",
    "PatchedFiles",
    "github_web_base",
    "patched_paths",
]
