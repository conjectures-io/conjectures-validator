"""Intake and read behaviour against a real PostgreSQL database.

Gated on `FC_POSTGRES_DSN`, because the schema is PostgreSQL-only. Start one with:

    cp .env.example .env && docker compose -f docker-compose.db.yml up -d
    export FC_POSTGRES_DSN=postgresql+psycopg://conjectures:<pw>@127.0.0.1:<port>/conjectures
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("fastapi", reason="submission API tests need the service extra")
pytest.importorskip("sqlalchemy", reason="submission API tests need the db extra")
pytest.importorskip("httpx", reason="submission API tests need the service extra")
pytest.importorskip("psycopg", reason="submission API tests need the db extra")

from conftest_api import (
    COLDKEY,
    HOTKEY,
    OTHER_HOTKEY,
    TASK_DIGEST,
    TASK_ID,
    VALID_PROOF,
    harness,
    manifest_json,
    new_key,
    postgres_dsn,
    read_headers,
    submission_headers,
    valid_bundle,
)
from conjectures_subnet.db import digests
from conjectures_subnet.db.models import (
    ApiRejectionLog,
    ManualReviewState,
    PayoutState,
    ProblemWinner,
    Proof,
    ReviewerKind,
    ReviewOutcome,
    RewardEvent,
    RewardState,
    Submission,
    SubmissionEvent,
    SubmissionStatusField,
    VerificationState,
)
from verifier.errors import ReasonCode
from verifier.hashing import sha256_bytes


pytestmark = pytest.mark.skipif(
    postgres_dsn() is None, reason="set FC_POSTGRES_DSN to run the database tests"
)


def run(coroutine):
    return asyncio.run(coroutine)


async def _client(kit, *, raise_app_exceptions: bool = True):
    from httpx import ASGITransport, AsyncClient

    return AsyncClient(
        transport=ASGITransport(app=kit.app, raise_app_exceptions=raise_app_exceptions),
        base_url="http://validator.test",
    )


async def _post(kit, bundle: bytes, **overrides):
    async with await _client(kit) as client:
        return await client.post(
            "/v1/submissions", content=bundle, headers=submission_headers(bundle, **overrides)
        )


async def _count(kit, model) -> int:
    from sqlalchemy import func, select

    async with kit.session() as session:
        return await session.scalar(select(func.count()).select_from(model))


# --- intake -------------------------------------------------------------------------


def test_a_paid_submission_is_recorded_and_queued():
    async def scenario():
        kit = await harness().setup()
        try:
            response = await _post(kit, valid_bundle())
            assert response.status_code == 201, response.text
            body = response.json()

            # The status axes are reported separately, never collapsed into one field.
            assert body["verification_status"] == VerificationState.UNVERIFIED.value
            assert body["manual_review_status"] == ManualReviewState.UNREVIEWED.value
            assert body["reward_status"] == RewardState.INELIGIBLE.value
            assert body["hotkey"] == HOTKEY
            assert body["task_id"] == TASK_ID
            assert body["problem_id"] == "fc-test-fixture-problem"
            assert body["task_mode"] == "formalized"
            assert body["proof_sha256"] == sha256_bytes(VALID_PROOF)
            assert body["manual_review_required"] is True

            # Payment is a precondition, so it is present unconditionally.
            assert body["payment"]["sender"] == COLDKEY
            assert body["payment"]["amount_rao"] == 500_000_000
            assert body["payment"]["block"] > 0

            # The proof bytes are stored in the database, content-addressed.
            async with kit.session() as session:
                proof = await session.get(Proof, digests.to_bytes(body["proof_sha256"]))
                assert proof is not None
                assert bytes(proof.content) == VALID_PROOF
                assert proof.byte_length == len(VALID_PROOF)
                submission = await session.get(Submission, body["submission_id"])
                assert submission.request_timestamp_ms > 0
        finally:
            await kit.teardown()

    run(scenario())


def test_intake_records_a_created_event():
    async def scenario():
        kit = await harness().setup()
        try:
            created = (await _post(kit, valid_bundle())).json()
            from sqlalchemy import select

            async with kit.session() as session:
                events = (
                    await session.execute(select(SubmissionEvent).order_by(SubmissionEvent.id))
                ).scalars().all()
            assert len(events) == 1
            event = events[0]
            assert event.status_field == SubmissionStatusField.CREATED
            # CREATED has no from_status, enforced by events_created_has_no_from.
            assert event.from_status is None
            assert event.actor == "api"
            assert str(event.submission_id) == created["submission_id"]
        finally:
            await kit.teardown()

    run(scenario())


def test_solver_metadata_does_not_block_intake():
    async def scenario():
        kit = await harness().setup()
        try:
            bundle = valid_bundle(
                manifest=manifest_json(solver={"name": "deep-solver", "version": "0.9.1"})
            )
            assert (await _post(kit, bundle)).status_code == 201
        finally:
            await kit.teardown()

    run(scenario())


# --- idempotency and uniqueness -----------------------------------------------------


def test_identical_replay_returns_the_original():
    async def scenario():
        kit = await harness().setup()
        try:
            bundle = valid_bundle()
            key = new_key()
            first = await _post(kit, bundle, idempotency_key=key)
            assert first.status_code == 201
            second = await _post(kit, bundle, idempotency_key=key)
            assert second.status_code == 200
            assert second.json()["submission_id"] == first.json()["submission_id"]
            assert await _count(kit, Submission) == 1
        finally:
            await kit.teardown()

    run(scenario())


def test_replay_does_not_require_the_body_again():
    async def scenario():
        kit = await harness().setup()
        try:
            bundle = valid_bundle()
            key = new_key()
            first = await _post(kit, bundle, idempotency_key=key)
            assert first.status_code == 201
            async with await _client(kit) as client:
                headers = submission_headers(bundle, idempotency_key=key)
                headers["Content-Length"] = str(len(bundle))
                replay = await client.post("/v1/submissions", content=b"", headers=headers)
            assert replay.status_code == 200
            assert replay.json()["submission_id"] == first.json()["submission_id"]
        finally:
            await kit.teardown()

    run(scenario())


def test_reusing_a_key_with_different_data_conflicts():
    async def scenario():
        kit = await harness().setup()
        try:
            bundle = valid_bundle()
            key = new_key()
            assert (await _post(kit, bundle, idempotency_key=key)).status_code == 201
            response = await _post(
                kit, bundle, idempotency_key=key, payment_reference="0xpayment-other"
            )
            assert response.status_code == 409
            assert response.json()["reason_code"] == "IDEMPOTENCY_CONFLICT"
        finally:
            await kit.teardown()

    run(scenario())


def test_a_non_uuid_idempotency_key_is_refused():
    async def scenario():
        kit = await harness().setup()
        try:
            assert (await _post(kit, valid_bundle(), idempotency_key="not-a-uuid")).status_code == 400
        finally:
            await kit.teardown()

    run(scenario())


def test_the_same_proof_cannot_be_submitted_twice():
    async def scenario():
        kit = await harness().setup()
        try:
            # proof_digest is globally UNIQUE: one proof is payable at most once.
            assert (await _post(kit, valid_bundle())).status_code == 201
            response = await _post(kit, valid_bundle(), payment_reference="0xpayment-second")
            assert response.status_code == 409
            assert response.json()["reason_code"] == "DUPLICATE_PROOF"
        finally:
            await kit.teardown()

    run(scenario())


def test_a_payment_reference_backs_only_one_submission():
    async def scenario():
        kit = await harness().setup()
        try:
            first = await _post(kit, valid_bundle(), payment_reference="0xshared")
            assert first.status_code == 201
            other = b"theorem target : type_of% VerifierFixtures.direct := by\n  trivial -- 2\n"
            response = await _post(
                kit,
                valid_bundle(
                    manifest=manifest_json(
                        proof_sha256=sha256_bytes(other), proof_bytes=len(other)
                    ),
                    proof=other,
                ),
                payment_reference="0xshared",
                proof_digest=sha256_bytes(other),
            )
            assert response.status_code == 409
            assert response.json()["reason_code"] == "DUPLICATE_PAYMENT"
        finally:
            await kit.teardown()

    run(scenario())


def test_concurrent_identical_requests_create_one_submission():
    async def scenario():
        kit = await harness().setup()
        try:
            bundle = valid_bundle()
            key = new_key()
            responses = await asyncio.gather(
                *(_post(kit, bundle, idempotency_key=key) for _ in range(6)),
                return_exceptions=True,
            )
            codes = sorted(
                r.status_code for r in responses if not isinstance(r, BaseException)
            )
            assert 201 in codes
            assert all(code in {200, 201, 409} for code in codes), codes
            assert await _count(kit, Submission) == 1
        finally:
            await kit.teardown()

    run(scenario())


# --- payment gating -----------------------------------------------------------------


def test_an_unconfirmed_payment_creates_no_submission():
    async def scenario():
        kit = await harness(DEVELOPMENT_PAYMENT_REFERENCES="0xonly-this-one").setup()
        try:
            response = await _post(kit, valid_bundle(), payment_reference="0xsomething-else")
            assert response.status_code == 402
            assert response.json()["reason_code"] == "PAYMENT_NOT_FINALIZED"
            assert await _count(kit, Submission) == 0
            # The rejection log is the only record a paying miner would otherwise not get.
            assert await _count(kit, ApiRejectionLog) == 1
        finally:
            await kit.teardown()

    run(scenario())


# --- rejection logging --------------------------------------------------------------


def test_a_hostile_bundle_is_refused_and_logged():
    async def scenario():
        kit = await harness().setup()
        try:
            response = await _post(kit, valid_bundle(trailer=b"\x00" * 8))
            assert response.status_code == 422
            assert response.json()["reason_code"] == ReasonCode.BUNDLE_POLICY_VIOLATION.value

            from sqlalchemy import select

            async with kit.session() as session:
                logged = (await session.execute(select(ApiRejectionLog))).scalars().all()
            assert len(logged) == 1
            row = logged[0]
            assert row.reason_code == ReasonCode.BUNDLE_POLICY_VIOLATION.value
            assert row.http_status == 422
            assert row.hotkey_claimed == HOTKEY
            assert row.task_id == TASK_ID
            assert row.payment_reference == "0xpayment-0001"
            # Digests are stored as bare hex text in this table.
            assert row.task_bundle_sha256 == TASK_DIGEST.removeprefix("sha256:")
            assert await _count(kit, Submission) == 0
        finally:
            await kit.teardown()

    run(scenario())


def test_a_malformed_request_is_logged_even_when_unverifiable():
    async def scenario():
        kit = await harness().setup()
        try:
            # A malformed idempotency key must still be logged, which is why that column is
            # TEXT with no domain.
            response = await _post(kit, valid_bundle(), idempotency_key="not-a-uuid")
            assert response.status_code == 400
            from sqlalchemy import select

            async with kit.session() as session:
                row = (await session.execute(select(ApiRejectionLog))).scalars().one()
            assert row.idempotency_key == "not-a-uuid"
            assert row.http_status == 400
        finally:
            await kit.teardown()

    run(scenario())


def test_a_successful_submission_is_not_logged_as_a_rejection():
    async def scenario():
        kit = await harness().setup()
        try:
            assert (await _post(kit, valid_bundle())).status_code == 201
            assert await _count(kit, ApiRejectionLog) == 0
        finally:
            await kit.teardown()

    run(scenario())


# --- request validation -------------------------------------------------------------


def test_oversized_declared_length_is_refused_before_the_body_is_read():
    async def scenario():
        kit = await harness().setup()
        try:
            bundle = valid_bundle()
            async with await _client(kit) as client:
                headers = submission_headers(bundle)
                headers["Content-Length"] = str(kit.settings.max_bundle_bytes + 1)
                response = await client.request(
                    "POST", "/v1/submissions", content=bundle, headers=headers
                )
            assert response.status_code == 413
        finally:
            await kit.teardown()

    run(scenario())


def test_body_over_the_configured_limit_dies_mid_stream():
    async def scenario():
        kit = await harness(MAX_BUNDLE_BYTES="512").setup()
        try:
            assert (await _post(kit, valid_bundle())).status_code == 413
        finally:
            await kit.teardown()

    run(scenario())


@pytest.mark.parametrize(
    "override",
    [
        {"task_id": "Fixture"},
        {"task_digest": "not-a-digest"},
        {"proof_digest": "not-a-digest"},
        {"payment_reference": "no"},
        {"timestamp_ms": "abc"},
        {"content_type": "application/json"},
    ],
)
def test_malformed_headers_are_refused(override):
    async def scenario():
        kit = await harness().setup()
        try:
            assert (await _post(kit, valid_bundle(), **override)).status_code == 400
        finally:
            await kit.teardown()

    run(scenario())


def test_a_declared_proof_digest_must_match_the_archive():
    async def scenario():
        kit = await harness().setup()
        try:
            response = await _post(kit, valid_bundle(), proof_digest="sha256:" + "ff" * 32)
            assert response.status_code in {400, 422}
            assert await _count(kit, Submission) == 0
        finally:
            await kit.teardown()

    run(scenario())


def test_unknown_task_is_not_found():
    async def scenario():
        kit = await harness().setup()
        try:
            response = await _post(kit, valid_bundle(), task_id="no-such-task")
            assert response.status_code == 404
            assert response.json()["reason_code"] == "TASK_NOT_ALLOWED"
        finally:
            await kit.teardown()

    run(scenario())


def test_wrong_task_digest_is_not_found():
    async def scenario():
        kit = await harness().setup()
        try:
            assert (await _post(kit, valid_bundle(), task_digest="sha256:" + "ee" * 32)).status_code == 404
        finally:
            await kit.teardown()

    run(scenario())


def test_a_bundle_naming_another_miner_is_rejected():
    async def scenario():
        kit = await harness().setup()
        try:
            response = await _post(
                kit, valid_bundle(manifest=manifest_json(miner_hotkey=OTHER_HOTKEY))
            )
            assert response.status_code == 422
            assert response.json()["reason_code"] == ReasonCode.BUNDLE_MANIFEST_INVALID.value
        finally:
            await kit.teardown()

    run(scenario())


def test_a_proof_violating_lean_policy_is_rejected():
    async def scenario():
        kit = await harness().setup()
        try:
            proof = b"theorem target : True := by sorry\n"
            bundle = valid_bundle(
                manifest=manifest_json(
                    proof_sha256=sha256_bytes(proof), proof_bytes=len(proof)
                ),
                proof=proof,
            )
            response = await _post(kit, bundle, proof_digest=sha256_bytes(proof))
            assert response.status_code == 422
            body = response.json()
            assert body["reason_code"] == ReasonCode.SUBMISSION_POLICY_VIOLATION.value
            assert "sorry is prohibited" in body["detail"]
            # Nothing is stored for a refused proof.
            assert await _count(kit, Proof) == 0
        finally:
            await kit.teardown()

    run(scenario())


def test_a_bad_signature_is_unauthorized_and_creates_nothing():
    async def scenario():
        kit = await harness().setup()
        try:
            response = await _post(kit, valid_bundle(), signature="ab" * 64)
            assert response.status_code == 401
            assert response.json()["reason_code"] == "SIGNATURE_INVALID"
            assert await _count(kit, Submission) == 0
        finally:
            await kit.teardown()

    run(scenario())


# --- reads --------------------------------------------------------------------------


def test_status_read_reports_each_axis():
    async def scenario():
        kit = await harness().setup()
        try:
            created = (await _post(kit, valid_bundle())).json()
            async with await _client(kit) as client:
                response = await client.get(
                    f"/v1/submissions/{created['submission_id']}", headers=read_headers()
                )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["verification_status"] == VerificationState.UNVERIFIED.value
            assert body["manual_review_status"] == ManualReviewState.UNREVIEWED.value
            assert body["reward_status"] == RewardState.INELIGIBLE.value
            assert body["verification"]["report_available"] is False
        finally:
            await kit.teardown()

    run(scenario())


def test_a_miner_cannot_read_another_miners_submission():
    async def scenario():
        kit = await harness().setup()
        try:
            created = (await _post(kit, valid_bundle())).json()
            async with await _client(kit) as client:
                response = await client.get(
                    f"/v1/submissions/{created['submission_id']}",
                    headers=read_headers(OTHER_HOTKEY),
                )
            # Absent rather than forbidden, so ids cannot be probed.
            assert response.status_code == 404
        finally:
            await kit.teardown()

    run(scenario())


def test_unknown_submission_is_not_found():
    async def scenario():
        kit = await harness().setup()
        try:
            missing = "00000000-0000-4000-8000-000000000000"
            async with await _client(kit) as client:
                response = await client.get(
                    f"/v1/submissions/{missing}", headers=read_headers()
                )
            assert response.status_code == 404
        finally:
            await kit.teardown()

    run(scenario())


def test_report_is_a_conflict_until_verification_finishes():
    async def scenario():
        kit = await harness().setup()
        try:
            created = (await _post(kit, valid_bundle())).json()
            async with await _client(kit) as client:
                response = await client.get(
                    f"/v1/submissions/{created['submission_id']}/report",
                    headers=read_headers(),
                )
            assert response.status_code == 409
            assert response.json()["verification_status"] == VerificationState.UNVERIFIED.value
        finally:
            await kit.teardown()

    run(scenario())


# --- verdict recording --------------------------------------------------------------


async def _record_verdict(kit, submission_id, *, accepted: bool):
    import json
    from datetime import datetime, timezone

    from conjectures_subnet.db import submissions as store

    payload = json.dumps({"accepted": accepted}).encode()
    async with kit.session() as session:
        submission = await session.get(Submission, submission_id)
        await store.record_verification_result(
            session,
            submission,
            accepted=accepted,
            reason_code="VERIFIED" if accepted else "LEAN_KERNEL_REJECTED",
            stage="COMPLETED" if accepted else "RUN_KERNEL",
            verifier_version="test",
            container_digest="sha256:" + "11" * 32,
            sandbox_mode="landrun+seccomp",
            checks={"lean_kernel_passed": accepted},
            report=payload,
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )
        await session.commit()


def test_a_verified_proof_is_held_when_review_is_required():
    async def scenario():
        kit = await harness().setup()
        try:
            created = (await _post(kit, valid_bundle())).json()
            await _record_verdict(kit, created["submission_id"], accepted=True)
            async with await _client(kit) as client:
                status_response = await client.get(
                    f"/v1/submissions/{created['submission_id']}", headers=read_headers()
                )
                report = await client.get(
                    f"/v1/submissions/{created['submission_id']}/report",
                    headers=read_headers(),
                )
            body = status_response.json()
            assert body["verification_status"] == VerificationState.VERIFIED.value
            # Held: review is required, so it is not reward-eligible yet.
            assert body["manual_review_status"] == ManualReviewState.UNREVIEWED.value
            assert body["reward_status"] == RewardState.INELIGIBLE.value
            assert body["verification"]["sandbox_mode"] == "landrun+seccomp"
            assert report.status_code == 200
            assert report.json()["report"] == {"accepted": True}
            assert report.json()["report_sha256"].startswith("sha256:")
        finally:
            await kit.teardown()

    run(scenario())


def test_a_human_review_is_audited_and_claims_the_problem_winner():
    async def scenario():
        import uuid

        from conjectures_subnet.db import submissions as store

        kit = await harness().setup()
        try:
            created = (await _post(kit, valid_bundle())).json()
            await _record_verdict(kit, created["submission_id"], accepted=True)
            async with kit.session() as session:
                view = await store.record_human_review(
                    session,
                    uuid.UUID(created["submission_id"]),
                    decision=ReviewOutcome.APPROVED,
                    reviewer="operator:alice",
                    reason_code="REVIEW_APPROVED",
                    notes="internal audit note",
                )
                await session.commit()
                assert view.submission.manual_review_status == ManualReviewState.APPROVED
                assert view.submission.reward_status == RewardState.ELIGIBLE
                assert view.review.kind == ReviewerKind.HUMAN
                assert view.review.reviewer == "operator:alice"
                assert view.winner.submission_id == view.submission.id
        finally:
            await kit.teardown()

    run(scenario())


def test_a_verified_proof_is_eligible_when_review_is_disabled():
    async def scenario():
        kit = await harness(MANUAL_REWARD_REVIEW_ENABLED="false").setup()
        try:
            created = (await _post(kit, valid_bundle())).json()
            assert created["manual_review_required"] is False
            await _record_verdict(kit, created["submission_id"], accepted=True)
            async with kit.session() as session:
                submission = await session.get(Submission, created["submission_id"])
                assert submission.verification_status == VerificationState.VERIFIED
                # Recorded as an AUTOMATIC policy decision, not left implicit.
                assert submission.manual_review_status == ManualReviewState.APPROVED
                assert submission.reward_status == RewardState.ELIGIBLE
        finally:
            await kit.teardown()

    run(scenario())


def test_a_rejected_proof_never_becomes_eligible():
    async def scenario():
        kit = await harness(MANUAL_REWARD_REVIEW_ENABLED="false").setup()
        try:
            created = (await _post(kit, valid_bundle())).json()
            await _record_verdict(kit, created["submission_id"], accepted=False)
            async with kit.session() as session:
                submission = await session.get(Submission, created["submission_id"])
                assert submission.verification_status == VerificationState.REJECTED
                assert submission.reward_status == RewardState.INELIGIBLE
                assert submission.failure_reason == "LEAN_KERNEL_REJECTED"
        finally:
            await kit.teardown()

    run(scenario())


def test_the_event_history_covers_every_status_change():
    async def scenario():
        kit = await harness(MANUAL_REWARD_REVIEW_ENABLED="false").setup()
        try:
            created = (await _post(kit, valid_bundle())).json()
            await _record_verdict(kit, created["submission_id"], accepted=True)
            from sqlalchemy import select

            async with kit.session() as session:
                events = (
                    await session.execute(select(SubmissionEvent).order_by(SubmissionEvent.id))
                ).scalars().all()
            assert [event.status_field for event in events] == [
                SubmissionStatusField.CREATED,
                SubmissionStatusField.VERIFICATION,
                SubmissionStatusField.MANUAL_REVIEW,
                SubmissionStatusField.REWARD,
            ]
            # Exactly one typed cause per event, enforced by events_single_cause.
            verification = events[1]
            assert verification.verification_run_id is not None
            assert verification.review_decision_id is None
            assert verification.reward_event_id is None
        finally:
            await kit.teardown()

    run(scenario())


def test_positive_and_counterexample_share_one_atomic_winner_and_one_payout():
    async def scenario():
        import json
        import uuid
        from datetime import datetime, timezone

        from sqlalchemy import func, select

        from conjectures_subnet.db import submissions as store

        kit = await harness(MANUAL_REWARD_REVIEW_ENABLED="false").setup()
        try:
            created = []
            async with kit.session() as session:
                for index, mode in enumerate(("formalized", "counterexample"), start=1):
                    proof = f"theorem target : True := by trivial\n-- outcome {index}\n".encode()
                    proof_digest = sha256_bytes(proof)
                    key = uuid.uuid4()
                    task_id = f"paired-{mode}"
                    payment_reference = f"100-{index:04d}"
                    request_digest = store.canonical_request_digest(
                        hotkey=HOTKEY,
                        task_id=task_id,
                        task_bundle_sha256=TASK_DIGEST,
                        proof_sha256=proof_digest,
                        payment_reference=payment_reference,
                        idempotency_key=str(key),
                    )
                    view = await store.create_submission(
                        session,
                        store.NewSubmission(
                            hotkey=HOTKEY,
                            idempotency_key=key,
                            request_digest=request_digest,
                            task_id=task_id,
                            problem_id="shared-mathematical-problem",
                            task_mode=mode,
                            task_bundle_sha256=TASK_DIGEST,
                            proof_content=proof,
                            proof_sha256=proof_digest,
                            payment_reference=payment_reference,
                            payment_sender=COLDKEY,
                            payment_amount_rao=500_000_000,
                            payment_block=100,
                            request_timestamp_ms=1_700_000_000_000 + index,
                            hotkey_signature=b"s" * 64,
                            manual_review_required=False,
                            review_policy_version="v1",
                        ),
                    )
                    now = datetime.now(timezone.utc)
                    await store.record_verification_result(
                        session,
                        view.submission,
                        accepted=True,
                        reason_code="VERIFIED",
                        stage="COMPLETED",
                        verifier_version="test",
                        container_digest="sha256:" + "11" * 32,
                        sandbox_mode="landrun+seccomp",
                        checks={"lean_kernel_passed": True},
                        report=json.dumps({"accepted": True, "mode": mode}).encode(),
                        started_at=now,
                        finished_at=now,
                    )
                    created.append(view.submission.id)
                await session.commit()

            async with kit.session() as session:
                first = await session.get(Submission, created[0])
                second = await session.get(Submission, created[1])
                assert first.reward_status == RewardState.ELIGIBLE
                assert second.reward_status == RewardState.INELIGIBLE
                assert second.failure_reason == "PROBLEM_ALREADY_WON"
                assert await session.scalar(select(func.count()).select_from(ProblemWinner)) == 1
                winner = await session.get(ProblemWinner, "shared-mathematical-problem")
                assert winner.submission_id == first.id

                payout = await store.create_reward_event(
                    session,
                    first.id,
                    bounty_amount_rao=1_000_000_000,
                    bounty_commit="abcdef0",
                )
                await session.commit()
                assert payout.status == PayoutState.PENDING

                await store.mark_reward_submitted(
                    session,
                    payout.id,
                    extrinsic_reference="200-0003",
                    submitted_block=200,
                )
                await session.commit()
                await store.mark_reward_confirmed(
                    session, payout.id, finalized_block=200
                )
                await session.commit()
                confirmed = await session.get(RewardEvent, payout.id)
                rewarded = await session.get(Submission, first.id)
                assert confirmed.status == PayoutState.CONFIRMED
                assert confirmed.extrinsic_reference == "200-0003"
                assert rewarded.reward_status == RewardState.REWARDED
        finally:
            await kit.teardown()

    run(scenario())


# --- discovery and operations -------------------------------------------------------


def test_task_list_publishes_the_submission_contract():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await _client(kit) as client:
                response = await client.get("/v1/tasks")
            body = response.json()
            assert response.status_code == 200
            assert body["bundle_format"] == "conjectures-submission/v1"
            assert body["submission_price_rao"] == 500_000_000
            assert body["payment_recipient"] == kit.settings.payment_recipient
            assert [task["task_id"] for task in body["tasks"]] == [TASK_ID]
            assert body["tasks"][0]["problem_id"] == "fc-test-fixture-problem"
            assert body["tasks"][0]["mode"] == "formalized"
            assert body["tasks"][0]["tier"] == "tier-1"
        finally:
            await kit.teardown()

    run(scenario())


def test_health_and_readiness():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await _client(kit) as client:
                health = await client.get("/healthz")
                ready = await client.get("/readyz")
            assert health.status_code == 200
            assert ready.status_code == 200
            assert ready.json() == {
                "status": "ok",
                "database": True,
                "task_pool": True,
                "tasks": 1,
            }
        finally:
            await kit.teardown()

    run(scenario())


def test_validation_errors_do_not_leak_internals():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await _client(kit) as client:
                response = await client.get(
                    "/v1/submissions/not-a-uuid", headers=read_headers()
                )
            assert response.status_code == 400
            raw = response.text
            assert "/home" not in raw and ".py" not in raw and "line " not in raw
        finally:
            await kit.teardown()

    run(scenario())
