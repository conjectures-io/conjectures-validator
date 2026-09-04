"""`/v1/contributions`: the public read surface over the mirrored contribution corpus.

Driven through a `StaticContributionMirror`, so no test here reaches github.com and every one of
them is deterministic. The fetching side is `tests/test_contributions.py`.

The properties worth guarding are the ones a listing endpoint gets wrong quietly: an unread corpus
published as an empty one, a target reachable under one of its four names but not the others, a
filter that silently matches an absent value, and a page whose `total` disagrees with what a
caller can actually reach by paging.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("fastapi", reason="submission API tests need the service extra")
pytest.importorskip("sqlalchemy", reason="submission API tests need the db extra")
pytest.importorskip("httpx", reason="submission API tests need the service extra")

from conftest_api import harness, postgres_dsn, task_entry
from test_contributions import (
    AUTHOR,
    COLDKEY,
    COMMIT,
    HOTKEY,
    OTHER_AUTHOR,
    PAGE,
    REPOSITORY,
    entry,
    index,
    snapshot,
)

from submission_api.contributions import ContributionSnapshot, parse_empty_target, parse_target
from submission_api.github import StaticContributionMirror
from verifier.task_pool import reward_target_identity

needs_db = pytest.mark.skipif(
    postgres_dsn() is None,
    reason="no database: run `docker compose -f docker-compose.pytest-db.yml up -d`",
)

pytestmark = needs_db


def run(coroutine):
    return asyncio.run(coroutine)


async def _client(kit):
    from httpx import ASGITransport, AsyncClient

    return AsyncClient(
        transport=ASGITransport(app=kit.app, raise_app_exceptions=True),
        base_url="http://validator.test",
    )


def corpus() -> ContributionSnapshot:
    """Three targets: two with work on them, one empty. Two author keys sharing a coldkey."""
    return snapshot(
        parse_target(
            index(
                contributions=(
                    entry(
                        contribution_id="a" * 64,
                        author=AUTHOR,
                        added="2026-09-01",
                        title="Extremal witness and monotonicity API",
                        declarations=("Contribution.Erdos535BasicAPI.f_le",),
                    ),
                    entry(
                        contribution_id="b" * 64,
                        author=OTHER_AUTHOR,
                        added="2026-08-01",
                        kind="idea",
                        mode="counterexample",
                        title="A Sidon-set reduction",
                        declarations=("Contribution.Erdos535Sidon.reduce",),
                        coldkey=None,
                        hotkey=None,
                    ),
                )
            ),
            directory="erdos-535",
        ),
        parse_target(
            index(
                target="erdos-100",
                reward_target_id="fc-target:Erdos100.erdos_100",
                contributions=(
                    entry(
                        contribution_id="c" * 64,
                        author=AUTHOR,
                        added="2026-07-01",
                        title="Divisor bound for a block of primes",
                    ),
                ),
            ),
            directory="erdos-100",
        ),
        parse_empty_target(
            PAGE.format(target="erdos-1049", theorem="Erdos1049.erdos_1049"),
            directory="erdos-1049",
        ),
    )


def kit(*, contributions=None, entries=None, **overrides):
    return harness(
        contributions=(
            StaticContributionMirror(corpus()) if contributions is None else contributions
        ),
        entries=entries,
        **overrides,
    )


async def _get(kit_, path, **kwargs):
    client = await _client(kit_)
    async with client:
        return await client.get(path, **kwargs)


def get(path, *, kit_=None, **kwargs):
    subject = kit_ or kit()
    return run(_get(subject, path, **kwargs))


# --- Availability ------------------------------------------------------------------------------


def test_an_unread_corpus_is_a_503_rather_than_an_empty_listing():
    """The whole reason this endpoint group has a failure mode at all.

    A deployment that has not fetched the corpus knows nothing about it. Publishing `items: []`
    would state that nothing has been contributed, which is a claim about the repository rather
    than about this process.
    """
    subject = harness()  # the unavailable mirror, which is the suite-wide default

    for path in ("/v1/contributions", "/v1/contributions/targets", "/v1/contributions/meta"):
        response = get(path, kit_=subject)
        assert response.status_code == 503, path
        assert response.json()["reason_code"] == "CONTRIBUTIONS_UNAVAILABLE"
        assert response.json()["retry_after_seconds"] > 0


def test_meta_publishes_the_provenance_and_the_freshness_of_what_is_served():
    body = get("/v1/contributions/meta").json()

    assert body["repository"] == REPOSITORY
    assert body["repository_url"] == f"https://github.com/{REPOSITORY}"
    assert body["head_commit"] == COMMIT
    assert body["targets"] == 3
    assert body["contributions"] == 3
    assert body["authors"] == 2
    assert body["unreadable_targets"] == 0
    # The fixture snapshot is timestamped in the past, so it is correctly reported as stale.
    assert body["stale"] is True
    assert body["age_seconds"] > 0


def test_unreadable_targets_are_counted_so_a_short_listing_is_never_read_as_a_complete_one():
    built = snapshot(
        parse_target(index(), directory="erdos-535"),
        unreadable=(("erdos-100", "unsupported index schema_version 99"),),
    )
    body = get("/v1/contributions/meta", kit_=kit(contributions=StaticContributionMirror(built))).json()

    assert body["unreadable_targets"] == 1


# --- Listing -----------------------------------------------------------------------------------


def test_the_listing_is_newest_first_and_reports_a_total_a_caller_can_page_to():
    body = get("/v1/contributions").json()

    assert body["total"] == 3
    assert [item["added"] for item in body["items"]] == [
        "2026-09-01",
        "2026-08-01",
        "2026-07-01",
    ]
    assert body["items"][0]["short_id"] == "a" * 12


def test_a_page_is_a_window_on_the_same_total():
    first = get("/v1/contributions?limit=2").json()
    second = get("/v1/contributions?limit=2&offset=2").json()

    assert first["total"] == second["total"] == 3
    assert len(first["items"]) == 2
    assert len(second["items"]) == 1
    assert {item["contribution_id"] for item in first["items"]}.isdisjoint(
        item["contribution_id"] for item in second["items"]
    )


def test_a_contribution_carries_both_naming_schemes_and_the_join_between_them():
    item = get("/v1/contributions").json()["items"][0]

    assert item["target"] == "erdos-535"
    assert item["reward_target_id"] == "fc-target:Erdos535.erdos_535"
    assert item["conjecture_slug"] == "erdos535-erdos-535"
    assert item["html_url"].startswith(f"https://github.com/{REPOSITORY}/tree/main/contributions/")


def test_in_pool_is_answered_by_this_validators_live_catalog_not_by_the_mirror():
    """The corpus pins its own revision of the pool, so it cannot be the authority on this."""
    offered = task_entry(reward_target_id=reward_target_identity("Erdos535.erdos_535"))
    with_pool = kit(entries=(offered,))

    body = get("/v1/contributions?target=erdos-535", kit_=with_pool).json()
    assert all(item["in_pool"] for item in body["items"])

    # The default fixture pool offers a different theorem entirely.
    body = get("/v1/contributions?target=erdos-535").json()
    assert not any(item["in_pool"] for item in body["items"])


# --- Filters -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query, expected",
    [
        ("target=erdos-535", 2),
        ("target=erdos-535&target=erdos-100", 3),
        ("conjecture=erdos535-erdos-535", 2),
        ("kind=idea", 1),
        ("mode=counterexample", 1),
        (f"author={AUTHOR[:10]}", 2),
        (f"hotkey={HOTKEY[:8]}", 2),
        (f"coldkey={COLDKEY}", 2),
        ("declares=sidon", 1),
        ("q=monotonicity", 1),
        ("since=2026-08-15", 1),
        ("until=2026-08-01", 2),
        ("rewarded=false", 1),
        ("target=erdos-535&kind=idea", 1),
    ],
)
def test_filters_and_together_while_a_repeated_one_ors_its_values(query, expected):
    assert get(f"/v1/contributions?{query}").json()["total"] == expected


def test_a_filter_never_matches_an_absent_value():
    """The contribution that opted out of a reward has no coldkey to test, so it is not returned
    by a coldkey filter — rather than being returned because the prefix test vacuously passed."""
    body = get(f"/v1/contributions?coldkey={COLDKEY}").json()

    assert body["total"] == 2
    assert all(item["coldkey"] == COLDKEY for item in body["items"])


def test_the_conjecture_filter_does_not_accept_the_corpuss_own_target_slug():
    """The two naming schemes are different strings for the same theorem, and conflating them is
    how a filter starts working by accident on the targets where they happen to look alike."""
    assert get("/v1/contributions?conjecture=erdos-535").json()["total"] == 0
    assert get("/v1/contributions?target=erdos-535").json()["total"] == 2


@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        "limit=1000",
        "offset=-1",
        "sort=coldkey",
        "order=sideways",
        "q=" + "x" * 200,
        "target=" + "y" * 300,
    ],
)
def test_an_out_of_bounds_query_is_refused_rather_than_answered(query):
    """`400`, the status this API gives every request-validation failure."""
    assert get(f"/v1/contributions?{query}").status_code == 400


def test_sorting_is_stable_across_repeated_reads():
    first = get("/v1/contributions?sort=title&order=asc").json()
    second = get("/v1/contributions?sort=title&order=asc").json()

    assert first == second
    assert first["items"][0]["title"] == "A Sidon-set reduction"


# --- Targets -----------------------------------------------------------------------------------


def test_the_target_listing_covers_empty_targets_so_what_needs_work_is_answerable():
    body = get("/v1/contributions/targets").json()

    assert body["total"] == 3
    assert get("/v1/contributions/targets?empty=true").json()["total"] == 1
    assert get("/v1/contributions/targets?empty=false").json()["total"] == 2


def test_an_empty_target_still_carries_the_identity_that_joins_it_to_a_conjecture():
    body = get("/v1/contributions/targets?empty=true").json()
    row = body["items"][0]

    assert row["target"] == "erdos-1049"
    assert row["conjecture_slug"] == "erdos1049-erdos-1049"
    assert row["contributions"] == 0
    assert row["first_added"] is None


@pytest.mark.parametrize(
    "name",
    [
        "erdos-535",
        "erdos535-erdos-535",
        "fc-target:Erdos535.erdos_535",
        "fc-379fc029-erdos-535-problem",
    ],
)
def test_one_target_is_reachable_under_every_name_it_has(name):
    body = get(f"/v1/contributions/targets/{name}").json()

    assert body["target"]["target"] == "erdos-535"
    assert len(body["contributions"]) == 2


def test_a_target_the_corpus_does_not_track_is_a_404():
    assert get("/v1/contributions/targets/erdos-99999").status_code == 404


def test_a_target_row_counts_from_the_rows_it_lists():
    body = get("/v1/contributions/targets/erdos-535").json()

    assert body["target"]["contributions"] == len(body["contributions"])
    assert body["target"]["kinds"] == ["idea", "lemma"]
    assert body["target"]["modes"] == ["counterexample", "formalized"]


def test_targets_sort_by_how_much_has_accumulated():
    body = get("/v1/contributions/targets?sort=contributions&order=desc").json()

    assert [row["target"] for row in body["items"]] == [
        "erdos-535",
        "erdos-100",
        "erdos-1049",
    ]


# --- Authors and pending -----------------------------------------------------------------------


def test_the_author_grain_reports_a_coldkey_two_signing_keys_share():
    body = get("/v1/contributions/authors").json()

    assert body["total"] == 2
    top = body["items"][0]
    assert top["author"] == AUTHOR
    assert top["contributions"] == 2
    assert top["targets"] == ["erdos-100", "erdos-535"]
    # Nobody shares here: the second author opted out of a reward destination altogether.
    assert get("/v1/contributions/authors?shared_coldkey=false").json()["total"] == 2


def test_two_signing_keys_paying_into_one_coldkey_are_reported_as_sharing_it():
    """Not called a Sybil — several keys held by one contributor look identical — but published,
    because it is the shape one takes and a reader should not have to join two listings to see it."""
    shared = snapshot(
        parse_target(
            index(
                contributions=(
                    entry(contribution_id="a" * 64, author=AUTHOR),
                    entry(contribution_id="b" * 64, author=OTHER_AUTHOR),
                )
            ),
            directory="erdos-535",
        )
    )
    body = get(
        "/v1/contributions/authors?shared_coldkey=true",
        kit_=kit(contributions=StaticContributionMirror(shared)),
    ).json()

    assert body["total"] == 2


def test_an_author_is_reachable_by_a_key_prefix():
    body = get(f"/v1/contributions/authors?author={AUTHOR[:8]}").json()

    assert body["total"] == 1
    assert body["items"][0]["author"] == AUTHOR


def test_pending_pull_requests_are_a_separate_listing_from_accepted_work():
    assert get("/v1/contributions/pending").json()["total"] == 0
    assert get("/v1/contributions").json()["total"] == 3


# --- One contribution --------------------------------------------------------------------------


def test_one_contribution_is_readable_by_full_id_and_by_short_id():
    full = get("/v1/contributions/" + "a" * 64).json()
    short = get("/v1/contributions/" + "a" * 12).json()

    assert full == short
    assert full["contribution_id"] == "a" * 64


@pytest.mark.parametrize(
    "identifier, status",
    [
        pytest.param("d" * 64, 404, id="well-formed but unknown"),
        pytest.param("aaaa", 400, id="too short to be an identifier"),
        pytest.param("A" * 64, 400, id="not lowercase hex"),
    ],
)
def test_a_contribution_id_that_names_nothing_is_refused_or_not_found(identifier, status):
    assert get(f"/v1/contributions/{identifier}").status_code == status


def test_the_fixed_segments_are_not_shadowed_by_the_id_route():
    """`/targets`, `/authors`, `/pending` and `/meta` are not hex, so they cannot be read as ids."""
    for path in ("/targets", "/authors", "/pending", "/meta"):
        assert get(f"/v1/contributions{path}").status_code == 200


# --- Caching -----------------------------------------------------------------------------------


def test_every_listing_carries_a_strong_etag_and_answers_304_to_a_caller_that_holds_it():
    subject = kit()
    for path in (
        "/v1/contributions",
        "/v1/contributions/meta",
        "/v1/contributions/targets",
        "/v1/contributions/targets/erdos-535",
        "/v1/contributions/authors",
        "/v1/contributions/" + "a" * 64,
    ):
        first = get(path, kit_=subject)
        assert first.status_code == 200, path
        etag = first.headers["etag"]
        assert etag.startswith('"')

        again = get(path, kit_=subject, headers={"If-None-Match": etag})
        assert again.status_code == 304, path
        assert again.headers["etag"] == etag
        assert again.content == b""


def test_a_different_filter_is_a_different_entity():
    subject = kit()
    everything = get("/v1/contributions", kit_=subject).headers["etag"]
    filtered = get("/v1/contributions?kind=idea", kit_=subject).headers["etag"]

    assert everything != filtered


def test_the_surface_is_unauthenticated():
    """No credential is sent and none is required — the same posture as the catalog."""
    response = get("/v1/contributions")

    assert response.status_code == 200
    assert "set-cookie" not in response.headers
