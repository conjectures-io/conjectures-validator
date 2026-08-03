"""Filtering, faceting and paging over the in-memory task catalog.

The public conjecture list is served entirely from the catalog `TaskCatalog.load` built at
startup — no database, because none of it lives in the database. That has two useful
consequences: the endpoint cannot be made slow by an anonymous caller, and it cannot be made to
disagree with the audited allowlist, because it is reading the same objects the submission path
resolves against.

Kept out of the router so that filter semantics are testable without HTTP, and so the router
stays about status codes and cache headers.

Facet counts follow the usual faceted-search rule: each facet is counted over the results
matching every filter *except its own*. Without that, selecting `category=research open`
collapses the category facet to one row and the reader loses the ability to switch selection —
the counts have to describe what selecting something else would give them.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace

from submission_api.taskpool import TaskCatalog, TaskEntry

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
    entries: tuple[TaskEntry, ...]
    facets: tuple[tuple[str, tuple[FacetCount, ...]], ...]


def is_open(entry: TaskEntry) -> bool:
    """Whether the conjecture is still open upstream.

    Read from the source declaration's `category`, which is what the catalog extractor recorded,
    rather than inferred from `depends_on_sorry`: a task whose statement happens to depend on an
    admitted lemma is not the same claim as a conjecture nobody has proved.
    """
    return entry.source.category == OPEN_CATEGORY


def title(entry: TaskEntry) -> str:
    """The conjecture's citable name: the fully-qualified source theorem.

    An identifier, not prose. No human title exists in the upstream catalog and inventing one
    here would mean the website displayed a name that appears in no audited artifact; the
    docstring, carried as `summary`, is the human-readable half.
    """
    return entry.source.theorem


def searchable(entry: TaskEntry) -> str:
    parts = (
        entry.task_id,
        entry.source.theorem,
        entry.source.module,
        entry.source.type_pretty,
        entry.source.docstring or "",
    )
    return " ".join(parts).casefold()


# One accessor per facet field. Multi-valued fields return several labels, which is why every
# one returns a tuple: `ams_subject` puts a conjecture in every subject it is tagged with.
_FACET_VALUES: dict[str, Callable[[TaskEntry], tuple[str, ...]]] = {
    FACET_CATEGORY: lambda entry: (entry.source.category,),
    FACET_CLASSIFICATION: lambda entry: (entry.manifest.classification.value,),
    FACET_TASK_MODE: lambda entry: (entry.manifest.task_mode,),
    FACET_TIER: lambda entry: (entry.tier,),
    FACET_AMS_SUBJECT: lambda entry: tuple(
        str(subject) for subject in entry.source.ams_subjects
    ),
}


def _predicates(
    filters: ConjectureFilters,
) -> dict[str, Callable[[TaskEntry], bool]]:
    """One predicate per filter, keyed by the facet field it constrains.

    Keyed rather than combined so a facet can be counted with its own predicate left out.
    `query` and `is_open` are not facets, so they use keys no facet claims and are therefore
    always applied.
    """
    predicates: dict[str, Callable[[TaskEntry], bool]] = {}
    for field, selected in (
        (FACET_CATEGORY, filters.category),
        (FACET_CLASSIFICATION, filters.classification),
        (FACET_TASK_MODE, filters.task_mode),
        (FACET_TIER, filters.tier),
    ):
        if selected:
            allowed = frozenset(selected)
            accessor = _FACET_VALUES[field]
            predicates[field] = lambda entry, a=allowed, f=accessor: bool(
                a.intersection(f(entry))
            )
    if filters.ams_subject:
        subjects = frozenset(filters.ams_subject)
        predicates[FACET_AMS_SUBJECT] = lambda entry: bool(
            subjects.intersection(entry.source.ams_subjects)
        )
    if filters.is_open is not None:
        wanted = filters.is_open
        predicates["_is_open"] = lambda entry: is_open(entry) is wanted
    if filters.query:
        needle = filters.query
        predicates["_query"] = lambda entry: needle in searchable(entry)
    return predicates


def _matching(
    entries: Iterable[TaskEntry],
    predicates: dict[str, Callable[[TaskEntry], bool]],
    *,
    ignoring: str | None = None,
) -> list[TaskEntry]:
    active = [
        predicate for field, predicate in predicates.items() if field != ignoring
    ]
    return [entry for entry in entries if all(check(entry) for check in active)]


_SORT_KEYS: dict[str, Callable[[TaskEntry], tuple]] = {
    SORT_SLUG: lambda entry: (entry.task_id,),
    SORT_CATEGORY: lambda entry: (entry.source.category, entry.task_id),
    # `answer` groups the value-producing classifications together, which is what a solver
    # browsing for something they have an adapter for is actually filtering on.
    SORT_ANSWER: lambda entry: (
        entry.manifest.classification.value,
        entry.task_id,
    ),
}


def query(
    catalog: TaskCatalog,
    filters: ConjectureFilters,
    *,
    sort: str = SORT_SLUG,
    limit: int,
    offset: int,
) -> CatalogPage:
    """Apply the filters, count the facets, and return one page.

    Sorted before paging and always with `task_id` as the final key, so the order is total and
    two requests for the same page return the same rows. `offset` paging is safe here, unlike on
    the result feeds: the catalog is a fixed list of a few hundred entries held in memory, so
    there is neither a scan to amortise nor an insert to shift the window.
    """
    entries = catalog.summaries()
    predicates = _predicates(filters.normalised())
    matched = _matching(entries, predicates)
    matched.sort(key=_SORT_KEYS.get(sort, _SORT_KEYS[SORT_SLUG]))

    facets: list[tuple[str, tuple[FacetCount, ...]]] = []
    for field in FACET_FIELDS:
        accessor = _FACET_VALUES[field]
        counts: Counter[str] = Counter()
        for entry in _matching(entries, predicates, ignoring=field):
            counts.update(accessor(entry))
        if counts:
            facets.append((field, _ordered(field, counts)))

    window = matched[offset : offset + limit]
    return CatalogPage(total=len(matched), entries=tuple(window), facets=tuple(facets))


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


def tally(entries: Sequence[TaskEntry], field: str) -> tuple[FacetCount, ...]:
    """Unfiltered counts for one field, for `/v1/catalog/meta`."""
    accessor = _FACET_VALUES[field]
    counts: Counter[str] = Counter()
    for entry in entries:
        counts.update(accessor(entry))
    return _ordered(field, counts)


__all__ = [
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
    "CatalogPage",
    "ConjectureFilters",
    "FacetCount",
    "is_open",
    "query",
    "searchable",
    "tally",
    "title",
]
