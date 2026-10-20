"""Every status the public surface publishes is a closed vocabulary, and it is the code's own.

The response models type these as `Literal`s so the OpenAPI document carries them as enums. That
only helps if the `Literal` matches what the code can produce, so each one is held here to the
enum or constant it is drawn from: a value added to the source without the schema fails this test
rather than failing response validation in production.
"""

from __future__ import annotations

from typing import get_args

from conjectures_subnet.db.models import (
    ManualReviewState,
    ReviewOutcome,
    RewardState,
    VerificationState,
)
from submission_api import schemas_public as public
from verifier.task_policy import PRODUCTION_TASK_MODES


def values(literal) -> set[str]:
    return set(get_args(literal))


def test_the_status_literals_are_the_database_enums():
    assert values(public.VerificationStatus) == {item.value for item in VerificationState}
    assert values(public.ManualReviewStatus) == {item.value for item in ManualReviewState}
    assert values(public.RewardStatus) == {item.value for item in RewardState}
    assert values(public.ReviewDecisionOutcome) == {item.value for item in ReviewOutcome}


def test_task_modes_are_the_production_modes():
    assert values(public.TaskMode) == set(PRODUCTION_TASK_MODES)


def test_every_listed_bounty_reason_is_one_the_pricer_or_the_catalog_writes():
    """The reasons are string literals in two modules rather than an enum. A reason produced but
    not listed already fails response validation wherever a test reaches it; this is the other
    direction, so the schema does not advertise a value nothing produces."""
    import inspect

    from conjectures_subnet import bounty
    from submission_api.routers import catalog

    source = inspect.getsource(bounty) + inspect.getsource(catalog)
    assert {reason for reason in values(public.BountyReason) if f'"{reason}"' in source} == values(
        public.BountyReason
    )


def test_the_openapi_document_publishes_the_vocabularies_as_enums():
    from conftest_api import build_settings

    from submission_api.app import create_app

    document = create_app(settings=build_settings()).openapi()
    schemas = document["components"]["schemas"]
    summary = schemas["ConjectureSummary"]["properties"]
    assert set(summary["display_status"]["enum"]) == values(public.DisplayStatus)
    assert set(schemas["BountyInfo"]["properties"]["reason"]["enum"]) == values(
        public.BountyReason
    )
    result = schemas["PublicResult"]["properties"]
    assert set(result["reward_status"]["enum"]) == {item.value for item in RewardState}
