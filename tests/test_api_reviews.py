"""The reviewer surface: `/v1/admin/reviews`.

Four things are worth a test here, and they are the four a later change could break without
anything else noticing:

* the role gate, from all three sides — anonymous, signed in without it, signed in with it;
* that a submission with no advisory assessment is still on the queue, because that is the case a
  reviewer must not be prevented from deciding;
* that the response is an allowlist — a citation's page text and an unknown verdict key are both
  written into the fixture rows on purpose, and neither may come back;
* that money and digests keep their precision across the boundary.

The advisory rows are inserted through the ORM mirror rather than by calling
`conjectures-autoreview`, which is a separate repository with a provider key. The database
constraints do the checking either way: a row whose promoted columns disagreed with its verdict, or
whose verdict was missing a base field, would be refused on insert.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

pytest.importorskip("fastapi", reason="submission API tests need the service extra")
pytest.importorskip("sqlalchemy", reason="submission API tests need the db extra")
pytest.importorskip("httpx", reason="submission API tests need the service extra")
pytest.importorskip("psycopg", reason="submission API tests need the db extra")

import datetime as dt
import json
from decimal import Decimal

from conftest_api import (
    HOTKEY,
    TASK_ID,
    distinct_bundle,
    harness,
    new_key,
    postgres_dsn,
    submission_headers,
)

from conjectures_subnet.db import accounts as account_store
from conjectures_subnet.db import submissions as store
from conjectures_subnet.db.autoreview_models import (
    AdvisoryConfidence,
    AdvisoryOutcome,
    AutoreviewRun,
    AutoreviewStageResult,
    RunOrigin,
    RunStatus,
    StageStatus,
)
from conjectures_subnet.db.models import (
    REVIEWER_ROLE,
    Account,
    LoginChallengeKind,
    Submission,
)
from submission_api import sessions

pytestmark = pytest.mark.skipif(
    postgres_dsn() is None,
    reason="no database: run `docker compose -f docker-compose.pytest-db.yml up -d`",
)

EMAIL = "reviewer@example.com"
PACK_SHA256 = bytes.fromhex("11" * 32)
ATTEMPT_SHA256 = bytes.fromhex("ab" * 32)
PROMPT_SHA256 = bytes.fromhex("cd" * 32)

REPORT = {
    "schema_version": 1,
    "task_id": TASK_ID,
    "accepted": True,
    "stage": "COMPLETED",
    "reason_code": "VERIFIED",
    "checks": {"lean_kernel_passed": True},
}

# A verdict as `conjectures-autoreview` archives one, plus two things this suite adds deliberately:
# a citation carrying `content`, and a verdict key the response model does not know. Both must be
# dropped rather than forwarded, and neither may raise.
VERDICT = {
    "reason_code": "ADVISORY_FAITHFUL",
    "confidence": "high",
    "summary": "The Lean target is a faithful rendering of the published conjecture.",
    "findings": [],
    "input_attempted_to_instruct": False,
    "informal_reading": "Every open subset of measure above a third contains a product triple.",
    "formal_reading": "The target quantifies over exactly those sets.",
    "definitions_not_shown": ["Green3.ProductFree"],
    "a_field_added_upstream_later": "must not reach the response",
}

CITATIONS = [
    {
        "url": "https://arxiv.org/abs/2604.24021",
        "title": "A paper the model was served",
        "retrieved_at": "2026-08-11T12:34:56+00:00",
        "content": "THE RETRIEVED PAGE TEXT, WHICH IS NEVER SERVED",
    }
]

SEARCH = {
    "id": "web",
    "engine": "exa",
    "max_results": 10,
    "search_prompt": "a long instruction of ours, not evidence about the submission",
}


def run(coroutine):
    return asyncio.run(coroutine)


async def _client(kit, **kwargs):
    from httpx import ASGITransport, AsyncClient

    return AsyncClient(
        transport=ASGITransport(app=kit.app, raise_app_exceptions=True),
        base_url="http://validator.test",
        **kwargs,
    )


async def _sign_in(kit, http) -> dict:
    """Complete the magic-link flow, minting a token this test knows.

    The challenge table stores only a digest, so the token cannot be read back out of it — the same
    approach `test_api_accounts.py` takes, and for the same reason.
    """
    requested = await http.post("/v1/auth/email/request-link", json={"email": EMAIL})
    assert requested.status_code == 202, requested.text

    token = sessions.new_token()
    async with kit.session() as session:
        await account_store.create_challenge(
            session,
            kind=LoginChallengeKind.EMAIL,
            secret_digest=account_store.digest(token),
            expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(minutes=15),
            email=EMAIL,
        )
        await session.commit()

    verified = await http.post("/v1/auth/email/verify", json={"token": token})
    assert verified.status_code == 200, verified.text
    return verified.json()["account"]


async def _grant_reviewer(kit, account_id: str) -> None:
    """Roles are never client input, so a test grants one the way an operator would: in the row."""
    async with kit.session() as session:
        account = await session.get(Account, uuid.UUID(account_id))
        assert account is not None
        account.roles = [*account.roles, REVIEWER_ROLE]
        await session.commit()


async def _reviewer(kit, http) -> dict:
    account = await _sign_in(kit, http)
    await _grant_reviewer(kit, account["id"])
    return account


async def _submit(kit, marker: str) -> str:
    bundle, digest = distinct_bundle(marker, hotkey=HOTKEY)
    async with await _client(kit) as http:
        response = await http.post(
            "/v1/submissions",
            content=bundle,
            headers=submission_headers(
                bundle,
                hotkey=HOTKEY,
                idempotency_key=new_key(),
                payment_reference=f"0xpay-{marker}",
                proof_digest=digest,
            ),
        )
    assert response.status_code == 201, response.text
    return response.json()["submission_id"]


async def _verify(kit, submission_id: str) -> None:
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
            checks={"lean_kernel_passed": True},
            report=json.dumps(REPORT).encode("utf-8"),
            started_at=dt.datetime.now(dt.UTC),
            finished_at=dt.datetime.now(dt.UTC),
        )
        await session.commit()


async def _verified(kit, marker: str) -> str:
    submission_id = await _submit(kit, marker)
    await _verify(kit, submission_id)
    return submission_id


async def _assess(
    kit,
    submission_id: str,
    *,
    stage: str = "faithfulness",
    status: StageStatus = StageStatus.COMPLETED,
    verdict: dict | None = None,
    citations: list | None = None,
    search: dict | None = None,
    cost_usd: Decimal | None = Decimal("0.116610"),
    attempt_sha256: bytes | None = ATTEMPT_SHA256,
    detail: str | None = None,
    attempt: int = 1,
    started_at: dt.datetime | None = None,
) -> int:
    """One advisory run and one stage row against it, as autoreview would have published them."""
    body = VERDICT if verdict is None else verdict
    complete = status is StageStatus.COMPLETED
    started = started_at or dt.datetime.now(dt.UTC)

    async with kit.session() as session:
        run_row = AutoreviewRun(
            submission_id=uuid.UUID(submission_id),
            attempt=attempt,
            status=RunStatus.COMPLETED,
            started_by=RunOrigin.SERVICE,
            pack_sha256=PACK_SHA256,
            review_policy_version="v1",
            tool_version="0.1.0",
            started_at=started,
            finished_at=started + dt.timedelta(seconds=40),
        )
        session.add(run_row)
        await session.flush()

        session.add(
            AutoreviewStageResult(
                submission_id=uuid.UUID(submission_id),
                run_id=run_row.id,
                stage=stage,
                stage_version="1",
                status=status,
                model_requested="anthropic/claude-opus-5",
                model_served="anthropic/claude-opus-5" if complete else None,
                provider="Amazon Bedrock" if complete else None,
                reason_code=body["reason_code"] if complete else None,
                outcome=AdvisoryOutcome.APPROVE if complete else None,
                confidence=(
                    AdvisoryConfidence(body["confidence"]) if complete else None
                ),
                summary=body["summary"] if complete else None,
                input_attempted_to_instruct=(
                    body["input_attempted_to_instruct"] if complete else None
                ),
                verdict=body if complete else None,
                search=search,
                citations=citations if citations is not None else [],
                detail=detail if not complete else None,
                prompt_tokens=12_345,
                completion_tokens=678,
                cost_usd=cost_usd,
                attempt_sha256=attempt_sha256 if complete else None,
                prompt_sha256=PROMPT_SHA256 if complete else None,
                archive_path=f"{submission_id}/{stage}-{ATTEMPT_SHA256.hex()[:8]}",
                started_at=started if complete else None,
                finished_at=started + dt.timedelta(seconds=39) if complete else None,
            )
        )
        await session.commit()
    return run_row.id


# --- The gate -------------------------------------------------------------------------------


def test_the_queue_is_refused_without_the_reviewer_role():
    """Anonymous is 401 and a signed-in miner is 403.

    The two are different answers on purpose: a caller with no session has something to do about
    it, and one with the wrong role does not. `ROLE_REQUIRED` says which, rather than making a
    reviewer wonder whether their session expired.
    """

    async def scenario():
        kit = await harness().setup()
        try:
            async with await _client(kit) as http:
                anonymous = await http.get("/v1/admin/reviews")
                assert anonymous.status_code == 401
                assert anonymous.json()["reason_code"] == "NOT_AUTHENTICATED"

                account = await _sign_in(kit, http)
                assert account["roles"] == ["MINER"]

                miner = await http.get("/v1/admin/reviews")
                assert miner.status_code == 403
                assert miner.json()["reason_code"] == "ROLE_REQUIRED"

                await _grant_reviewer(kit, account["id"])
                reviewer = await http.get("/v1/admin/reviews")
                assert reviewer.status_code == 200
        finally:
            await kit.teardown()

    run(scenario())


def test_the_detail_route_is_gated_too():
    """The dependency is on the router, so a route added later inherits it. This is the check that
    the router-level declaration is actually in force rather than only on the list route."""

    async def scenario():
        kit = await harness().setup()
        try:
            submission_id = await _verified(kit, "gate-detail")
            async with await _client(kit) as http:
                anonymous = await http.get(f"/v1/admin/reviews/{submission_id}")
                assert anonymous.status_code == 401

                await _reviewer(kit, http)
                allowed = await http.get(f"/v1/admin/reviews/{submission_id}")
                assert allowed.status_code == 200
        finally:
            await kit.teardown()

    run(scenario())


def test_the_review_surface_is_never_cached():
    """`no-store`, not the public feeds' `public, max-age`.

    These bodies are authorised by a session cookie and carry unpublished review material. The
    deployment puts a shared cache in front of the API, and one reviewer's page must not be able to
    answer the next request.
    """

    async def scenario():
        kit = await harness().setup()
        try:
            submission_id = await _verified(kit, "no-store")
            async with await _client(kit) as http:
                await _reviewer(kit, http)
                for path in ("/v1/admin/reviews", f"/v1/admin/reviews/{submission_id}"):
                    response = await http.get(path)
                    assert response.status_code == 200, path
                    assert response.headers["cache-control"] == "no-store", path
        finally:
            await kit.teardown()

    run(scenario())


# --- The queue ------------------------------------------------------------------------------


def test_a_submission_with_no_assessment_is_still_on_the_queue():
    """The case that must not be filtered out.

    A submission is due for review the moment Lean verifies it, whether or not the advisory service
    has reached it. Driving the queue off `autoreview.runs` instead of off the submissions awaiting
    review would hide exactly the rows a reviewer is waiting on.
    """

    async def scenario():
        kit = await harness().setup()
        try:
            submission_id = await _verified(kit, "unassessed")
            async with await _client(kit) as http:
                await _reviewer(kit, http)
                page = (await http.get("/v1/admin/reviews")).json()

                assert [item["submission_id"] for item in page["items"]] == [
                    submission_id
                ]
                item = page["items"][0]
                assert item["attempts"] == []
                assert item["manual_review_status"] == "UNREVIEWED"
                # Named from the catalog, not from a Lean identifier, and stated in full: the
                # reviewer is being asked whether the proof settles *this*.
                assert item["display_title"]
                assert item["statement"]
                assert item["hotkey"] == HOTKEY
                assert item["task_bundle_sha256"].startswith("sha256:")
        finally:
            await kit.teardown()

    run(scenario())


def test_the_queue_lists_only_undecided_work_but_the_detail_route_serves_a_decided_row():
    """A decided submission leaves the queue and stays readable.

    Both halves matter. A queue that kept decided rows would stop being a queue; a detail route that
    dropped them would make the advisory record unavailable at exactly the moment a decision is
    questioned.
    """

    async def scenario():
        kit = await harness().setup()
        try:
            submission_id = await _verified(kit, "decided")
            await _assess(kit, submission_id)
            async with kit.session() as session:
                submission = await session.get(Submission, uuid.UUID(submission_id))
                await store.approve_automatically(session, submission)
                await session.commit()

            async with await _client(kit) as http:
                await _reviewer(kit, http)
                page = (await http.get("/v1/admin/reviews")).json()
                assert page["items"] == []

                detail = await http.get(f"/v1/admin/reviews/{submission_id}")
                assert detail.status_code == 200
                body = detail.json()
                assert body["manual_review_status"] == "APPROVED"
                assert len(body["attempts"]) == 1
        finally:
            await kit.teardown()

    run(scenario())


def test_an_unverified_submission_is_absent_rather_than_forbidden():
    """Matching `GET /v1/results/{id}`: there is no advisory record for unverified work, so telling
    a caller the id exists would be the only thing a 403 achieved."""

    async def scenario():
        kit = await harness().setup()
        try:
            submission_id = await _submit(kit, "unverified")
            async with await _client(kit) as http:
                await _reviewer(kit, http)
                missing = await http.get(f"/v1/admin/reviews/{submission_id}")
                assert missing.status_code == 404
                assert (
                    await http.get(f"/v1/admin/reviews/{uuid.uuid4()}")
                ).status_code == 404
        finally:
            await kit.teardown()

    run(scenario())


# --- The assessments ------------------------------------------------------------------------


def test_an_assessment_is_served_with_its_verdict_and_a_key_naming_its_archive():
    """The key is `<stage>-<first 8 hex of attempt_sha256>`, which is also the archive directory.

    That is what lets a reviewer cite one attempt and an operator find its bytes under
    `assessments/<submission_id>/<key>` without a second lookup.
    """

    async def scenario():
        kit = await harness().setup()
        try:
            submission_id = await _verified(kit, "assessed")
            await _assess(kit, submission_id, search=SEARCH, citations=CITATIONS)
            async with await _client(kit) as http:
                await _reviewer(kit, http)
                item = (await http.get("/v1/admin/reviews")).json()["items"][0]

                (attempt,) = item["attempts"]
                assert attempt["key"] == f"faithfulness-{ATTEMPT_SHA256.hex()[:8]}"
                assert attempt["stage"] == "faithfulness"
                assert attempt["status"] == "COMPLETED"
                assert attempt["outcome"] == "APPROVE"
                assert attempt["model_requested"] == "anthropic/claude-opus-5"
                assert attempt["review_policy_version"] == "v1"
                assert attempt["attempt"] == 1

                verdict = attempt["verdict"]
                assert verdict["reason_code"] == "ADVISORY_FAITHFUL"
                assert verdict["confidence"] == "high"
                assert verdict["summary"] == VERDICT["summary"]
                assert verdict["input_attempted_to_instruct"] is False
                assert verdict["definitions_not_shown"] == ["Green3.ProductFree"]
                # Stage-specific fields of the other stage are null rather than absent, so one
                # client shape reads every stage.
                assert verdict["target_reading"] is None
                assert verdict["prior_sources"] == []

                # That it searched, and with what bounds. The search *prompt* is ours, not evidence.
                assert attempt["search"] == {
                    "id": "web",
                    "engine": "exa",
                    "max_results": 10,
                }
        finally:
            await kit.teardown()

    run(scenario())


def test_a_citations_page_text_is_never_served():
    """The retrieved third-party text is not stored, and this surface would not serve it if it were.

    The fixture row carries `content` on its citation deliberately. An allowlist keeps it out; a
    passthrough of the JSONB column would publish it to every reviewer's browser and into any log
    that records a response body.
    """

    async def scenario():
        kit = await harness().setup()
        try:
            submission_id = await _verified(kit, "citation")
            await _assess(kit, submission_id, citations=CITATIONS)
            async with await _client(kit) as http:
                await _reviewer(kit, http)
                response = await http.get("/v1/admin/reviews")
                (attempt,) = response.json()["items"][0]["attempts"]

                (citation,) = attempt["citations"]
                assert set(citation) == {"url", "title", "retrieved_at"}
                assert "NEVER SERVED" not in response.text
        finally:
            await kit.teardown()

    run(scenario())


def test_an_unknown_verdict_field_is_dropped_rather_than_forwarded_or_fatal():
    """`stage_results.verdict` is written by another repository on its own release cycle.

    So the response names the fields it serves. A key added upstream is invisible here until
    somebody adds a line — which is the failure mode worth having, and better than either
    forwarding whatever appears or refusing the row because a field is unrecognised.
    """

    async def scenario():
        kit = await harness().setup()
        try:
            submission_id = await _verified(kit, "unknown-field")
            await _assess(kit, submission_id)
            async with await _client(kit) as http:
                await _reviewer(kit, http)
                response = await http.get("/v1/admin/reviews")
                assert response.status_code == 200

                (attempt,) = response.json()["items"][0]["attempts"]
                assert "a_field_added_upstream_later" not in attempt["verdict"]
                assert "must not reach the response" not in response.text
                # The known fields still arrived: this is a drop, not a rejection of the row.
                assert attempt["verdict"]["summary"] == VERDICT["summary"]
        finally:
            await kit.teardown()

    run(scenario())


def test_cost_is_a_decimal_string_so_six_places_survive_the_boundary():
    """`NUMERIC(12, 6)` through a float would quietly lose what the column exists to keep. This is
    the number an operator reconciles a provider invoice against."""

    async def scenario():
        kit = await harness().setup()
        try:
            submission_id = await _verified(kit, "cost")
            await _assess(kit, submission_id, cost_usd=Decimal("0.000730"))
            async with await _client(kit) as http:
                await _reviewer(kit, http)
                (attempt,) = (await http.get("/v1/admin/reviews")).json()["items"][0][
                    "attempts"
                ]
                assert attempt["cost_usd"] == "0.000730"
                assert attempt["usage"] == {
                    "prompt_tokens": 12_345,
                    "completion_tokens": 678,
                }
        finally:
            await kit.teardown()

    run(scenario())


def test_a_skipped_stage_is_served_with_its_reason_and_no_verdict():
    """A stage the cascade never reached is history, not an absence.

    It has no digest and therefore no archive, so its key falls back to the row rather than naming a
    directory that does not exist. `detail` is what the database already forces it to carry.
    """

    async def scenario():
        kit = await harness().setup()
        try:
            submission_id = await _verified(kit, "skipped")
            await _assess(
                kit,
                submission_id,
                stage="originality",
                status=StageStatus.SKIPPED,
                cost_usd=None,
                attempt_sha256=None,
                detail="Not run: injection found ADVISORY_INJECTION_ATTEMPT",
            )
            async with await _client(kit) as http:
                await _reviewer(kit, http)
                (attempt,) = (await http.get("/v1/admin/reviews")).json()["items"][0][
                    "attempts"
                ]

                assert attempt["status"] == "SKIPPED"
                assert attempt["verdict"] is None
                assert attempt["outcome"] is None
                assert attempt["detail"].startswith("Not run:")
                assert attempt["cost_usd"] is None
                assert attempt["key"].startswith("originality-row")
        finally:
            await kit.teardown()

    run(scenario())


def test_attempts_come_back_newest_first_with_never_run_stages_last():
    """A later pass read a later pack or a later prompt, so it is the more informative one. A
    skipped stage has no time at all and sorts last rather than first, where a null would put it."""

    async def scenario():
        kit = await harness().setup()
        try:
            submission_id = await _verified(kit, "ordering")
            earlier = dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)
            await _assess(
                kit,
                submission_id,
                attempt=1,
                started_at=earlier,
                attempt_sha256=bytes.fromhex("01" * 32),
            )
            await _assess(
                kit,
                submission_id,
                attempt=2,
                attempt_sha256=bytes.fromhex("02" * 32),
            )
            await _assess(
                kit,
                submission_id,
                attempt=3,
                stage="originality",
                status=StageStatus.SKIPPED,
                cost_usd=None,
                attempt_sha256=None,
                detail="Not run: the sweep stopped earlier",
            )

            async with await _client(kit) as http:
                await _reviewer(kit, http)
                (item,) = (await http.get("/v1/admin/reviews")).json()["items"]
                keys = [attempt["key"] for attempt in item["attempts"]]

                assert keys[:2] == ["faithfulness-02020202", "faithfulness-01010101"]
                assert keys[2].startswith("originality-row")
                assert len(keys) == 3
        finally:
            await kit.teardown()

    run(scenario())
