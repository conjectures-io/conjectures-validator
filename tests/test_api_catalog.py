"""The public conjecture catalog: list, detail, meta, and anonymised activity.

The database is only touched for the attempt counters, so most of this runs against a synthetic
in-memory catalog. The tests that assert on counters need a real PostgreSQL server and are
skipped without one:

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
from conftest_api import (
    CHALLENGE_LEAN,
    HOTKEY,
    OTHER_HOTKEY,
    REPOSITORY_COMMIT,
    distinct_bundle,
    harness,
    new_key,
    postgres_dsn,
    submission_headers,
    task_entry,
)

from submission_api.routers.catalog import PSEUDONYM_LENGTH
from verifier.models import Classification
from verifier.task_generator import task_id as build_task_id

pytestmark = pytest.mark.skipif(
    postgres_dsn() is None,
    reason="no database: run `docker compose -f docker-compose.pytest-db.yml up -d`",
)

# The stable slugs of the three fixture conjectures, derived from their theorems rather than from
# their task ids. Spelled out rather than computed, so a change to slug derivation shows up here
# as a diff a reviewer has to agree with — these strings are a public contract.
OPEN_DIRECT = "erdos11-erdos-11"
SOLVED_DIRECT = "erdos12-erdos-12"
OPEN_ANSWER = "erdos13-erdos-13"


def run(coroutine):
    return asyncio.run(coroutine)


async def _client(kit):
    from httpx import ASGITransport, AsyncClient

    return AsyncClient(
        transport=ASGITransport(app=kit.app, raise_app_exceptions=True),
        base_url="http://validator.test",
    )


async def _get(kit, path: str, **params):
    async with await _client(kit) as client:
        return await client.get(path, params=params or None)


def pool():
    """A three-conjecture pool with genuinely different facet values.

    Built by hand rather than loaded from the checked-out task repository: the facet arithmetic
    is what is under test, and it needs values that differ along each axis. `category` varies on
    the source declaration and `classification` on the manifest, because that is where the
    catalog reads each of them from.
    """
    return (
        task_entry(
            task_id="open-direct",
            reward_target_id="fc-target:Erdos11.erdos_11",
            classification=Classification.DIRECT_PROP,
            source=declaration(
                theorem="Erdos11.erdos_11",
                classification=Classification.DIRECT_PROP,
                category="research open",
            ),
        ),
        task_entry(
            task_id="solved-direct",
            reward_target_id="fc-target:Erdos12.erdos_12",
            classification=Classification.DIRECT_PROP,
            source=declaration(
                theorem="Erdos12.erdos_12",
                classification=Classification.DIRECT_PROP,
                category="research solved",
            ),
        ),
        task_entry(
            task_id="open-answer",
            reward_target_id="fc-target:Erdos13.erdos_13",
            classification=Classification.NAT_ANSWER,
            source=declaration(
                theorem="Erdos13.erdos_13",
                classification=Classification.NAT_ANSWER,
                category="research open",
            ),
        ),
    )


# --- listing -----------------------------------------------------------------------------


def test_the_list_publishes_every_conjecture_with_its_facets():
    async def scenario():
        kit = await harness(entries=pool()).setup()
        try:
            response = await _get(kit, "/v1/catalog/conjectures")
            assert response.status_code == 200, response.text
            body = response.json()

            assert body["total"] == 3
            assert body["repository_commit"] == REPOSITORY_COMMIT
            assert [item["slug"] for item in body["items"]] == [
                OPEN_DIRECT,
                SOLVED_DIRECT,
                OPEN_ANSWER,
            ]

            facets = {facet["field"]: facet["values"] for facet in body["facets"]}
            assert {item["value"]: item["count"] for item in facets["category"]} == {
                "research open": 2,
                "research solved": 1,
            }
            assert {item["value"]: item["count"] for item in facets["tier"]} == {
                "tier-1": 3
            }

            # A public read is shared-cacheable: nothing here varies by caller.
            assert response.headers["cache-control"] == "public, max-age=60"
        finally:
            await kit.teardown()

    run(scenario())


def test_a_selected_facet_still_reports_the_alternatives():
    """The point of a facet count is that a reader can see what switching would give them.

    Counting each facet over the results matching every *other* filter is what keeps
    `category` from collapsing to one row once a category is selected. Without it the UI can
    filter in but never out.
    """

    async def scenario():
        kit = await harness(entries=pool()).setup()
        try:
            response = await _get(kit, "/v1/catalog/conjectures", category="research open")
            body = response.json()

            assert body["total"] == 2
            facets = {facet["field"]: facet["values"] for facet in body["facets"]}
            # The selected facet keeps both values and their unfiltered counts...
            assert {item["value"]: item["count"] for item in facets["category"]} == {
                "research open": 2,
                "research solved": 1,
            }
            # ...while every other facet is narrowed by the selection.
            assert {
                item["value"]: item["count"] for item in facets["classification"]
            } == {"DIRECT_PROP": 1, "NAT_ANSWER": 1}
        finally:
            await kit.teardown()

    run(scenario())


def test_filters_combine_and_the_open_flag_is_its_own_axis():
    async def scenario():
        kit = await harness(entries=pool()).setup()
        try:
            both = await _get(
                kit, "/v1/catalog/conjectures", is_open="true", classification="DIRECT_PROP"
            )
            assert [item["slug"] for item in both.json()["items"]] == [OPEN_DIRECT]

            closed = await _get(kit, "/v1/catalog/conjectures", is_open="false")
            assert [item["slug"] for item in closed.json()["items"]] == [SOLVED_DIRECT]
            assert closed.json()["items"][0]["is_open"] is False
        finally:
            await kit.teardown()

    run(scenario())


def test_the_free_text_filter_is_a_substring_test_over_published_fields():
    async def scenario():
        kit = await harness(entries=pool()).setup()
        try:
            hit = await _get(kit, "/v1/catalog/conjectures", q="ERDOS13")
            assert [item["slug"] for item in hit.json()["items"]] == [OPEN_ANSWER]

            # Not a pattern. A regular expression is data, not syntax, so it matches nothing
            # rather than being compiled — an anonymous caller cannot spend CPU on backtracking.
            miss = await _get(kit, "/v1/catalog/conjectures", q="(a+)+$")
            assert miss.json()["total"] == 0
        finally:
            await kit.teardown()

    run(scenario())


def test_the_page_size_is_capped():
    async def scenario():
        kit = await harness(entries=pool()).setup()
        try:
            async with await _client(kit) as client:
                too_big = await client.get("/v1/catalog/conjectures", params={"limit": 500})
                assert too_big.status_code == 400
                assert too_big.json()["reason_code"] == "MALFORMED_REQUEST"

                paged = await client.get(
                    "/v1/catalog/conjectures", params={"limit": 2, "offset": 2}
                )
                assert paged.json()["total"] == 3
                assert len(paged.json()["items"]) == 1
        finally:
            await kit.teardown()

    run(scenario())


# --- detail ------------------------------------------------------------------------------


def test_the_detail_serves_the_audited_challenge_and_the_machine_contract():
    async def scenario():
        kit = await harness(entries=pool()).setup()
        try:
            response = await _get(kit, f"/v1/catalog/conjectures/{OPEN_DIRECT}")
            assert response.status_code == 200, response.text
            body = response.json()

            assert body["slug"] == OPEN_DIRECT
            assert body["title"] == "Erdos11.erdos_11"
            assert body["is_open"] is True
            # The stable identity the slug comes from, and the per-revision one that moves.
            assert body["reward_target_id"] == "fc-target:Erdos11.erdos_11"
            assert body["problem_id"]

            # The Lean source and the contract belong to a *task*, one per attack direction, so
            # they live under `tasks` rather than on the conjecture.
            assert [task["task_id"] for task in body["tasks"]] == ["open-direct"]
            task = body["tasks"][0]
            # The exact bytes hashed into the published commitment, not a re-read from disk.
            assert task["challenge_lean"] == CHALLENGE_LEAN

            contract = task["machine_contract"]
            assert contract["reward_target_id"] == "fc-target:Erdos11.erdos_11"
            assert contract["task_bundle_sha256"] == task["task_bundle_sha256"]
            assert contract["bundle_format"] == "conjectures-submission/v1"
            assert contract["target_theorem"]
            assert contract["permitted_axioms"]
            assert contract["max_bundle_bytes"] == 2 * 1024 * 1024

            # The pin set a reader needs to reproduce the statement.
            components = {pin["component"] for pin in body["pins"]}
            assert {"formal_conjectures", "mathlib", "lean"} <= components
            assert body["submission_price_rao"] == 500_000_000
        finally:
            await kit.teardown()

    run(scenario())


def test_a_markdown_reference_is_split_into_a_label_and_a_url():
    async def scenario():
        source = declaration(theorem="Erdos11.erdos_11")
        source = type(source)(
            **{
                **{
                    field: getattr(source, field)
                    for field in source.__dataclass_fields__
                },
                "references": (
                    "[erdosproblems.com/11](https://www.erdosproblems.com/11)",
                    "Erdős, 1950",
                ),
            }
        )
        kit = await harness(entries=(task_entry(task_id="cited", source=source),)).setup()
        try:
            body = (await _get(kit, f"/v1/catalog/conjectures/{OPEN_DIRECT}")).json()
            assert body["references"] == [
                {
                    "label": "erdosproblems.com/11",
                    "url": "https://www.erdosproblems.com/11",
                },
                # A reference that is not a link still arrives usable rather than dropped.
                {"label": "Erdős, 1950", "url": None},
            ]
        finally:
            await kit.teardown()

    run(scenario())


def test_both_attack_directions_appear_on_one_conjecture_page():
    """A conjecture is issued as two tasks. The website shows one page with two directions on it,
    not two pages that look like duplicates.
    """

    async def scenario():
        source = declaration(
            theorem="Erdos11.erdos_11",
            classification=Classification.DIRECT_PROP,
            category="research open",
        )
        entries = (
            task_entry(task_id="prove-it", source=source, task_mode="formalized"),
            task_entry(
                task_id="refute-it",
                digest="sha256:" + "cd" * 32,
                source=source,
                task_mode="counterexample",
                mode="counterexample",
            ),
        )
        kit = await harness(entries=entries).setup()
        try:
            listing = (await _get(kit, "/v1/catalog/conjectures")).json()
            assert listing["total"] == 1
            item = listing["items"][0]
            assert item["slug"] == OPEN_DIRECT
            assert item["task_modes"] == ["formalized", "counterexample"]
            assert [task["task_id"] for task in item["tasks"]] == ["prove-it", "refute-it"]

            # Both task ids resolve to the same page, because they are the same conjecture.
            for task_id in ("prove-it", "refute-it"):
                moved = await _get(kit, f"/v1/catalog/conjectures/{task_id}")
                assert moved.status_code == 301, moved.text
                assert moved.headers["location"] == f"/v1/catalog/conjectures/{OPEN_DIRECT}"

            detail = (await _get(kit, f"/v1/catalog/conjectures/{OPEN_DIRECT}")).json()
            assert len(detail["tasks"]) == 2
            assert {task["task_mode"] for task in detail["tasks"]} == {
                "formalized",
                "counterexample",
            }
            # `meta` counts conjectures, so two tasks are one conjecture here.
            assert (await _get(kit, "/v1/catalog/meta")).json()["conjectures"] == 1
        finally:
            await kit.teardown()

    run(scenario())


def test_a_task_id_url_from_an_earlier_pin_is_redirected_not_404ed():
    """The cutover case. A link built from a task id — either one this API published before slugs
    existed, or one copied out of a bundle — must survive, including after the rotation that
    retires that task id.
    """

    async def scenario():
        kit = await harness(entries=pool()).setup()
        try:
            # A task id naming a commit this pool has never seen.
            stale = build_task_id("f" * 40, "Erdos11.erdos_11", "formalized", 1)
            moved = await _get(kit, f"/v1/catalog/conjectures/{stale}")
            assert moved.status_code == 301, moved.text
            assert moved.headers["location"] == f"/v1/catalog/conjectures/{OPEN_DIRECT}"

            # The suffix is preserved, so a bookmarked activity URL lands on activity.
            moved = await _get(kit, f"/v1/catalog/conjectures/{stale}/activity")
            assert moved.status_code == 301
            assert (
                moved.headers["location"]
                == f"/v1/catalog/conjectures/{OPEN_DIRECT}/activity"
            )

            # A task id for a theorem this pool does not carry is still a 404, not a guess.
            absent = build_task_id("f" * 40, "Erdos99.erdos_99", "formalized", 1)
            assert (await _get(kit, f"/v1/catalog/conjectures/{absent}")).status_code == 404
        finally:
            await kit.teardown()

    run(scenario())


def test_an_unknown_slug_is_a_problem_json_404():
    async def scenario():
        kit = await harness(entries=pool()).setup()
        try:
            response = await _get(kit, "/v1/catalog/conjectures/not-a-conjecture")
            assert response.status_code == 404
            assert response.headers["content-type"].startswith("application/problem+json")
            assert response.json()["reason_code"] == "NOT_FOUND"
        finally:
            await kit.teardown()

    run(scenario())


def test_a_slug_outside_the_task_id_alphabet_never_reaches_the_catalog():
    async def scenario():
        kit = await harness(entries=pool()).setup()
        try:
            for hostile in ("../../etc/passwd", "Open-Direct", "open direct"):
                response = await _get(kit, f"/v1/catalog/conjectures/{hostile}")
                assert response.status_code in (400, 404), hostile
        finally:
            await kit.teardown()

    run(scenario())


# --- meta --------------------------------------------------------------------------------


def test_meta_reports_the_pool_the_price_the_treasury_and_the_pins():
    async def scenario():
        kit = await harness(entries=pool()).setup()
        try:
            response = await _get(kit, "/v1/catalog/meta")
            assert response.status_code == 200, response.text
            body = response.json()

            assert body["conjectures"] == 3
            assert body["open_conjectures"] == 2
            assert body["credit_price_rao"] == 500_000_000
            assert body["credits_per_attempt"] == 1
            assert body["treasury_address"] == kit.settings.payment_recipient
            assert body["bounty"] == {
                "policy_version": "dynamic-age-v1",
                "balance_rao": 4_000_000_000,
                "wallet_coldkey": kit.settings.bounty_wallet_coldkey,
                "wallet_hotkey": kit.settings.bounty_wallet_hotkey,
                "netuid": 66,
                "asset": "alpha",
                "open_targets": 3,
                "total_age_weight": 3,
                "constant_numerator": 1,
                "constant_denominator": 4,
                "as_of": body["bounty"]["as_of"],
                "locked_at_submission": False,
            }
            assert body["pins_sha256"].startswith("sha256:")
            assert {item["value"]: item["count"] for item in body["categories"]} == {
                "research open": 2,
                "research solved": 1,
            }
        finally:
            await kit.teardown()

    run(scenario())


def test_meta_carries_a_strong_etag_and_answers_a_repeat_read_with_304():
    """Meta is stable within a pricing minute unless balance or solved state changes.

    The website hits it on every page load; a conditional request there costs a hash instead of
    a response body.
    """

    async def scenario():
        kit = await harness(entries=pool()).setup()
        try:
            async with await _client(kit) as client:
                first = await client.get("/v1/catalog/meta")
                etag = first.headers["etag"]
                assert etag.startswith('"') and etag.endswith('"')

                unchanged = await client.get(
                    "/v1/catalog/meta", headers={"If-None-Match": etag}
                )
                assert unchanged.status_code == 304
                assert unchanged.content == b""
                # A 304 must repeat the validator and the caching headers.
                assert unchanged.headers["etag"] == etag
                assert unchanged.headers["cache-control"] == "public, max-age=60"

                # A list, per RFC 9110, and the wildcard.
                for header in (f'"stale", {etag}', f"W/{etag}", "*"):
                    assert (
                        await client.get(
                            "/v1/catalog/meta", headers={"If-None-Match": header}
                        )
                    ).status_code == 304, header

                stale = await client.get(
                    "/v1/catalog/meta", headers={"If-None-Match": '"not-the-tag"'}
                )
                assert stale.status_code == 200
        finally:
            await kit.teardown()

    run(scenario())


def test_the_meta_etag_tracks_what_is_actually_published():
    """Hashed from the serialised payload, so the validator cannot drift from the body."""

    async def scenario():
        one = await harness(entries=pool()).setup()
        two = await harness(
            entries=pool(), BOUNTY_POOL_BALANCE_RAO="16800000000"
        ).setup()
        try:
            first = (await _get(one, "/v1/catalog/meta")).headers["etag"]
            second = (await _get(two, "/v1/catalog/meta")).headers["etag"]
            assert first != second
            # Same inputs, same tag: it is stable across requests, not per-response.
            assert first == (await _get(one, "/v1/catalog/meta")).headers["etag"]
        finally:
            await one.teardown()
            await two.teardown()

    run(scenario())


def test_meta_never_publishes_the_elan_asset_digests():
    """Per-platform archive hashes are an operator's download check, not public metadata."""

    async def scenario():
        kit = await harness(entries=pool()).setup()
        try:
            body = (await _get(kit, "/v1/catalog/meta")).json()
            elan = next(pin for pin in body["pins"] if pin["component"] == "elan")
            assert set(elan) == {
                "component",
                "repository",
                "commit",
                "toolchain",
                "version",
                "enabled",
            }
        finally:
            await kit.teardown()

    run(scenario())


# --- activity ----------------------------------------------------------------------------


async def _submit(kit, *, hotkey: str, payment_reference: str, task_id: str = "open-direct"):
    # Distinct proof bytes per call: `submissions.proof_digest` is globally unique, so two
    # submissions carrying identical bytes would make the second a duplicate rather than a
    # second attempt.
    bundle, digest = distinct_bundle(payment_reference, hotkey=hotkey)
    async with await _client(kit) as client:
        return await client.post(
            "/v1/submissions",
            content=bundle,
            headers=submission_headers(
                bundle,
                hotkey=hotkey,
                task_id=task_id,
                idempotency_key=new_key(),
                payment_reference=payment_reference,
                proof_digest=digest,
            ),
        )


def test_activity_counts_attempts_and_never_names_a_solver():
    async def scenario():
        kit = await harness(entries=pool()).setup()
        try:
            first = await _submit(kit, hotkey=HOTKEY, payment_reference="0xpay-0001")
            assert first.status_code == 201, first.text

            response = await _get(kit, f"/v1/catalog/conjectures/{OPEN_DIRECT}/activity")
            assert response.status_code == 200, response.text
            body = response.json()

            assert body["attempts"] == 1
            assert body["solvers"] == 1
            assert body["verified"] == 0
            assert body["certified"] == 0

            item = body["items"][0]
            assert item["event"] == "attempt"
            assert len(item["solver"]) == PSEUDONYM_LENGTH
            # The hotkey appears nowhere in the response, in any form.
            assert HOTKEY not in response.text
            assert item["solver"] != HOTKEY
            # Truncated to the hour, so the event cannot be joined to the funding transfer.
            assert item["occurred_at"].endswith(":00:00Z") or item[
                "occurred_at"
            ].endswith(":00:00+00:00")
        finally:
            await kit.teardown()

    run(scenario())


def test_a_solver_pseudonym_is_stable_per_conjecture_and_unlinkable_across_them():
    """Two attempts by one miner on one conjecture read as the same solver.

    The same miner on a different conjecture must not. The conjecture's reward target is inside
    the MAC, so a reader can count distinct solvers per conjecture without being able to rebuild
    one miner's history across the catalog.

    Keyed on the reward target rather than on a task id, which matters now that a conjecture page
    shows both attack directions: a task-keyed MAC would give one miner two pseudonyms on one
    page, and would rename every solver at each pin rotation.
    """

    async def scenario():
        kit = await harness(entries=pool()).setup()
        try:
            await _submit(kit, hotkey=HOTKEY, payment_reference="0xpay-0001")
            await _submit(kit, hotkey=OTHER_HOTKEY, payment_reference="0xpay-0002")

            here = (
                await _get(kit, f"/v1/catalog/conjectures/{OPEN_DIRECT}/activity")
            ).json()
            pseudonyms = {item["solver"] for item in here["items"]}
            assert len(pseudonyms) == 2
            assert here["solvers"] == 2

            # Same salt, same hotkey, different conjecture: a different pseudonym.
            from submission_api.routers.catalog import _pseudonym

            assert _pseudonym(
                kit.settings, "fc-target:Erdos11.erdos_11", HOTKEY
            ) != _pseudonym(kit.settings, "fc-target:Erdos13.erdos_13", HOTKEY)
        finally:
            await kit.teardown()

    run(scenario())


def test_activity_for_a_conjecture_with_no_attempts_is_empty_not_missing():
    async def scenario():
        kit = await harness(entries=pool()).setup()
        try:
            body = (
                await _get(kit, f"/v1/catalog/conjectures/{OPEN_ANSWER}/activity")
            ).json()
            assert body == {
                "slug": OPEN_ANSWER,
                "attempts": 0,
                "solvers": 0,
                "verified": 0,
                "certified": 0,
                "items": [],
            }
        finally:
            await kit.teardown()

    run(scenario())
