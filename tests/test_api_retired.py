"""What a retired conjecture serves over HTTP.

Retiring a target deletes its bundles, which is what closes it to submission — and, before this
existed, also 404'd its page. That page is where the results and attribution earned against the
target live, so a solver who proved something had their evidence deleted as a consequence of
proving it. These tests fix the shape of the fix:

* the page renders, with the statement recovered from the deleted bundle;
* nothing on it can be submitted against — no machine contract, no bounty amount;
* activity still resolves, because that is the part worth keeping;
* the live pool is untouched.

Needs a real PostgreSQL server for the attempt counters:

    docker compose -f docker-compose.pytest-db.yml up -d
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("fastapi", reason="submission API tests need the service extra")
pytest.importorskip("sqlalchemy", reason="submission API tests need the db extra")
pytest.importorskip("httpx", reason="submission API tests need the service extra")
pytest.importorskip("psycopg", reason="submission API tests need the db extra")

from conftest import declaration
from conftest_api import harness, postgres_dsn, task_entry

from submission_api.retired import RetiredConjecture, RetiredIndex, RetiredTask
from submission_api.slugs import slug_for

pytestmark = pytest.mark.skipif(
    postgres_dsn() is None,
    reason="no database: run `docker compose -f docker-compose.pytest-db.yml up -d`",
)

THEOREM = "Erdos10.erdos_10.variants.grechuk"
REWARD_TARGET = f"fc-target:{THEOREM}"
# The URL a reader reaches from a published result. Spelled out because it is a public contract.
RETIRED_SLUG = "erdos10-erdos-10-variants-grechuk"
DECISION_URL = "https://example.invalid/decisions/2026-08-06-erdos-10-grechuk.md"
CHALLENGE = "-- recovered from the deleted bundle\n"

LIVE_THEOREM = "Erdos11.erdos_11"
LIVE_SLUG = "erdos11-erdos-11"


def run(coroutine):
    return asyncio.run(coroutine)


async def _get(kit, path: str, **params):
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=kit.app, raise_app_exceptions=True),
        base_url="http://validator.test",
    ) as client:
        return await client.get(path, params=params or None)


def retired_index() -> RetiredIndex:
    item = RetiredConjecture(
        slug=slug_for(REWARD_TARGET),
        problem_id="fixture-problem",
        reward_target_id=REWARD_TARGET,
        tier="tier-1",
        retired_on="2026-08-06",
        reason_code="SOLVED + NOT_OPEN",
        reason="SOLVED + NOT_OPEN (settled by a verified submission)",
        decision_url=DECISION_URL,
        recovered_from_commit="c" * 40,
        source=declaration(theorem=THEOREM),
        tasks=(
            RetiredTask(
                task_id="retired-formalized",
                task_mode="formalized",
                task_bundle_sha256="sha256:" + "a" * 64,
                target_type_sha256="sha256:" + "b" * 64,
                challenge_lean=CHALLENGE,
            ),
        ),
    )
    return RetiredIndex(
        by_slug={item.slug: item},
        slug_by_task_id={task.task_id: item.slug for task in item.tasks},
    )


def live_entries():
    source = declaration(theorem=LIVE_THEOREM)
    return tuple(
        task_entry(
            task_id=f"live-{mode}",
            digest="sha256:" + f"{mode[:4]}".encode().hex().ljust(64, "0")[:64],
            source=source,
            task_mode=mode,
            mode=mode,
        )
        for mode in ("formalized", "counterexample")
    )


def kit():
    return harness(entries=live_entries(), retired=retired_index())


# --- the page comes back ----------------------------------------------------------------------


def test_a_retired_conjecture_still_has_a_page():
    """The bug this closes: this URL used to 404 the moment the target was retired."""
    response = run(_get(kit(), f"/v1/catalog/conjectures/{RETIRED_SLUG}"))

    assert response.status_code == 200
    body = response.json()
    assert body["slug"] == RETIRED_SLUG
    assert body["source_theorem"] == THEOREM
    assert body["reward_target_id"] == REWARD_TARGET


def test_the_statement_served_is_the_one_recovered_from_the_deleted_bundle():
    """Everything a reader came for survives the bundle it lived in."""
    body = run(_get(kit(), f"/v1/catalog/conjectures/{RETIRED_SLUG}")).json()

    assert body["statement"] == declaration(theorem=THEOREM).type_pretty
    assert body["classification"] == "DIRECT_PROP"
    assert body["ams_subjects"] == [5]
    assert [task["challenge_lean"] for task in body["tasks"]] == [CHALLENGE]


def test_the_retirement_is_explained_and_linked():
    body = run(_get(kit(), f"/v1/catalog/conjectures/{RETIRED_SLUG}")).json()

    assert body["retirement"] == {
        "retired_on": "2026-08-06",
        "reason_code": "SOLVED + NOT_OPEN",
        "reason": "SOLVED + NOT_OPEN (settled by a verified submission)",
        "decision_url": DECISION_URL,
        "recovered_from_commit": "c" * 40,
    }


# --- and nothing on it can be submitted against -----------------------------------------------


def test_a_retired_conjecture_publishes_no_machine_contract():
    """There is no bundle to compile against and no digest to commit to.

    A contract here would be fabricated, and a solver could build a bundle from it that the
    submission path would then reject — after taking payment for the attempt.
    """
    body = run(_get(kit(), f"/v1/catalog/conjectures/{RETIRED_SLUG}")).json()

    assert [task["machine_contract"] for task in body["tasks"]] == [None]


def test_the_bounty_is_withdrawn_and_quotes_no_amount():
    """`WITHDRAWN` is the signal for a client that reads only the bounty block.

    The amount is null rather than stale: a number next to a closed target is the one field on
    this page that could send someone to do work that cannot be paid.
    """
    body = run(_get(kit(), f"/v1/catalog/conjectures/{RETIRED_SLUG}")).json()

    assert body["bounty"]["reason"] == "WITHDRAWN"
    assert body["bounty"]["available"] is False
    assert body["bounty"]["amount_rao"] is None
    assert body["bounty"]["amount_usd"] is None


def test_a_retired_conjecture_is_not_in_the_list_or_the_counts():
    """The list answers "what can I work on", so a closed target must not appear in it.

    Also guards the facets and `meta`: counting a retired target would overstate the pool.
    """
    listed = run(_get(kit(), "/v1/catalog/conjectures", limit=100)).json()
    meta = run(_get(kit(), "/v1/catalog/meta")).json()

    assert [item["slug"] for item in listed["items"]] == [LIVE_SLUG]
    assert listed["total"] == 1
    assert meta["conjectures"] == 1


# --- activity, which is the point ---------------------------------------------------------------


def test_activity_still_resolves_for_a_retired_conjecture():
    """The attempts and solvers recorded against the target do not stop being real."""
    response = run(_get(kit(), f"/v1/catalog/conjectures/{RETIRED_SLUG}/activity"))

    assert response.status_code == 200
    assert response.json()["slug"] == RETIRED_SLUG


def test_a_task_id_from_a_deleted_bundle_redirects_to_the_page():
    """Every report and result already published for this target names one of these ids."""
    response = run(
        _get(kit(), "/v1/catalog/conjectures/retired-formalized")
    )

    assert response.status_code == 301
    assert response.headers["location"] == (
        f"/v1/catalog/conjectures/{RETIRED_SLUG}"
    )


# --- the live pool is untouched -----------------------------------------------------------------


def test_a_live_conjecture_is_unaffected():
    body = run(_get(kit(), f"/v1/catalog/conjectures/{LIVE_SLUG}")).json()

    assert body["retirement"] is None
    assert body["bounty"]["reason"] != "WITHDRAWN"
    assert all(task["machine_contract"] is not None for task in body["tasks"])


def test_an_unknown_slug_is_still_a_404():
    """Falling through to the retired index must not turn a dead URL into a page."""
    response = run(_get(kit(), "/v1/catalog/conjectures/erdos999-erdos-999"))

    assert response.status_code == 404
