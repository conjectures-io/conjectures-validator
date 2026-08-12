"""The public result feeds: certified, in review, the dashboard feed, one result, report, solution.

This is the surface with the strictest disclosure rules, so most of these tests are about the
boundary. What *is* published: the submitting hotkey on every result, every submission's three
state fields on the dashboard feed, and the proof itself once review has approved it. What is not,
at any state: the paying coldkey, the payment reference, the funding extrinsic, and the verifier's
stdout or stderr. The proof of a submission that is unverified, rejected, or still in review is not
published either, and the tests below pin each of those three cases — a rejected submission is
*listed* on the dashboard feed, which is a different thing from its artifacts being served, and the
tests hold that line separately. Needs a real PostgreSQL server:

    docker compose -f docker-compose.pytest-db.yml up -d
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

pytest.importorskip("fastapi", reason="submission API tests need the service extra")
pytest.importorskip("sqlalchemy", reason="submission API tests need the db extra")
pytest.importorskip("httpx", reason="submission API tests need the service extra")
pytest.importorskip("psycopg", reason="submission API tests need the db extra")

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from conftest_api import (
    COLDKEY,
    HOTKEY,
    OTHER_HOTKEY,
    TASK_ID,
    distinct_bundle,
    harness,
    new_key,
    postgres_dsn,
    submission_headers,
)

from conjectures_subnet.db import submissions as store
from conjectures_subnet.db.models import (
    ManualReviewState,
    PayoutState,
    ReviewDecision,
    ReviewerKind,
    ReviewOutcome,
    RewardEvent,
    RewardState,
    Submission,
)
from submission_api.pagination import decode_cursor, encode_cursor
from submission_api.routers.results import PUBLIC_REPORT_FIELDS
from submission_api.taostats import StaticAlphaUsdPriceReader

# The fixture's conjecture, as a stable slug. `TASK_ID` is one build of one attack direction
# against it; this is the identity a public link uses.
CONJECTURE_SLUG = "verifierfixtures-direct"

pytestmark = pytest.mark.skipif(
    postgres_dsn() is None,
    reason="no database: run `docker compose -f docker-compose.pytest-db.yml up -d`",
)

# A report shaped like verifier.models.VerificationReport.to_dict(), including the two fields
# that must never be published.
FULL_REPORT = {
    "schema_version": 1,
    "problem_id": "fixture-problem",
    "task_id": TASK_ID,
    "repository_commit": "e923379e609b9d5987011a1d1f06ec22ea25cd20",
    "source_theorem": "VerifierFixtures.direct",
    "task_mode": "formalized",
    "task_bundle_sha256": "sha256:" + "ab" * 32,
    "submission_sha256": "sha256:" + "cd" * 32,
    "accepted": True,
    "stage": "COMPLETED",
    "reason_code": "VERIFIED",
    "checks": {"lean_kernel_passed": True},
    "theorem_names": ["Bounty.target"],
    "permitted_axioms": ["propext"],
    "duration_ms": 1234,
    "comparator_exit_code": 0,
    "stdout_tail": "the miner's proof, quoted back by Lean: theorem target := by trivial",
    "stderr_tail": "warning referencing the submitted source",
    "workspace_retained": False,
    "sandbox_mode": "landrun+seccomp",
}


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


async def _submit(kit, marker: str, *, hotkey: str = HOTKEY) -> str:
    bundle, digest = distinct_bundle(marker, hotkey=hotkey)
    async with await _client(kit) as client:
        response = await client.post(
            "/v1/submissions",
            content=bundle,
            headers=submission_headers(
                bundle,
                hotkey=hotkey,
                idempotency_key=new_key(),
                payment_reference=f"0xpay-{marker}",
                proof_digest=digest,
            ),
        )
    assert response.status_code == 201, response.text
    return response.json()["submission_id"]


async def _verify(kit, submission_id: str, *, accepted: bool = True, report=None):
    payload = json.dumps(FULL_REPORT if report is None else report).encode("utf-8")
    async with kit.session() as session:
        submission = await session.get(Submission, uuid.UUID(submission_id))
        await store.record_verification_result(
            session,
            submission,
            accepted=accepted,
            reason_code="VERIFIED" if accepted else "LEAN_KERNEL_REJECTED",
            stage="COMPLETED",
            verifier_version="verifier-1.2.3",
            container_digest="sha256:" + "11" * 32,
            sandbox_mode="landrun+seccomp",
            checks={"lean_kernel_passed": accepted},
            report=payload,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        await session.commit()


async def _certify(
    kit,
    submission_id: str,
    *,
    notes_public: str | None = None,
    notes_internal: str | None = None,
    later_advisory: bool = False,
):
    """Drive a verified submission all the way to paid out.

    Approves the review, records a confirmed payout, and flips `reward_status`. Done directly
    rather than through a reward CLI because that component does not exist yet; what matters
    here is the state the feed reads.
    """
    async with kit.session() as session:
        submission = await session.get(Submission, uuid.UUID(submission_id))
        decision = await store.approve_automatically(session, submission)
        decision.notes_public = notes_public
        decision.notes = notes_internal
        if later_advisory:
            session.add(
                ReviewDecision(
                    submission_id=submission.id,
                    decision=ReviewOutcome.REJECTED,
                    kind=ReviewerKind.ADVISORY,
                    reviewer="private-model-id",
                    policy_version=submission.review_policy_version,
                    reason_code="PRIVATE_ADVISORY_CODE",
                    notes="later private advisory notes",
                    evidence={"private_prompt": "must never cross the public API"},
                )
            )
        now = datetime.now(UTC)
        session.add(
            RewardEvent(
                submission_id=submission.id,
                eligibility_reason="REVIEW_APPROVED",
                amount_rao=submission.bounty_amount_rao,
                pricing_policy_version=submission.bounty_policy_version,
                pricing_inputs=submission.bounty_inputs,
                destination_coldkey=COLDKEY,
                destination_hotkey=submission.hotkey,
                status=PayoutState.CONFIRMED,
                extrinsic_reference=f"0xpayout-{submission_id[:8]}",
                submitted_block=100,
                finalized_block=101,
                initiated_by="test",
                submitted_at=now,
                confirmed_at=now,
            )
        )
        submission.reward_status = RewardState.REWARDED
        await session.commit()


async def _approve(kit, submission_id: str, *, notes_public: str | None = None):
    """Approve a verified result without recording a payout."""
    async with kit.session() as session:
        submission = await session.get(Submission, uuid.UUID(submission_id))
        decision = await store.approve_automatically(session, submission)
        decision.notes_public = notes_public
        await session.commit()


# --- what is published, and what is not ----------------------------------------------------


def test_a_certified_result_is_attributed_to_conjectures_and_names_no_miner():
    async def scenario():
        kit = await harness(
            bounty_usd=StaticAlphaUsdPriceReader(Decimal(50))
        ).setup()
        try:
            submission_id = await _submit(kit, "0001")
            await _verify(kit, submission_id)
            await _certify(
                kit,
                submission_id,
                notes_public="Lean passed; the reviewed result earns the published award.",
                notes_internal="internal reviewer discussion must remain private",
                later_advisory=True,
            )

            response = await _get(kit, "/v1/results/certified")
            assert response.status_code == 200, response.text
            body = response.json()
            assert len(body["items"]) == 1

            item = body["items"][0]
            assert item["id"] == submission_id
            # The stable conjecture slug, derived from the row's own reward target rather than
            # from its task id, so a result produced under an earlier pin still links to the
            # live conjecture page. The task it was produced against is a separate field.
            assert item["slug"] == CONJECTURE_SLUG
            assert item["task_id"] == TASK_ID
            assert item["attribution"] == "conjectures.io"
            assert item["certified_at"] is not None
            assert item["verified_at"] is not None
            assert item["bounty_amount_rao"] == 1_000_000_000
            assert item["bounty_amount_usd"] == "50.00"
            assert item["verifier_version"] == "verifier-1.2.3"
            assert item["report_available"] is True
            assert item["review"]["decision"] == "APPROVED"
            assert item["review"]["reason_code"] == "AUTO_REVIEW_DISABLED"
            assert item["review"]["decided_at"] is not None
            assert item["review"]["notes_public"] == (
                "Lean passed; the reviewed result earns the published award."
            )

            # Credited to the submitting hotkey.
            assert item["hotkey"] == HOTKEY
            # But nothing that reaches the miner's money: no paying coldkey, no payment
            # reference, no extrinsic. That is the boundary the row type still enforces.
            assert COLDKEY not in response.text
            assert "0xpay-0001" not in response.text
            assert "internal reviewer discussion" not in response.text
            assert "private-model-id" not in response.text
            assert "private_prompt" not in response.text
            assert "PRIVATE_ADVISORY_CODE" not in response.text
            assert not {"coldkey", "payment_reference", "payment", "extrinsic"} & set(item)
        finally:
            await kit.teardown()

    run(scenario())


def test_a_pending_payout_is_the_displayed_bounty_but_not_a_certification():
    async def scenario():
        kit = await harness().setup()
        try:
            submission_id = await _submit(kit, "0001-pending")
            await _verify(kit, submission_id)
            await _approve(kit, submission_id)
            async with kit.session() as session:
                submission = await session.get(Submission, uuid.UUID(submission_id))
                session.add(
                    RewardEvent(
                        submission_id=submission.id,
                        eligibility_reason="REVIEW_APPROVED",
                        amount_rao=750_000_000,
                        pricing_policy_version="payout-time-correction",
                        pricing_inputs={"quote_basis": "payout-time"},
                        destination_coldkey=COLDKEY,
                        destination_hotkey=submission.hotkey,
                        status=PayoutState.PENDING,
                        initiated_by="test",
                    )
                )
                await session.commit()

            response = await _get(kit, f"/v1/results/{submission_id}")
            assert response.status_code == 200, response.text
            item = response.json()
            assert item["bounty_amount_rao"] == 750_000_000
            assert item["bounty_policy_version"] == "payout-time-correction"
            assert item["certified_at"] is None
        finally:
            await kit.teardown()

    run(scenario())


def test_an_in_review_result_names_its_solver_but_carries_no_proof():
    async def scenario():
        kit = await harness().setup()
        try:
            submission_id = await _submit(kit, "0002")
            await _verify(kit, submission_id)

            response = await _get(kit, "/v1/results/in-review")
            body = response.json()
            assert [item["id"] for item in body["items"]] == [submission_id]

            item = body["items"][0]
            assert item["attribution"] == "conjectures.io"
            # No proof file, and no digest of one: review has not approved it yet.
            assert not {"proof", "proof_sha256", "challenge_lean"} & set(item)
            # The solver is named even in review — being listed is what publishes the hotkey.
            assert item["hotkey"] == HOTKEY
            assert COLDKEY not in response.text
        finally:
            await kit.teardown()

    run(scenario())


def test_a_result_publishes_the_review_policy_that_governed_it_not_the_one_in_force_now():
    """The policy version is read off the row, so bumping the setting cannot rewrite history.

    `MANUAL_REVIEW_CRITERIA.md` says a material change needs a new version rather than a
    reinterpretation of the old one. That only holds if an already-published result keeps naming
    the version it was judged under: a reader checking what a payout was made against has to land
    on the rules as they stood, not as they stand.
    """

    async def scenario():
        kit = await harness().setup()
        retired = "v0-retired"
        assert retired != kit.settings.review_policy_version
        try:
            submission_id = await _submit(kit, "0003")
            # Accepted under the older policy. Set before review, so the decision copies it too.
            async with kit.session() as session:
                submission = await session.get(Submission, uuid.UUID(submission_id))
                submission.review_policy_version = retired
                await session.commit()
            await _verify(kit, submission_id)

            in_review = (await _get(kit, "/v1/results/in-review")).json()["items"][0]
            assert in_review["review_policy_version"] == retired

            await _certify(kit, submission_id)
            certified = (await _get(kit, "/v1/results/certified")).json()["items"][0]
            assert certified["review"]["policy_version"] == retired
        finally:
            await kit.teardown()

    run(scenario())


def test_an_unverified_submission_is_on_the_dashboard_feed_but_not_the_narrow_ones():
    async def scenario():
        kit = await harness().setup()
        try:
            submission_id = await _submit(kit, "0003")

            assert (await _get(kit, "/v1/results/certified")).json()["items"] == []
            assert (await _get(kit, "/v1/results/in-review")).json()["items"] == []

            # The dashboard feed is unfiltered, so a queued attempt is listed — with its state
            # saying so, and with nothing to fetch.
            item = (await _get(kit, "/v1/results/submissions")).json()["items"][0]
            assert item["id"] == submission_id
            assert item["verification_status"] == "UNVERIFIED"
            assert item["manual_review_status"] == "UNREVIEWED"
            assert item["reward_status"] == "INELIGIBLE"
            assert item["verified_at"] is None
            assert item["certified_at"] is None
            assert item["report_available"] is False
            assert item["solution_available"] is False

            # Reading it by id is still 404: not published is reported as absent, so an id alone
            # cannot be probed for the state of unpublished work.
            single = await _get(kit, f"/v1/results/{submission_id}")
            assert single.status_code == 404
            assert single.json()["reason_code"] == "NOT_FOUND"
        finally:
            await kit.teardown()

    run(scenario())


def test_a_rejected_submission_is_listed_but_publishes_no_artifact():
    async def scenario():
        kit = await harness().setup()
        try:
            submission_id = await _submit(kit, "0004")
            await _verify(kit, submission_id, accepted=False)

            assert (await _get(kit, "/v1/results/in-review")).json()["items"] == []
            assert (await _get(kit, "/v1/results/certified")).json()["items"] == []

            # Listed on the dashboard feed: a rejected attempt is part of the history, and a feed
            # that dropped it would report only successes.
            item = (await _get(kit, "/v1/results/submissions")).json()["items"][0]
            assert item["id"] == submission_id
            assert item["verification_status"] == "REJECTED"
            # `verified_at` is set: Lean ran and reached a verdict, so the timestamp is real and
            # `verification_status` is what says the verdict was a rejection. Not certified,
            # though — that needs a confirmed payout.
            assert item["verified_at"] is not None
            assert item["certified_at"] is None

            # Being listed publishes the state and nothing else. The report exists on the run but
            # is not served for a row on neither published feed, and the feed says so rather than
            # letting a client discover it by collecting 404s.
            assert item["report_available"] is False
            assert item["solution_available"] is False
            assert (await _get(kit, f"/v1/results/{submission_id}/report")).status_code == 404
            assert (await _get(kit, f"/v1/results/{submission_id}/solution")).status_code == 404
            # And the by-id read stays restricted to the published feeds.
            assert (await _get(kit, f"/v1/results/{submission_id}")).status_code == 404
        finally:
            await kit.teardown()

    run(scenario())


def test_a_random_uuid_is_a_404_not_a_500():
    async def scenario():
        kit = await harness().setup()
        try:
            response = await _get(kit, f"/v1/results/{uuid.uuid4()}")
            assert response.status_code == 404
        finally:
            await kit.teardown()

    run(scenario())


# --- the published solution ----------------------------------------------------------------


def test_an_approved_unpaid_result_publishes_its_record_report_and_solution():
    async def scenario():
        kit = await harness().setup()
        try:
            submission_id = await _submit(kit, "0030")
            await _verify(kit, submission_id)

            # Lean-verified but unreviewed: listed, with nothing to fetch. The feed says so
            # rather than making a client discover it by getting a 404.
            listed = (await _get(kit, "/v1/results/submissions")).json()["items"][0]
            assert listed["solution_available"] is False
            assert (await _get(kit, f"/v1/results/{submission_id}/solution")).status_code == 404

            await _approve(
                kit,
                submission_id,
                notes_public="Lean passed and the binding review approved this result.",
            )

            listed = (await _get(kit, "/v1/results/submissions")).json()["items"][0]
            assert listed["solution_available"] is True
            assert listed["report_available"] is True
            assert listed["reward_status"] == "ELIGIBLE"
            assert listed["certified_at"] is None

            # Approval ends the disclosure hold. Chain settlement is what promotes the row to
            # the certified feed, not what makes its record and artifacts public.
            assert (await _get(kit, "/v1/results/certified")).json()["items"] == []
            assert (await _get(kit, "/v1/results/in-review")).json()["items"] == []

            record = await _get(kit, f"/v1/results/{submission_id}")
            assert record.status_code == 200, record.text
            assert record.json()["review"]["reason_code"] == "AUTO_REVIEW_DISABLED"

            report = await _get(kit, f"/v1/results/{submission_id}/report")
            assert report.status_code == 200, report.text

            response = await _get(kit, f"/v1/results/{submission_id}/solution")
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["filename"] == "Main.lean"
            assert body["byte_length"] == len(body["source"].encode("utf-8"))
            assert body["attribution"] == "conjectures.io"
            # The proof is now public, so its digest can be published too — it can no longer be
            # used to test an unpublished candidate for prior submission.
            assert body["proof_sha256"].startswith("sha256:")
            # Credited to the solver who submitted it, with no path to their money.
            assert body["hotkey"] == HOTKEY
            assert COLDKEY not in response.text
        finally:
            await kit.teardown()

    run(scenario())


def test_a_review_rejected_result_publishes_its_record_and_report_but_not_solution():
    async def scenario():
        kit = await harness().setup()
        try:
            submission_id = await _submit(kit, "0034")
            await _verify(kit, submission_id)

            async with kit.session() as session:
                submission = await session.get(Submission, uuid.UUID(submission_id))
                session.add(
                    ReviewDecision(
                        submission_id=submission.id,
                        decision=ReviewOutcome.REJECTED,
                        kind=ReviewerKind.HUMAN,
                        reviewer="test-reviewer",
                        policy_version=submission.review_policy_version,
                        reason_code="DUPLICATE_OF_EARLIER_SUBMISSION",
                        notes_public="A prior eligible result holds this reward target.",
                    )
                )
                submission.manual_review_status = ManualReviewState.REJECTED
                await session.commit()

            listed = (await _get(kit, "/v1/results/submissions")).json()["items"][0]
            assert listed["review"]["reason_code"] == "DUPLICATE_OF_EARLIER_SUBMISSION"
            assert listed["report_available"] is True
            assert listed["solution_available"] is False

            record = await _get(kit, f"/v1/results/{submission_id}")
            assert record.status_code == 200, record.text
            assert record.json()["review"]["reason_code"] == "DUPLICATE_OF_EARLIER_SUBMISSION"

            report = await _get(kit, f"/v1/results/{submission_id}/report")
            assert report.status_code == 200, report.text
            assert (await _get(kit, f"/v1/results/{submission_id}/solution")).status_code == 404
        finally:
            await kit.teardown()

    run(scenario())


def test_a_rejected_submissions_proof_is_never_published():
    async def scenario():
        kit = await harness().setup()
        try:
            submission_id = await _submit(kit, "0031")
            await _verify(kit, submission_id, accepted=False)

            # Not on a feed at all, so the first gate already answers. Asserted anyway: this is
            # the case where publishing the bytes would be irreversible.
            assert (await _get(kit, f"/v1/results/{submission_id}/solution")).status_code == 404
        finally:
            await kit.teardown()

    run(scenario())


def test_an_unverified_submissions_proof_is_never_published():
    async def scenario():
        kit = await harness().setup()
        try:
            submission_id = await _submit(kit, "0032")
            assert (await _get(kit, f"/v1/results/{submission_id}/solution")).status_code == 404
            assert (await _get(kit, f"/v1/results/{uuid.uuid4()}/solution")).status_code == 404
        finally:
            await kit.teardown()

    run(scenario())


def test_the_published_solution_is_the_exact_bytes_that_were_verified():
    async def scenario():
        kit = await harness().setup()
        try:
            submission_id = await _submit(kit, "0033")
            await _verify(kit, submission_id)
            await _certify(kit, submission_id)

            body = (await _get(kit, f"/v1/results/{submission_id}/solution")).json()

            # Byte-exact against the digest the fixture submitted under: a reader must be able
            # to recompile precisely what the kernel accepted, so the served source has to hash
            # to the same value `submissions.proof_digest` holds.
            _, submitted_digest = distinct_bundle("0033")
            served = body["source"].encode("utf-8")
            assert body["proof_sha256"] == submitted_digest
            assert "sha256:" + hashlib.sha256(served).hexdigest() == submitted_digest
            assert body["byte_length"] == len(served)
        finally:
            await kit.teardown()

    run(scenario())


# --- the dashboard feed --------------------------------------------------------------------


def test_the_dashboard_feed_lists_every_submission_whatever_state_it_reached():
    async def scenario():
        kit = await harness().setup()
        try:
            # Every submission first, then the verdicts. Certifying one closes the bounty on the
            # fixture's conjecture, and intake answers `409 BOUNTY_CLOSED` to anything after that
            # — so a scenario that needs four attempts on one conjecture has to pay for them all
            # before the first payout confirms.
            certified_id = await _submit(kit, "0020")
            in_review_id = await _submit(kit, "0021")
            rejected_id = await _submit(kit, "0022")
            unverified_id = await _submit(kit, "0023")

            await _verify(kit, rejected_id, accepted=False)
            await _verify(kit, in_review_id)
            await _verify(kit, certified_id)
            await _certify(kit, certified_id)

            page = (await _get(kit, "/v1/results/submissions")).json()
            ids = [item["id"] for item in page["items"]]

            # All four states in one request — that is the point of the endpoint. A dashboard
            # showing only the two publishable ones would report successes as the whole history.
            assert set(ids) == {certified_id, in_review_id, rejected_id, unverified_id}

            # Newest first, and the four were created in order, so this is the reverse of it.
            assert ids == [unverified_id, rejected_id, in_review_id, certified_id]

            # The three state axes are what a client branches on. One shape for every row.
            by_id = {item["id"]: item for item in page["items"]}
            assert [
                (
                    by_id[submission_id]["verification_status"],
                    by_id[submission_id]["manual_review_status"],
                    by_id[submission_id]["reward_status"],
                )
                for submission_id in (certified_id, in_review_id, rejected_id, unverified_id)
            ] == [
                ("VERIFIED", "APPROVED", "REWARDED"),
                ("VERIFIED", "UNREVIEWED", "INELIGIBLE"),
                ("REJECTED", "UNREVIEWED", "INELIGIBLE"),
                ("UNVERIFIED", "UNREVIEWED", "INELIGIBLE"),
            ]

            # Certification is visible as a nullable field rather than as a second response
            # model, so a dashboard reads one shape and branches on `certified_at`.
            assert by_id[certified_id]["certified_at"] is not None
            assert by_id[certified_id]["review"] is not None
            assert by_id[in_review_id]["certified_at"] is None
            assert by_id[in_review_id]["review"] is None

            # And the artifact gates are unchanged by the widening: only the approved row
            # publishes a proof, and only the two rows on a published feed publish a report.
            assert [by_id[i]["solution_available"] for i in ids] == [
                False,
                False,
                False,
                True,
            ]
            assert by_id[rejected_id]["report_available"] is False
            assert by_id[unverified_id]["report_available"] is False
            assert by_id[in_review_id]["report_available"] is True
            assert by_id[certified_id]["report_available"] is True
        finally:
            await kit.teardown()

    run(scenario())


def test_the_dashboard_feed_names_no_miner_and_carries_no_proof():
    async def scenario():
        kit = await harness().setup()
        try:
            submission_id = await _submit(kit, "0024")
            await _verify(kit, submission_id)

            response = await _get(kit, "/v1/results/submissions")
            item = response.json()["items"][0]

            # The same disclosure rules the per-result endpoints are held to. This feed reuses
            # `PublicResult`, so it cannot drift from them by construction — the assertion is
            # here because "reuses" is a decision a later change could quietly reverse.
            assert item["hotkey"] == HOTKEY
            assert COLDKEY not in response.text
            assert "0xpay-0024" not in response.text
            assert not {"coldkey", "payment_reference", "payment", "extrinsic"} & set(item)

            assert item["attribution"] == "conjectures.io"
            assert item["slug"] == CONJECTURE_SLUG
        finally:
            await kit.teardown()

    run(scenario())


def test_the_dashboard_feed_pages_newest_first_and_ends_with_a_null_cursor():
    async def scenario():
        kit = await harness().setup()
        try:
            created = []
            for index in range(5):
                submission_id = await _submit(kit, f"02{index}")
                await _verify(kit, submission_id)
                created.append(submission_id)

            seen = []
            cursor = None
            pages = 0
            while True:
                params = {"limit": 2}
                if cursor:
                    params["cursor"] = cursor
                page = (await _get(kit, "/v1/results/submissions", **params)).json()
                seen.extend(item["id"] for item in page["items"])
                pages += 1
                cursor = page["next_cursor"]
                if cursor is None:
                    break
                assert pages < 10, "cursor did not terminate"

            assert seen == list(reversed(created))
            # Three pages, not four: the `limit + 1` read means the cursor is null on the page
            # that exhausts the feed rather than one wasted request later.
            assert pages == 3
        finally:
            await kit.teardown()

    run(scenario())


# --- the published report ------------------------------------------------------------------


def test_the_public_report_withholds_verifier_output_and_the_proof_digest():
    async def scenario():
        kit = await harness().setup()
        try:
            submission_id = await _submit(kit, "0005")
            await _verify(kit, submission_id)
            await _certify(kit, submission_id)

            response = await _get(kit, f"/v1/results/{submission_id}/report")
            assert response.status_code == 200, response.text
            body = response.json()

            # The digest is of the full immutable bytes, so it still matches the miner's copy
            # and the row on the run, not of the reduced projection below.
            assert body["report_sha256"].startswith("sha256:")
            assert body["slug"] == CONJECTURE_SLUG

            report = body["report"]
            assert set(report) == set(PUBLIC_REPORT_FIELDS)
            assert report["accepted"] is True
            assert report["checks"] == {"lean_kernel_passed": True}

            # Lean's output quotes the submitted proof back, so it is not published.
            assert "stdout_tail" not in report
            assert "stderr_tail" not in report
            assert "the miner's proof" not in response.text
            # proof_digest is globally UNIQUE, so publishing it would let anyone test a
            # candidate proof for prior submission.
            assert "submission_sha256" not in report
            assert "sha256:" + "cd" * 32 not in response.text
        finally:
            await kit.teardown()

    run(scenario())


def test_the_report_field_set_is_an_allowlist_so_a_new_field_is_withheld():
    """A field the verifier adds later must not be published by default.

    This is the difference between an allowlist and a denylist, and it is the whole reason the
    projection names what it copies.
    """

    async def scenario():
        kit = await harness().setup()
        try:
            submission_id = await _submit(kit, "0006")
            await _verify(
                kit,
                submission_id,
                report={**FULL_REPORT, "future_diagnostic_field": "leak me"},
            )
            await _certify(kit, submission_id)

            response = await _get(kit, f"/v1/results/{submission_id}/report")
            assert "future_diagnostic_field" not in response.json()["report"]
            assert "leak me" not in response.text
        finally:
            await kit.teardown()

    run(scenario())


def test_a_result_with_no_recorded_report_is_a_404():
    async def scenario():
        kit = await harness().setup()
        try:
            submission_id = await _submit(kit, "0007")
            async with kit.session() as session:
                submission = await session.get(Submission, uuid.UUID(submission_id))
                await store.record_verification_result(
                    session,
                    submission,
                    accepted=True,
                    reason_code="VERIFIED",
                    stage="COMPLETED",
                    verifier_version="verifier-1.2.3",
                    container_digest="sha256:" + "11" * 32,
                    sandbox_mode="landrun+seccomp",
                    checks=None,
                    report=None,  # the run died before writing one
                    started_at=datetime.now(UTC),
                    finished_at=datetime.now(UTC),
                )
                await session.commit()

            assert (await _get(kit, f"/v1/results/{submission_id}")).status_code == 200
            report = await _get(kit, f"/v1/results/{submission_id}/report")
            assert report.status_code == 404
        finally:
            await kit.teardown()

    run(scenario())


# --- paging --------------------------------------------------------------------------------


def test_the_feed_pages_newest_first_and_ends_with_a_null_cursor():
    async def scenario():
        kit = await harness().setup()
        try:
            created = []
            for index in range(5):
                submission_id = await _submit(kit, f"01{index}")
                await _verify(kit, submission_id)
                created.append(submission_id)

            seen = []
            cursor = None
            pages = 0
            while True:
                params = {"limit": 2}
                if cursor:
                    params["cursor"] = cursor
                page = (await _get(kit, "/v1/results/in-review", **params)).json()
                seen.extend(item["id"] for item in page["items"])
                pages += 1
                cursor = page["next_cursor"]
                if cursor is None:
                    break
                assert pages < 10, "cursor did not terminate"

            # Newest first, every row exactly once, and no wasted final request: the cursor is
            # null on the page that exhausts the feed rather than one page later.
            assert seen == list(reversed(created))
            assert pages == 3
        finally:
            await kit.teardown()

    run(scenario())


def test_a_tampered_cursor_is_one_clean_400():
    async def scenario():
        kit = await harness().setup()
        try:
            submission_id = await _submit(kit, "0008")
            await _verify(kit, submission_id)
            first = (await _get(kit, "/v1/results/in-review", limit=1)).json()
            issued = first["next_cursor"]
            assert issued is None or isinstance(issued, str)

            # Correctly signed, but by a key this deployment does not hold. Nothing below the
            # signature check ever parses it, so it cannot reach the query as a predicate.
            forged = encode_cursor(
                "a-secret-this-deployment-never-configured",
                created_at=datetime.now(UTC),
                id=uuid.uuid4(),
            )
            for hostile in (forged, "not-a-cursor", "AAAA.BBBB", "1.99999999999999999999.x"):
                response = await _get(kit, "/v1/results/in-review", cursor=hostile)
                assert response.status_code == 400, hostile
                # One reason code for every failure mode: a client learns nothing about
                # whether it was the shape or the signature that was wrong.
                assert response.json()["reason_code"] == "INVALID_CURSOR", hostile

            # An over-long cursor never reaches the decoder: the query parameter's own length
            # bound rejects it first, which is the cheaper place to do it.
            too_long = await _get(kit, "/v1/results/in-review", cursor="x" * 300)
            assert too_long.status_code == 400
            assert too_long.json()["reason_code"] == "MALFORMED_REQUEST"
        finally:
            await kit.teardown()

    run(scenario())


def test_a_cursor_round_trips_exactly():
    moment = datetime(2026, 8, 3, 14, 15, 16, 123456, tzinfo=UTC)
    identifier = uuid.uuid4()
    encoded = encode_cursor("secret", created_at=moment, id=identifier)
    decoded = decode_cursor("secret", encoded)
    assert decoded.created_at == moment
    assert decoded.id == identifier
    # Opaque and URL-safe, so it needs no escaping in a query string.
    assert "/" not in encoded and "+" not in encoded and "=" not in encoded


def test_the_feed_page_size_is_capped():
    async def scenario():
        kit = await harness().setup()
        try:
            response = await _get(kit, "/v1/results/certified", limit=1000)
            assert response.status_code == 400
        finally:
            await kit.teardown()

    run(scenario())


# --- what a result calls the conjecture it is against ------------------------------------------
#
# Two things are being held at once here. A result is durable and the pool is not: retiring a target
# deletes its bundles, and every feed resolves its labels through the catalog index, so before the
# retired index was consulted retiring a target quietly relabelled every result already earned
# against it. And `title` is a Lean identifier, so a dashboard that rendered it showed one — which
# is what `display_title` is for.


def _retired_index():
    """One withdrawn target, distinct from the live fixture conjecture.

    Built here rather than imported from `test_api_retired` because what this module is about is the
    *result* rows pointing at it; that module is about the catalog page. Filed under a real Erdős
    module so the display title is the one a reader would actually see.
    """
    from conftest import declaration

    from submission_api.retired import RetiredConjecture, RetiredIndex, RetiredTask
    from submission_api.slugs import slug_for

    item = RetiredConjecture(
        slug=slug_for(RETIRED_TARGET),
        problem_id="withdrawn-problem",
        reward_target_id=RETIRED_TARGET,
        tier="tier-1",
        retired_on="2026-08-06",
        reason_code="SOLVED + NOT_OPEN",
        reason="SOLVED + NOT_OPEN (settled by a verified submission)",
        decision_url=None,
        recovered_from_commit="c" * 40,
        source=replace(
            declaration(theorem=RETIRED_THEOREM),
            module="FormalConjectures.ErdosProblems.«10»",
        ),
        tasks=(
            RetiredTask(
                task_id="withdrawn-formalized",
                task_mode="formalized",
                task_bundle_sha256="sha256:" + "a" * 64,
                target_type_sha256="sha256:" + "b" * 64,
                challenge_lean="-- recovered from the deleted bundle\n",
            ),
        ),
    )
    return RetiredIndex(
        by_slug={item.slug: item},
        slug_by_task_id={task.task_id: item.slug for task in item.tasks},
    )


RETIRED_THEOREM = "Erdos10.erdos_10.variants.grechuk"
RETIRED_TARGET = f"fc-target:{RETIRED_THEOREM}"
RETIRED_SLUG = "erdos10-erdos-10-variants-grechuk"
RETIRED_DISPLAY_TITLE = "Erdős problem 10 — grechuk"
# A target in neither index: what `V004` left behind when it could not map a row's `problem_id` to a
# known reward target. There is no conjecture to look up, so the labels have nowhere to go.
UNKNOWN_TARGET = "legacy-problem-id-nobody-recognises"


async def _retarget(kit, submission_id: str, target: str) -> None:
    """Point a recorded submission at another reward target.

    Which is the real sequence, not a shortcut around intake: a submission is admitted against a
    live target and the retirement happens afterwards, so by the time a reader loads the feed the row
    names something the pool no longer issues tasks for. Editing the row is how a test reaches that
    state without a second pin rotation.
    """
    async with kit.session() as session:
        submission = await session.get(Submission, uuid.UUID(submission_id))
        submission.reward_target_id = target
        await session.commit()


def test_a_result_is_named_for_a_reader_and_not_with_a_lean_identifier():
    """`title` stays the citable theorem; `display_title` is what a dashboard renders.

    Asserted on all three endpoints that shape a `PublicResult`, because each is a separate call into
    `_result` and a fix applied to only the feed would leave the detail page wrong.
    """

    async def scenario():
        kit = await harness(bounty_usd=StaticAlphaUsdPriceReader(Decimal(50))).setup()
        try:
            submission_id = await _submit(kit, "0011")
            await _verify(kit, submission_id)
            await _certify(kit, submission_id)

            feed = await _get(kit, "/v1/results/submissions")
            certified = await _get(kit, "/v1/results/certified")
            one = await _get(kit, f"/v1/results/{submission_id}")

            for row in (feed.json()["items"][0], certified.json()["items"][0], one.json()):
                assert row["title"] == "VerifierFixtures.direct"
                assert row["display_title"] == "Test Fixtures — direct"
                assert row["title_parts"] == {
                    "collection": "testfixtures",
                    "collection_label": "Test Fixtures",
                    "reference": "Test Fixtures",
                    "qualifier": "direct",
                }
        finally:
            await kit.teardown()

    run(scenario())


def test_an_in_review_result_is_named_the_same_way():
    """`/in-review` shapes an `InReviewResult` through its own function, so it needs its own test."""

    async def scenario():
        kit = await harness().setup()
        try:
            submission_id = await _submit(kit, "0012")
            await _verify(kit, submission_id)

            item = (await _get(kit, "/v1/results/in-review")).json()["items"][0]

            assert item["display_title"] == "Test Fixtures — direct"
            assert item["title_parts"]["qualifier"] == "direct"
        finally:
            await kit.teardown()

    run(scenario())


def test_a_result_against_a_retired_conjecture_keeps_the_name_of_what_it_closed():
    """The bug this closes: `title` fell back to the slug the moment the target was withdrawn.

    Which is the case that matters most — a target is usually retired *because* a submission settled
    it, so the result whose page went blank is the one that earned the retirement.
    """

    async def scenario():
        kit = await harness(
            retired=_retired_index(), bounty_usd=StaticAlphaUsdPriceReader(Decimal(50))
        ).setup()
        try:
            certified_id = await _submit(kit, "0013")
            await _verify(kit, certified_id)
            await _certify(kit, certified_id)
            await _retarget(kit, certified_id, RETIRED_TARGET)

            pending_id = await _submit(kit, "0014")
            await _verify(kit, pending_id)
            await _retarget(kit, pending_id, RETIRED_TARGET)

            feed = await _get(kit, "/v1/results/submissions")
            certified = await _get(kit, "/v1/results/certified")
            in_review = await _get(kit, "/v1/results/in-review")
            one = await _get(kit, f"/v1/results/{certified_id}")

            rows = [
                *feed.json()["items"],
                *certified.json()["items"],
                *in_review.json()["items"],
                one.json(),
            ]
            assert len(rows) == 5
            for row in rows:
                # The slug is unchanged and still links to the retired page; what changes is that it
                # is no longer doing double duty as the conjecture's name.
                assert row["slug"] == RETIRED_SLUG
                assert row["title"] == RETIRED_THEOREM
                assert row["display_title"] == RETIRED_DISPLAY_TITLE
                assert row["statement"] == "True"
        finally:
            await kit.teardown()

    run(scenario())


def test_a_result_against_a_target_in_neither_index_still_degrades_to_its_slug():
    """The fallback survives, because one class of row genuinely has no conjecture.

    A public feed must not fail over a historical row, and degrading is honest here in a way it was
    not for a retired target: this identity resolves to no conjecture in any pin, so there is no name
    being withheld. `title_parts` is null rather than invented.
    """

    async def scenario():
        kit = await harness(retired=_retired_index()).setup()
        try:
            submission_id = await _submit(kit, "0015")
            await _verify(kit, submission_id)
            await _retarget(kit, submission_id, UNKNOWN_TARGET)

            item = (await _get(kit, "/v1/results/submissions")).json()["items"][0]

            assert item["slug"] == UNKNOWN_TARGET
            assert item["title"] == UNKNOWN_TARGET
            assert item["display_title"] == UNKNOWN_TARGET
            assert item["title_parts"] is None
            assert item["statement"] == ""
        finally:
            await kit.teardown()

    run(scenario())


def test_two_solvers_on_one_conjecture_are_each_credited_to_their_own_hotkey():
    async def scenario():
        kit = await harness().setup()
        try:
            mine = await _submit(kit, "0009", hotkey=HOTKEY)
            theirs = await _submit(kit, "0010", hotkey=OTHER_HOTKEY)
            await _verify(kit, mine)
            await _verify(kit, theirs)

            response = await _get(kit, "/v1/results/in-review")
            items = {item["id"]: item for item in response.json()["items"]}
            assert set(items) == {mine, theirs}
            # Each row credited to the hotkey that actually submitted it. The pairing is what
            # matters: the rows are decorated from two separate queries keyed by id, so a join
            # that lost its ordering would attribute a proof to the wrong solver.
            assert items[mine]["hotkey"] == HOTKEY
            assert items[theirs]["hotkey"] == OTHER_HOTKEY
        finally:
            await kit.teardown()

    run(scenario())
