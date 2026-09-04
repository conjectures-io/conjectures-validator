"""The contribution corpus, as an immutable in-memory snapshot.

A *contribution* is partial Lean that helps someone else finish a target — a lemma, a definition,
an API, a special case. It is not a solution: solutions come to this validator as paid submissions
and live in `submissions`. Contributions live in a separate public repository,
`conjectures-io/conjectures-contribution`, where each accepted one is a directory under
`contributions/<target>/` and each target carries a CI-generated `index.json` listing what has
been contributed to it.

This module owns the *shape* of that corpus and the queries over it. Fetching it is
`submission_api/github.py`; serving it is `routers/contributions.py`. The split is the same one
`taostats.py`/`rates.py` already draw, and it is what lets the whole query surface be tested
without a network.

Four decisions shape the model:

**The snapshot is the unit, not the row.** Every query answers from one `ContributionSnapshot`
built atomically by a refresh. A half-applied update can therefore never be observed: a reader
either sees the corpus at commit A or at commit B, and `meta.head_commit` says which. Nothing here
is mutable, so a refresh landing mid-request cannot change what that request is answering from.

**`reward_target_id` is the join, and it is the only one.** The contribution repository names
targets with its own pool slug (`erdos-535`); this API names conjectures with a slug derived from
the durable reward target (`erdos535-erdos-535` — see `submission_api/slugs.py`). The two schemes
are not the same string and must not be assumed to be. What lets a contribution be found by the
conjecture it is about is `index.json`'s `reward_target_id`, which is exactly the identity this
validator prices, pays and retires against, so `conjecture_slug` below is *derived* from it rather
than guessed from the target name. A target whose index carries no reward target simply has no
conjecture slug, and is reported that way instead of being matched approximately.

**An unreadable target is counted, never dropped silently.** A malformed or unparseable
`index.json` is excluded from the rows and added to `unreadable`, which `meta` publishes. The
alternative — serving the corpus minus whatever failed to parse, with no sign anything is missing —
would make "no contributions on this target" and "we could not read this target" the same answer.
That is the distinction `docs/querying.md` in the contribution repository calls the `?`/`—` rule,
and it is worth preserving across the wire.

**Nothing here is authenticated and nothing here is money.** A contribution's `coldkey` and
`hotkey` are reward destinations the contributor published in a public repository, and this module
republishes them verbatim. It computes no share, no weight and no payout: `reviews/` and `payouts/`
in that repository are empty, and inventing a number for them here would be reporting a policy
that does not exist yet.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime

from submission_api.slugs import SlugError, slug_for

# The contribution id is a SHA-256 over the contribution's own bytes, so it is 64 lowercase hex
# characters. `docs/querying.md` prints the first 12; a caller holding one of those truncations
# must still be able to fetch the row, so lookup accepts any unambiguous prefix at or above
# `MIN_ID_PREFIX`. Below that a prefix is not an identifier, it is a scan.
CONTRIBUTION_ID = re.compile(r"^[0-9a-f]{64}$")
ID_PREFIX = re.compile(r"^[0-9a-f]{8,64}$")
MIN_ID_PREFIX = 8
SHORT_ID_LENGTH = 12

# A target directory name, and the `target` field inside its index. Constrained because it is
# interpolated into the repository URLs published on every row: an unvalidated value here would
# put attacker-chosen path segments into a link the website renders.
TARGET_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,254}$")

# An SS58 wallet address, as loosely as this module needs to know it. Deliberately not
# `verifier.bundle.SS58_ADDRESS`: that pattern gates money moving, and this one gates a string
# being echoed back out of a public file. Applying the strict one here would make a contributor's
# typo look like corruption of the whole target.
WALLET = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{40,64}$")

# An author key: the Ed25519 public key that signed the contribution, hex. See `docs/identity.md`
# in the contribution repository for why this is not the reward wallet.
AUTHOR_KEY = re.compile(r"^[0-9a-f]{64}$")

# The vocabularies `index.json` uses. Kept as accepted-values rather than as an enum: a value the
# corpus introduces later must reach a reader as itself, not as a parse failure — so these are
# what the *filters* validate against, and an unrecognised value still round-trips on a row.
KINDS = ("idea", "lemma", "partial-proof", "refutation")
MODES = ("formalized", "counterexample", "either")

# `index.json` is machine-written by CI, so its shape is stable — but it is still an external
# document fetched over the network. Every unbounded field is capped here rather than trusted.
MAX_TITLE_LENGTH = 500
MAX_DECLARATION_LENGTH = 500
MAX_DECLARATIONS = 500
MAX_PARENTS = 100
MAX_ARTIFACTS = 200
MAX_CONTRIBUTIONS_PER_TARGET = 1000

SCHEMA_VERSIONS = (2,)

# The identity line `contrib-admin index` writes at the top of every `contributions/<target>/`
# page, indexed or not:
#
#     **Problem** `fc-379fc029-erdos1049-…-problem` · **Reward target** `fc-target:Erdos1049.…`
#
# Read for one reason only: a target nobody has contributed to yet has an `index.md` and no
# `index.json`, and 152 of the corpus's 209 targets are in that state. Without this they would
# either be missing from the target listing — making "which conjectures need work" unanswerable,
# which is the single most useful question this surface can answer — or present with no identity
# and therefore unjoinable to this API's own catalog.
#
# A generated file is a weak contract compared to JSON, so it is read defensively: the match is
# anchored on both labels and both backtick spans, and a failure yields a target row with no
# identity rather than no target row. Nothing that reaches a `Contribution` is parsed from here.
IDENTITY_LINE = re.compile(
    r"\*\*Problem\*\*\s+`(?P<problem>[^`\n]{1,255})`"
    r"\s*[^\S\n]*\S?[^\S\n]*"
    r"\*\*Reward target\*\*\s+`(?P<reward>[^`\n]{1,255})`"
)
# How much of a generated page to search for it. The line is in the first few hundred bytes of
# every one of them; scanning further would be reading prose for identity.
IDENTITY_SCAN_BYTES = 4096


class ContributionsError(ValueError):
    """One target's index could not be read. Never raised past a snapshot build."""


@dataclass(frozen=True)
class Contribution:
    """One accepted contribution, as its target's index records it.

    `conjecture_slug` is the only derived field: `slug_for(reward_target_id)`, so a reader can go
    from a contribution straight to `/v1/catalog/conjectures/{slug}` without knowing how either
    naming scheme works. It is None when the index named no reward target, or named one this
    validator cannot turn into a public slug.
    """

    contribution_id: str
    target: str
    reward_target_id: str | None
    problem_id: str | None
    conjecture_slug: str | None
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
    path: str

    @property
    def short_id(self) -> str:
        return self.contribution_id[:SHORT_ID_LENGTH]

    @property
    def rewarded(self) -> bool:
        """Whether the contributor named anywhere for a reward to go.

        Not a claim that anything has been or will be paid. `payouts/` in the contribution
        repository is empty; this says only that a destination is on file.
        """
        return self.coldkey is not None or self.hotkey is not None


@dataclass(frozen=True)
class ContributionTarget:
    """One target, and what has accumulated on it.

    Every count is derived from the rows rather than read from the index's stored
    `contribution_count`. The two can disagree — that is one of the things `contrib doctor`
    exists to find — and when they do, what is actually listed is the honest answer.
    """

    target: str
    reward_target_id: str | None
    problem_id: str | None
    conjecture_slug: str | None
    open: bool
    contributions: tuple[Contribution, ...]

    @property
    def contribution_count(self) -> int:
        return len(self.contributions)

    @property
    def authors(self) -> tuple[str, ...]:
        return tuple(sorted({item.author for item in self.contributions}))

    @property
    def coldkeys(self) -> tuple[str, ...]:
        return tuple(
            sorted({item.coldkey for item in self.contributions if item.coldkey})
        )

    @property
    def declarations(self) -> tuple[str, ...]:
        return tuple(
            sorted({name for item in self.contributions for name in item.declarations})
        )

    @property
    def kinds(self) -> tuple[str, ...]:
        return tuple(sorted({item.kind for item in self.contributions}))

    @property
    def modes(self) -> tuple[str, ...]:
        return tuple(sorted({item.mode for item in self.contributions}))

    @property
    def first_added(self) -> date | None:
        return min((item.added for item in self.contributions), default=None)

    @property
    def last_added(self) -> date | None:
        return max((item.added for item in self.contributions), default=None)


@dataclass(frozen=True)
class ContributionAuthor:
    """One author key, across every target it is active on.

    The cross-cut grain from `docs/querying.md`. `shared_coldkey` is computed over the whole
    snapshot, so it can only be built here rather than per row.
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


@dataclass(frozen=True)
class PendingContribution:
    """An open pull request against the contribution repository.

    Deliberately a different type from `Contribution`, because it is a different claim. A merged
    contribution has been reviewed, indexed and assigned an id derived from its own bytes; an open
    pull request is somebody's intent, carrying whatever labels CI has put on it so far. Collapsing
    the two into one list with a `state` field is how "contributions on this target" quietly starts
    counting work nobody has accepted.

    `author_login` is the GitHub account that opened the pull request. It is already visible to
    anyone who opens the repository, alongside the same `hotkey:` label — this republishes the pair
    rather than revealing it. Note that it is an identity the merged corpus deliberately does *not*
    record: `index.json` names an Ed25519 author key precisely so that contributing does not
    require publishing a GitHub identity. Nothing joins the two here.
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


@dataclass(frozen=True)
class ContributionSnapshot:
    """The whole corpus at one commit. Immutable, and built in one shot by `build`."""

    repository: str
    branch: str
    head_commit: str | None
    fetched_at: datetime
    targets: tuple[ContributionTarget, ...]
    contributions: tuple[Contribution, ...]
    authors: tuple[ContributionAuthor, ...]
    pending: tuple[PendingContribution, ...]
    # Target directories whose index could not be read, with the reason. Published in `meta` so a
    # short listing is never mistaken for a complete one.
    unreadable: tuple[tuple[str, str], ...] = ()

    _by_id: Mapping[str, Contribution] = field(init=False, repr=False)
    _by_target: Mapping[str, ContributionTarget] = field(init=False, repr=False)
    _by_alias: Mapping[str, ContributionTarget] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        by_id = {item.contribution_id: item for item in self.contributions}
        by_target = {row.target: row for row in self.targets}
        # Every other way a caller might name a target, resolved to the same row: the public
        # conjecture slug this API publishes, the durable reward target id, and the per-revision
        # problem id. An alias that would collide with a real target slug loses to it, because the
        # directory name is the corpus's own primary key.
        aliases: dict[str, ContributionTarget] = {}
        for row in self.targets:
            for alias in (row.conjecture_slug, row.reward_target_id, row.problem_id):
                if alias and alias not in by_target:
                    aliases.setdefault(alias, row)
        object.__setattr__(self, "_by_id", by_id)
        object.__setattr__(self, "_by_target", by_target)
        object.__setattr__(self, "_by_alias", aliases)

    @property
    def available(self) -> bool:
        """Whether this snapshot is a reading of the corpus rather than the absence of one.

        A snapshot with no head commit has never been fetched. Endpoints refuse rather than
        serving its empty lists, because an empty corpus and an unfetched one are different facts
        and only one of them is true.
        """
        return self.head_commit is not None

    def age_seconds(self, *, now: datetime | None = None) -> int:
        moment = now or datetime.now(tz=UTC)
        return max(0, int((moment - self.fetched_at).total_seconds()))

    @classmethod
    def empty(
        cls,
        *,
        repository: str,
        branch: str = "main",
        fetched_at: datetime | None = None,
    ) -> ContributionSnapshot:
        """The never-fetched snapshot. `available` is False, and every list is empty."""
        return cls(
            repository=repository,
            branch=branch,
            head_commit=None,
            fetched_at=fetched_at or datetime.fromtimestamp(0, tz=UTC),
            targets=(),
            contributions=(),
            authors=(),
            pending=(),
        )

    @classmethod
    def build(
        cls,
        *,
        repository: str,
        branch: str,
        head_commit: str,
        fetched_at: datetime,
        targets: Iterable[ContributionTarget],
        pending: Iterable[PendingContribution] = (),
        unreadable: Iterable[tuple[str, str]] = (),
    ) -> ContributionSnapshot:
        """Assemble a snapshot, deriving the flat contribution list and the author cross-cut.

        Ordering is fixed here rather than left to the caller, so two refreshes of one commit
        produce byte-identical responses and the `ETag` on every endpoint is stable.
        """
        rows = tuple(sorted(targets, key=lambda row: row.target))
        contributions = tuple(
            sorted(
                (item for row in rows for item in row.contributions),
                key=lambda item: (item.added, item.contribution_id),
                reverse=True,
            )
        )
        # An open pull request carries a `target:` label and nothing else that identifies the
        # conjecture, so its public slug is resolved here against the merged corpus rather than
        # derived from the label. A pull request opening the first contribution on a target the
        # corpus has never seen therefore has no conjecture slug, which is the truth: the index
        # that would name its reward target does not exist yet.
        slugs = {row.target: row.conjecture_slug for row in rows}
        return cls(
            repository=repository,
            branch=branch,
            head_commit=head_commit,
            fetched_at=fetched_at,
            targets=rows,
            contributions=contributions,
            authors=_authors(contributions),
            pending=tuple(
                sorted(
                    (
                        replace(item, conjecture_slug=slugs.get(item.target))
                        if item.conjecture_slug is None and item.target
                        else item
                        for item in pending
                    ),
                    key=lambda item: -item.number,
                )
            ),
            unreadable=tuple(sorted(unreadable)),
        )

    def target(self, name: str) -> ContributionTarget | None:
        """One target by its own slug, its conjecture slug, its reward target or its problem id."""
        return self._by_target.get(name) or self._by_alias.get(name)

    def contribution(self, identifier: str) -> Contribution | None:
        """One contribution by full id, or by an unambiguous prefix of at least `MIN_ID_PREFIX`.

        An ambiguous prefix resolves to nothing rather than to the first match. Two contributions
        sharing a prefix is vanishingly unlikely with a SHA-256 id, but "vanishingly unlikely" is
        not a reason to serve one row under another's name.
        """
        exact = self._by_id.get(identifier)
        if exact is not None:
            return exact
        if len(identifier) < MIN_ID_PREFIX or ID_PREFIX.fullmatch(identifier) is None:
            return None
        matched = [
            item for key, item in self._by_id.items() if key.startswith(identifier)
        ]
        return matched[0] if len(matched) == 1 else None


def _authors(contributions: Sequence[Contribution]) -> tuple[ContributionAuthor, ...]:
    """The author grain, and the only place `shared_coldkey` can be decided.

    Two distinct author keys paying into one coldkey is not by itself wrong — one person may hold
    several signing keys — but it is the shape a Sybil takes, so it is computed and published
    rather than left for a reader to join out of two listings.
    """
    grouped: dict[str, list[Contribution]] = {}
    for item in contributions:
        grouped.setdefault(item.author, []).append(item)
    coldkey_owners: dict[str, set[str]] = {}
    for author, items in grouped.items():
        for item in items:
            if item.coldkey:
                coldkey_owners.setdefault(item.coldkey, set()).add(author)
    rows = []
    for author, items in grouped.items():
        coldkeys = tuple(sorted({item.coldkey for item in items if item.coldkey}))
        rows.append(
            ContributionAuthor(
                author=author,
                contributions=len(items),
                targets=tuple(sorted({item.target for item in items})),
                conjecture_slugs=tuple(
                    sorted({item.conjecture_slug for item in items if item.conjecture_slug})
                ),
                declarations=tuple(
                    sorted({name for item in items for name in item.declarations})
                ),
                coldkeys=coldkeys,
                hotkeys=tuple(sorted({item.hotkey for item in items if item.hotkey})),
                first_seen=min(item.added for item in items),
                last_seen=max(item.added for item in items),
                shared_coldkey=any(
                    len(coldkey_owners.get(coldkey, ())) > 1 for coldkey in coldkeys
                ),
            )
        )
    return tuple(sorted(rows, key=lambda row: (-row.contributions, row.author)))


# --- Parsing ---------------------------------------------------------------------------------
# Everything below reads a document fetched over the network from a repository this process does
# not control. It is written the way `pins.py` and `taskpool.py` read their inputs: validate the
# shape, bound every length, and raise rather than coerce. A target that fails lands in
# `unreadable` and is reported; it never becomes a half-populated row.


def parse_target(payload: object, *, directory: str) -> ContributionTarget:
    """Read one `contributions/<target>/index.json` into a target row.

    `directory` is the directory the document was found in. It has to agree with the document's
    own `target` field: a mismatch means the index was copied rather than generated, and the two
    names would then disagree about which target a contribution is on.
    """
    if not isinstance(payload, Mapping):
        raise ContributionsError("index is not a JSON object")
    version = payload.get("schema_version")
    if version not in SCHEMA_VERSIONS:
        raise ContributionsError(f"unsupported index schema_version {version!r}")

    target = _slug(payload.get("target"), field="target")
    if target != directory:
        raise ContributionsError(
            f"index names target {target!r} but sits in directory {directory!r}"
        )
    reward_target_id = _optional_text(
        payload.get("reward_target_id"), field="reward_target_id", maximum=255
    )
    raw = payload.get("contributions")
    if not isinstance(raw, list):
        raise ContributionsError("index has no contributions list")
    if len(raw) > MAX_CONTRIBUTIONS_PER_TARGET:
        raise ContributionsError(
            f"index lists more than {MAX_CONTRIBUTIONS_PER_TARGET} contributions"
        )
    conjecture_slug = _conjecture_slug(reward_target_id)
    problem_id = _optional_text(
        payload.get("problem_id"), field="problem_id", maximum=255
    )
    items = tuple(
        _parse_contribution(
            entry,
            target=target,
            reward_target_id=reward_target_id,
            problem_id=problem_id,
            conjecture_slug=conjecture_slug,
        )
        for entry in raw
    )
    identifiers = {item.contribution_id for item in items}
    if len(identifiers) != len(items):
        raise ContributionsError("index lists one contribution id more than once")
    return ContributionTarget(
        target=target,
        reward_target_id=reward_target_id,
        problem_id=problem_id,
        conjecture_slug=conjecture_slug,
        open=bool(payload.get("open", True)),
        contributions=tuple(
            sorted(items, key=lambda item: (item.added, item.contribution_id), reverse=True)
        ),
    )


def parse_empty_target(page: str, *, directory: str) -> ContributionTarget:
    """A target that has a generated page but no index: it exists, and nothing is on it.

    `directory` is trusted as the target name here, because it is the only name available — there
    is no JSON `target` field to cross-check it against. It is validated to the same slug shape,
    so an unexpected directory still cannot reach a URL.

    An unmatched identity line is not an error. The row is still true and still useful — the target
    exists and is empty — it simply cannot be joined to a conjecture, which is reported as a null
    `conjecture_slug` rather than as a missing target.
    """
    target = _slug(directory, field="target")
    matched = IDENTITY_LINE.search(page[:IDENTITY_SCAN_BYTES])
    problem_id = matched.group("problem").strip() if matched else None
    reward_target_id = matched.group("reward").strip() if matched else None
    return ContributionTarget(
        target=target,
        reward_target_id=reward_target_id,
        problem_id=problem_id,
        conjecture_slug=_conjecture_slug(reward_target_id),
        open=True,
        contributions=(),
    )


def _parse_contribution(
    payload: object,
    *,
    target: str,
    reward_target_id: str | None,
    problem_id: str | None,
    conjecture_slug: str | None,
) -> Contribution:
    if not isinstance(payload, Mapping):
        raise ContributionsError("a contribution entry is not a JSON object")
    contribution_id = _text(
        payload.get("contribution_id"), field="contribution_id", maximum=64
    )
    if CONTRIBUTION_ID.fullmatch(contribution_id) is None:
        raise ContributionsError("contribution_id is not 64 lowercase hex characters")
    author = _text(payload.get("author"), field="author", maximum=64)
    if AUTHOR_KEY.fullmatch(author) is None:
        raise ContributionsError("author is not a 64-character hex key")
    return Contribution(
        contribution_id=contribution_id,
        target=target,
        reward_target_id=reward_target_id,
        problem_id=problem_id,
        conjecture_slug=conjecture_slug,
        title=_text(payload.get("title"), field="title", maximum=MAX_TITLE_LENGTH),
        author=author,
        coldkey=_wallet(payload.get("coldkey"), field="coldkey"),
        hotkey=_wallet(payload.get("hotkey"), field="hotkey"),
        kind=_text(payload.get("kind"), field="kind", maximum=64),
        mode=_text(payload.get("mode"), field="mode", maximum=64),
        added=_date(payload.get("added"), field="added"),
        declarations=_names(
            payload.get("declarations"), field="declarations", maximum=MAX_DECLARATIONS
        ),
        parents=_names(payload.get("parents"), field="parents", maximum=MAX_PARENTS),
        artifacts=_names(
            payload.get("artifacts"), field="artifacts", maximum=MAX_ARTIFACTS
        ),
        tasks_commit=_optional_text(
            payload.get("tasks_commit"), field="tasks_commit", maximum=64
        ),
        path=f"contributions/{target}/{contribution_id}",
    )


def _conjecture_slug(reward_target_id: str | None) -> str | None:
    """This API's public slug for a reward target, or None when there is no honest answer.

    A `SlugError` here is not a corrupt index: the contribution repository is pinned to its own
    pool revision, and a target it knows about may name a reward target this validator's slug rules
    cannot represent. Publishing no slug is correct — the contribution is still fully readable by
    its own target name.
    """
    if not reward_target_id:
        return None
    try:
        return slug_for(reward_target_id)
    except SlugError:
        return None


def _text(value: object, *, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ContributionsError(f"{field} is missing or not a string")
    cleaned = value.strip()
    if not cleaned:
        raise ContributionsError(f"{field} is empty")
    if len(cleaned) > maximum:
        raise ContributionsError(f"{field} exceeds {maximum} characters")
    if any(ord(char) < 32 for char in cleaned):
        raise ContributionsError(f"{field} contains control characters")
    return cleaned


def _optional_text(value: object, *, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _text(value, field=field, maximum=maximum)


def _slug(value: object, *, field: str) -> str:
    slug = _text(value, field=field, maximum=255)
    if TARGET_SLUG.fullmatch(slug) is None:
        raise ContributionsError(f"{field} is not a lowercase slug")
    return slug


def _wallet(value: object, *, field: str) -> str | None:
    """A reward destination, or None when the contributor opted out.

    An address that does not look like SS58 is dropped to None rather than raising. It is display
    metadata on a public listing — nothing here pays it — and refusing a whole target because one
    contributor mistyped a wallet would hide correct rows to report an incorrect one.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ContributionsError(f"{field} is not a string")
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned if WALLET.fullmatch(cleaned) else None


def _date(value: object, *, field: str) -> date:
    if not isinstance(value, str):
        raise ContributionsError(f"{field} is missing or not a string")
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ContributionsError(f"{field} is not an ISO date") from exc


def _names(value: object, *, field: str, maximum: int) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ContributionsError(f"{field} is not a list")
    if len(value) > maximum:
        raise ContributionsError(f"{field} lists more than {maximum} entries")
    return tuple(
        _text(entry, field=field, maximum=MAX_DECLARATION_LENGTH) for entry in value
    )


__all__ = [
    "AUTHOR_KEY",
    "CONTRIBUTION_ID",
    "ID_PREFIX",
    "KINDS",
    "MIN_ID_PREFIX",
    "MODES",
    "SHORT_ID_LENGTH",
    "TARGET_SLUG",
    "Contribution",
    "ContributionAuthor",
    "ContributionSnapshot",
    "ContributionTarget",
    "ContributionsError",
    "PendingContribution",
    "parse_empty_target",
    "parse_target",
]
