"""The public conjecture view over the audited task pool: grouping, filtering, faceting, paging.

The pool holds *tasks*; the website shows *conjectures*. Those are not the same count. Every
theorem is issued as one task per production mode — `formalized` to prove it and
`counterexample` to refute it — so a pool of N theorems is 2N tasks. Listing tasks would show
every conjecture twice, with two unrelated ids and near-identical statements, and would make
"attempts" mean attempts in one direction rather than attempts on the problem.

So tasks are grouped by `reward_target_id` into a `Conjecture`, keyed by the stable slug
`submission_api.slugs` derives from that same identity. The grouping is built once at startup, so
a slug collision or a group whose tasks disagree about their shared facts is a startup failure
rather than something a reader discovers.

Everything is served from that in-memory index — no database, because none of it lives in the
database. Two useful consequences: the endpoint cannot be made slow by an anonymous caller, and
it cannot be made to disagree with the audited allowlist, because it is reading the same objects
the submission path resolves against.

Kept out of the router so that grouping and filter semantics are testable without HTTP, and so
the router stays about status codes and cache headers.

Facet counts follow the usual faceted-search rule: each facet is counted over the results
matching every filter *except its own*. Without that, selecting `category=research open`
collapses the category facet to one row and the reader loses the ability to switch selection —
the counts have to describe what selecting something else would give them.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace

from submission_api.retired import RetiredConjecture, RetiredIndex
from submission_api.slugs import legacy_theorem_slug, matches_legacy_slug, slug_for
from submission_api.taskpool import TaskCatalog, TaskEntry
from verifier.models import CatalogDeclaration
from verifier.task_policy import PRODUCTION_TASK_MODES

# The category the upstream catalog uses for a conjecture with no known formal proof. That is
# exactly the set worth a bounty, so it is the one filter with a dedicated boolean.
OPEN_CATEGORY = "research open"

# Facet fields, in the order they are reported. Every one is a low-cardinality label from the
# audited task bundle, so counting them is bounded by the size of the pool.
FACET_CATEGORY = "category"
FACET_CLASSIFICATION = "classification"
FACET_TASK_MODE = "task_mode"
FACET_TIER = "tier"
FACET_AMS_SUBJECT = "ams_subject"
FACET_FIELDS = (
    FACET_CATEGORY,
    FACET_CLASSIFICATION,
    FACET_TASK_MODE,
    FACET_TIER,
    FACET_AMS_SUBJECT,
)

SORT_SLUG = "slug"
SORT_CATEGORY = "category"
SORT_ANSWER = "answer"
SORTS = (SORT_SLUG, SORT_CATEGORY, SORT_ANSWER)

MAX_QUERY_LENGTH = 200
# The free-text filter is a plain case-folded substring test over fields already published on
# the summary. No regular expression is ever built from caller input — a catastrophically
# backtracking pattern over a few hundred statements would be a free CPU sink.
QUERY_STRIP = re.compile(r"\s+")

# What separates a conjecture's root theorem from the qualifier naming one of its variants:
# `Erdos1.erdos_1` against `Erdos1.erdos_1.variants.lb`. Upstream never nests these — no
# declaration in the pinned catalog contains the separator twice — so one split is enough, and
# splitting at most once keeps a dotted qualifier like `monotone.parts.i` whole rather than
# truncating it at its first dot.
VARIANT_SEPARATOR = ".variants."

# Upstream files each Erdős problem in a module named for its number on erdosproblems.com:
# `FormalConjectures.ErdosProblems.«1»`, guillemets included because a bare digit is not a legal
# Lean identifier.
#
# The number is read from the *module*, never from the theorem name. Every one of the 509 Erdős
# modules in the pinned catalog matches this pattern exactly, while 20 theorem names are mangled
# by Lean's private-declaration scheme into `_private.FormalConjectures.ErdosProblems.«1049».0.
# Erdos1049.lambert_convergent` — a theorem-name parser would have to special-case those, and
# getting it wrong publishes the wrong problem number rather than none.
ERDOS_MODULE = re.compile(r"^FormalConjectures\.ErdosProblems\.«(\d{1,9})»$")


class CatalogGroupingError(RuntimeError):
    """The pool cannot be presented as a set of uniquely addressable conjectures.

    Always raised during startup, never in a request: the grouping is built once from immutable
    catalog state, so if it holds at boot it holds for the life of the process.
    """


@dataclass(frozen=True)
class Conjecture:
    """One mathematical conjecture and every task issued against it.

    `slug` is the stable public identity. The task ids are kept — a solver needs them to build a
    bundle, and they are what a report and a submission record — but they are fields of a
    conjecture rather than the name of one.
    """

    slug: str
    problem_id: str
    reward_target_id: str
    tier: str
    # One per task mode, ordered by `PRODUCTION_TASK_MODES` so `formalized` is first and the
    # order does not depend on how the pool happened to be walked.
    tasks: tuple[TaskEntry, ...]

    @property
    def primary(self) -> TaskEntry:
        """The task whose shared facts are published for the conjecture as a whole.

        Both modes are generated from one source declaration, so statement, docstring, category,
        AMS subjects and classification are identical across them; this picks a deterministic
        one rather than asserting equality on every read. `_grouped` has already checked that
        the facts published as single values really do agree.
        """
        return self.tasks[0]

    @property
    def source(self) -> CatalogDeclaration:
        return self.primary.source

    @property
    def classification(self) -> str:
        return self.primary.manifest.classification.value

    @property
    def task_modes(self) -> tuple[str, ...]:
        return tuple(task.manifest.task_mode for task in self.tasks)

    @property
    def task_ids(self) -> tuple[str, ...]:
        return tuple(task.task_id for task in self.tasks)


def root_theorem(theorem: str) -> str:
    """The theorem a variant is a variant *of*, or the theorem itself.

    `Erdos1.erdos_1.variants.lb` and `Erdos1.erdos_1` both answer `Erdos1.erdos_1`, which is what
    makes them one problem in the index.
    """
    return theorem.split(VARIANT_SEPARATOR, 1)[0]


def variant_qualifier(theorem: str) -> str | None:
    """The part of a variant's name that says *which* variant, or None for a root theorem.

    Returned whole, dots and all: `Erdos357.erdos_357.variants.monotone.parts.i` qualifies as
    `monotone.parts.i`, because upstream nests the qualifier rather than the variant.
    """
    parts = theorem.split(VARIANT_SEPARATOR, 1)
    return parts[1] if len(parts) == 2 else None


def erdos_problem_number(module: str) -> int | None:
    """The erdosproblems.com problem number this module formalises, or None.

    None is the answer for every other collection in the pool — Wikipedia, OEIS, the Millennium
    problems, and the rest — rather than an error, because "not an Erdős problem" is an ordinary
    thing for a conjecture to be. It is also the answer if a future rotation invents a module name
    this does not recognise: publishing no number is recoverable, publishing a wrong one is not.
    """
    matched = ERDOS_MODULE.match(module)
    return int(matched.group(1)) if matched is not None else None


@dataclass(frozen=True)
class FamilyMember:
    """One conjecture as the problem index sees it, live or retired.

    A normalised view rather than either concrete type. `Conjecture` and `RetiredConjecture` are
    deliberately different classes — one carries the `TaskManifest` a verifier runs against, the
    other cannot and must not fabricate one — and the index needs exactly the four facts they both
    honestly have. Flattening them here is what lets the grouping treat a retired variant as a
    member of its family without either type learning about the other.

    `retired` is carried on the member, not inferred by the caller from which list it came out of.
    That is the whole reason a retired conjecture can be published in a table of contents at all:
    the flag travels with it.
    """

    slug: str
    theorem: str
    module: str
    task_modes: tuple[str, ...]
    retired: bool


def _member(item: Conjecture | RetiredConjecture, *, retired: bool) -> FamilyMember:
    return FamilyMember(
        slug=item.slug,
        theorem=item.source.theorem,
        module=item.source.module,
        task_modes=item.task_modes,
        retired=retired,
    )


@dataclass(frozen=True)
class ProblemFamily:
    """One upstream problem: the conjecture that stands for it, and its pooled variants.

    Coarser than a `Conjecture`. Upstream formalises a problem as a root theorem plus any number
    of `.variants.*` siblings — weaker forms, one-sided bounds, named special cases — and each of
    those is its own reward target, its own slug and its own bounty. That is correct for everything
    the rest of the catalog does, and wrong for a caller that wants to know which *problems* exist:
    Erdős 1 alone would be eight rows.

    So the derived fields are flattened onto this record rather than left as properties over
    `representative`. A family is what the index publishes, and computing `qualifier` from a
    theorem name at the point of serialisation is how the two drift apart.

    `retired` describes the conjecture at `slug` alone — never the family. A family headed by a
    retired root can still hold submittable variants, so a caller that wants open targets has to
    read the members' own flags rather than filter on this one.
    """

    slug: str
    source_theorem: str
    erdos_problem_number: int | None
    qualifier: str | None
    retired: bool
    # The other members of the family, ordered by slug, never including the one at `slug` above.
    variants: tuple[FamilyMember, ...]


# What makes one member of a family the one that heads it. Lower sorts first, so: the root theorem
# before any variant, a live conjecture before a retired one, then slug for a total order.
#
# The root wins *even when it is retired* and live variants exist. That looks backwards until you
# ask what changes when a root is retired: under the other rule the entry's `slug` and
# `source_theorem` would move to some variant, so retiring one target would silently rename the
# problem it belongs to. The whole point of a stable slug is that a published URL survives events
# like this, and a table of contents whose rows are renumbered by a retirement is not one. So the
# header stays put and `retired` reports what happened to it.
def _precedence(member: FamilyMember, root: str) -> tuple[bool, bool, str]:
    return (member.theorem != root, member.retired, member.slug)


def families(index: ConjectureIndex) -> tuple[ProblemFamily, ...]:
    """Group the pool into one entry per upstream problem, ordered by slug.

    Retired targets are included, each flagged as such. Unlike `all()` and `query()` — which
    describe what may be submitted against, and so must never show a withdrawn target — this is a
    table of contents: a problem that has left the pool is still part of what the pool has covered,
    its detail page is still readable, and the results earned against it are still citable. Hiding
    it here would make the index disagree with the pages it links to.

    The two lists cannot collide. `ConjectureIndex.build` refuses to start when a slug is both live
    and retired, so every member of every family has a distinct slug and no conjecture can be
    grouped twice.

    The representative is the root theorem when the pool carries it. When it does not — 241 of the
    pinned catalog's 942 variants have no root declaration at all, which leaves 70 of its 2395
    problems headed by a variant — the best-precedence variant stands in for the problem, and a
    non-null `qualifier` is what tells a reader that happened. An ordinary case with real data
    behind it, not a defensive branch.

    Note that `erdos_problem_number` is not a key over the result: 934 of those problems carry a
    number and only 509 numbers are distinct, because upstream regularly formalises one Erdős
    problem as several independent root theorems in one module.
    """
    members = [_member(item, retired=False) for item in index.all()]
    members.extend(_member(item, retired=True) for item in index.retired.all())

    grouped: dict[str, list[FamilyMember]] = {}
    for member in members:
        grouped.setdefault(root_theorem(member.theorem), []).append(member)

    built = []
    for root, family in grouped.items():
        representative = min(family, key=lambda item: _precedence(item, root))
        built.append(
            ProblemFamily(
                slug=representative.slug,
                source_theorem=representative.theorem,
                # Read from the representative alone, which is well defined because a family never
                # spans two modules: no root theorem in the pinned catalog has a variant declared
                # outside its own file.
                erdos_problem_number=erdos_problem_number(representative.module),
                qualifier=variant_qualifier(representative.theorem),
                retired=representative.retired,
                # Ordered by slug alone, deliberately not with the retired ones pushed to the end:
                # retiring a variant must not reorder the list a reader has already seen.
                variants=tuple(
                    sorted(
                        (item for item in family if item.slug != representative.slug),
                        key=lambda item: item.slug,
                    )
                ),
            )
        )
    return tuple(sorted(built, key=lambda item: item.slug))


def _mode_order(entry: TaskEntry) -> tuple[int, str]:
    mode = entry.manifest.task_mode
    if mode in PRODUCTION_TASK_MODES:
        return (PRODUCTION_TASK_MODES.index(mode), mode)
    # A non-production mode should not reach a public catalog, but ordering it last is better
    # than an exception in a sort key.
    return (len(PRODUCTION_TASK_MODES), mode)


@dataclass(frozen=True)
class ConjectureIndex:
    """Slug-addressable conjectures, built once from a `TaskCatalog`."""

    repository_commit: str
    by_slug: Mapping[str, Conjecture]
    # Every current task id, so a URL minted from one can be redirected to its slug rather than
    # 404ing. Only this rotation's ids are in here; older ones go through `resolve_legacy`.
    slug_by_task_id: Mapping[str, str]
    # Targets that have left the pool. Held beside the live grouping rather than merged into it:
    # `all()` and `query()` describe what can be submitted against, and a retired target must
    # never appear in either. Only `get_retired` reaches this, and only after `get` has missed.
    retired: RetiredIndex = RetiredIndex.empty()

    @classmethod
    def build(
        cls, catalog: TaskCatalog, *, retired: RetiredIndex | None = None
    ) -> ConjectureIndex:
        by_slug = _grouped(catalog.summaries())
        resolved = retired or RetiredIndex.empty()
        # A live target and a retired one at the same URL would make which page a reader gets
        # depend on lookup order. It cannot happen — retirement removes the target from the
        # allowlist that built `by_slug` — so if it does, the pin is inconsistent.
        collisions = sorted(set(by_slug) & set(resolved.by_slug))
        if collisions:
            raise CatalogGroupingError(
                f"slugs are both live and retired: {collisions}; the pinned task checkout "
                "disagrees with its own allowlist"
            )
        slug_by_task_id = {
            task_id: conjecture.slug
            for conjecture in by_slug.values()
            for task_id in conjecture.task_ids
        }
        return cls(
            repository_commit=catalog.repository_commit,
            by_slug=by_slug,
            slug_by_task_id=slug_by_task_id,
            retired=resolved,
        )

    def get(self, slug: str) -> Conjecture | None:
        """The live conjecture at this slug, or None. Never returns a retired one.

        Callers that serve a page fall through to `get_retired`; callers that decide whether
        something may be submitted against must not, which is why this stays live-only.
        """
        return self.by_slug.get(slug)

    def get_retired(self, slug: str) -> RetiredConjecture | None:
        return self.retired.get(slug)

    def all(self) -> tuple[Conjecture, ...]:
        return tuple(sorted(self.by_slug.values(), key=lambda item: item.slug))

    def resolve_legacy(self, candidate: str) -> str | None:
        """The stable slug a task-id-shaped URL should redirect to, or None.

        Two paths. A task id from the *current* pool is an exact lookup. A task id from an
        earlier rotation is not in the pool at all — that is the whole problem — so it is matched
        on the theorem fragment embedded in it, which does not depend on the commit.

        Retired targets participate in both paths. A task id naming a bundle that was deleted is
        precisely the link most likely to be in circulation — it is on every report and every
        result already published against that target — and it now resolves to the page that
        explains why the target closed rather than to a 404.

        A fragment matching more than one theorem returns None, so an ambiguous legacy URL 404s.
        Sending a reader to the wrong conjecture is worse than telling them the link is dead.
        """
        exact = self.slug_by_task_id.get(candidate) or self.retired.slug_by_task_id.get(
            candidate
        )
        if exact is not None:
            return exact
        embedded = legacy_theorem_slug(candidate)
        if embedded is None:
            return None
        matched = [
            conjecture.slug
            for conjecture in (*self.by_slug.values(), *self.retired.by_slug.values())
            if matches_legacy_slug(conjecture.source.theorem, embedded)
        ]
        if len(matched) != 1:
            return None
        return matched[0]


def _grouped(entries: Sequence[TaskEntry]) -> dict[str, Conjecture]:
    """Group tasks into slug-keyed conjectures, refusing anything unaddressable.

    Three ways this fails, all at startup:

    * two theorems slugify to one slug, so one would be served at the other's URL;
    * two reward targets produce the same slug, same reason;
    * one reward target's tasks disagree on `problem_id` or `tier`, both of which are published
      as a single value per conjecture and so cannot be picked arbitrarily from a member task.
    """
    grouped: dict[str, list[TaskEntry]] = {}
    for entry in entries:
        grouped.setdefault(entry.reward_target_id, []).append(entry)

    conjectures: dict[str, Conjecture] = {}
    owners: dict[str, str] = {}
    for reward_target_id, members in sorted(grouped.items()):
        slug = slug_for(reward_target_id)
        if slug in owners:
            raise CatalogGroupingError(
                f"reward targets {owners[slug]!r} and {reward_target_id!r} both produce the "
                f"slug {slug!r}; one conjecture would be served at the other's URL"
            )
        owners[slug] = reward_target_id

        problem_ids = {member.problem_id for member in members}
        tiers = {member.tier for member in members}
        if len(problem_ids) != 1:
            raise CatalogGroupingError(
                f"reward target {reward_target_id!r} spans problem ids {sorted(problem_ids)}; "
                "a conjecture publishes one"
            )
        if len(tiers) != 1:
            raise CatalogGroupingError(
                f"reward target {reward_target_id!r} spans tiers {sorted(tiers)}; "
                "a conjecture publishes one"
            )
        ordered = tuple(sorted(members, key=_mode_order))
        conjectures[slug] = Conjecture(
            slug=slug,
            problem_id=problem_ids.pop(),
            reward_target_id=reward_target_id,
            tier=tiers.pop(),
            tasks=ordered,
        )
    return conjectures


@dataclass(frozen=True)
class ConjectureFilters:
    """The filters a caller may apply. Every field is optional and ANDed with the rest."""

    category: tuple[str, ...] = ()
    classification: tuple[str, ...] = ()
    task_mode: tuple[str, ...] = ()
    tier: tuple[str, ...] = ()
    ams_subject: tuple[int, ...] = ()
    is_open: bool | None = None
    query: str = ""

    def normalised(self) -> ConjectureFilters:
        return replace(self, query=QUERY_STRIP.sub(" ", self.query).strip().casefold())


@dataclass(frozen=True)
class FacetCount:
    value: str
    count: int


@dataclass(frozen=True)
class CatalogPage:
    total: int
    items: tuple[Conjecture, ...]
    facets: tuple[tuple[str, tuple[FacetCount, ...]], ...]


def is_open(conjecture: Conjecture) -> bool:
    """Whether the conjecture is still open upstream.

    Read from the source declaration's `category`, which is what the catalog extractor recorded,
    rather than inferred from `depends_on_sorry`: a task whose statement happens to depend on an
    admitted lemma is not the same claim as a conjecture nobody has proved.
    """
    return conjecture.source.category == OPEN_CATEGORY


def title(conjecture: Conjecture) -> str:
    """The conjecture's citable name: the fully-qualified source theorem.

    An identifier, not prose. No human title exists in the upstream catalog and inventing one
    here would mean the website displayed a name that appears in no audited artifact; the
    docstring, carried as `summary`, is the human-readable half.
    """
    return conjecture.source.theorem


def searchable(conjecture: Conjecture) -> str:
    """The haystack the free-text filter tests against.

    Includes the slug and every task id, so a reader who pastes an identifier from a report, a
    bundle or an old URL into the search box finds the conjecture it belongs to.
    """
    parts = (
        conjecture.slug,
        *conjecture.task_ids,
        conjecture.source.theorem,
        conjecture.source.module,
        conjecture.source.type_pretty,
        conjecture.source.docstring or "",
    )
    return " ".join(parts).casefold()


# One accessor per facet field. Multi-valued fields return several labels, which is why every
# one returns a tuple: `ams_subject` puts a conjecture in every subject it is tagged with, and
# `task_mode` puts it in every direction it can be attacked from.
_FACET_VALUES: dict[str, Callable[[Conjecture], tuple[str, ...]]] = {
    FACET_CATEGORY: lambda item: (item.source.category,),
    FACET_CLASSIFICATION: lambda item: (item.classification,),
    FACET_TASK_MODE: lambda item: item.task_modes,
    FACET_TIER: lambda item: (item.tier,),
    FACET_AMS_SUBJECT: lambda item: tuple(
        str(subject) for subject in item.source.ams_subjects
    ),
}


def _predicates(
    filters: ConjectureFilters,
) -> dict[str, Callable[[Conjecture], bool]]:
    """One predicate per filter, keyed by the facet field it constrains.

    Keyed rather than combined so a facet can be counted with its own predicate left out.
    `query` and `is_open` are not facets, so they use keys no facet claims and are therefore
    always applied.
    """
    predicates: dict[str, Callable[[Conjecture], bool]] = {}
    for field, selected in (
        (FACET_CATEGORY, filters.category),
        (FACET_CLASSIFICATION, filters.classification),
        (FACET_TASK_MODE, filters.task_mode),
        (FACET_TIER, filters.tier),
    ):
        if selected:
            allowed = frozenset(selected)
            accessor = _FACET_VALUES[field]
            predicates[field] = lambda item, a=allowed, f=accessor: bool(
                a.intersection(f(item))
            )
    if filters.ams_subject:
        subjects = frozenset(filters.ams_subject)
        predicates[FACET_AMS_SUBJECT] = lambda item: bool(
            subjects.intersection(item.source.ams_subjects)
        )
    if filters.is_open is not None:
        wanted = filters.is_open
        predicates["_is_open"] = lambda item: is_open(item) is wanted
    if filters.query:
        needle = filters.query
        predicates["_query"] = lambda item: needle in searchable(item)
    return predicates


def _matching(
    items: Iterable[Conjecture],
    predicates: dict[str, Callable[[Conjecture], bool]],
    *,
    ignoring: str | None = None,
) -> list[Conjecture]:
    active = [
        predicate for field, predicate in predicates.items() if field != ignoring
    ]
    return [item for item in items if all(check(item) for check in active)]


_SORT_KEYS: dict[str, Callable[[Conjecture], tuple]] = {
    SORT_SLUG: lambda item: (item.slug,),
    SORT_CATEGORY: lambda item: (item.source.category, item.slug),
    # `answer` groups the value-producing classifications together, which is what a solver
    # browsing for something they have an adapter for is actually filtering on.
    SORT_ANSWER: lambda item: (item.classification, item.slug),
}


def query(
    index: ConjectureIndex,
    filters: ConjectureFilters,
    *,
    sort: str = SORT_SLUG,
    limit: int,
    offset: int,
) -> CatalogPage:
    """Apply the filters, count the facets, and return one page.

    Sorted before paging and always with `slug` as the final key, so the order is total and two
    requests for the same page return the same rows. `offset` paging is safe here, unlike on the
    result feeds: the catalog is a fixed list of a few hundred conjectures held in memory, so
    there is neither a scan to amortise nor an insert to shift the window.
    """
    items = index.all()
    predicates = _predicates(filters.normalised())
    matched = _matching(items, predicates)
    matched.sort(key=_SORT_KEYS.get(sort, _SORT_KEYS[SORT_SLUG]))

    facets: list[tuple[str, tuple[FacetCount, ...]]] = []
    for field in FACET_FIELDS:
        accessor = _FACET_VALUES[field]
        counts: Counter[str] = Counter()
        for item in _matching(items, predicates, ignoring=field):
            counts.update(accessor(item))
        if counts:
            facets.append((field, _ordered(field, counts)))

    window = matched[offset : offset + limit]
    return CatalogPage(total=len(matched), items=tuple(window), facets=tuple(facets))


def _ordered(field: str, counts: Counter[str]) -> tuple[FacetCount, ...]:
    """Descending by count, then by value, so the order is stable across identical requests.

    AMS subjects sort numerically instead: they are a numbered classification, and reading them
    ordered by popularity rather than by subject number is not what a reader expects of them.
    """
    if field == FACET_AMS_SUBJECT:
        ordered = sorted(counts.items(), key=lambda item: int(item[0]))
    else:
        ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return tuple(FacetCount(value=value, count=count) for value, count in ordered)


def tally(items: Sequence[Conjecture], field: str) -> tuple[FacetCount, ...]:
    """Unfiltered counts for one field, for `/v1/catalog/meta`."""
    accessor = _FACET_VALUES[field]
    counts: Counter[str] = Counter()
    for item in items:
        counts.update(accessor(item))
    return _ordered(field, counts)


__all__ = [
    "ERDOS_MODULE",
    "FACET_AMS_SUBJECT",
    "FACET_CATEGORY",
    "FACET_CLASSIFICATION",
    "FACET_FIELDS",
    "FACET_TASK_MODE",
    "FACET_TIER",
    "MAX_QUERY_LENGTH",
    "OPEN_CATEGORY",
    "SORTS",
    "SORT_ANSWER",
    "SORT_CATEGORY",
    "SORT_SLUG",
    "VARIANT_SEPARATOR",
    "CatalogGroupingError",
    "CatalogPage",
    "Conjecture",
    "ConjectureFilters",
    "ConjectureIndex",
    "FacetCount",
    "FamilyMember",
    "ProblemFamily",
    "erdos_problem_number",
    "families",
    "is_open",
    "query",
    "root_theorem",
    "searchable",
    "tally",
    "title",
    "variant_qualifier",
]
