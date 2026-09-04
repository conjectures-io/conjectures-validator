"""Response models for `/v1/contributions`.

Its own module for the reason `schemas_public.py` gives for being separate from `schemas.py`: these
answer a different question from a different source. Everything here is a projection of a mirrored
public GitHub repository rather than of this validator's own database, and keeping that in one file
makes the boundary visible — nothing in this module can accidentally acquire a field from a
submission, a payment or an account, because it has never been handed one.

The `Model` base is shared with the public surface, so `extra="forbid"` and `frozen=True` apply
here too: a typo in a field name fails at construction rather than silently serialising nothing.

**Null means absent, and it is never rendered as zero or false.** A contribution that opted out of
a reward has `coldkey: null`; a target the corpus knows no reward target for has
`conjecture_slug: null`. Both are facts. The one thing a reader must not do is read a short list as
a complete one without checking `meta.unreadable_targets` — see `ContributionsMeta`.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Generic, TypeVar

from submission_api.schemas_public import Model

ItemT = TypeVar("ItemT")


class OffsetPage(Model, Generic[ItemT]):
    """One page of an in-memory listing.

    Offset paging rather than the signed keyset cursor the result feeds use, and the difference is
    justified by what is underneath. A cursor exists there because `OFFSET 50000` makes PostgreSQL
    read and discard fifty thousand rows, and because a feed that grows under concurrent inserts
    would silently skip rows across pages. Neither applies to a tuple in memory: the slice is
    O(1) after an already-bounded filter, and the snapshot a page is cut from is immutable, so
    every page of one listing sees exactly the corpus the first page saw.

    `total` is therefore honest and cheap here, which is why this page has one and `CursorPage`
    does not.
    """

    items: tuple[ItemT, ...]
    total: int
    limit: int
    offset: int


class ContributionsMeta(Model):
    """What the served snapshot is, and how much of it could be read.

    `stale` is the field to check before trusting a listing. It says the last successful refresh is
    older than this deployment's tolerance, which means GitHub has been unreachable or refusing —
    the rows below are still true as of `fetched_at`, they are just not current.

    `unreadable_targets` is the other honesty field. It counts target indexes that failed to parse
    and are therefore missing from every listing. A non-zero value means "this answer is
    incomplete", and a reader must not report an empty result for a target as evidence that nothing
    has been contributed to it.
    """

    repository: str
    repository_url: str
    branch: str
    head_commit: str | None
    fetched_at: datetime
    age_seconds: int
    stale: bool
    refresh_seconds: int
    targets: int
    contributions: int
    authors: int
    pending: int
    unreadable_targets: int


class ContributionItem(Model):
    """One accepted contribution.

    `conjecture_slug` is this API's own identity for the conjecture the contribution is about, and
    it is what `/v1/catalog/conjectures/{slug}` takes. It is derived from `reward_target_id`, never
    from `target` — the contribution repository's target slug and this API's conjecture slug are
    different naming schemes over the same theorem.

    `in_pool` is resolved against the live catalog at render time rather than stored, because the
    pool rotates weekly and a mirrored snapshot must not be the thing that decides whether a
    conjecture is currently offered.
    """

    contribution_id: str
    short_id: str
    target: str
    reward_target_id: str | None
    problem_id: str | None
    conjecture_slug: str | None
    in_pool: bool
    title: str
    author: str
    coldkey: str | None
    hotkey: str | None
    kind: str
    mode: str
    added: date
    declarations: tuple[str, ...]
    parents: tuple[str, ...]
    artifacts: tuple[str, ...]
    tasks_commit: str | None
    rewarded: bool
    html_url: str


class ContributionTargetSummary(Model):
    """One target and the shape of what has accumulated on it.

    `contributions` is counted from the rows actually listed, never read from the index's stored
    `contribution_count`. Where the two disagree the count that matches what is served is the one
    published.

    `open` is the corpus's own flag for whether the target accepts contributions. `in_pool` is this
    validator's separate answer to whether the conjecture is still offered for solving. They are
    different questions and a reader should not substitute one for the other.
    """

    target: str
    reward_target_id: str | None
    problem_id: str | None
    conjecture_slug: str | None
    in_pool: bool
    open: bool
    contributions: int
    authors: tuple[str, ...]
    coldkeys: tuple[str, ...]
    declarations: tuple[str, ...]
    kinds: tuple[str, ...]
    modes: tuple[str, ...]
    first_added: date | None
    last_added: date | None
    html_url: str


class ContributionTargetDetail(Model):
    """One target with every contribution on it, newest first."""

    target: ContributionTargetSummary
    contributions: tuple[ContributionItem, ...]


class ContributionAuthorSummary(Model):
    """One author key across the corpus.

    `author` is an Ed25519 public key, not a person and not a GitHub account — see `docs/identity.md`
    in the contribution repository for why the signing key and the reward wallet are separate
    things.

    `shared_coldkey` says another author key pays into one of the same coldkeys. It is published
    because it is the shape a Sybil takes, and it is deliberately not called one: several signing
    keys held by one contributor produce exactly the same value.
    """

    author: str
    contributions: int
    targets: tuple[str, ...]
    conjecture_slugs: tuple[str, ...]
    declarations: tuple[str, ...]
    coldkeys: tuple[str, ...]
    hotkeys: tuple[str, ...]
    first_seen: date
    last_seen: date
    shared_coldkey: bool


class PendingContributionItem(Model):
    """An open pull request: work somebody has offered and nobody has accepted.

    Reported apart from `ContributionItem` because it is a weaker claim. Nothing here has been
    reviewed, indexed, or assigned a content-derived id, and `target` is a CI-applied label rather
    than a field of a signed index. Counting these alongside merged contributions would report
    intent as achievement.
    """

    number: int
    title: str
    target: str | None
    conjecture_slug: str | None
    hotkey: str | None
    author_login: str
    branch: str
    draft: bool
    created_at: datetime
    updated_at: datetime
    html_url: str


ContributionPage = OffsetPage[ContributionItem]
ContributionTargetPage = OffsetPage[ContributionTargetSummary]
ContributionAuthorPage = OffsetPage[ContributionAuthorSummary]
PendingContributionPage = OffsetPage[PendingContributionItem]


__all__ = [
    "ContributionAuthorPage",
    "ContributionAuthorSummary",
    "ContributionItem",
    "ContributionPage",
    "ContributionTargetDetail",
    "ContributionTargetPage",
    "ContributionTargetSummary",
    "ContributionsMeta",
    "OffsetPage",
    "PendingContributionItem",
    "PendingContributionPage",
]
