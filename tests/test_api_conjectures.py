"""How the catalog groups tasks into slug-addressable conjectures.

No database and no HTTP: this is the in-memory layer that decides what a public URL looks like.
Slug *derivation* is tested in `test_api_slugs.py`; what is tested here is everything that needs a
whole pool to be meaningful — pin-rotation invariance end to end, the fail-closed checks that
refuse to boot an unaddressable pool, and legacy task-id resolution against real catalog entries.

The first test is the one that matters most: a slug must not move when the pinned source revision
does.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="submission API tests need the service extra")
pytest.importorskip("sqlalchemy", reason="submission API tests need the db extra")

from conftest import declaration
from conftest_api import REPOSITORY_COMMIT, task_entry

from submission_api import slugs
from submission_api.conjectures import (
    FACET_TASK_MODE,
    CatalogGroupingError,
    ConjectureFilters,
    ConjectureIndex,
    query,
    tally,
)
from submission_api.taskpool import catalog_from_entries
from verifier.models import Classification
from verifier.task_generator import task_id as build_task_id
from verifier.task_pool import reward_target_identity

OTHER_COMMIT = "b" * 40


def conjecture_tasks(theorem: str, *, prefix: str, commit: str = REPOSITORY_COMMIT):
    """One theorem as the pool really issues it: one task per production mode."""
    source = declaration(
        theorem=theorem, classification=Classification.DIRECT_PROP, category="research open"
    )
    return tuple(
        task_entry(
            task_id=build_task_id(commit, theorem, mode, 1),
            digest="sha256:" + f"{prefix}{mode[:2]}".encode().hex().ljust(64, "0")[:64],
            source=source,
            task_mode=mode,
            mode=mode,
        )
        for mode in ("formalized", "counterexample")
    )


def index_for(*entry_groups, commit: str = REPOSITORY_COMMIT) -> ConjectureIndex:
    entries = tuple(entry for group in entry_groups for entry in group)
    return ConjectureIndex.build(
        catalog_from_entries(repository_commit=commit, entries=entries)
    )


# --- the durability property ----------------------------------------------------------------


def test_the_slug_is_unchanged_by_a_pin_rotation():
    """The whole reason this module exists.

    A rotation moves `repository_commit`, which moves every `task_id` — twice over, since the
    commit is both a prefix and an input to the digest. A website URL cannot move with it.
    """
    before = index_for(conjecture_tasks("Erdos11.erdos_11", prefix="aa"))
    after = index_for(
        conjecture_tasks("Erdos11.erdos_11", prefix="bb", commit=OTHER_COMMIT),
        commit=OTHER_COMMIT,
    )

    assert [item.slug for item in before.all()] == ["erdos11-erdos-11"]
    assert [item.slug for item in after.all()] == [item.slug for item in before.all()]
    # ...while the task ids really did move, so the test is not vacuous.
    assert before.all()[0].task_ids != after.all()[0].task_ids


def test_the_slug_keeps_the_whole_theorem_path():
    """`task_generator.task_slug` keeps only the last two segments, which is safe inside a
    `task_id` because a digest disambiguates there. A bare slug has no digest, so dropping the
    namespace would collapse a variant onto anything ending in the same two segments.
    """
    index = index_for(
        conjecture_tasks("Erdos11.erdos_11", prefix="aa"),
        conjecture_tasks("Erdos11.erdos_11.variants.not_four_dvd", prefix="bb"),
    )
    assert sorted(item.slug for item in index.all()) == [
        "erdos11-erdos-11",
        "erdos11-erdos-11-variants-not-four-dvd",
    ]


def test_two_theorems_that_slugify_alike_fail_at_startup():
    """`slugify` is lossy: `_` and `.` both become `-`. Serving one conjecture at another's
    stable, citable URL is worse than refusing to boot, so this is a startup failure.
    """
    with pytest.raises(CatalogGroupingError, match="both produce the slug"):
        index_for(
            conjecture_tasks("Ambiguous.a_b", prefix="aa"),
            conjecture_tasks("Ambiguous.a.b", prefix="bb"),
        )


# --- grouping -------------------------------------------------------------------------------


def test_both_attack_directions_fold_into_one_conjecture():
    index = index_for(conjecture_tasks("Erdos11.erdos_11", prefix="aa"))

    assert len(index.all()) == 1
    item = index.all()[0]
    # `formalized` first, from PRODUCTION_TASK_MODES, not from pool walk order.
    assert item.task_modes == ("formalized", "counterexample")
    assert len(item.task_ids) == 2
    assert item.reward_target_id == reward_target_identity("Erdos11.erdos_11")


def test_a_group_whose_tasks_disagree_on_their_shared_facts_is_refused():
    """`tier` and `problem_id` are published once per conjecture, so they cannot be picked
    arbitrarily from whichever member task happened to sort first.
    """
    proof, counterexample = conjecture_tasks("Erdos11.erdos_11", prefix="aa")
    with pytest.raises(CatalogGroupingError, match="spans tiers"):
        index_for((proof, task_entry(**{**_kwargs(counterexample), "tier": "tier-2"})))


def _kwargs(entry) -> dict:
    """The `task_entry` arguments that reproduce an entry, so a test can vary one of them."""
    return {
        "task_id": entry.task_id,
        "digest": entry.task_bundle_sha256,
        "tier": entry.tier,
        "source": entry.source,
        "task_mode": entry.manifest.task_mode,
        "mode": entry.mode,
        "reward_target_id": entry.reward_target_id,
    }


def test_the_task_mode_facet_counts_a_conjecture_in_every_direction():
    """A folded list must not lose the fact that both directions exist — a solver filtering for
    `counterexample` work is asking a real question.
    """
    index = index_for(
        conjecture_tasks("Erdos11.erdos_11", prefix="aa"),
        conjecture_tasks("Erdos12.erdos_12", prefix="bb"),
    )
    counts = {item.value: item.count for item in tally(index.all(), FACET_TASK_MODE)}
    assert counts == {"formalized": 2, "counterexample": 2}

    page = query(index, ConjectureFilters(task_mode=("counterexample",)), limit=10, offset=0)
    assert page.total == 2


def test_the_free_text_filter_finds_a_conjecture_by_a_task_id():
    """A reader pasting an identifier out of a report or a bundle should land on its conjecture."""
    index = index_for(conjecture_tasks("Erdos11.erdos_11", prefix="aa"))
    needle = index.all()[0].task_ids[0]
    page = query(index, ConjectureFilters(query=needle), limit=10, offset=0)
    assert [item.slug for item in page.items] == ["erdos11-erdos-11"]


# --- legacy task-id URLs --------------------------------------------------------------------


def test_a_task_id_from_the_current_pool_resolves_to_its_slug():
    index = index_for(conjecture_tasks("Erdos11.erdos_11", prefix="aa"))
    for task_id in index.all()[0].task_ids:
        assert index.resolve_legacy(task_id) == "erdos11-erdos-11"


def test_a_task_id_from_an_earlier_rotation_still_resolves():
    """The case that matters for a link already in the wild. The task id names a commit this
    pool has never seen, so it cannot be an exact lookup — it is matched on the theorem fragment
    inside it, which does not depend on the commit.
    """
    index = index_for(conjecture_tasks("Erdos11.erdos_11", prefix="aa"))
    stale = build_task_id("f" * 40, "Erdos11.erdos_11", "formalized", 1)

    assert stale not in index.all()[0].task_ids
    assert index.resolve_legacy(stale) == "erdos11-erdos-11"


def test_an_ambiguous_legacy_fragment_is_not_guessed_at():
    """The embedded fragment keeps only two theorem segments, so it can name more than one
    conjecture. A wrong redirect is worse than a dead link, so this returns nothing.
    """
    index = index_for(
        conjecture_tasks("First.Shared.erdos_11", prefix="aa"),
        conjecture_tasks("Second.Shared.erdos_11", prefix="bb"),
    )
    # Distinct conjectures with distinct slugs, so grouping is happy...
    assert sorted(item.slug for item in index.all()) == [
        "first-shared-erdos-11",
        "second-shared-erdos-11",
    ]
    # ...but `task_slug` keeps only two segments, so both produce the same legacy fragment and
    # a task id built from either one names both.
    stale = build_task_id("f" * 40, "First.Shared.erdos_11", "formalized", 1)
    assert slugs.legacy_theorem_slug(stale) == "shared-erdos-11"
    assert slugs.matches_legacy_slug("Second.Shared.erdos_11", "shared-erdos-11")
    assert index.resolve_legacy(stale) is None


def test_a_string_that_is_not_a_task_id_resolves_to_nothing():
    index = index_for(conjecture_tasks("Erdos11.erdos_11", prefix="aa"))
    for candidate in ("erdos11-erdos-99", "fc-nothex01-x-0123456789-formalized-v1", "fc"):
        assert index.resolve_legacy(candidate) is None


def test_the_legacy_pattern_does_not_swallow_the_trailing_digest():
    """The embedded fragment may contain hex characters, so a greedy match has to backtrack to
    the *last* ten-hex group rather than the first thing that looks like one.
    """
    task_id = build_task_id(REPOSITORY_COMMIT, "Erdos11.abcdef01", "formalized", 1)
    assert slugs.legacy_theorem_slug(task_id) == "erdos11-abcdef01"
