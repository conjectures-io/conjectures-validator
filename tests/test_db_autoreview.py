"""The `autoreview` schema's invariants, against a real PostgreSQL database.

These test the SQL, not a writer: every insert below is raw, because the writer lives in
`conjectures-autoreview` and the point here is that the constraints hold whatever writes them.
The projection is advisory and rebuildable, so what the schema has to guarantee is narrow and
exact: a skipped stage cannot look like an approval, a promoted column cannot disagree with the
verdict it was lifted from, and two workers cannot both hold one submission.

Skipped unless a server is reachable. Start the fixed test stack:

    docker compose -f docker-compose.pytest-db.yml up -d
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import DATABASE_SKIP_REASON, postgres_dsn
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from conjectures_subnet.db import submissions as store
from conjectures_subnet.db.engine import async_session_factory, create_async_db_engine
from conjectures_subnet.db.models import _AUTOREVIEW_TABLES, Base
from verifier.hashing import sha256_bytes

pytestmark = pytest.mark.skipif(postgres_dsn() is None, reason=DATABASE_SKIP_REASON)

HOTKEY = "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"
COLDKEY = "5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy"
TASK_ID = "fc-e923379e-fixture-formalized-v1"
TASK_DIGEST = "sha256:" + "ab" * 32
PACK_SHA256 = b"\xa0" * 32
ATTEMPT_SHA256 = b"\xb0" * 32
OWNER = "worker-a"


def run(coroutine):
    return asyncio.run(coroutine)


INSERT_RUN = text(
    """
    INSERT INTO autoreview.runs (
        submission_id, attempt, status, started_by, pack_sha256,
        review_policy_version, tool_version, lease_owner, lease_until,
        last_error, started_at, finished_at
    ) VALUES (
        :submission_id, :attempt, CAST(:status AS autoreview.run_status),
        CAST(:started_by AS autoreview.run_origin), :pack_sha256,
        :review_policy_version, :tool_version, :lease_owner, :lease_until,
        :last_error, :started_at, :finished_at
    )
    RETURNING id
    """
)

# Every JSONB value arrives as a JSON string and is cast, so NULL and an object travel the same
# way and the test does not need a psycopg adapter to say what it means.
INSERT_STAGE = text(
    """
    INSERT INTO autoreview.stage_results (
        submission_id, run_id, stage, stage_version, status,
        model_requested, model_served, provider,
        reason_code, outcome, confidence, summary, input_attempted_to_instruct,
        verdict, search, citations, detail,
        prompt_tokens, completion_tokens, cost_usd,
        attempt_sha256, prompt_sha256, archive_path, started_at, finished_at
    ) VALUES (
        :submission_id, :run_id, :stage, :stage_version,
        CAST(:status AS autoreview.stage_status),
        :model_requested, :model_served, :provider,
        :reason_code, CAST(:outcome AS autoreview.outcome),
        CAST(:confidence AS autoreview.confidence), :summary, :input_attempted_to_instruct,
        CAST(:verdict AS JSONB), CAST(:search AS JSONB), CAST(:citations AS JSONB), :detail,
        :prompt_tokens, :completion_tokens, :cost_usd,
        :attempt_sha256, :prompt_sha256, :archive_path, :started_at, :finished_at
    )
    RETURNING id
    """
)


def verdict(**overrides: Any) -> dict[str, Any]:
    """The five base fields the contract promises are always present."""
    base: dict[str, Any] = {
        "reason_code": "ADVISORY_NO_FINDING",
        "confidence": "high",
        "summary": "nothing found",
        "findings": [],
        "input_attempted_to_instruct": False,
    }
    base.update(overrides)
    return base


def completed(**overrides: Any) -> dict[str, Any]:
    """A COMPLETED stage row whose promoted columns agree with its verdict."""
    body = overrides.pop("verdict", verdict())
    row: dict[str, Any] = {
        "stage": "injection",
        "stage_version": "1",
        "status": "COMPLETED",
        "model_requested": "google/gemini-2.5-flash",
        "model_served": "google/gemini-2.5-flash",
        "provider": "Google",
        "reason_code": body.get("reason_code"),
        "outcome": "NO_FINDING",
        "confidence": body.get("confidence"),
        "summary": body.get("summary"),
        "input_attempted_to_instruct": body.get("input_attempted_to_instruct"),
        "verdict": json.dumps(body),
        "search": None,
        "citations": "[]",
        "detail": None,
        "prompt_tokens": 1200,
        "completion_tokens": 300,
        "cost_usd": "0.001234",
        "attempt_sha256": ATTEMPT_SHA256,
        "prompt_sha256": b"\xc0" * 32,
        "archive_path": "0000/injection-deadbeef",
        "started_at": datetime.now(UTC) - timedelta(seconds=30),
        "finished_at": datetime.now(UTC),
    }
    row.update(overrides)
    return row


def skipped(**overrides: Any) -> dict[str, Any]:
    """A stage the cascade never reached. Every completeness CHECK relaxes; `detail` does not."""
    row: dict[str, Any] = {
        "stage": "originality",
        "stage_version": "1",
        "status": "SKIPPED",
        "model_requested": "anthropic/claude-opus-5",
        "model_served": None,
        "provider": None,
        "reason_code": None,
        "outcome": None,
        "confidence": None,
        "summary": None,
        "input_attempted_to_instruct": None,
        "verdict": None,
        "search": None,
        "citations": "[]",
        "detail": "Not run: injection found ADVISORY_INJECTION_ATTEMPT",
        "prompt_tokens": None,
        "completion_tokens": None,
        "cost_usd": None,
        "attempt_sha256": None,
        "prompt_sha256": None,
        "archive_path": None,
        "started_at": None,
        "finished_at": None,
    }
    row.update(overrides)
    return row


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

    def session(self):
        return async_session_factory(self.engine)()

    async def submit(self) -> uuid.UUID:
        content = f"theorem t : True := trivial -- {uuid.uuid4()}".encode()
        digest = sha256_bytes(content)
        target = f"fc-e923379e-fixture-{uuid.uuid4()}-problem"
        async with self.session() as session:
            view = await store.create_submission(
                session,
                store.NewSubmission(
                    hotkey=HOTKEY,
                    idempotency_key=uuid.uuid4(),
                    request_digest=digest,
                    task_id=TASK_ID,
                    task_bundle_sha256=TASK_DIGEST,
                    problem_id=target,
                    reward_target_id=target,
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

    async def insert_run(self, submission_id: uuid.UUID, **overrides: Any) -> int:
        started = overrides.pop("started_at", datetime.now(UTC) - timedelta(minutes=1))
        params: dict[str, Any] = {
            "submission_id": submission_id,
            "attempt": 1,
            "status": "RUNNING",
            "started_by": "SERVICE",
            "pack_sha256": None,
            "review_policy_version": "v1",
            "tool_version": "0.1.0",
            "lease_owner": OWNER,
            "lease_until": started + timedelta(minutes=20),
            "last_error": None,
            "started_at": started,
            "finished_at": None,
        }
        params.update(overrides)
        async with self.session() as session:
            run_id = await session.scalar(INSERT_RUN, params)
            await session.commit()
        assert run_id is not None
        return run_id

    async def insert_stage(
        self, submission_id: uuid.UUID, run_id: int, row: dict[str, Any]
    ) -> int:
        async with self.session() as session:
            stage_id = await session.scalar(
                INSERT_STAGE, {"submission_id": submission_id, "run_id": run_id, **row}
            )
            await session.commit()
        assert stage_id is not None
        return stage_id

    async def refused(self, coroutine) -> str:
        """The constraint name PostgreSQL refused with, so a test names what it relies on."""
        with pytest.raises(IntegrityError) as caught:
            await coroutine
        return str(caught.value)


# --- the mirror ---------------------------------------------------------------------


def test_the_mirror_is_reachable_by_importing_models_alone():
    """The whole reason `models.py` imports `autoreview_models` at its bottom.

    A declarative class in a module nobody imported is absent from `Base.metadata`, so
    `create_all` would silently omit both tables and the drift check would report them missing
    with no hint as to why. Every existing `from conjectures_subnet.db.models import Base` — the
    drift check, both test harnesses — depends on this holding.
    """
    assert len(_AUTOREVIEW_TABLES) == 2
    assert "autoreview.runs" in Base.metadata.tables
    assert "autoreview.stage_results" in Base.metadata.tables
    for table in _AUTOREVIEW_TABLES:
        assert table.__table__.schema == "autoreview"


# --- runs: the lock and the lease ---------------------------------------------------


def test_only_one_run_per_submission_may_be_running():
    """`runs_one_live_idx` is the lock the whole lifecycle rests on."""

    async def scenario():
        kit = await Kit.setup()
        try:
            submission_id = await kit.submit()
            await kit.insert_run(submission_id, attempt=1)
            message = await kit.refused(kit.insert_run(submission_id, attempt=2))
            assert "runs_one_live_idx" in message

            # A settled run does not block the next claim: that is what makes an operator re-run
            # the same code path as a first assessment.
            async with kit.session() as session:
                await session.execute(
                    text(
                        "UPDATE autoreview.runs SET status = 'FAILED', finished_at = now(), "
                        "lease_owner = NULL, lease_until = NULL, last_error = 'boom'"
                    )
                )
                await session.commit()
            assert await kit.insert_run(submission_id, attempt=2) > 0
        finally:
            await kit.teardown()

    run(scenario())


def test_a_running_run_must_hold_a_lease_and_a_settled_one_must_not():
    async def scenario():
        kit = await Kit.setup()
        try:
            submission_id = await kit.submit()
            # RUNNING without a lease: nothing could ever reclaim it.
            message = await kit.refused(
                kit.insert_run(submission_id, lease_owner=None, lease_until=None)
            )
            assert "runs_running_is_leased" in message

            # COMPLETED while still holding one: it would keep blocking the next claim.
            message = await kit.refused(
                kit.insert_run(
                    submission_id,
                    status="COMPLETED",
                    pack_sha256=PACK_SHA256,
                    finished_at=datetime.now(UTC),
                )
            )
            assert "runs_running_is_leased" in message
        finally:
            await kit.teardown()

    run(scenario())


def test_a_settled_run_must_say_why_and_a_completed_one_must_have_a_pack():
    """The parked-payout lesson: an operator failure that writes no cause leaves nothing to
    decide from later."""

    async def scenario():
        kit = await Kit.setup()
        try:
            submission_id = await kit.submit()
            for status in ("FAILED", "CANCELLED"):
                message = await kit.refused(
                    kit.insert_run(
                        submission_id,
                        status=status,
                        lease_owner=None,
                        lease_until=None,
                        finished_at=datetime.now(UTC),
                        last_error=None,
                    )
                )
                assert "runs_settled_says_why" in message

            message = await kit.refused(
                kit.insert_run(
                    submission_id,
                    status="COMPLETED",
                    lease_owner=None,
                    lease_until=None,
                    finished_at=datetime.now(UTC),
                    pack_sha256=None,
                )
            )
            assert "runs_completed_has_pack" in message
        finally:
            await kit.teardown()

    run(scenario())


# --- stage_results: the projection invariants ---------------------------------------


def test_a_skipped_stage_is_a_row_and_cannot_carry_a_verdict():
    """A skip is written rather than left absent, so the frontend needs to know neither the stage
    list nor the cascade rules to tell it from a stage that did not exist yet."""

    async def scenario():
        kit = await Kit.setup()
        try:
            submission_id = await kit.submit()
            run_id = await kit.insert_run(submission_id)
            assert await kit.insert_stage(submission_id, run_id, skipped()) > 0

            message = await kit.refused(
                kit.insert_stage(
                    submission_id,
                    run_id,
                    skipped(stage="faithfulness", verdict=json.dumps(verdict())),
                )
            )
            assert "stage_results_incomplete_has_no_verdict" in message

            message = await kit.refused(
                kit.insert_stage(
                    submission_id, run_id, skipped(stage="faithfulness", detail=None)
                )
            )
            assert "stage_results_incomplete_says_why" in message
        finally:
            await kit.teardown()

    run(scenario())


def test_a_completed_stage_cannot_lack_an_answer():
    async def scenario():
        kit = await Kit.setup()
        try:
            submission_id = await kit.submit()
            run_id = await kit.insert_run(submission_id)
            assert await kit.insert_stage(submission_id, run_id, completed()) > 0

            for missing in ("model_served", "attempt_sha256", "finished_at"):
                message = await kit.refused(
                    kit.insert_stage(
                        submission_id,
                        run_id,
                        completed(stage=f"lens-{missing}", **{missing: None}),
                    )
                )
                assert "stage_results_completed_is_complete" in message
        finally:
            await kit.teardown()

    run(scenario())


def test_a_promoted_column_may_not_disagree_with_the_verdict_it_came_from():
    """Two renderings of one fact must not be able to disagree. The contract prints
    `reason_code`, `confidence`, `summary` and `input_attempted_to_instruct` both in the envelope
    and inside `verdict`, so the lift is constrained rather than trusted."""

    async def scenario():
        kit = await Kit.setup()
        try:
            submission_id = await kit.submit()
            run_id = await kit.insert_run(submission_id)

            disagreements = (
                {"summary": "something else entirely"},
                {"confidence": "low"},
                {"reason_code": "ADVISORY_INJECTION_ATTEMPT"},
                {"input_attempted_to_instruct": True},
            )
            for index, column in enumerate(disagreements):
                message = await kit.refused(
                    kit.insert_stage(
                        submission_id, run_id, completed(stage=f"lens-{index}", **column)
                    )
                )
                assert "stage_results_promoted_match_verdict" in message
        finally:
            await kit.teardown()

    run(scenario())


def test_a_verdict_missing_a_base_field_entirely_is_rejected():
    """The NULL case, not the wrong-type case.

    `jsonb_typeof(verdict -> 'findings')` returns NULL for an absent key, `NULL = 'array'` is
    NULL, and a CHECK passes on NULL — so a constraint written with `=` would accept a verdict
    with the key missing and catch only a wrong type. `IS NOT DISTINCT FROM` is what closes that,
    and testing only the wrong-type half would leave the hole open and the suite green.
    """

    async def scenario():
        kit = await Kit.setup()
        try:
            submission_id = await kit.submit()
            run_id = await kit.insert_run(submission_id)

            # `findings` is the one base field that is not also a promoted column, so it is where
            # this constraint can be observed on its own: nothing else in the table refers to it.
            body = verdict()
            del body["findings"]
            message = await kit.refused(
                kit.insert_stage(
                    submission_id, run_id, completed(stage="no-findings", verdict=body)
                )
            )
            assert "stage_results_verdict_has_base_fields" in message

            # A key present with the wrong JSON type, each arranged so the promoted column still
            # agrees with `->>` and the completeness CHECK still passes. Without the type clause
            # every one of these would be stored.
            #
            # The boolean-as-a-string case is the realistic one: `->> 'x'` yields 'false', which
            # casts to the boolean false and matches the promoted column exactly, so
            # stage_results_promoted_match_verdict is satisfied by a verdict that is malformed.
            wrong_type = (
                ("findings-object", verdict(findings={}), {}),
                ("summary-number", verdict(summary=123), {"summary": "123"}),
                (
                    "instruct-string",
                    verdict(input_attempted_to_instruct="false"),
                    {"input_attempted_to_instruct": False},
                ),
            )
            for stage, malformed, promoted in wrong_type:
                message = await kit.refused(
                    kit.insert_stage(
                        submission_id,
                        run_id,
                        completed(stage=stage, verdict=malformed, **promoted),
                    )
                )
                assert "stage_results_verdict_has_base_fields" in message, stage

            # A promoted key deleted outright is refused too -- by whichever of the three
            # constraints PostgreSQL evaluates first, since the writer that dropped it from the
            # JSON also has nothing to lift into the column. Three overlapping CHECKs rather than
            # one is the point: the field cannot go missing quietly by any route.
            for field in ("reason_code", "confidence", "summary", "input_attempted_to_instruct"):
                body = verdict()
                del body[field]
                message = await kit.refused(
                    kit.insert_stage(
                        submission_id,
                        run_id,
                        completed(stage=f"missing-{field}", verdict=body),
                    )
                )
                assert "violates check constraint" in message, field
                assert "stage_results_" in message, field
        finally:
            await kit.teardown()

    run(scenario())


def test_citations_default_to_an_empty_array_rather_than_null():
    """The contract promises arrays can be `[]` but are never missing. A default is how you keep
    a promise like that instead of restating it in every writer."""

    async def scenario():
        kit = await Kit.setup()
        try:
            submission_id = await kit.submit()
            run_id = await kit.insert_run(submission_id)
            async with kit.session() as session:
                stage_id = await session.scalar(
                    text(
                        "INSERT INTO autoreview.stage_results "
                        "(submission_id, run_id, stage, stage_version, status, "
                        " model_requested, detail) "
                        "VALUES (:s, :r, 'faithfulness', '1', 'FAILED', 'm', 'provider down') "
                        "RETURNING id"
                    ),
                    {"s": submission_id, "r": run_id},
                )
                await session.commit()
            async with kit.session() as session:
                stored = await session.scalar(
                    text("SELECT citations FROM autoreview.stage_results WHERE id = :id"),
                    {"id": stage_id},
                )
            assert stored == []
        finally:
            await kit.teardown()

    run(scenario())


def test_a_stage_result_cannot_belong_to_another_submissions_run():
    """With `submission_id` repeated on the stage row, only the composite foreign key enforces
    that the two agree."""

    async def scenario():
        kit = await Kit.setup()
        try:
            mine = await kit.submit()
            theirs = await kit.submit()
            run_id = await kit.insert_run(mine)
            message = await kit.refused(kit.insert_stage(theirs, run_id, completed()))
            assert "stage_results_run_same_submission" in message
        finally:
            await kit.teardown()

    run(scenario())


def test_republishing_a_stage_upserts_onto_one_row():
    """`UNIQUE (run_id, stage, model_requested)` is what makes re-syncing an archive free rather
    than duplicating it, and what makes a panel a GROUP BY."""

    async def scenario():
        kit = await Kit.setup()
        try:
            submission_id = await kit.submit()
            run_id = await kit.insert_run(submission_id)
            await kit.insert_stage(submission_id, run_id, completed())

            upsert = text(
                """
                INSERT INTO autoreview.stage_results (
                    submission_id, run_id, stage, stage_version, status,
                    model_requested, model_served, reason_code, outcome, confidence,
                    summary, input_attempted_to_instruct, verdict,
                    attempt_sha256, started_at, finished_at
                ) VALUES (
                    :s, :r, 'injection', '1', 'COMPLETED', 'google/gemini-2.5-flash',
                    'google/gemini-2.5-flash', 'ADVISORY_NO_FINDING',
                    'NO_FINDING', 'low', 'a second look', false,
                    CAST(:verdict AS JSONB), :digest, now() - interval '10 seconds', now()
                )
                ON CONFLICT (run_id, stage, model_requested) DO UPDATE SET
                    confidence = EXCLUDED.confidence,
                    summary = EXCLUDED.summary,
                    verdict = EXCLUDED.verdict,
                    attempt_sha256 = EXCLUDED.attempt_sha256,
                    published_at = now()
                """
            )
            body = verdict(confidence="low", summary="a second look")
            async with kit.session() as session:
                await session.execute(
                    upsert,
                    {
                        "s": submission_id,
                        "r": run_id,
                        "verdict": json.dumps(body),
                        "digest": b"\xd0" * 32,
                    },
                )
                await session.commit()

            async with kit.session() as session:
                rows = (
                    await session.execute(
                        text(
                            "SELECT summary, confidence::TEXT FROM autoreview.stage_results "
                            "WHERE run_id = :r"
                        ),
                        {"r": run_id},
                    )
                ).all()
            assert rows == [("a second look", "low")]
        finally:
            await kit.teardown()

    run(scenario())


def test_dropping_a_run_takes_its_stage_rows_with_it():
    """`ON DELETE CASCADE` on the run link, and none from submissions: submissions are never
    deleted, but a run being re-published should take its rows in one statement."""

    async def scenario():
        kit = await Kit.setup()
        try:
            submission_id = await kit.submit()
            run_id = await kit.insert_run(submission_id)
            await kit.insert_stage(submission_id, run_id, completed())
            await kit.insert_stage(submission_id, run_id, skipped())

            async with kit.session() as session:
                await session.execute(
                    text("DELETE FROM autoreview.runs WHERE id = :r"), {"r": run_id}
                )
                await session.commit()
            async with kit.session() as session:
                remaining = await session.scalar(
                    text("SELECT count(*) FROM autoreview.stage_results")
                )
            assert remaining == 0
        finally:
            await kit.teardown()

    run(scenario())
