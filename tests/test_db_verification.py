"""Claiming, leasing and recording, against a real PostgreSQL database.

Deliberately independent of the API test harness: the verification seam is a component in its
own right, and these are the properties that stop two workers paying twice for one proof or
charging a miner for our dead container.

Skipped unless a server is reachable. Start the fixed test stack:

    docker compose -f docker-compose.pytest-db.yml up -d
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import DATABASE_SKIP_REASON, postgres_dsn
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from conjectures_subnet.db import submissions as store
from conjectures_subnet.db import verification as queue
from conjectures_subnet.db.engine import async_session_factory, create_async_db_engine
from conjectures_subnet.db.models import Base, Submission, VerificationState
from verification_worker.runner import RunnerFailure, VerifierRun
from verification_worker.settings import WorkerSettings
from verification_worker.tasks import ResolvedTask, resolver_from_tasks
from verification_worker.worker import Outcome, VerificationWorker
from verifier.hashing import canonical_json_bytes, sha256_bytes

pytestmark = pytest.mark.skipif(
    postgres_dsn() is None, reason=DATABASE_SKIP_REASON
)

HOTKEY = "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"
COLDKEY = "5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy"
TASK_ID = "fc-e923379e-fixture-formalized-v1"
TASK_DIGEST = "sha256:" + "ab" * 32
OWNER = "worker-a"
OTHER_OWNER = "worker-b"
CONTAINER_DIGEST = "sha256:" + "cd" * 32


def run(coroutine):
    return asyncio.run(coroutine)


@dataclass
class Kit:
    engine: AsyncEngine

    @classmethod
    async def setup(cls) -> Kit:
        engine = create_async_db_engine(postgres_dsn())
        async with engine.begin() as connection:
            # The server is reused across tests, so start from a clean schema each time.
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
        return cls(engine=engine)

    async def teardown(self) -> None:
        await self.engine.dispose()

    @property
    def sessions(self):
        return async_session_factory(self.engine)

    def session(self):
        return self.sessions()

    async def submit(
        self,
        content: bytes = b"theorem t : True := trivial",
        *,
        problem_id: str | None = None,
    ) -> uuid.UUID:
        """One paid submission. Each gets its own conjecture unless a test asks them to
        share: only one submission per problem may hold a reward, so submissions that share
        a problem exercise that contention rather than the lease behaviour tested here."""
        digest = sha256_bytes(content)
        async with self.session() as session:
            view = await store.create_submission(
                session,
                store.NewSubmission(
                    hotkey=HOTKEY,
                    idempotency_key=uuid.uuid4(),
                    request_digest=digest,
                    task_id=TASK_ID,
                    task_bundle_sha256=TASK_DIGEST,
                    problem_id=problem_id or f"fc-e923379e-fixture-{uuid.uuid4()}-problem",
                    task_mode=store.TaskMode.FORMALIZED,
                    proof_content=content,
                    proof_sha256=digest,
                    payment_reference=f"ref-{uuid.uuid4()}",
                    payment_sender=COLDKEY,
                    payment_amount_rao=500_000_000,
                    payment_block=1,
                    hotkey_signature=b"\x11" * 64,
                    manual_review_required=True,
                    review_policy_version="v1",
                    bounty_amount_rao=1_000_000_000,
                    bounty_policy_version="flat-v1",
                ),
            )
            await session.commit()
            return view.submission.id

    async def expire_lease(self, submission_id: uuid.UUID) -> None:
        """Move the lease into the past, rather than sleeping through a real one."""
        async with self.session() as session:
            await session.execute(
                text(
                    "UPDATE submissions SET verification_lease_until = now() - "
                    "interval '1 second' WHERE id = :id"
                ),
                {"id": submission_id},
            )
            await session.commit()

    async def row(self, submission_id: uuid.UUID) -> Submission:
        async with self.session() as session:
            submission = await session.get(Submission, submission_id)
            assert submission is not None
            return submission

    async def runs(self, submission_id: uuid.UUID) -> list:
        async with self.session() as session:
            result = await session.execute(
                text(
                    "SELECT id FROM verification_runs WHERE submission_id = :id "
                    "ORDER BY id"
                ),
                {"id": submission_id},
            )
            return list(result.all())

    async def claim(self, *, owner: str = OWNER, lease: int = 120, cap: int = 3):
        async with self.session() as session:
            claimed = await queue.claim_next(
                session, owner=owner, lease_seconds=lease, max_attempts=cap
            )
            await session.commit()
            return claimed


# --- claiming -----------------------------------------------------------------------


def test_a_claim_leases_the_row_so_a_second_worker_skips_it():
    async def scenario():
        kit = await Kit.setup()
        try:
            submission_id = await kit.submit()
            first = await kit.claim(owner=OWNER)
            assert first is not None
            assert first.submission_id == submission_id
            assert first.task_bundle_sha256 == TASK_DIGEST
            assert first.attempts == 1
            # The claim committed, so the lease — not a held row lock — is what excludes the
            # next worker. This is the whole reason the lease exists.
            assert await kit.claim(owner=OTHER_OWNER) is None

            row = await kit.row(submission_id)
            assert row.verification_lease_owner == OWNER
            assert row.verification_lease_until is not None
        finally:
            await kit.teardown()

    run(scenario())


def test_an_expired_lease_is_claimable_again_and_counts_the_attempt():
    async def scenario():
        kit = await Kit.setup()
        try:
            submission_id = await kit.submit()
            await kit.claim(owner=OWNER)
            # Standing in for a worker that died mid-verification.
            await kit.expire_lease(submission_id)

            second = await kit.claim(owner=OTHER_OWNER)
            assert second is not None
            # Attempts count claims, so a crash spends one on purpose: that is what makes the
            # cap converge instead of retrying a broken submission forever.
            assert second.attempts == 2
            assert (await kit.row(submission_id)).verification_lease_owner == OTHER_OWNER
        finally:
            await kit.teardown()

    run(scenario())


def test_the_attempt_cap_parks_a_submission_instead_of_cycling():
    async def scenario():
        kit = await Kit.setup()
        try:
            submission_id = await kit.submit()
            for expected in (1, 2):
                claimed = await kit.claim(cap=2)
                assert claimed is not None and claimed.attempts == expected
                await kit.expire_lease(submission_id)
            # Two attempts have established that something is wrong with this submission. A
            # third would cost another full task timeout and learn nothing; an operator has to
            # decide whether a refund is owed.
            assert await kit.claim(cap=2) is None
        finally:
            await kit.teardown()

    run(scenario())


def test_a_ruled_submission_is_no_longer_on_the_queue():
    async def scenario():
        kit = await Kit.setup()
        try:
            submission_id = await kit.submit()
            claimed = await kit.claim()
            assert claimed is not None
            await record(kit, submission_id, accepted=False)
            assert await kit.claim() is None
            assert (await kit.row(submission_id)).verification_lease_until is None
        finally:
            await kit.teardown()

    run(scenario())


# --- releasing and extending --------------------------------------------------------


def test_release_returns_the_row_without_waiting_for_the_lease():
    async def scenario():
        kit = await Kit.setup()
        try:
            submission_id = await kit.submit()
            await kit.claim(owner=OWNER, lease=3600)
            async with kit.session() as session:
                assert await queue.release(session, submission_id, owner=OWNER)
                await session.commit()
            # Our failure, so the next worker should not wait out an hour-long lease.
            again = await kit.claim(owner=OTHER_OWNER)
            assert again is not None
            assert again.attempts == 2
        finally:
            await kit.teardown()

    run(scenario())


def test_release_cannot_strip_another_workers_lease():
    async def scenario():
        kit = await Kit.setup()
        try:
            submission_id = await kit.submit()
            await kit.claim(owner=OWNER, lease=3600)
            async with kit.session() as session:
                # A worker whose own lease expired long ago must not be able to unlock a row a
                # second worker is now actively verifying.
                assert not await queue.release(
                    session, submission_id, owner=OTHER_OWNER
                )
                await session.commit()
            assert (await kit.row(submission_id)).verification_lease_owner == OWNER
        finally:
            await kit.teardown()

    run(scenario())


def test_extend_fails_once_the_lease_is_not_ours():
    async def scenario():
        kit = await Kit.setup()
        try:
            submission_id = await kit.submit()
            await kit.claim(owner=OWNER)
            async with kit.session() as session:
                assert await queue.extend(
                    session, submission_id, owner=OWNER, lease_seconds=3900
                )
                await session.commit()

            await kit.expire_lease(submission_id)
            await kit.claim(owner=OTHER_OWNER)
            async with kit.session() as session:
                # False tells the first worker to abandon the job rather than start a verifier
                # whose verdict it is no longer entitled to write.
                assert not await queue.extend(
                    session, submission_id, owner=OWNER, lease_seconds=3900
                )
                await session.commit()
        finally:
            await kit.teardown()

    run(scenario())


# --- recording ----------------------------------------------------------------------


async def record(kit: Kit, submission_id: uuid.UUID, *, accepted: bool):
    async with kit.session() as session:
        submission = await session.get(Submission, submission_id)
        assert submission is not None
        recorded = await store.record_verification_result(
            session,
            submission,
            accepted=accepted,
            reason_code="VERIFIED" if accepted else "LEAN_KERNEL_REJECTED",
            stage="COMPLETED" if accepted else "RUN_KERNEL",
            verifier_version="test",
            container_digest=CONTAINER_DIGEST,
            sandbox_mode="landrun+seccomp",
            checks={"lean_kernel_passed": accepted},
            report=canonical_json_bytes({"accepted": accepted}),
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        await session.commit()
        return recorded


def test_recording_a_verdict_clears_the_lease():
    async def scenario():
        kit = await Kit.setup()
        try:
            submission_id = await kit.submit()
            await kit.claim()
            recorded = await record(kit, submission_id, accepted=True)
            assert recorded.applied

            row = await kit.row(submission_id)
            assert row.verification_status == VerificationState.VERIFIED
            # Cleared in the same transaction as the verdict: the worker is done with the row,
            # and a stale lease would make an operator think work is still in flight.
            assert row.verification_lease_until is None
            assert row.verification_lease_owner is None
        finally:
            await kit.teardown()

    run(scenario())


def test_a_second_verdict_is_kept_but_does_not_overwrite_the_first():
    async def scenario():
        kit = await Kit.setup()
        try:
            submission_id = await kit.submit()
            await record(kit, submission_id, accepted=False)

            # A worker whose lease expired mid-verification finishing after the winner. This is
            # the case the guard exists for.
            late = await record(kit, submission_id, accepted=True)
            assert not late.applied

            row = await kit.row(submission_id)
            # REJECTED is terminal: a late accept must not resurrect a proof the kernel
            # refused, nor make it reward-eligible.
            assert row.verification_status == VerificationState.REJECTED
            assert row.failure_reason == "LEAN_KERNEL_REJECTED"

            # The attempt still happened, so its report is kept for the audit trail.
            assert len(await kit.runs(submission_id)) == 2
        finally:
            await kit.teardown()

    run(scenario())


def test_a_late_accept_files_no_second_review_decision():
    async def scenario():
        kit = await Kit.setup()
        try:
            submission_id = await kit.submit()
            async with kit.session() as session:
                submission = await session.get(Submission, submission_id)
                assert submission is not None
                submission.manual_review_required = False
                await session.commit()

            first = await record(kit, submission_id, accepted=True)
            assert first.applied
            late = await record(kit, submission_id, accepted=True)
            assert not late.applied

            async with kit.session() as session:
                rows = await session.execute(
                    text(
                        "SELECT count(*) FROM review_decisions WHERE submission_id = :id"
                    ),
                    {"id": submission_id},
                )
                # Two AUTOMATIC approvals for one submission would read as two independent
                # policy decisions and could be paid twice.
                assert rows.scalar_one() == 1
        finally:
            await kit.teardown()

    run(scenario())


# --- the worker loop ----------------------------------------------------------------


@dataclass
class FakeRunner:
    """Stands in for the container. The boundary is a report, so a report is all this needs."""

    payload: dict | None = None
    failure: str | None = None
    calls: int = 0

    async def run(self, *, task_dir, proof, expected_task_sha256, timeout_seconds):
        self.calls += 1
        assert expected_task_sha256 == TASK_DIGEST
        assert proof
        if self.failure is not None:
            raise RunnerFailure(self.failure)
        assert self.payload is not None
        return VerifierRun(
            report=self.payload,
            report_bytes=canonical_json_bytes(self.payload),
            container_digest=CONTAINER_DIGEST,
            verifier_version="test",
        )


def report(**overrides) -> dict:
    payload = {
        "accepted": True,
        "reason_code": "VERIFIED",
        "stage": "COMPLETED",
        "checks": {"lean_kernel_passed": True},
        "sandbox_mode": "landrun+seccomp",
    }
    payload.update(overrides)
    return payload


def worker(kit: Kit, runner, env: dict[str, str] | None = None) -> VerificationWorker:
    return VerificationWorker(
        settings=WorkerSettings.from_env(env or {}),
        sessions=kit.sessions,
        runner=runner,
        tasks=resolver_from_tasks(
            repository_commit="e923379e609b9d5987011a1d1f06ec22ea25cd20",
            tasks=(
                ResolvedTask(
                    task_id=TASK_ID,
                    tier="tier-1",
                    task_dir=Path("tasks/pool/tier-1") / TASK_ID,
                    task_bundle_sha256=TASK_DIGEST,
                    timeout_seconds=30,
                ),
            ),
        ),
    )


def test_the_worker_claims_verifies_and_records():
    async def scenario():
        kit = await Kit.setup()
        try:
            submission_id = await kit.submit()
            runner = FakeRunner(payload=report())
            processed = await worker(kit, runner).process_one()

            assert processed is not None
            assert processed.outcome is Outcome.VERDICT
            assert processed.applied
            assert runner.calls == 1

            row = await kit.row(submission_id)
            assert row.verification_status == VerificationState.VERIFIED
            assert row.verification_lease_until is None
            assert len(await kit.runs(submission_id)) == 1

            # The queue is empty, so a second pass has nothing to do.
            assert await worker(kit, FakeRunner(payload=report())).process_one() is None
        finally:
            await kit.teardown()

    run(scenario())


def test_a_rejected_proof_is_terminal_and_never_reward_eligible():
    async def scenario():
        kit = await Kit.setup()
        try:
            submission_id = await kit.submit()
            processed = await worker(
                kit,
                FakeRunner(
                    payload=report(
                        accepted=False,
                        reason_code="LEAN_KERNEL_REJECTED",
                        stage="RUN_KERNEL",
                    )
                ),
            ).process_one()
            assert processed is not None and processed.applied

            row = await kit.row(submission_id)
            assert row.verification_status == VerificationState.REJECTED
            assert row.failure_reason == "LEAN_KERNEL_REJECTED"
            assert row.reward_status.value == "INELIGIBLE"
        finally:
            await kit.teardown()

    run(scenario())


def test_our_own_failure_writes_no_verdict_and_returns_the_row():
    async def scenario():
        kit = await Kit.setup()
        try:
            submission_id = await kit.submit()
            processed = await worker(
                kit,
                FakeRunner(
                    payload=report(
                        accepted=False,
                        reason_code="INSECURE_SANDBOX",
                        stage="CREATE_WORKSPACE",
                    )
                ),
            ).process_one()

            assert processed is not None
            assert processed.outcome is Outcome.OPERATOR
            row = await kit.row(submission_id)
            # The miner already paid. A sandbox that would not start says nothing about their
            # proof, so recording a rejection here would take their fee for our outage.
            assert row.verification_status == VerificationState.UNVERIFIED
            assert row.failure_reason is None
            assert await kit.runs(submission_id) == []
            # Released rather than left leased, so a fixed deployment picks it straight up.
            assert row.verification_lease_until is None
        finally:
            await kit.teardown()

    run(scenario())


def test_an_accept_without_the_real_sandbox_is_refused():
    async def scenario():
        kit = await Kit.setup()
        try:
            submission_id = await kit.submit()
            processed = await worker(
                kit, FakeRunner(payload=report(sandbox_mode="development-fake-landrun"))
            ).process_one()

            assert processed is not None
            assert processed.outcome is Outcome.OPERATOR
            assert processed.reason_code == "INSECURE_SANDBOX"
            # An accept produced outside the reviewed Landlock/seccomp profile is not sound,
            # whatever the report says about it, so it must not become money.
            assert (
                await kit.row(submission_id)
            ).verification_status == VerificationState.UNVERIFIED
        finally:
            await kit.teardown()

    run(scenario())


def test_an_accept_without_the_real_sandbox_is_recorded_when_explicitly_permitted():
    async def scenario():
        kit = await Kit.setup()
        try:
            submission_id = await kit.submit()
            processed = await worker(
                kit,
                FakeRunner(payload=report(sandbox_mode="development-fake-landrun")),
                {"VERIFICATION_ALLOW_INSECURE_SANDBOX": "1"},
            ).process_one()

            assert processed is not None
            assert processed.outcome is Outcome.VERDICT
            # The point of the override: on a host that cannot provide the real isolation the
            # alternative is that every valid proof parks unjudged, which teaches nothing.
            row = await kit.row(submission_id)
            assert row.verification_status == VerificationState.VERIFIED
            # And the run records the isolation that actually ran, so no later reader can mistake
            # this for a verdict produced under the reviewed profile.
            async with kit.session() as session:
                modes = await session.execute(
                    text(
                        "SELECT sandbox_mode FROM verification_runs "
                        "WHERE submission_id = :id"
                    ),
                    {"id": submission_id},
                )
                assert modes.scalars().all() == ["development-fake-landrun"]
        finally:
            await kit.teardown()

    run(scenario())


def test_a_dead_container_leaves_the_submission_on_the_queue():
    async def scenario():
        kit = await Kit.setup()
        try:
            submission_id = await kit.submit()
            processed = await worker(
                kit, FakeRunner(failure="docker: no such image")
            ).process_one()

            assert processed is not None
            assert processed.outcome is Outcome.OPERATOR
            assert await kit.runs(submission_id) == []
            # Claimable again immediately, and the attempt was spent, so a broken
            # deployment still walks to the cap rather than looping forever.
            assert await kit.claim() is not None
        finally:
            await kit.teardown()

    run(scenario())


def test_a_task_that_left_the_allowlist_is_not_verified_against_new_bytes():
    async def scenario():
        kit = await Kit.setup()
        try:
            submission_id = await kit.submit()
            unit = worker(kit, FakeRunner(payload=report()))
            # The published bundle has been regenerated since the miner paid. Their proof was
            # written against the old bytes, so verifying it against the new ones would answer
            # a question nobody asked.
            unit.tasks = resolver_from_tasks(
                repository_commit="e923379e609b9d5987011a1d1f06ec22ea25cd20",
                tasks=(
                    ResolvedTask(
                        task_id=TASK_ID,
                        tier="tier-1",
                        task_dir=Path("tasks/pool/tier-1") / TASK_ID,
                        task_bundle_sha256="sha256:" + "ef" * 32,
                        timeout_seconds=30,
                    ),
                ),
            )
            processed = await unit.process_one()

            assert processed is not None
            assert processed.outcome is Outcome.OPERATOR
            assert processed.reason_code == "TASK_COMMITMENT_MISMATCH"
            assert (
                await kit.row(submission_id)
            ).verification_status == VerificationState.UNVERIFIED
        finally:
            await kit.teardown()

    run(scenario())


def test_drain_works_through_the_whole_queue():
    async def scenario():
        kit = await Kit.setup()
        try:
            for index in range(3):
                await kit.submit(content=f"theorem t{index} : True := trivial".encode())
            processed = await worker(kit, FakeRunner(payload=report())).drain()
            assert len(processed) == 3
            assert all(item.applied for item in processed)
        finally:
            await kit.teardown()

    run(scenario())
