"""The public-GitHub mirror behind `/v1/contributions`.

Keeps a `ContributionSnapshot` of `conjectures-io/conjectures-contribution` current, so that every
read of the contribution surface is answered from memory and no miner-facing request ever waits on
github.com. The refresh runs on its own task at a configured interval, and the endpoints serve
whatever the last successful one produced.

**Three requests per poll, and usually all three are free.** The unauthenticated REST API allows 60
requests an hour per address, which a naive minutely poll of a 210-directory corpus would exhaust
in seconds. Two properties make the interval affordable instead:

1. *Conditional requests.* Every poll sends `If-None-Match`. A `304` does **not** count against the
   rate limit, so a quiet corpus costs nothing at all — the steady state of this loop is three
   `304`s a minute and zero budget consumed.
2. *One archive, not one request per file.* When the head commit does move, the whole corpus is
   read from a single `tarball` response rather than from 210 `contents` calls. The repository is
   under a megabyte, so this is both cheaper in requests and simpler: there is no partial state to
   reconcile, and the parse either produces a complete snapshot at that commit or leaves the
   previous one in place.

`CONTRIBUTIONS_GITHUB_TOKEN` is supported and never required. A token raises the ceiling to 5000
requests an hour, which matters only for a deployment sharing an egress address with other GitHub
traffic; the loop is designed to be affordable without one.

**Everything fetched here is untrusted input.** The archive is read from memory and never written
to disk, so a hostile member path cannot escape anywhere — nothing is extracted, only
`extractfile`'d and parsed. Three separate caps bound what a malicious or accidental
multi-gigabyte response can do: the compressed download, the decompressed total, and each member.
A failure at any point leaves the previous snapshot serving, because a stale corpus is a much
better answer than an empty one.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import tarfile
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import httpx

from conjectures_subnet.axiom import get_axiom
from submission_api.contributions import (
    ContributionSnapshot,
    ContributionTarget,
    ContributionsError,
    PendingContribution,
    parse_empty_target,
    parse_target,
)

logger = logging.getLogger(__name__)

GITHUB_API_URL = "https://api.github.com"
GITHUB_HTML_URL = "https://github.com"
DEFAULT_CONTRIBUTIONS_REPOSITORY = "conjectures-io/conjectures-contribution"

# `owner/name`, as GitHub itself constrains them. Validated because it is interpolated into every
# request path and into the links published on every row.
REPOSITORY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")
COMMIT_SHA = re.compile(r"^[0-9a-f]{7,64}$")

# Where a target index sits inside the archive, after the single wrapper directory GitHub adds.
# Anchored at both ends: a path that merely contains `contributions/x/index.json` somewhere is not
# a target index, and treating it as one is how a repository could be made to inject rows.
INDEX_MEMBER = re.compile(r"^[^/]+/contributions/(?P<target>[^/]+)/index\.json$")
# The generated page every target directory has, indexed or not. Read only for the targets
# with no `index.json`, which is how an empty target reaches the listing at all — see
# `contributions.parse_empty_target`.
PAGE_MEMBER = re.compile(r"^[^/]+/contributions/(?P<target>[^/]+)/index\.md$")

# The `target:` and `hotkey:` labels CI puts on a contribution pull request.
TARGET_LABEL = "target:"
HOTKEY_LABEL = "hotkey:"

# Bounds on what one refresh may pull in. The repository is presently under a megabyte and holds
# 210 target directories, so all three are far above what a healthy corpus needs and are here for
# the unhealthy case.
DEFAULT_MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
MAX_INDEX_BYTES = 4 * 1024 * 1024
MAX_MEMBERS = 200_000
MAX_PENDING = 100

DEFAULT_TIMEOUT_SECONDS = 20.0
# How long to stay quiet after GitHub says the budget is gone and gives no reset time of its own.
FALLBACK_BACKOFF_SECONDS = 300


class ContributionMirror(Protocol):
    """A source of the contribution corpus that a request handler can read without awaiting."""

    def snapshot(self) -> ContributionSnapshot:
        """The most recent successfully built snapshot. Never raises, never blocks."""
        ...

    async def refresh(self) -> bool:
        """Bring the snapshot up to date. True when it changed."""
        ...

    async def aclose(self) -> None:
        """Release any network resources held by the mirror."""
        ...


@dataclass(frozen=True)
class UnavailableContributionMirror:
    """The explicit result when the mirror is switched off.

    Its snapshot reports `available=False`, which is what makes the endpoints answer `503` rather
    than serving empty lists. A deployment that has not turned this on has *no* information about
    the corpus, and saying "there are no contributions" would be a claim it cannot support.
    """

    repository: str = DEFAULT_CONTRIBUTIONS_REPOSITORY

    def snapshot(self) -> ContributionSnapshot:
        return ContributionSnapshot.empty(repository=self.repository)

    async def refresh(self) -> bool:
        return False

    async def aclose(self) -> None:
        return None


class StaticContributionMirror:
    """A fixed snapshot. For tests, and for serving a corpus assembled some other way."""

    def __init__(self, snapshot: ContributionSnapshot) -> None:
        self._snapshot = snapshot

    def snapshot(self) -> ContributionSnapshot:
        return self._snapshot

    async def refresh(self) -> bool:
        return False

    async def aclose(self) -> None:
        return None


class GitHubContributionMirror:
    """Polls the public GitHub API and rebuilds the snapshot when the corpus moves."""

    def __init__(
        self,
        *,
        repository: str = DEFAULT_CONTRIBUTIONS_REPOSITORY,
        branch: str = "main",
        api_base_url: str = GITHUB_API_URL,
        token: str = "",
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_archive_bytes: int = DEFAULT_MAX_ARCHIVE_BYTES,
        include_pending: bool = True,
        client: httpx.AsyncClient | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
    ) -> None:
        if REPOSITORY.fullmatch(repository) is None:
            raise ValueError("the contribution repository must be 'owner/name'")
        if BRANCH.fullmatch(branch) is None:
            raise ValueError("the contribution branch is not a valid ref name")
        if timeout_seconds <= 0:
            raise ValueError("the contribution fetch timeout must be positive")
        if max_archive_bytes <= 0:
            raise ValueError("the contribution archive limit must be positive")

        self._repository = repository
        self._branch = branch
        self._api = api_base_url.rstrip("/")
        self._max_archive_bytes = max_archive_bytes
        self._include_pending = include_pending
        self._monotonic = monotonic
        self._now = now

        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "conjectures-validator/0.1",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = client or httpx.AsyncClient(
            timeout=timeout_seconds, headers=headers, follow_redirects=True
        )
        self._owns_client = client is None
        # Set on the injected client too: a test transport should see the same request the real
        # one sends, including the conditional and version headers.
        self._headers = headers

        self._lock = asyncio.Lock()
        self._snapshot = ContributionSnapshot.empty(
            repository=repository, branch=branch
        )
        # What the last successful archive parse produced, kept so a poll where only the open pull
        # requests moved can rebuild without re-downloading a corpus that did not change.
        self._targets: tuple[ContributionTarget, ...] = ()
        self._unreadable: tuple[tuple[str, str], ...] = ()
        self._head_etag: str | None = None
        self._pending_etag: str | None = None
        self._pending: tuple[PendingContribution, ...] = ()
        self._blocked_until = 0.0

    def snapshot(self) -> ContributionSnapshot:
        return self._snapshot

    async def refresh(self) -> bool:
        """One poll. Returns whether the served snapshot changed.

        Never raises. Every failure mode — a network error, a rate-limit refusal, a malformed
        archive — is logged, reported to Axiom, and leaves the previous snapshot in place. This is
        driven by a background task with nobody to hand an exception to, and a refresher that dies
        on the first transient failure would serve a snapshot that silently stops advancing.
        """
        async with self._lock:
            if self._monotonic() < self._blocked_until:
                return False
            try:
                return await self._refresh()
            except _RateLimited as exc:
                self._blocked_until = self._monotonic() + exc.seconds
                logger.warning(
                    "contribution mirror is rate-limited for %ss", exc.seconds
                )
                get_axiom().warn(
                    source="api-contributions",
                    event_type="contributions_rate_limited",
                    repository=self._repository,
                    backoff_seconds=exc.seconds,
                )
                return False
            except (httpx.HTTPError, tarfile.TarError, OSError, ValueError) as exc:
                logger.warning("contribution mirror refresh failed: %s", exc)
                get_axiom().warn(
                    source="api-contributions",
                    event_type="contributions_refresh_failed",
                    repository=self._repository,
                    error=f"{type(exc).__name__}: {exc}",
                    head_commit=self._snapshot.head_commit,
                )
                return False

    async def _refresh(self) -> bool:
        # The two validators are held locally and only committed once the whole poll has
        # succeeded. Storing them as they arrive is how a mirror gets stuck: a head fetch that
        # succeeds followed by an archive read that fails would leave the *new* commit's `ETag`
        # cached, so every later poll would be answered `304` and the failed commit would never be
        # retried — the snapshot would sit at the old corpus indefinitely, looking healthy.
        head, head_etag, unchanged = await self._head_commit()
        if unchanged:
            head = self._snapshot.head_commit
        if head is None:
            return False
        head_changed = head != self._snapshot.head_commit

        pending, pending_etag = await self._open_pull_requests()
        if pending is not None:
            self._pending = pending

        if head_changed or not self._targets:
            targets, unreadable = await self._read_archive(head)
            self._targets = targets
            self._unreadable = unreadable
        elif pending is None:
            self._head_etag = head_etag or self._head_etag
            return False

        self._head_etag = head_etag or self._head_etag
        self._pending_etag = pending_etag or self._pending_etag
        self._snapshot = ContributionSnapshot.build(
            repository=self._repository,
            branch=self._branch,
            head_commit=head,
            fetched_at=self._now(),
            targets=self._targets,
            pending=self._pending,
            unreadable=self._unreadable,
        )
        get_axiom().info(
            source="api-contributions",
            event_type="contributions_refreshed",
            repository=self._repository,
            head_commit=head,
            targets=len(self._snapshot.targets),
            contributions=len(self._snapshot.contributions),
            authors=len(self._snapshot.authors),
            pending=len(self._snapshot.pending),
            unreadable=len(self._snapshot.unreadable),
        )
        return True

    async def _head_commit(self) -> tuple[str | None, str | None, bool]:
        """The branch's current commit as `(sha, etag, unchanged)`.

        One conditional request. Deliberately the cheapest thing that answers "is there anything
        new": the alternative of diffing the tree costs the same request and tells us no more,
        because a corpus change we care about is always a commit.

        No validator is sent while nothing is loaded. Otherwise a mirror whose first archive read
        failed would be told `304` forever and would never make a second attempt — the one state
        where an unconditional request is worth its cost.
        """
        response = await self._get(
            f"{self._api}/repos/{self._repository}/commits/{self._branch}",
            etag=self._head_etag if self._targets else None,
        )
        if response.status_code == 304:
            return None, self._head_etag, True
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise ValueError("GitHub returned a non-object commit")
        sha = payload.get("sha")
        if not isinstance(sha, str) or COMMIT_SHA.fullmatch(sha) is None:
            raise ValueError("GitHub returned no usable commit sha")
        return sha, response.headers.get("etag"), False

    async def _open_pull_requests(
        self,
    ) -> tuple[tuple[PendingContribution, ...] | None, str | None]:
        """The open pull requests as `(rows, etag)`, with `rows` None when they have not moved.

        A malformed entry is skipped rather than failing the poll: one pull request with an
        unreadable timestamp must not cost the whole listing.
        """
        if not self._include_pending:
            return None, self._pending_etag
        response = await self._get(
            f"{self._api}/repos/{self._repository}/pulls",
            etag=self._pending_etag,
            params={
                "state": "open",
                "per_page": MAX_PENDING,
                "sort": "updated",
                "direction": "desc",
            },
        )
        if response.status_code == 304:
            return None, self._pending_etag
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("GitHub returned a non-list pull request page")
        rows = []
        for entry in payload[:MAX_PENDING]:
            row = _pending_from(entry)
            if row is not None:
                rows.append(row)
        return tuple(rows), response.headers.get("etag")

    async def _read_archive(
        self, commit: str
    ) -> tuple[tuple[ContributionTarget, ...], tuple[tuple[str, str], ...]]:
        """Every `contributions/<target>/index.json` at `commit`, from one tarball."""
        archive = await self._download(
            f"{self._api}/repos/{self._repository}/tarball/{commit}"
        )
        targets: list[ContributionTarget] = []
        unreadable: list[tuple[str, str]] = []
        # Every target's generated page, kept for the second pass below. Only the ones whose
        # directory turns out to have no `index.json` are parsed, but which those are is not known
        # until the whole archive has been walked, and walking a gzip stream twice would mean
        # decompressing it twice. 209 pages at a kilobyte and a half is nothing to hold.
        pages: dict[str, str] = {}
        decompressed = 0
        members = 0
        # `r:gz` rather than `r|gz`: random access is not needed, but the streaming reader cannot
        # be asked for a member's size before reading it, which is what the per-member cap needs.
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
            for member in bundle:
                members += 1
                if members > MAX_MEMBERS:
                    raise ValueError("contribution archive holds too many members")
                decompressed += max(0, member.size)
                if decompressed > MAX_UNCOMPRESSED_BYTES:
                    raise ValueError("contribution archive decompresses too large")
                if not member.isfile():
                    continue
                index = INDEX_MEMBER.fullmatch(member.name)
                page = None if index else PAGE_MEMBER.fullmatch(member.name)
                if index is None and page is None:
                    continue
                matched = index if index is not None else page
                directory = matched.group("target")
                if member.size > MAX_INDEX_BYTES:
                    if index is not None:
                        unreadable.append((directory, "index exceeds the size limit"))
                    continue
                handle = bundle.extractfile(member)
                if handle is None:  # pragma: no cover - isfile() already excludes these
                    continue
                raw = handle.read()
                if page is not None:
                    # Decoded lazily-ish and never allowed to fail the poll: this file is prose,
                    # and only its first generated line is ever read.
                    pages[directory] = raw.decode("utf-8", errors="replace")
                    continue
                try:
                    targets.append(parse_target(json.loads(raw), directory=directory))
                except (ContributionsError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                    unreadable.append((directory, str(exc)))

        # Second pass: a target directory with a page and no index is a target nobody has
        # contributed to yet. It belongs in the listing — "which conjectures need work" is the
        # question this surface is most useful for — and it is the only way those rows exist,
        # because the corpus writes no JSON for an empty target.
        indexed = {row.target for row in targets} | {name for name, _ in unreadable}
        for directory, page_text in sorted(pages.items()):
            if directory in indexed:
                continue
            try:
                targets.append(parse_empty_target(page_text, directory=directory))
            except ContributionsError as exc:
                unreadable.append((directory, str(exc)))

        if not targets and not unreadable:
            # The corpus has over two hundred target directories; an archive with none of them is
            # a wrong archive rather than an empty corpus, and replacing a good snapshot with it
            # would be worse than keeping the old one.
            raise ValueError("contribution archive contains no target directories")
        return tuple(targets), tuple(unreadable)

    async def _download(self, url: str) -> bytes:
        """Stream a response into memory, refusing it the moment it passes the byte cap.

        Streamed rather than read whole so an oversized body is abandoned mid-flight instead of
        being buffered in full and then rejected — the same reason `POST /v1/submissions` caps its
        request body as it arrives.
        """
        async with self._client.stream("GET", url) as response:
            self._raise_for_rate_limit(response)
            response.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > self._max_archive_bytes:
                    raise ValueError(
                        f"contribution archive exceeds {self._max_archive_bytes} bytes"
                    )
                chunks.append(chunk)
        return b"".join(chunks)

    async def _get(
        self,
        url: str,
        *,
        etag: str | None = None,
        params: Mapping[str, object] | None = None,
    ) -> httpx.Response:
        headers = {"If-None-Match": etag} if etag else None
        response = await self._client.get(url, params=params, headers=headers)
        self._raise_for_rate_limit(response)
        return response

    def _raise_for_rate_limit(self, response: httpx.Response) -> None:
        """Turn GitHub's budget refusal into a backoff rather than a retried failure.

        `403` and `429` both carry it. Retrying at the poll interval through an exhausted hour
        would keep the address in the penalty box and delay recovery, so the reset time GitHub
        itself supplies is honoured.
        """
        if response.status_code not in (403, 429):
            return
        remaining = response.headers.get("x-ratelimit-remaining")
        retry_after = response.headers.get("retry-after")
        reset = response.headers.get("x-ratelimit-reset")
        if remaining not in ("0", None) and retry_after is None:
            return
        seconds = FALLBACK_BACKOFF_SECONDS
        if retry_after and retry_after.isdigit():
            seconds = int(retry_after)
        elif reset and reset.isdigit():
            seconds = int(int(reset) - self._now().timestamp())
        raise _RateLimited(max(1, min(seconds, 3600)))

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class _RateLimited(Exception):
    """GitHub refused for budget reasons and said when to come back."""

    def __init__(self, seconds: int) -> None:
        super().__init__(f"rate limited for {seconds}s")
        self.seconds = seconds


def _pending_from(payload: object) -> PendingContribution | None:
    """One open pull request, or None when it is not readable as one."""
    if not isinstance(payload, Mapping):
        return None
    try:
        number = payload["number"]
        title = payload["title"]
        head = payload["head"]
        user = payload["user"]
        html_url = payload["html_url"]
        created_at = _timestamp(payload["created_at"])
        updated_at = _timestamp(payload["updated_at"])
    except (KeyError, TypeError, ValueError):
        return None
    if not isinstance(number, int) or not isinstance(title, str):
        return None
    if not isinstance(head, Mapping) or not isinstance(html_url, str):
        return None
    labels = _labels(payload.get("labels"))
    target = _label_value(labels, TARGET_LABEL)
    return PendingContribution(
        number=number,
        title=title[:500],
        target=target,
        # Filled in by `ContributionSnapshot.build` from the merged corpus: an open pull request
        # carries no reward target of its own.
        conjecture_slug=None,
        hotkey=_label_value(labels, HOTKEY_LABEL),
        author_login=str(user.get("login", ""))[:100] if isinstance(user, Mapping) else "",
        branch=str(head.get("ref", ""))[:255],
        draft=bool(payload.get("draft", False)),
        created_at=created_at,
        updated_at=updated_at,
        html_url=html_url[:500],
    )


def _labels(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        entry["name"]
        for entry in value
        if isinstance(entry, Mapping) and isinstance(entry.get("name"), str)
    )


def _label_value(labels: Iterable[str], prefix: str) -> str | None:
    """The value of the first `prefix`-tagged label, bounded.

    A label is repository metadata anyone with write access sets, so its value is truncated rather
    than trusted — it is published on the pending listing and must not be able to carry a payload.
    """
    for label in labels:
        if label.startswith(prefix):
            value = label[len(prefix) :].strip()
            if value:
                return value[:255]
    return None


def _timestamp(value: object) -> datetime:
    """One GitHub timestamp. `fromisoformat` reads the trailing `Z` directly on 3.11+.

    A value without an offset is read as UTC rather than as local time: GitHub sends UTC, and a
    naive datetime reaching a response model would be serialised without a zone and read by a
    browser as whatever zone it happened to be in.
    """
    if not isinstance(value, str):
        raise ValueError("timestamp is not a string")
    moment = datetime.fromisoformat(value)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


class ContributionRefresher:
    """The background task that keeps a mirror current.

    Owned by the application lifespan rather than by the mirror, for the same reason the API's
    other periodic work is: a task started inside a constructor outlives every attempt to reason
    about when it stops, and this one must be cancelled cleanly on shutdown so a poll in flight
    does not keep the event loop alive.

    The first refresh runs immediately rather than after one interval, so a freshly started replica
    is serving the corpus within a few seconds instead of answering `503` for a minute.
    """

    def __init__(
        self, *, mirror: ContributionMirror, interval_seconds: int
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("the contribution refresh interval must be positive")
        self._mirror = mirror
        self._interval = interval_seconds
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(
                self._run(), name="contributions-refresh"
            )

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        while True:
            # `refresh` swallows its own failures, so nothing here can end the loop except
            # cancellation. That is the point: this task existing is what the endpoints' freshness
            # depends on, and it must not be able to die quietly.
            await self._mirror.refresh()
            await asyncio.sleep(self._interval)


def build_contribution_mirror(settings) -> ContributionMirror:  # type: ignore[no-untyped-def]
    """The mirror for a deployment, or the unavailable one when the surface is switched off."""
    if not settings.contributions_enabled:
        return UnavailableContributionMirror(
            repository=settings.contributions_repository
        )
    return GitHubContributionMirror(
        repository=settings.contributions_repository,
        branch=settings.contributions_branch,
        api_base_url=settings.contributions_api_base_url,
        token=settings.contributions_token,
        timeout_seconds=settings.contributions_timeout_seconds,
        max_archive_bytes=settings.contributions_max_archive_bytes,
    )


def repository_url(repository: str) -> str:
    return f"{GITHUB_HTML_URL}/{repository}"


__all__ = [
    "DEFAULT_CONTRIBUTIONS_REPOSITORY",
    "GITHUB_API_URL",
    "ContributionMirror",
    "ContributionRefresher",
    "GitHubContributionMirror",
    "StaticContributionMirror",
    "UnavailableContributionMirror",
    "build_contribution_mirror",
    "repository_url",
]
