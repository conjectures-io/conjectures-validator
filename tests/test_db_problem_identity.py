"""One exact theorem target, two proof/refutation tasks, one reward.

The task pool issues a `formalized` and a `counterexample` task for every theorem target, and both
carry a stable `reward_target_id`. Independently formalized parents, parts, and variants have
different identities and therefore separate rewards. These tests cover intake, exclusivity for
one exact target, independence between targets, and contradictory proof/refutation outcomes.

Skipped unless a PostgreSQL server is reachable:

    docker compose -f docker-compose.pytest-db.yml up -d
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest

pytest.importorskip("fastapi", reason="these tests build the API harness")
pytest.importorskip("sqlalchemy", reason="the db extra provides SQLAlchemy")
pytest.importorskip("psycopg", reason="the db extra provides psycopg")

from conftest_api import (
    COLDKEY,
    HOTKEY,
    TASK_DIGEST,
    TASK_ID,
    VALID_PROOF,
    harness,
    postgres_dsn,
    submission_headers,
    task_entry,
    valid_bundle,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from conjectures_subnet.db import submissions as store
from conjectures_subnet.db.models import (
    ManualReviewState,
    ReviewDecision,
    ReviewerKind,
    ReviewOutcome,
    RewardState,
    Submission,
    TaskMode,
    VerificationState,
)
from verifier.hashing import sha256_bytes

pytestmark = pytest.mark.skipif(
    postgres_dsn() is None,
    reason="no database: run `docker compose -f docker-compose.pytest-db.yml up -d`",
)

PROBLEM = "fc-e923379e-erdos11-erdos-11-problem"
VARIANT_PROBLEM = "fc-e923379e-erdos11-not-four-dvd-problem"
REPINNED_PROBLEM = "fc-newcommit-erdos11-erdos-11-problem"
REWARD_TARGET = "fc-target:Erdos11.erdos_11"
VARIANT_REWARD_TARGET = "fc-target:Erdos11.erdos_11.variants.not_four_dvd"


def run(coroutine):
    return asyncio.run(coroutine)


def new_submission(
    *,
    problem_id: str = PROBLEM,
    reward_target_id: str | None = None,
    task_mode: TaskMode = TaskMode.FORMALIZED,
    proof: bytes = VALID_PROOF,
    manual_review_required: bool = False,
) -> store.NewSubmission:
    """One paid submission. The proof bytes vary because `proof_digest` is globally unique."""
    return store.NewSubmission(
        hotkey=HOTKEY,
        idempotency_key=uuid.uuid4(),
        request_digest=sha256_bytes(proof + problem_id.encode()),
        task_id=TASK_ID,
        task_bundle_sha256=TASK_DIGEST,
        problem_id=problem_id,
        reward_target_id=reward_target_id or REWARD_TARGET,
        task_mode=task_mode,
        proof_content=proof,
        proof_sha256=sha256_bytes(proof),
        payment_reference=f"ref-{uuid.uuid4()}",
        payment_sender=COLDKEY,
        payment_amount_rao=500_000_000,
        payment_block=1,
        hotkey_signature=b"\x01" * 64,
        manual_review_required=manual_review_required,
        review_policy_version="test-v1",
        bounty_amount_rao=1,
        bounty_policy_version="test-v1",
    )


async def verify(session, submission, *, accepted: bool = True):
    """Record a completed verifier run, the way the worker does."""
    moment = datetime.now(UTC)
    return await store.record_verification_result(
        session,
        submission,
        accepted=accepted,
        reason_code="VERIFIED" if accepted else "LEAN_KERNEL_REJECTED",
        stage="COMPLETED",
        verifier_version="test",
        container_digest="sha256:" + "cd" * 32,
        sandbox_mode="landrun+seccomp",
        checks={"lean_kernel_passed": accepted},
        report=None,
        started_at=moment,
        finished_at=moment,
    )


async def decisions_for(session, submission_id) -> list[ReviewDecision]:
    result = await session.execute(
        select(ReviewDecision)
        .where(ReviewDecision.submission_id == submission_id)
        .order_by(ReviewDecision.id)
    )
    return list(result.scalars())


# --- intake ---------------------------------------------------------------------------


def test_intake_records_the_problem_and_mode_from_the_allowlist():
    """The miner sends a task ID and a digest; the server decides what problem that is.

    A miner who could name the problem could aim a cheap task's proof at an expensive
    problem's reward, so neither value is read from the request.
    """

    async def scenario():
        kit = await harness(
            entries=(
                task_entry(
                    mode="counterexample",
                    problem_id=PROBLEM,
                    reward_target_id=REWARD_TARGET,
                ),
            )
        ).setup()
        try:
            from httpx import ASGITransport, AsyncClient

            bundle = valid_bundle()
            async with AsyncClient(
                transport=ASGITransport(app=kit.app),
                base_url="http://validator.test",
            ) as client:
                response = await client.post(
                    "/v1/submissions",
                    content=bundle,
                    headers=submission_headers(bundle),
                )
            assert response.status_code == 201, response.text

            async with kit.session() as session:
                row = await session.get(Submission, uuid.UUID(response.json()["submission_id"]))
                assert row is not None
                assert row.problem_id == PROBLEM
                assert row.reward_target_id == REWARD_TARGET
                assert row.task_mode is TaskMode.COUNTEREXAMPLE
        finally:
            await kit.teardown()

    run(scenario())


# --- one reward per exact theorem target ------------------------------------------------


def test_many_submissions_may_share_a_problem_while_none_is_awarded():
    """Every miner may pay to attempt the same conjecture, from either side."""

    async def scenario():
        kit = await harness().setup()
        try:
            async with kit.session() as session:
                for index, mode in enumerate(
                    (TaskMode.FORMALIZED, TaskMode.COUNTEREXAMPLE, TaskMode.FORMALIZED)
                ):
                    await store.create_submission(
                        session,
                        new_submission(
                            task_mode=mode,
                            proof=VALID_PROOF + f"\n-- {index}\n".encode(),
                            manual_review_required=True,
                        ),
                    )
                await session.commit()

            async with kit.session() as session:
                rows = (
                    await session.execute(
                        select(Submission).where(Submission.problem_id == PROBLEM)
                    )
                ).scalars()
                statuses = [row.reward_status for row in rows]
            assert len(statuses) == 3
            assert all(status is RewardState.INELIGIBLE for status in statuses)
        finally:
            await kit.teardown()

    run(scenario())


def test_the_schema_refuses_a_second_reward_for_one_problem():
    """The unique index is the authority, not the service's read-then-write check."""

    async def scenario():
        kit = await harness().setup()
        try:
            async with kit.session() as session:
                first = await store.create_submission(
                    session, new_submission(manual_review_required=True)
                )
                second = await store.create_submission(
                    session,
                    new_submission(
                        task_mode=TaskMode.COUNTEREXAMPLE,
                        proof=VALID_PROOF + b"\n-- second\n",
                        manual_review_required=True,
                    ),
                )
                first.submission.reward_status = RewardState.ELIGIBLE
                await session.flush()

                # A different mode of the same conjecture is still the same reward.
                second.submission.reward_status = RewardState.ELIGIBLE
                with pytest.raises(IntegrityError, match=store.REWARD_TARGET_CONSTRAINT):
                    await session.flush()
                await session.rollback()
        finally:
            await kit.teardown()

    run(scenario())


def test_a_failed_payout_keeps_its_claim_on_the_problem():
    """FAILED is outside the index's exclusion, so the problem is not silently reassigned."""

    async def scenario():
        kit = await harness().setup()
        try:
            async with kit.session() as session:
                first = await store.create_submission(
                    session, new_submission(manual_review_required=True)
                )
                second = await store.create_submission(
                    session,
                    new_submission(
                        proof=VALID_PROOF + b"\n-- second\n", manual_review_required=True
                    ),
                )
                first.submission.reward_status = RewardState.FAILED
                await session.flush()

                second.submission.reward_status = RewardState.ELIGIBLE
                with pytest.raises(IntegrityError, match=store.REWARD_TARGET_CONSTRAINT):
                    await session.flush()
                await session.rollback()
        finally:
            await kit.teardown()

    run(scenario())


def test_an_independent_variant_has_its_own_reward():
    async def scenario():
        kit = await harness().setup()
        try:
            async with kit.session() as session:
                mine = await store.create_submission(
                    session, new_submission(manual_review_required=True)
                )
                theirs = await store.create_submission(
                    session,
                    new_submission(
                        problem_id=VARIANT_PROBLEM,
                        reward_target_id=VARIANT_REWARD_TARGET,
                        proof=VALID_PROOF + b"\n-- independent variant\n",
                        manual_review_required=True,
                    ),
                )
                mine.submission.reward_status = RewardState.ELIGIBLE
                theirs.submission.reward_status = RewardState.ELIGIBLE
                await session.flush()  # no conflict: two problems, two rewards
                await session.commit()
        finally:
            await kit.teardown()

    run(scenario())


def test_a_source_repin_of_the_same_target_cannot_pay_twice():
    """A new source commit does not create a second bounty for the same theorem target."""

    async def scenario():
        kit = await harness().setup()
        try:
            async with kit.session() as session:
                original = await store.create_submission(
                    session,
                    new_submission(manual_review_required=True),
                )
                repinned = await store.create_submission(
                    session,
                    new_submission(
                        problem_id=REPINNED_PROBLEM,
                        reward_target_id=REWARD_TARGET,
                        proof=VALID_PROOF + b"\n-- source repin\n",
                        manual_review_required=True,
                    ),
                )
                original.submission.reward_status = RewardState.ELIGIBLE
                await session.flush()

                repinned.submission.reward_status = RewardState.ELIGIBLE
                with pytest.raises(IntegrityError, match=store.REWARD_TARGET_CONSTRAINT):
                    await session.flush()
                await session.rollback()
        finally:
            await kit.teardown()

    run(scenario())


# --- what the service does with that ---------------------------------------------------


def test_a_valid_proof_for_an_awarded_problem_is_rejected_not_crashed():
    """Losing the race is a recorded reward decision, not an IntegrityError.

    Nothing is wrong with the second proof, so it is not a verification rejection; the reward
    is simply already spent, and the reason is durable on a review_decisions row.
    """

    async def scenario():
        kit = await harness().setup()
        try:
            async with kit.session() as session:
                winner = await store.create_submission(session, new_submission())
                await verify(session, winner.submission)
                assert winner.submission.reward_status is RewardState.ELIGIBLE

                # The same mode: two miners both proved the conjecture, and only the first
                # can be paid. A different mode would be a contradiction, not a race.
                loser = await store.create_submission(
                    session,
                    new_submission(proof=VALID_PROOF + b"\n-- loser\n"),
                )
                await verify(session, loser.submission)

                # The verdict stands: the proof was Lean-valid.
                assert loser.submission.verification_status is VerificationState.VERIFIED
                # But it cannot be paid, and that is the review outcome, not a Lean one.
                assert loser.submission.reward_status is RewardState.INELIGIBLE
                assert loser.submission.manual_review_status is ManualReviewState.REJECTED
                assert loser.submission.failure_reason is None

                recorded = await decisions_for(session, loser.submission.id)
                assert [decision.reason_code for decision in recorded] == [
                    store.PROBLEM_ALREADY_AWARDED
                ]
                assert recorded[0].decision is ReviewOutcome.REJECTED
                assert recorded[0].kind is ReviewerKind.AUTOMATIC
        finally:
            await kit.teardown()

    run(scenario())


def test_a_problem_proved_and_refuted_is_escalated_rather_than_paid():
    """A conjecture cannot be both true and false, so no automatic path may pay either side.

    Either the generated negation is not the negation, or something worse is true. The
    submission is left UNREVIEWED so it enters the human review queue, with an ADVISORY row
    saying why — advisory because it is evidence for a reviewer, never a binding decision.
    """

    async def scenario():
        kit = await harness().setup()
        try:
            async with kit.session() as session:
                # The proof verifies but is held for review, so it claims no reward yet.
                proved = await store.create_submission(
                    session, new_submission(manual_review_required=True)
                )
                await verify(session, proved.submission)
                assert proved.submission.reward_status is RewardState.INELIGIBLE

                refuted = await store.create_submission(
                    session,
                    new_submission(
                        task_mode=TaskMode.COUNTEREXAMPLE,
                        proof=VALID_PROOF + b"\n-- refuted\n",
                    ),
                )
                await verify(session, refuted.submission)

                assert refuted.submission.verification_status is VerificationState.VERIFIED
                assert refuted.submission.reward_status is RewardState.INELIGIBLE
                # Untouched, so the review queue picks it up.
                assert refuted.submission.manual_review_status is ManualReviewState.UNREVIEWED

                recorded = await decisions_for(session, refuted.submission.id)
                assert [decision.reason_code for decision in recorded] == [
                    store.PROBLEM_CONTRADICTED
                ]
                assert recorded[0].kind is ReviewerKind.ADVISORY
                assert "counterexample" in (recorded[0].notes or "")
                assert "formalized" in (recorded[0].notes or "")
        finally:
            await kit.teardown()

    run(scenario())
