"""The public conjecture catalog: list, detail, meta, and anonymised activity.

The database is only touched for the attempt counters, so most of this runs against a synthetic
in-memory catalog. The tests that assert on counters need a real PostgreSQL server and are
skipped without one:

    docker compose -f docker-compose.pytest-db.yml up -d
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

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
from submission_api.taostats import StaticAlphaUsdPriceReader
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
        kit = await harness(
            entries=pool(),
            bounty_usd=StaticAlphaUsdPriceReader(Decimal("37.50")),
        ).setup()
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
            assert body["items"][0]["bounty"]["amount_rao"] == 1_000_000_000
            assert body["items"][0]["bounty"]["amount_usd"] == "37.50"

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


def _with_subjects(theorem: str, subjects: tuple[int, ...]):
    """A source declaration tagged with specific AMS subjects.

    The shared `declaration()` fixture tags everything `(5,)`, which cannot show that a subject
    filter discriminates rather than merely returning something.
    """
    source = declaration(theorem=theorem)
    return type(source)(
        **{
            **{field: getattr(source, field) for field in source.__dataclass_fields__},
            "ams_subjects": subjects,
        }
    )


def subject_pool():
    return (
        task_entry(
            task_id="open-direct",
            reward_target_id="fc-target:Erdos11.erdos_11",
            source=_with_subjects("Erdos11.erdos_11", (5, 11)),
        ),
        task_entry(
            task_id="open-answer",
            reward_target_id="fc-target:Erdos13.erdos_13",
            source=_with_subjects("Erdos13.erdos_13", (94,)),
        ),
    )


def test_the_ams_subject_filter_selects_by_subject():
    """`?ams_subject=11` answered `500` for every value a caller could send.

    `Query(ge=0, le=99)` sat on `list[int]`, so the bound was applied to the *list* rather than to
    its items: Pydantic raised `TypeError: Unable to apply constraint 'ge' to supplied value [11]`,
    which is not a validation error and so escaped as an unhandled server error. The filter was
    unreachable — there was no value that worked, only values that crashed.
    """

    async def scenario():
        kit = await harness(entries=subject_pool()).setup()
        try:
            one = await _get(kit, "/v1/catalog/conjectures", ams_subject=11)
            assert one.status_code == 200, one.text
            assert [item["slug"] for item in one.json()["items"]] == [OPEN_DIRECT]

            other = await _get(kit, "/v1/catalog/conjectures", ams_subject=94)
            assert [item["slug"] for item in other.json()["items"]] == [OPEN_ANSWER]

            # A subject nothing is tagged with is an empty result, not an error.
            assert (await _get(kit, "/v1/catalog/conjectures", ams_subject=42)).json()[
                "total"
            ] == 0

            # The boundary values of the MSC range are ordinary inputs.
            for edge in (0, 99):
                assert (
                    await _get(kit, "/v1/catalog/conjectures", ams_subject=edge)
                ).status_code == 200, edge
        finally:
            await kit.teardown()

    run(scenario())


def test_the_ams_subject_filter_is_repeatable_and_ored_within_the_field():
    async def scenario():
        kit = await harness(entries=subject_pool()).setup()
        try:
            async with await _client(kit) as client:
                both = await client.get(
                    "/v1/catalog/conjectures", params=[("ams_subject", 11), ("ams_subject", 94)]
                )
            assert both.status_code == 200, both.text
            assert {item["slug"] for item in both.json()["items"]} == {
                OPEN_DIRECT,
                OPEN_ANSWER,
            }

            # And the facet is counted over the other filters, so it still offers the alternative.
            facets = {facet["field"]: facet["values"] for facet in both.json()["facets"]}
            assert {item["value"] for item in facets["ams_subject"]} >= {"11", "94"}
        finally:
            await kit.teardown()

    run(scenario())


@pytest.mark.parametrize("value", [100, -1, "abc", ""])
def test_an_unusable_ams_subject_is_a_400_and_never_a_500(value):
    """The distinction the bug erased: a bad value is the caller's error, not the server's."""

    async def scenario():
        kit = await harness(entries=subject_pool()).setup()
        try:
            response = await _get(kit, "/v1/catalog/conjectures", ams_subject=value)
            assert response.status_code == 400, (value, response.status_code, response.text)
            assert response.json()["reason_code"] == "MALFORMED_REQUEST"
        finally:
            await kit.teardown()

    run(scenario())


def test_a_repeatable_filter_bounds_both_its_length_and_its_repetitions():
    """Two different limits, and the older annotation only ever expressed one of them.

    `Query(max_length=64)` on a `list[str]` is valid, so it never crashed — but it means "at most
    64 values", not "at most 64 characters each". The per-value bound the module claims was
    therefore not in force at all, and a single filter value of any length was accepted.
    """

    async def scenario():
        kit = await harness(entries=subject_pool()).setup()
        try:
            async with await _client(kit) as client:
                at_limit = await client.get(
                    "/v1/catalog/conjectures", params={"category": "x" * 64}
                )
                assert at_limit.status_code == 200, at_limit.text

                too_long = await client.get(
                    "/v1/catalog/conjectures", params={"category": "x" * 65}
                )
                assert too_long.status_code == 400, too_long.text
                assert too_long.json()["reason_code"] == "MALFORMED_REQUEST"

                # The repetition bound is still enforced, and separately.
                many = await client.get(
                    "/v1/catalog/conjectures",
                    params=[("ams_subject", 5)] * 65,
                )
                assert many.status_code == 400, many.text
        finally:
            await kit.teardown()

    run(scenario())


def test_no_query_parameter_carries_a_constraint_pydantic_could_not_apply():
    """An app-wide guard for the class of bug `ams_subject` was an instance of.

    A numeric bound that Pydantic *could* apply becomes `minimum`/`maximum` in the schema. One it
    could not — because it was attached to a container instead of to the container's items — is
    left in the document as a raw `ge`/`le`/`gt`/`lt` key, which is not a JSON Schema keyword. So
    a stray one of those names is exactly the signature of a constraint that will raise at request
    time instead of validating, on any endpoint, including ones added later.
    """
    unapplied = ("ge", "le", "gt", "lt", "multiple_of", "min_length", "max_length")

    async def scenario():
        kit = await harness(entries=subject_pool()).setup()
        try:
            schema = kit.app.openapi()
            offenders = []
            for path, operations in schema["paths"].items():
                for method, operation in operations.items():
                    for parameter in operation.get("parameters", ()):
                        for node in _schema_nodes(parameter.get("schema", {})):
                            for name in unapplied:
                                if name in node:
                                    offenders.append(
                                        f"{method.upper()} {path} "
                                        f"{parameter['name']}: stray {name!r}"
                                    )
            assert not offenders, offenders
        finally:
            await kit.teardown()

    run(scenario())


def _schema_nodes(node):
    """Every subschema of a parameter schema, including through `anyOf` and `items`."""
    if not isinstance(node, dict) or not node:
        return
    yield node
    for branch in node.get("anyOf") or ():
        yield from _schema_nodes(branch)
    yield from _schema_nodes(node.get("items"))


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
                    # A citation that merely *contains* a link, and ends in a year. The shape
                    # most of the pinned catalog uses, and the one this endpoint used to publish
                    # as raw Markdown with the URL run on through the authors.
                    "[Er46](https://doi.org/10.2307/2305092) Erdős, P. On sets. (1946)",
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
                # The link is found mid-citation and the surrounding words are kept, so a client
                # gets one clickable address and a label with no Markdown left in it.
                {
                    "label": "Er46 Erdős, P. On sets. (1946)",
                    "url": "https://doi.org/10.2307/2305092",
                },
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
        kit = await harness(
            entries=pool(),
            bounty_usd=StaticAlphaUsdPriceReader(Decimal("37.50")),
        ).setup()
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
                "balance_usd": "150.00",
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


# --- index -------------------------------------------------------------------------------


def _in_module(theorem: str, module: str):
    """A source declaration with a specific module.

    The module matters here and nowhere else in this file. `erdos_problem_number` is read from it
    rather than from the theorem name, so a fixture that left every module as `TestFixtures` could
    not tell a correct implementation from one that scraped the digits out of `Erdos1.erdos_1`.
    """
    source = declaration(theorem=theorem)
    return type(source)(
        **{
            **{field: getattr(source, field) for field in source.__dataclass_fields__},
            "module": module,
        }
    )


def _conjecture(theorem: str, module: str, *, modes=("formalized", "counterexample")):
    """One conjecture's tasks: one per attack direction, sharing a reward target.

    Sharing the reward target is what makes them one conjecture rather than several — the same
    grouping the rest of the catalog relies on — and it is why `variants` can report a slug and a
    mode without the two ever disagreeing.
    """
    return tuple(
        task_entry(
            task_id=f"{theorem}-{mode}",
            reward_target_id=f"fc-target:{theorem}",
            task_mode=mode,
            mode=mode,
            source=_in_module(theorem, module),
        )
        for mode in modes
    )


ERDOS_1 = "FormalConjectures.ErdosProblems.«1»"


def family_pool():
    """A pool whose problems differ along every axis the index reports.

    Four problems out of six conjectures, which is the whole point of the endpoint: a pool listed
    per conjecture would be six rows and would not say that three of them are one problem.
    """
    return (
        # A problem whose root is pooled, with two variants — one attackable both ways, one only
        # provable, so the mode expansion cannot be faked by doubling every row.
        *_conjecture("Erdos1.erdos_1", ERDOS_1),
        *_conjecture("Erdos1.erdos_1.variants.lb", ERDOS_1),
        *_conjecture("Erdos1.erdos_1.variants.real", ERDOS_1, modes=("formalized",)),
        # A problem pooled *only* as a variant. 241 of the pinned catalog's variants have no root
        # declaration, leaving 70 problems headed by one, so this is an ordinary case with real
        # data behind it — and `qualifier` is what reports that it happened.
        *_conjecture(
            "Erdos1062.erdos_1062.variants.lower_bound",
            "FormalConjectures.ErdosProblems.«1062»",
        ),
        # A dotted qualifier: upstream nests the qualifier, not the variant.
        *_conjecture(
            "Erdos357.erdos_357.variants.monotone.parts.i",
            "FormalConjectures.ErdosProblems.«357»",
        ),
        # Not an Erdős problem at all, so it must report a null number rather than be omitted.
        *_conjecture("ABC.abc", "FormalConjectures.Wikipedia.ABC"),
    )


def test_the_index_publishes_one_entry_per_problem_with_its_variants():
    async def scenario():
        kit = await harness(entries=family_pool()).setup()
        try:
            response = await _get(kit, "/v1/catalog/index")
            assert response.status_code == 200, response.text
            body = response.json()

            assert body["repository_commit"] == REPOSITORY_COMMIT
            # Six conjectures, four problems. `total` counts problems and agrees with `items`.
            assert body["total"] == 4 == len(body["items"])
            assert body["items"] == [
                {
                    "slug": "abc-abc",
                    "source_theorem": "ABC.abc",
                    "erdos_problem_number": None,
                    "qualifier": None,
                    "retired": False,
                    "variants": [],
                },
                {
                    "slug": "erdos1-erdos-1",
                    "source_theorem": "Erdos1.erdos_1",
                    "erdos_problem_number": 1,
                    # The root itself is pooled, so nothing qualifies the entry.
                    "qualifier": None,
                    "retired": False,
                    "variants": [
                        {
                            "slug": "erdos1-erdos-1-variants-lb",
                            "task_mode": "formalized",
                            "retired": False,
                        },
                        {
                            "slug": "erdos1-erdos-1-variants-lb",
                            "task_mode": "counterexample",
                            "retired": False,
                        },
                        # Only one row: this variant has one direction issued against it.
                        {
                            "slug": "erdos1-erdos-1-variants-real",
                            "task_mode": "formalized",
                            "retired": False,
                        },
                    ],
                },
                {
                    "slug": "erdos1062-erdos-1062-variants-lower-bound",
                    "source_theorem": "Erdos1062.erdos_1062.variants.lower_bound",
                    "erdos_problem_number": 1062,
                    # No root in the pool, so the variant stands in and says which one it is.
                    "qualifier": "lower_bound",
                    "retired": False,
                    "variants": [],
                },
                {
                    "slug": "erdos357-erdos-357-variants-monotone-parts-i",
                    "source_theorem": "Erdos357.erdos_357.variants.monotone.parts.i",
                    "erdos_problem_number": 357,
                    # Kept whole rather than truncated at its first dot.
                    "qualifier": "monotone.parts.i",
                    "retired": False,
                    "variants": [],
                },
            ]
        finally:
            await kit.teardown()

    run(scenario())


def test_the_index_never_lists_a_conjecture_as_its_own_variant():
    """Whatever represents a problem must not also appear underneath it.

    The representative is picked out of the same family it heads, so the obvious implementation
    lists it twice — once as `slug` and once in its own `variants`. Checked with retired members
    mixed in too, where the family is assembled from two separate indexes and the duplicate is
    that much easier to reintroduce.
    """

    async def scenario():
        for entries, retired in (
            (family_pool(), None),
            (mixed_pool(), mixed_retired()),
        ):
            kit = await harness(entries=entries, retired=retired).setup()
            try:
                body = (await _get(kit, "/v1/catalog/index")).json()
                assert body["items"]
                for item in body["items"]:
                    assert item["slug"] not in {
                        variant["slug"] for variant in item["variants"]
                    }
                # And no conjecture is grouped under two problems, which is what the live/retired
                # slug disjointness `ConjectureIndex.build` enforces buys here.
                owners: dict[str, set[str]] = {}
                for item in body["items"]:
                    for variant in item["variants"]:
                        owners.setdefault(variant["slug"], set()).add(item["slug"])
                assert all(len(problems) == 1 for problems in owners.values()), owners
            finally:
                await kit.teardown()

    run(scenario())


def test_the_index_carries_a_strong_etag_and_answers_a_repeat_read_with_304():
    """The index is byte-identical until a pin rotation, so a repeat read should cost no body.

    A stronger claim than meta's: this payload holds no counter, no bounty quote and no price, so
    nothing but a new pool can change it.
    """

    async def scenario():
        kit = await harness(entries=family_pool()).setup()
        try:
            async with await _client(kit) as client:
                first = await client.get("/v1/catalog/index")
                etag = first.headers["etag"]
                assert etag.startswith('"') and etag.endswith('"')

                unchanged = await client.get(
                    "/v1/catalog/index", headers={"If-None-Match": etag}
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
                            "/v1/catalog/index", headers={"If-None-Match": header}
                        )
                    ).status_code == 304, header

                stale = await client.get(
                    "/v1/catalog/index", headers={"If-None-Match": '"not-the-tag"'}
                )
                assert stale.status_code == 200
                assert stale.json() == first.json()

                # Nothing moves between two unconditional reads, which is the property the tag is
                # claiming. A counter in the body would break this.
                assert (await client.get("/v1/catalog/index")).headers["etag"] == etag
        finally:
            await kit.teardown()

    run(scenario())


def test_the_index_etag_tracks_what_is_actually_published():
    """Hashed from the serialised payload, so the validator cannot drift from the body.

    Retiring a conjecture changes a flag inside `variants` and nothing else — no slug appears or
    disappears at the top level — so a tag assembled from the pool's inputs rather than from the
    response could easily miss it.
    """

    async def scenario():
        one = await harness(entries=mixed_pool()).setup()
        two = await harness(entries=mixed_pool(), retired=mixed_retired()).setup()
        try:
            async with await _client(one) as client:
                without = await client.get("/v1/catalog/index")
            async with await _client(two) as client:
                with_retired = await client.get("/v1/catalog/index")

            assert without.json() != with_retired.json()
            assert without.headers["etag"] != with_retired.headers["etag"]
        finally:
            await one.teardown()
            await two.teardown()

    run(scenario())


def test_the_index_is_cacheable_and_reads_no_database():
    """Unpaginated is only defensible because the work is bounded and the database is untouched.

    The engine is disposed before the request, so any query at all fails rather than merely being
    slow — which is what makes this a test of the claim and not a restatement of it.
    """

    async def scenario():
        kit = await harness(entries=family_pool()).setup()
        try:
            await kit.engine.dispose()
            response = await _get(kit, "/v1/catalog/index")
            assert response.status_code == 200, response.text
            assert response.json()["total"] == 4
            assert response.headers["Cache-Control"] == "public, max-age=60"
        finally:
            await kit.teardown()

    run(scenario())


def _retired(theorem: str, module: str, *, modes=("formalized", "counterexample")):
    """One retired conjecture, shaped like a real recovered entry.

    A `RetiredTask` rather than a `TaskEntry`, matching the production type: the bundle is gone, so
    there is no manifest and no machine contract to carry — which is exactly why the index reports
    a flag rather than pretending the target is submittable.
    """
    from submission_api.retired import RetiredConjecture, RetiredTask
    from submission_api.slugs import slug_for

    return RetiredConjecture(
        slug=slug_for(f"fc-target:{theorem}"),
        problem_id="fixture-problem",
        reward_target_id=f"fc-target:{theorem}",
        tier="tier-1",
        retired_on="2026-08-06",
        reason_code="SOLVED + NOT_OPEN",
        reason="SOLVED + NOT_OPEN (settled by a verified submission)",
        decision_url=None,
        recovered_from_commit="c" * 40,
        source=_in_module(theorem, module),
        tasks=tuple(
            RetiredTask(
                task_id=f"{theorem}-{mode}-retired",
                task_mode=mode,
                task_bundle_sha256="sha256:" + "a" * 64,
                target_type_sha256="sha256:" + "b" * 64,
                challenge_lean="-- recovered from the deleted bundle\n",
            )
            for mode in modes
        ),
    )


ERDOS_99 = "FormalConjectures.ErdosProblems.«99»"


def mixed_pool():
    """`family_pool` plus a live variant of a problem whose root is retired.

    Erdős 99 is the case that decides the precedence rule: its root has left the pool but a variant
    of it is still submittable, so something has to head the family and the choice is visible.
    """
    return (*family_pool(), *_conjecture("Erdos99.erdos_99.variants.weak", ERDOS_99))


def mixed_retired():
    from submission_api.retired import RetiredIndex

    items = (
        # A retired variant of a problem whose root is live: belongs under `erdos1-erdos-1`.
        _retired("Erdos1.erdos_1.variants.weaker", ERDOS_1),
        # A retired root whose problem still has a live variant.
        _retired("Erdos99.erdos_99", ERDOS_99),
    )
    return RetiredIndex(
        by_slug={item.slug: item for item in items},
        slug_by_task_id={
            task_id: item.slug for item in items for task_id in item.task_ids
        },
    )


def test_the_index_includes_a_retired_variant_under_its_live_problem():
    async def scenario():
        kit = await harness(entries=mixed_pool(), retired=mixed_retired()).setup()
        try:
            body = (await _get(kit, "/v1/catalog/index")).json()
            entries = {item["slug"]: item for item in body["items"]}

            # A retired variant adds no problem of its own — it joins the family it belongs to.
            assert body["total"] == 5
            erdos_1 = entries["erdos1-erdos-1"]
            assert erdos_1["retired"] is False
            assert erdos_1["variants"] == [
                {
                    "slug": "erdos1-erdos-1-variants-lb",
                    "task_mode": "formalized",
                    "retired": False,
                },
                {
                    "slug": "erdos1-erdos-1-variants-lb",
                    "task_mode": "counterexample",
                    "retired": False,
                },
                {
                    "slug": "erdos1-erdos-1-variants-real",
                    "task_mode": "formalized",
                    "retired": False,
                },
                # Ordered by slug like the rest, not pushed to the end: retiring a variant must
                # not reorder a list a reader has already seen.
                {
                    "slug": "erdos1-erdos-1-variants-weaker",
                    "task_mode": "formalized",
                    "retired": True,
                },
                {
                    "slug": "erdos1-erdos-1-variants-weaker",
                    "task_mode": "counterexample",
                    "retired": True,
                },
            ]
        finally:
            await kit.teardown()

    run(scenario())


def test_a_retired_root_still_heads_its_problem_and_keeps_its_live_variants():
    """Retiring a root must not rename the problem it belongs to.

    Handing the header to a live variant instead would move the entry's `slug` and
    `source_theorem`, so one retirement would silently renumber a published table of contents —
    the opposite of what a stable slug is for. The header stays put and `retired` reports it.
    """

    async def scenario():
        kit = await harness(entries=mixed_pool(), retired=mixed_retired()).setup()
        try:
            body = (await _get(kit, "/v1/catalog/index")).json()
            entries = {item["slug"]: item for item in body["items"]}

            assert entries["erdos99-erdos-99"] == {
                "slug": "erdos99-erdos-99",
                "source_theorem": "Erdos99.erdos_99",
                "erdos_problem_number": 99,
                "qualifier": None,
                "retired": True,
                "variants": [
                    {
                        "slug": "erdos99-erdos-99-variants-weak",
                        "task_mode": "formalized",
                        "retired": False,
                    },
                    {
                        "slug": "erdos99-erdos-99-variants-weak",
                        "task_mode": "counterexample",
                        "retired": False,
                    },
                ],
            }
            # Which is why `retired` on the entry is not a filter for "nothing to do here": this
            # problem is headed by a closed target and still has two directions open.
            assert [
                variant
                for variant in entries["erdos99-erdos-99"]["variants"]
                if not variant["retired"]
            ]
        finally:
            await kit.teardown()

    run(scenario())


def test_a_wholly_retired_problem_is_its_own_entry_and_still_readable():
    async def scenario():
        from test_api_retired import RETIRED_SLUG, retired_index

        kit = await harness(entries=family_pool(), retired=retired_index()).setup()
        try:
            body = (await _get(kit, "/v1/catalog/index")).json()
            entries = {item["slug"]: item for item in body["items"]}

            # Erdős 10 is carried by nothing but this retired variant, so it is a problem of its
            # own — a fifth entry beside `family_pool`'s four.
            assert body["total"] == 5
            assert entries[RETIRED_SLUG] == {
                "slug": RETIRED_SLUG,
                "source_theorem": "Erdos10.erdos_10.variants.grechuk",
                # `retired_index` builds its declaration with the shared fixture module, which is
                # not an Erdős module — so the number is null here, and that is the honest answer
                # for a module this cannot recognise.
                "erdos_problem_number": None,
                "qualifier": "grechuk",
                "retired": True,
                "variants": [],
            }

            # The index agrees with the page it links to, which is the reason to publish it here.
            detail = await _get(kit, f"/v1/catalog/conjectures/{RETIRED_SLUG}")
            assert detail.status_code == 200, detail.text
            assert detail.json()["retirement"]["reason_code"] == "SOLVED + NOT_OPEN"
        finally:
            await kit.teardown()

    run(scenario())


def test_the_index_never_offers_a_retired_conjecture_as_submittable():
    """Whatever the index publishes, the submission path must still refuse a retired target.

    The flag is a display concern; this is the boundary underneath it. `TaskCatalog` is built from
    the allowlist alone and never consults the retired index, so every retired slug in the index
    resolves to nothing on the live grouping the submission path uses.
    """

    async def scenario():
        kit = await harness(entries=mixed_pool(), retired=mixed_retired()).setup()
        try:
            body = (await _get(kit, "/v1/catalog/index")).json()
            retired_slugs = {
                item["slug"] for item in body["items"] if item["retired"]
            } | {
                variant["slug"]
                for item in body["items"]
                for variant in item["variants"]
                if variant["retired"]
            }
            assert retired_slugs == {
                "erdos1-erdos-1-variants-weaker",
                "erdos99-erdos-99",
            }
            for slug in retired_slugs:
                assert kit.services.index.get(slug) is None
                detail = (await _get(kit, f"/v1/catalog/conjectures/{slug}")).json()
                # No bundle, so no contract a submission could even be assembled against.
                assert detail["retirement"] is not None
                assert detail["bounty"]["reason"] == "WITHDRAWN"
                assert all(
                    task["machine_contract"] is None for task in detail["tasks"]
                )
        finally:
            await kit.teardown()

    run(scenario())


def test_the_erdos_number_comes_from_the_module_not_the_theorem_name():
    """Lean mangles private declarations, and the mangled name carries a misleading number.

    `_private.FormalConjectures.ErdosProblems.«1049».0.Erdos1049.lambert_convergent` is a real
    catalog entry. Twenty of them exist, and a theorem-name parser reads them wrong or not at all;
    the module is unambiguous for all 509 Erdős modules in the pinned catalog.
    """
    from submission_api.conjectures import (
        erdos_problem_number,
        root_theorem,
        variant_qualifier,
    )

    assert erdos_problem_number("FormalConjectures.ErdosProblems.«1049»") == 1049
    assert erdos_problem_number("FormalConjectures.ErdosProblems.«9»") == 9
    # Every other collection, and anything a future rotation invents, reports no number rather
    # than a wrong one or an exception.
    assert erdos_problem_number("FormalConjectures.Wikipedia.ABC") is None
    assert erdos_problem_number("FormalConjectures.ErdosProblems.«12a»") is None
    assert erdos_problem_number("TestFixtures") is None

    assert root_theorem("Erdos1.erdos_1.variants.lb") == "Erdos1.erdos_1"
    assert root_theorem("Erdos1.erdos_1") == "Erdos1.erdos_1"
    assert variant_qualifier("Erdos1.erdos_1") is None
    assert (
        variant_qualifier("Erdos357.erdos_357.variants.monotone.parts.i")
        == "monotone.parts.i"
    )


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
