"""Private PostgreSQL tests: ownership, timing, review state, duplicates, retention and no payout."""
import asyncio
import datetime as dt
import json
import uuid
from dataclasses import replace

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from conftest_api import MINER_COLDKEY, TASK_DIGEST, distinct_bundle, harness, postgres_dsn
from test_api_accounts import client, same_origin, sign_in_by_email
from test_api_reviews import _grant_reviewer, _verify
from test_api_session_submissions import SESSION, _digest, keyless_account, params, zip_headers
from test_trajectory_schema import consent_params, example
from scripts.trajectory_assets import build_package
from conjectures_subnet.bounty_policy import TREASURY_CHAT_POLICY
from conjectures_subnet.db import submissions as proof_store
from conjectures_subnet.db.models import ReviewOutcome, RewardEvent, Submission
from conjectures_subnet.db.trajectory_models import ChatLogProgram, TrajectoryAccess, TrajectoryClaim, TrajectoryFingerprint
from submission_api.trajectory_terms import TERMS_SHA256, TERMS_VERSION
from submission_api.trajectory_intake import MAX_UPLOAD_BYTES
from submission_api.routers.trajectories import Checklist

pytestmark = pytest.mark.skipif(postgres_dsn() is None, reason="requires verified private fixture")


# After every fixture proof, so those proofs count as submitted before intake opened.
FUTURE_OPENING = dt.datetime(2027, 1, 1, tzinfo=dt.UTC)


class OpenedHarness:
    """A harness whose chat-log program record says intake opened at `opened_at` (None: not yet)."""
    def __init__(self, inner, opened_at):
        self.inner, self.opened_at = inner, opened_at

    async def setup(self):
        kit = await self.inner.setup()
        if self.opened_at is not None:
            async with kit.session() as session:
                session.add(ChatLogProgram(id=1, opened_at=self.opened_at, terms_version=TERMS_VERSION,
                                           terms_sha256=bytes.fromhex(TERMS_SHA256.removeprefix("sha256:")),
                                           opened_by="test fixture"))
                await session.commit()
        return kit


def kit_config(*, opened_at=FUTURE_OPENING, enabled=True, **options):
    return OpenedHarness(harness(TRAJECTORY_BONUS_ENABLED=str(enabled).lower(), **options), opened_at)


DEPOSITS = "/v1/me/chat-log-deposits"


def dated(p, before):
    """Date the chat (and the event showing the proof) shortly before `before`."""
    start = before - dt.timedelta(hours=1)
    for i, event in enumerate(p["events"]):
        event["timestamp"] = (start + dt.timedelta(seconds=i)).isoformat()
    p["manifest"]["exported_at"] = (start + dt.timedelta(minutes=30)).isoformat()
    return p


def package_for(proof_hash, submission_id):
    p = example()
    p["manifest"].update(synthetic=False, submission_id=submission_id, task_id="fixture",
                         task_bundle_sha256=TASK_DIGEST, final_proof_sha256=proof_hash)
    # Synthetic test-only evidence; the proof itself is the submission's, not a copy in the zip.
    p["artifacts"][0].update(sha256=proof_hash, included=False)
    p["files"].pop("artifacts/Solution.lean")
    return dated(p, dt.datetime.now(dt.UTC))


def relinked(p, **manifest):
    """The same package with different linkage in its manifest."""
    q = json.loads(json.dumps({k: v for k, v in p.items() if k != "files"}))
    q["files"] = dict(p["files"])
    q["manifest"].update(manifest)
    return q


def as_upload(document, state_version=None, origin=None, **consent):
    """Request arguments: the zip package is the body, consent rides in the query."""
    params = consent_params(**consent)
    if state_version is not None:
        params["expected_state_version"] = state_version
    headers = {**same_origin(), "content-type": "application/zip"}
    if origin:
        headers["Origin"] = origin
    return {"content": build_package(document), "params": params, "headers": headers}


async def accepted(kit, http, *, code="REVIEW_APPROVED", required=False, marker="trajectory-fixture",
                   email="session@example.com"):
    """An approved proof. For a new bounty its logs are deposited first, as intake requires."""
    account = await keyless_account(kit, http, email=email)
    bundle, proof_hash = distinct_bundle(marker, coldkey=None)
    p = package_for(proof_hash, None)
    if required:
        deposited = await http.post(DEPOSITS, **as_upload(p))
        assert deposited.status_code == 200 and deposited.json()["status"] == "DEPOSITED", deposited.text
    result = await http.post(SESSION, params=params(bundle_sha256=_digest(bundle)), content=bundle,
                             headers=zip_headers(http))
    assert result.status_code == 201, result.text
    sid = result.json()["submission"]["id"]
    await _verify(kit, sid)
    async with kit.session() as session:
        await proof_store.record_human_decision(session, uuid.UUID(sid), decision=ReviewOutcome.APPROVED,
                                                reason_code=code, reviewer="fixture-reviewer")
        await session.commit()
    if not required:
        p["manifest"]["submission_id"] = sid
    return account, sid, p


async def reviewer(kit, http):
    account = await sign_in_by_email(kit, http, email="trajectory-reviewer@example.com")
    await _grant_reviewer(kit, account["id"])
    return account


def review_body(*, decision="APPROVED_MANUAL_PAYMENT", revision=1, state_version=1):
    return {"decision": decision, "expected_revision": revision, "expected_state_version": state_version,
            "note": "Test-only human evidence checks completed.",
            "checklist": {k: True for k in Checklist.model_fields} if decision == "APPROVED_MANUAL_PAYMENT" else None}


def test_owner_intake_replay_review_and_no_automatic_payout():
    async def scenario():
        kit = await kit_config().setup()
        try:
            async with await client(kit) as owner, await client(kit) as admin, await client(kit) as stranger:
                account, sid, p = await accepted(kit, owner)
                path = f"/v1/me/submissions/{sid}/trajectory"
                assert (await stranger.get(path)).status_code == 401
                await sign_in_by_email(kit, stranger, email="stranger@example.com")
                assert (await stranger.get(path)).status_code == 404
                assert (await stranger.post(path, **as_upload(p))).status_code == 404
                assert (await owner.post(path, **as_upload(p, origin="https://evil.invalid"))).status_code == 403
                assert (await owner.get("/v1/admin/trajectories")).status_code == 403
                upload = await owner.post(path, **as_upload(p))
                assert upload.status_code == 200, upload.text
                assert upload.headers["cache-control"] == "no-store"
                cid = upload.json()["id"]
                assert upload.json()["status"] == "UNDER_REVIEW"
                retry = await owner.post(path, **as_upload(p))
                assert retry.json()["id"] == cid and retry.json()["revision"] == 1
                altered = json.loads(json.dumps(p))
                altered["events"][0]["content"] += " edit"
                assert (await owner.post(path, **as_upload(altered))).status_code == 409
                await reviewer(kit, admin)
                queue = await admin.get("/v1/admin/trajectories")
                assert queue.status_code == 200, queue.text
                assert queue.json()["items"][0]["license_status"] == "NOT_LICENSED"
                downloaded = await admin.get(f"/v1/admin/trajectories/{cid}/package")
                assert downloaded.status_code == 200 and downloaded.headers["cache-control"] == "no-store"
                assert downloaded.headers["content-disposition"].startswith("attachment;")
                assert "sandbox" in downloaded.headers["content-security-policy"]
                assert downloaded.headers["x-content-type-options"] == "nosniff"
                async with kit.session() as session:
                    assert await session.scalar(select(func.count()).select_from(TrajectoryAccess)) == 1
                    record = await session.get(TrajectoryClaim, uuid.UUID(cid))
                    assert record.consent["accepted"] and record.consent["account_id"] == account["id"]
                    assert record.consent["terms_sha256"] == TERMS_SHA256
                    assert record.proof_policy_snapshot["review_policy_version"] == (await session.get(Submission, uuid.UUID(sid))).review_policy_version
                assert (await stranger.get(f"/v1/admin/trajectories/{cid}/package")).status_code == 403
                decision_path = f"/v1/admin/trajectories/{cid}/decision"
                whitespace = review_body()
                whitespace["note"] = " " * 20
                assert (await admin.post(decision_path, json=whitespace, headers=same_origin())).status_code == 400
                missing_checks = review_body()
                missing_checks["checklist"] = None
                assert (await admin.post(decision_path, json=missing_checks, headers=same_origin())).status_code == 400
                partial = review_body()
                partial["checklist"]["original_chat_that_produced_the_proof"] = False
                assert (await admin.post(decision_path, json=partial, headers=same_origin())).status_code == 400
                approved = await admin.post(decision_path, json=review_body(), headers=same_origin())
                assert approved.status_code == 200, approved.text
                assert approved.json()["status"] == "APPROVED_MANUAL_PAYMENT"
                assert approved.json()["payment_status"] == "NOT_ISSUED"
                assert approved.json()["license_status"] == "LICENSED_FOR_SALE"
                assert (await admin.post(decision_path, json=review_body(), headers=same_origin())).status_code == 409
                async with kit.session() as session:
                    assert await session.scalar(select(func.count()).select_from(RewardEvent)) == 0
                    assert await session.scalar(select(func.count()).select_from(TrajectoryClaim)) == 1
                    assert await session.scalar(select(func.count()).select_from(TrajectoryFingerprint)) == 1
                    submission = await session.get(Submission, uuid.UUID(sid))
                    assert str(submission.reward_status) == "ELIGIBLE"
                    current = await proof_store.latest_binding_decision(session, submission.id)
                    await proof_store.correct_human_decision(session, submission.id, supersedes_id=current.id,
                        decision=ReviewOutcome.REJECTED, reason_code="NOT_NOVEL", reviewer="fixture-correction",
                        notes="Prior-established proof discovered", notes_public="Prior-established proof discovered")
                    await session.commit()
                final = await owner.get(path)
                assert final.json()["claim"]["status"] == "BLOCKED_PROOF_CHANGED"
                assert final.json()["eligible"] is False
                kit.app.state.services = replace(kit.services, settings=replace(kit.settings, trajectory_bonus_enabled=False))
                revoked = await admin.post(f"/v1/admin/trajectories/{cid}/reopen", headers=same_origin(),
                    json={"action": "REVOKE_PROOF_CHANGED", "expected_revision": 1, "expected_state_version": 2, "note": "Proof approval was revoked; release bonus reservation."})
                assert revoked.status_code == 200 and revoked.json()["status"] == "REVOKED_PROOF_CHANGED"
                assert revoked.json()["license_status"] == "NOT_LICENSED"
                assert dt.datetime.fromisoformat(revoked.json()["expires_at"]) > dt.datetime.now(dt.UTC) + dt.timedelta(days=89)
                reopened = await admin.post(f"/v1/admin/trajectories/{cid}/reopen", headers=same_origin(),
                    json={"action": "REOPEN_REVIEW", "expected_revision": 1, "expected_state_version": 3, "note": "Reopen for a new full review of this proof."})
                assert reopened.status_code == 200 and reopened.json()["status"] == "UNDER_REVIEW"
                rejected = await admin.post(decision_path, headers=same_origin(), json=review_body(decision="REJECTED", state_version=4))
                assert rejected.status_code == 200  # Review remains operable with intake paused.
        finally:
            await kit.teardown()
    asyncio.run(scenario())


def test_revisions_withdrawal_expiry_and_self_review_refusal():
    async def scenario():
        kit = await kit_config().setup()
        try:
            async with await client(kit) as owner, await client(kit) as admin:
                account, sid, p = await accepted(kit, owner)
                path = f"/v1/me/submissions/{sid}/trajectory"
                first = await owner.post(path, **as_upload(p))
                cid = first.json()["id"]
                await _grant_reviewer(kit, account["id"])
                decision_path = f"/v1/admin/trajectories/{cid}/decision"
                assert (await owner.post(decision_path, json=review_body(), headers=same_origin())).status_code == 409
                await reviewer(kit, admin)
                result = await admin.post(decision_path, json=review_body(decision="NEEDS_CHANGES"), headers=same_origin())
                assert result.status_code == 200, result.text
                p["events"][0]["content"] += " corrected"
                changed = await owner.post(path, **as_upload(p, 2))
                assert changed.json()["revision"] == 2
                assert (await admin.post(decision_path, json=review_body(), headers=same_origin())).status_code == 409
                withdrawn = await owner.post(path + "/withdraw", json={"expected_state_version": 3}, headers=same_origin())
                assert withdrawn.status_code == 200 and withdrawn.json()["content_available"] is False
                assert (await admin.get(f"/v1/admin/trajectories/{cid}/package")).status_code == 404
                p["events"][0]["content"] += " third"
                third = await owner.post(path, **as_upload(p, 4))
                assert third.json()["revision"] == 3
                async with kit.session() as session:
                    claim = await session.get(TrajectoryClaim, uuid.UUID(cid))
                    claim.created_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=100)
                    claim.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
                    await session.commit()
                assert (await admin.get(f"/v1/admin/trajectories/{cid}/package")).status_code == 404
                assert (await admin.post(decision_path, json=review_body(revision=3), headers=same_origin())).status_code == 409
                from scripts.purge_trajectories import purge
                async with kit.session() as session:
                    assert (await purge(session))[0] == 1
                    record = await session.get(TrajectoryClaim, uuid.UUID(cid))
                    assert record.status == "EXPIRED" and record.package is None
                rejected = await admin.post(decision_path, json=review_body(revision=3, decision="REJECTED", state_version=6), headers=same_origin())
                assert rejected.status_code == 200
        finally:
            await kit.teardown()
    asyncio.run(scenario())


def test_disabled_ineligible_synthetic_linkage_and_safe_validation():
    async def scenario():
        kit = await kit_config().setup()
        try:
            async with await client(kit) as owner:
                _, sid, p = await accepted(kit, owner)
                path = f"/v1/me/submissions/{sid}/trajectory"
                kit.app.state.services = replace(kit.services, settings=replace(kit.settings, trajectory_bonus_enabled=False))
                assert (await owner.post(path, **as_upload(p))).status_code == 503
                kit.app.state.services = kit.services
                p["manifest"]["synthetic"] = True
                assert (await owner.post(path, **as_upload(p))).status_code == 409
                p["manifest"]["synthetic"] = False
                p["manifest"]["task_id"] = "wrong-task"
                assert (await owner.post(path, **as_upload(p))).status_code == 409
                p["manifest"]["task_id"] = "fixture"
                async with kit.session() as session:
                    sub = await session.get(Submission, uuid.UUID(sid))
                    submitted_at = sub.created_at
                    sub.created_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=2)
                    await session.commit()
                after = await owner.post(path, **as_upload(p))
                assert after.status_code == 409 and after.json()["reason_code"] == "TRAJECTORY_AFTER_THE_FACT"
                async with kit.session() as session:
                    (await session.get(Submission, uuid.UUID(sid))).created_at = submitted_at
                    await session.commit()
                p["events"][0]["role"] = "SECRET_MUST_NOT_BE_ECHOED"
                invalid = await owner.post(path, **as_upload(p))
                assert invalid.status_code == 400
                assert "SECRET_MUST_NOT_BE_ECHOED" not in invalid.text
                p["events"][0]["role"] = "user"
                oversized = await owner.post(path, content=b"PK", params=consent_params(), headers={
                    **same_origin(), "content-type": "application/zip", "content-length": str(MAX_UPLOAD_BYTES + 1)})
                assert oversized.status_code == 413
                wrong_format = await owner.post(path, content=b"{}", params=consent_params(),
                                                headers={**same_origin(), "content-type": "application/json"})
                assert wrong_format.status_code == 415
                fresh_upload_budget(kit)
                not_a_zip = await owner.post(path, content=b"{}", params=consent_params(),
                                             headers={**same_origin(), "content-type": "application/zip"})
                assert not_a_zip.status_code == 400 and not_a_zip.json()["validation_errors"][0]["message"] == "upload a .zip package"
                unconsented = await owner.post(path, **as_upload(p, accepted="false"))
                assert unconsented.status_code == 400 and unconsented.json()["reason_code"] == "TRAJECTORY_TERMS_CHANGED"
                async with kit.session() as session:
                    current = await proof_store.latest_binding_decision(session, uuid.UUID(sid))
                    await proof_store.correct_human_decision(session, uuid.UUID(sid), supersedes_id=current.id,
                        decision=ReviewOutcome.APPROVED, reason_code="FORMALIZATION_DEFECT_AWARD", reviewer="fixture-correction",
                        notes="Defect award only", notes_public="Defect award only")
                    await session.commit()
                assert (await owner.post(path, **as_upload(p))).status_code == 409
        finally:
            await kit.teardown()
    asyncio.run(scenario())


def test_concurrent_upload_and_review_are_serialized():
    async def scenario():
        kit = await kit_config().setup()
        try:
            async with await client(kit) as owner, await client(kit) as admin:
                _, sid, p = await accepted(kit, owner)
                path = f"/v1/me/submissions/{sid}/trajectory"
                first, second = await asyncio.gather(
                    owner.post(path, **as_upload(p)),
                    owner.post(path, **as_upload(p)))
                assert sorted([first.status_code, second.status_code]) in ([200, 200], [200, 429]), (first.text, second.text)
                if first.status_code != 200:
                    first, second = second, first
                retry = await owner.post(path, **as_upload(p))
                assert retry.json()["id"] == first.json()["id"]
                await reviewer(kit, admin)
                dpath = f"/v1/admin/trajectories/{first.json()['id']}/decision"
                one, two = await asyncio.gather(
                    admin.post(dpath, json=review_body(), headers=same_origin()),
                    admin.post(dpath, json=review_body(), headers=same_origin()))
                assert sorted([one.status_code, two.status_code]) == [200, 409]
                # A changed proof approval blocks this bonus but keeps its reservation until
                # the audited revocation route releases it. The target can receive a new proof.
                async with kit.session() as session:
                    current = await proof_store.latest_binding_decision(session, uuid.UUID(sid))
                    await proof_store.correct_human_decision(session, uuid.UUID(sid), supersedes_id=current.id,
                        decision=ReviewOutcome.REJECTED, reason_code="NOT_NOVEL", reviewer="fixture-correction",
                        notes="Prior-established proof", notes_public="Prior-established proof")
                    await session.commit()
                await keyless_account(kit, owner)
                second_bundle, second_proof = distinct_bundle("different-target-constraint-proof", coldkey=None)
                second_submission = await owner.post(SESSION, params=params(bundle_sha256=_digest(second_bundle)),
                                                     content=second_bundle, headers=zip_headers(owner))
                assert second_submission.status_code == 201, second_submission.text
                async with kit.session() as session:
                    assert await session.scalar(select(func.count()).select_from(TrajectoryClaim)) == 1
                    assert await session.scalar(select(func.count()).select_from(RewardEvent)) == 0
                    original = await session.get(TrajectoryClaim, uuid.UUID(first.json()["id"]))
                    fp = await session.scalar(select(TrajectoryFingerprint))
                    original_id, fp_digest = original.id, fp.digest
                    copy_values = {column.name: getattr(original, column.name)
                                   for column in TrajectoryClaim.__table__.columns}
                    copy_values.update(id=uuid.uuid4(),
                                       submission_id=uuid.UUID(second_submission.json()["submission"]["id"]),
                                       proof_digest=bytes.fromhex(second_proof.removeprefix("sha256:")))
                    # The DB refuses a second approved bonus for a stable target, even if a
                    # future writer bypasses this API's stronger proof/linkage gate.
                    session.add(TrajectoryClaim(**copy_values))
                    with pytest.raises(IntegrityError) as duplicate_target:
                        await session.flush()
                    assert duplicate_target.value.orig.diag.constraint_name == "trajectory_one_approved_target"
                    await session.rollback()
                    session.expunge_all()
                    # A second digest reservation cannot be inserted even by a different code path.
                    session.add(TrajectoryFingerprint(digest=fp_digest, claim_id=original_id))
                    with pytest.raises(IntegrityError):
                        await session.flush()
                    await session.rollback()
        finally:
            await kit.teardown()
    asyncio.run(scenario())


def with_new_bounties(kit):
    kit.services = replace(kit.services, pricing=replace(kit.services.pricing, new_target_policy=TREASURY_CHAT_POLICY))
    kit.app.state.services = kit.services


def fresh_upload_budget(kit):
    # Intake allows three successful uploads per account per hour; scenarios here need more.
    if hasattr(kit.app.state, "trajectory_upload_budget"):
        del kit.app.state.trajectory_upload_budget


def test_new_bounty_logs_are_due_before_the_proof_and_rejection_reopens_the_target():
    from conftest_api import task_entry
    from scripts.purge_trajectories import purge
    target = task_entry().reward_target_id

    async def submit(http, bundle):
        return await http.post(SESSION, params=params(bundle_sha256=_digest(bundle)), content=bundle,
                               headers=zip_headers(http))

    async def scenario():
        kit = await kit_config().setup()
        with_new_bounties(kit)
        try:
            async with await client(kit) as owner, await client(kit) as rival, await client(kit) as admin:
                await keyless_account(kit, owner)
                bundle, proof_hash = distinct_bundle("logs-first", coldkey=None)
                # No logs: refused before the upload and before any credit is spent.
                missing = await submit(owner, bundle)
                assert missing.status_code == 409 and missing.json()["reason_code"] == "CHAT_LOG_REQUIRED"
                assert (await owner.get("/v1/me/credits")).json()["credits_available"] == 1
                p = package_for(proof_hash, None)
                named = relinked(p, submission_id=str(uuid.uuid4()))
                linked = await owner.post(DEPOSITS, **as_upload(named))
                assert linked.status_code == 409 and linked.json()["reason_code"] == "TRAJECTORY_LINK_MISMATCH"
                deposited = await owner.post(DEPOSITS, **as_upload(p))
                assert deposited.status_code == 200, deposited.text
                assert deposited.json()["status"] == "DEPOSITED" and deposited.json()["submission_id"] is None
                assert deposited.headers["cache-control"] == "no-store"
                retry = await owner.post(DEPOSITS, **as_upload(p))
                assert retry.json()["id"] == deposited.json()["id"]
                # Logs for one proof never satisfy another.
                wrong = await submit(owner, distinct_bundle("another-proof", coldkey=None)[0])
                assert wrong.status_code == 409 and wrong.json()["reason_code"] == "CHAT_LOG_REQUIRED"
                fresh_upload_budget(kit)
                # Logs whose proof never follows are deleted outright.
                abandoned = await owner.post(DEPOSITS,  **as_upload(
                    package_for(distinct_bundle("abandoned-proof", coldkey=None)[1], None)))
                assert abandoned.status_code == 200, abandoned.text
                async with kit.session() as session:
                    row = await session.get(TrajectoryClaim, uuid.UUID(abandoned.json()["id"]))
                    row.created_at -= dt.timedelta(days=2)
                    row.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
                    await session.commit()
                async with kit.session() as session:
                    assert (await purge(session))[0] == 1
                    assert await session.get(TrajectoryClaim, uuid.UUID(abandoned.json()["id"])) is None
                created = await submit(owner, bundle)
                assert created.status_code == 201, created.text
                sid = created.json()["submission"]["id"]
                state = (await owner.get(f"/v1/me/submissions/{sid}/trajectory")).json()
                claim = state["claim"]
                assert claim["id"] == deposited.json()["id"] and claim["status"] == "UNDER_REVIEW"
                assert claim["submission_id"] == sid and state["chat_logs_required"] and not state["eligible"]
                async with kit.session() as session:
                    record = await session.get(TrajectoryClaim, uuid.UUID(claim["id"]))
                    assert record.expires_at == record.created_at + dt.timedelta(days=90)
                    assert [e["action"] for e in record.history] == ["DEPOSITED", "SUBMITTED_WITH_PROOF"]
                # Logs cannot be added or replaced after the proof.
                fresh_upload_budget(kit)
                late = await owner.post(f"/v1/me/submissions/{sid}/trajectory",
                                        **as_upload(relinked(p, submission_id=sid)))
                assert late.status_code == 409 and late.json()["reason_code"] == "CHAT_LOGS_FIXED_AT_SUBMISSION"
                again = await owner.post(DEPOSITS, **as_upload(p))
                assert again.status_code == 409 and again.json()["reason_code"] == "TRAJECTORY_ALREADY_SUBMITTED"
                await reviewer(kit, admin)
                dpath = f"/v1/admin/trajectories/{claim['id']}/decision"
                early = await admin.post(dpath, headers=same_origin(), json=review_body(
                    decision="REJECTED", state_version=claim["state_version"]))
                assert early.status_code == 409 and early.json()["reason_code"] == "TRAJECTORY_VERIFICATION_PENDING"
                await _verify(kit, sid)
                async with kit.session() as session:
                    await proof_store.record_human_decision(session, uuid.UUID(sid), decision=ReviewOutcome.APPROVED,
                                                            reason_code="REVIEW_APPROVED", reviewer="fixture-reviewer")
                    await session.commit()
                    assert (await kit.services.pricing.quote(session, reward_target_id=target)).reason == "ALREADY_SOLVED"
                changes = await admin.post(dpath, headers=same_origin(), json=review_body(
                    decision="NEEDS_CHANGES", state_version=claim["state_version"]))
                assert changes.status_code == 409 and changes.json()["reason_code"] == "CHAT_LOGS_FIXED_AT_SUBMISSION"
                rejected = await admin.post(dpath, headers=same_origin(), json=review_body(
                    decision="REJECTED", state_version=claim["state_version"]))
                assert rejected.status_code == 200, rejected.text
                async with kit.session() as session:
                    record = await session.get(TrajectoryClaim, uuid.UUID(claim["id"]))
                    forfeit = await proof_store.latest_binding_decision(session, uuid.UUID(sid))
                    assert record.history[-1]["forfeit_decision_id"] == forfeit.id
                    assert str(forfeit.decision) == "REJECTED" and forfeit.reason_code == "CHAT_LOGS_NOT_ACCEPTED"
                    proof = await session.get(Submission, uuid.UUID(sid))
                    assert str(proof.reward_status) == "INELIGIBLE" and str(proof.manual_review_status) == "REJECTED"
                    reopened = await kit.services.pricing.quote(session, reward_target_id=target)
                    assert reopened.available and reopened.inputs["committed_bounty_rao"] == 0
                # Another solver may now claim the target; once they do, the rejection is final.
                await accepted(kit, rival, required=True, marker="rival-proof", email="rival@example.com")
                appeal = await admin.post(f"/v1/admin/trajectories/{claim['id']}/reopen", headers=same_origin(), json={
                    "action": "REOPEN_REVIEW", "expected_revision": 1,
                    "expected_state_version": rejected.json()["state_version"], "note": "Appeal of the rejected chat logs."})
                assert appeal.status_code == 409 and appeal.json()["reason_code"] == "TRAJECTORY_TARGET_REASSIGNED"
                fresh_upload_budget(kit)
                closed = await owner.post(DEPOSITS,  **as_upload(
                    package_for(distinct_bundle("late-proof", coldkey=None)[1], None)))
                assert closed.status_code == 409 and closed.json()["reason_code"] == "TRAJECTORY_INELIGIBLE"
        finally:
            await kit.teardown()
    asyncio.run(scenario())


def test_intake_stays_closed_until_opened_and_the_opening_moves_only_by_recorded_correction():
    from scripts.open_chat_log_intake import correct_opening, open_intake

    async def scenario():
        kit = await kit_config(opened_at=None).setup()
        try:
            async with await client(kit) as owner:
                _, sid, p = await accepted(kit, owner)
                closed = await owner.get("/v1/trajectory-offer")
                assert closed.json()["enabled"] is False and closed.json()["start_at"] is None
                refused = await owner.post(f"/v1/me/submissions/{sid}/trajectory", **as_upload(p))
                assert refused.status_code == 503
                assert (await owner.post(DEPOSITS, **as_upload(p))).status_code == 503
                assert (await owner.get(f"/v1/me/submissions/{sid}/trajectory")).json()["eligibility_reason"] == "Chat-log intake is not open."
                async with kit.session() as session:
                    first, new = await open_intake(session, opened_by="test operator")
                    assert new
                async with kit.session() as session:
                    again, new = await open_intake(session, opened_by="someone else")
                    assert again == first and not new
                    for statement in ("UPDATE chat_log_program SET opened_at = now() - interval '1 year'",
                                      "DELETE FROM chat_log_program", "TRUNCATE chat_log_program"):
                        with pytest.raises(DBAPIError, match="permanent"):
                            await session.execute(text(statement))
                        await session.rollback()
                    record = await session.get(ChatLogProgram, 1)
                    assert record.opened_by == "test operator" and record.terms_version == TERMS_VERSION
                # A recorded correction can move the moment; the correction itself is permanent.
                async with kit.session() as session:
                    earlier = first - dt.timedelta(hours=1)
                    with pytest.raises(SystemExit, match="future"):
                        await correct_opening(session, opened_at=first + dt.timedelta(days=1),
                                              corrected_by="test operator", reason="typo")
                    assert await correct_opening(session, opened_at=earlier, corrected_by="test operator",
                                                 reason="opened by mistake") == first
                    await correct_opening(session, opened_at=first, corrected_by="test operator",
                                          reason="restore the original moment")
                async with kit.session() as session:
                    moves = (await session.execute(text(
                        "SELECT previous_opened_at, opened_at FROM chat_log_program_corrections ORDER BY id"))).all()
                    assert moves == [(first, earlier), (earlier, first)]
                    for statement in ("UPDATE chat_log_program_corrections SET reason = 'x'",
                                      "DELETE FROM chat_log_program_corrections",
                                      "TRUNCATE chat_log_program_corrections"):
                        with pytest.raises(DBAPIError, match="permanent"):
                            await session.execute(text(statement))
                        await session.rollback()
                opened = (await owner.get("/v1/trajectory-offer")).json()
                assert opened["enabled"] and dt.datetime.fromisoformat(opened["start_at"]) == first
                # This proof was submitted moments before opening, so it now qualifies.
                assert (await owner.get(f"/v1/me/submissions/{sid}/trajectory")).json()["eligible"]
        finally:
            await kit.teardown()
    asyncio.run(scenario())


def test_signed_intent_confirmation_also_requires_logs_for_the_exact_proof():
    from submission_api.auth import development_signature
    from test_api_accounts import designate, full_intent

    async def scenario():
        kit = await kit_config().setup()
        with_new_bounties(kit)
        try:
            async with await client(kit) as http:
                account = await sign_in_by_email(kit, http)
                await designate(kit, http, MINER_COLDKEY)
                intent_id, uploaded = await full_intent(kit, http, uuid.UUID(account["id"]), marker="intent-logs")
                confirm = f"/v1/submissions/intents/{intent_id}/confirm"
                body = {"signature": development_signature()}
                refused = await http.post(confirm, json=body, headers=same_origin())
                assert refused.status_code == 409 and refused.json()["reason_code"] == "CHAT_LOG_REQUIRED"
                async with kit.session() as session:
                    assert await session.scalar(select(func.count()).select_from(Submission)) == 0
                deposited = await http.post(DEPOSITS,
                                            **as_upload(package_for(uploaded["proof_sha256"], None)))
                assert deposited.status_code == 200, deposited.text
                confirmed = await http.post(confirm, json=body, headers=same_origin())
                assert confirmed.status_code == 201, confirmed.text
                sid = confirmed.json()["submission"]["id"]
                claim = (await http.get(f"/v1/me/submissions/{sid}/trajectory")).json()["claim"]
                assert claim["id"] == deposited.json()["id"] and claim["status"] == "UNDER_REVIEW"
        finally:
            await kit.teardown()
    asyncio.run(scenario())


def test_required_logs_withdrawal_forfeits_and_releases_the_target():
    from conftest_api import task_entry
    target = task_entry().reward_target_id

    async def scenario():
        kit = await kit_config().setup()
        with_new_bounties(kit)
        try:
            async with await client(kit) as owner:
                _, sid, _ = await accepted(kit, owner, required=True)
                claim = (await owner.get(f"/v1/me/submissions/{sid}/trajectory")).json()["claim"]
                withdrawn = await owner.post(f"/v1/me/submissions/{sid}/trajectory/withdraw", headers=same_origin(),
                                             json={"expected_state_version": claim["state_version"]})
                assert withdrawn.status_code == 200 and withdrawn.json()["status"] == "WITHDRAWN"
                assert not withdrawn.json()["content_available"]
                async with kit.session() as session:
                    forfeit = await proof_store.latest_binding_decision(session, uuid.UUID(sid))
                    assert forfeit.reason_code == "CHAT_LOGS_NOT_ACCEPTED" and forfeit.supersedes_id is not None
                    assert (await kit.services.pricing.quote(session, reward_target_id=target)).available
        finally:
            await kit.teardown()
    asyncio.run(scenario())


def test_stream_limit_releases_transaction_before_body_and_refuses_bearer(monkeypatch):
    from test_api_cli_sessions import bearer, linked_account_with_cli_token
    from submission_api import trajectories as store
    from submission_api.routers import trajectories as router
    async def scenario():
        kit = await kit_config().setup()
        try:
            async with await client(kit) as owner, await client(kit) as cli:
                _, sid, p = await accepted(kit, owner)
                path = f"/v1/me/submissions/{sid}/trajectory"
                observed = {}
                original = store.owned_submission
                async def capture(session, *args, **kwargs):
                    result = await original(session, *args, **kwargs)
                    observed["session"] = session
                    return result
                monkeypatch.setattr(store, "owned_submission", capture)
                monkeypatch.setattr(router, "MAX_UPLOAD_BYTES", 1024)
                async def chunks():
                    assert not observed["session"].in_transaction()
                    yield b"x" * 800
                    await asyncio.sleep(0)
                    yield b"x" * 800
                oversized = await owner.post(path, content=chunks(), params=consent_params(),
                                             headers={**same_origin(), "Content-Type": "application/zip"})
                assert oversized.status_code == 413 and oversized.json()["reason_code"] == "TRAJECTORY_SIZE"
                assert oversized.headers["cache-control"] == "no-store"
                account, token = await linked_account_with_cli_token(kit)
                await _grant_reviewer(kit, account["id"])
                for endpoint in (path, f"/v1/admin/trajectories/{uuid.uuid4()}/decision"):
                    refused = await cli.post(endpoint, json={}, headers={**bearer(token), **same_origin()})
                    assert refused.status_code == 403
                    assert refused.json()["reason_code"] == ("ROLE_REQUIRES_BROWSER_SESSION" if "/admin/" in endpoint else "BROWSER_SESSION_REQUIRED")
        finally:
            await kit.teardown()
    asyncio.run(scenario())


def test_policy_v4_prior_established_proof_is_ineligible():
    async def scenario():
        kit = await kit_config().setup()
        try:
            async with await client(kit) as owner:
                _, sid, p = await accepted(kit, owner)
                async with kit.session() as session:
                    sub = await session.get(Submission, uuid.UUID(sid))
                    assert sub.review_policy_version == "v4"
                    current = await proof_store.latest_binding_decision(session, sub.id)
                    await proof_store.correct_human_decision(session, sub.id, supersedes_id=current.id,
                        decision=ReviewOutcome.REJECTED, reason_code="NOT_NOVEL", reviewer="fixture-correction",
                        notes="Previously established result under v4", notes_public="Previously established result")
                    await session.commit()
                result = await owner.post(f"/v1/me/submissions/{sid}/trajectory", **as_upload(p))
                assert result.status_code == 409 and result.json()["reason_code"] == "TRAJECTORY_INELIGIBLE"
        finally:
            await kit.teardown()
    asyncio.run(scenario())


def test_reviews_really_contend_on_same_proof_lock(monkeypatch):
    from submission_api.routers import trajectories as router
    async def scenario():
        kit = await kit_config().setup()
        try:
            async with await client(kit) as owner, await client(kit) as admin:
                _, sid, p = await accepted(kit, owner)
                upload = await owner.post(f"/v1/me/submissions/{sid}/trajectory", **as_upload(p))
                await reviewer(kit, admin)
                path = f"/v1/admin/trajectories/{upload.json()['id']}/decision"
                entered, release = asyncio.Event(), asyncio.Event()
                original = router.latest_binding_decision
                calls = 0
                async def held(*args, **kwargs):
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        entered.set()
                        await release.wait()
                    return await original(*args, **kwargs)
                monkeypatch.setattr(router, "latest_binding_decision", held)
                first = asyncio.create_task(admin.post(path, json=review_body(), headers=same_origin()))
                await asyncio.wait_for(entered.wait(), 5)
                second = asyncio.create_task(admin.post(path, json=review_body(), headers=same_origin()))
                await asyncio.sleep(0.05)
                assert not second.done()  # First transaction holds the same submission row lock.
                release.set()
                results = await asyncio.gather(first, second)
                assert [r.status_code for r in results] == [200, 409]
        finally:
            await kit.teardown()
    asyncio.run(scenario())


def test_state_token_prevents_stale_approval_after_reopen_and_same_file_withdrawal_retry():
    async def scenario():
        kit = await kit_config().setup()
        try:
            async with await client(kit) as owner, await client(kit) as admin:
                _, sid, p = await accepted(kit, owner)
                path = f"/v1/me/submissions/{sid}/trajectory"
                first = await owner.post(path, **as_upload(p))
                cid = first.json()["id"]
                await reviewer(kit, admin)
                dpath = f"/v1/admin/trajectories/{cid}/decision"
                result = await admin.post(dpath, json=review_body(decision="REJECTED"), headers=same_origin())
                assert result.json()["state_version"] == 2
                reopened = await admin.post(f"/v1/admin/trajectories/{cid}/reopen", headers=same_origin(), json={
                    "action": "REOPEN_REVIEW", "expected_revision": 1, "expected_state_version": 2,
                    "note": "Reopen after the first independent review."})
                assert reopened.json()["state_version"] == 3 and reopened.json()["revision"] == 1
                stale = await admin.post(dpath, json=review_body(), headers=same_origin())
                assert stale.status_code == 409 and stale.json()["reason_code"] == "TRAJECTORY_STALE_REVIEW"
                withdrawal = await owner.post(path + "/withdraw", headers=same_origin(), json={"expected_state_version": 3})
                assert withdrawal.json()["state_version"] == 4
                restored = await owner.post(path, **as_upload(p, 4))
                assert restored.status_code == 200, restored.text
                assert restored.json()["revision"] == 2 and restored.json()["state_version"] == 5
                assert restored.json()["content_available"] and restored.json()["status"] == "UNDER_REVIEW"
                changes = await admin.post(dpath, json=review_body(decision="NEEDS_CHANGES", revision=2, state_version=5), headers=same_origin())
                assert changes.json()["state_version"] == 6
                unchanged = await owner.post(path, **as_upload(p, 6))
                assert unchanged.status_code == 409 and unchanged.json()["reason_code"] == "TRAJECTORY_UNCHANGED"
                async with kit.session() as session:
                    record = await session.get(TrajectoryClaim, uuid.UUID(cid))
                    assert all("consent_sha256" in e and "proof_policy_sha256" in e
                               for e in record.history if e["action"] == "UPLOADED")
        finally:
            await kit.teardown()
    asyncio.run(scenario())


def test_same_outcome_proof_rebind_after_expiry_uses_snapshot_and_retains_review():
    async def scenario():
        kit = await kit_config().setup()
        try:
            async with await client(kit) as owner, await client(kit) as admin:
                _, sid, p = await accepted(kit, owner)
                path = f"/v1/me/submissions/{sid}/trajectory"
                first = await owner.post(path, **as_upload(p))
                cid = first.json()["id"]
                await reviewer(kit, admin)
                kit.app.state.services = replace(kit.services, settings=replace(kit.settings,
                    trajectory_bonus_enabled=False))
                approved = await admin.post(f"/v1/admin/trajectories/{cid}/decision", json=review_body(), headers=same_origin())
                assert approved.status_code == 200, approved.text  # Config cleared after enrollment.
                async with kit.session() as session:
                    record = await session.get(TrajectoryClaim, uuid.UUID(cid))
                    assert "consent_sha256" in record.history[-1]
                    record.created_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=120)
                    record.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=30)
                    record.package = None
                    current = await proof_store.latest_binding_decision(session, uuid.UUID(sid))
                    await proof_store.correct_human_decision(session, uuid.UUID(sid), supersedes_id=current.id,
                        decision=ReviewOutcome.APPROVED, reason_code="REVIEW_APPROVED", reviewer="fixture-correction",
                        notes="Correct public explanation only", notes_public="Correct public explanation only")
                    await session.commit()
                assert (await owner.get(path)).json()["claim"]["status"] == "BLOCKED_PROOF_CHANGED"
                body = {"action": "REBIND_PROOF_DECISION", "expected_revision": 1, "expected_state_version": 2,
                        "note": "Verified same outcome and policy after explanation correction."}
                rebound = await admin.post(f"/v1/admin/trajectories/{cid}/reopen", headers=same_origin(), json=body)
                assert rebound.status_code == 200, rebound.text
                assert rebound.json()["status"] == "APPROVED_MANUAL_PAYMENT" and rebound.json()["state_version"] == 3
                assert not rebound.json()["content_available"] and rebound.json()["payment_status"] == "NOT_ISSUED"
                async with kit.session() as session:
                    record = await session.get(TrajectoryClaim, uuid.UUID(cid))
                    assert record.checklist is not None and record.history[-1]["previous_proof_review_decision_id"]
                    current = await proof_store.latest_binding_decision(session, uuid.UUID(sid))
                    await proof_store.correct_human_decision(session, uuid.UUID(sid), supersedes_id=current.id,
                        decision=ReviewOutcome.REJECTED, reason_code="NOT_NOVEL", reviewer="fixture-correction",
                        notes="Disqualified proof", notes_public="Disqualified proof")
                    await session.commit()
                body["expected_state_version"] = 3
                denied = await admin.post(f"/v1/admin/trajectories/{cid}/reopen", headers=same_origin(), json=body)
                assert denied.status_code == 409 and denied.json()["reason_code"] == "TRAJECTORY_REBIND_INELIGIBLE"
                async with kit.session() as session:
                    current = await proof_store.latest_binding_decision(session, uuid.UUID(sid))
                    await proof_store.correct_human_decision(session, uuid.UUID(sid), supersedes_id=current.id,
                        decision=ReviewOutcome.APPROVED, reason_code="REVIEW_APPROVED", reviewer="fixture-correction",
                        notes="Appeal granted", notes_public="Appeal granted")
                    await session.commit()
                # Re-approval after rejection requires a fresh evidence review, not a same-outcome rebind.
                denied = await admin.post(f"/v1/admin/trajectories/{cid}/reopen", headers=same_origin(), json=body)
                assert denied.status_code == 409 and denied.json()["reason_code"] == "TRAJECTORY_REBIND_INELIGIBLE"
        finally:
            await kit.teardown()
    asyncio.run(scenario())


def test_requested_revision_after_initial_claim_window_and_changed_cutoff(monkeypatch):
    async def scenario():
        kit = await kit_config().setup()
        try:
            async with await client(kit) as owner, await client(kit) as admin:
                _, sid, p = await accepted(kit, owner)
                path = f"/v1/me/submissions/{sid}/trajectory"
                first = await owner.post(path, **as_upload(p))
                def unavailable_original(*args):
                    raise AssertionError("An enrolled claim must keep its original amount without reopening the bundle")
                monkeypatch.setattr("submission_api.trajectories.amount_for", unavailable_original)
                cid = first.json()["id"]
                await reviewer(kit, admin)
                changed = await admin.post(f"/v1/admin/trajectories/{cid}/decision", json=review_body(decision="NEEDS_CHANGES"), headers=same_origin())
                assert changed.status_code == 200
                async with kit.session() as session:
                    sub = await session.get(Submission, uuid.UUID(sid))
                    sub.created_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=370)
                    claim = await session.get(TrajectoryClaim, uuid.UUID(cid))
                    claim.offer_start_at = sub.created_at - dt.timedelta(days=1)
                    await session.commit()
                assert (await owner.get(path)).json()["eligible"]
                p["events"][0]["content"] += " Revision requested while review content remains live."
                dated(p, dt.datetime.now(dt.UTC) - dt.timedelta(days=371))  # The chat predates the proof.
                revised = await owner.post(path, **as_upload(p, 2))
                assert revised.status_code == 200, revised.text
                assert revised.json()["revision"] == 2
        finally:
            await kit.teardown()
    asyncio.run(scenario())
