"""Private PostgreSQL tests: ownership, review state, rights, duplicates, retention and no payout."""
import asyncio
import datetime as dt
import json
import uuid
from dataclasses import replace

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from conftest_api import TASK_DIGEST, distinct_bundle, harness, postgres_dsn
from test_api_accounts import client, same_origin, sign_in_by_email
from test_api_reviews import _grant_reviewer, _verify
from test_api_session_submissions import SESSION, _digest, keyless_account, params, zip_headers
from test_trajectory_schema import example
from conjectures_subnet.db import submissions as proof_store
from conjectures_subnet.db.models import ReviewOutcome, RewardEvent, Submission
from conjectures_subnet.db.trajectory_models import TrajectoryAccess, TrajectoryClaim, TrajectoryFingerprint
from submission_api.trajectory_terms import REVIEW_TERMS_VERSION, REVIEW_TERMS_SHA256, ASSENT_STATEMENTS_SHA256
from submission_api.trajectory_intake import MAX_ENVELOPE_BYTES
from submission_api.routers.trajectories import Checklist

pytestmark = pytest.mark.skipif(postgres_dsn() is None, reason="requires verified private fixture")


def kit_config():
    return harness(TRAJECTORY_BONUS_ENABLED="true", TRAJECTORY_BONUS_START_AT="2026-01-01T00:00:00Z")


async def accepted(kit, http, *, code="REVIEW_APPROVED"):
    account = await keyless_account(kit, http)
    bundle, proof_hash = distinct_bundle("trajectory-fixture", coldkey=None)
    result = await http.post(SESSION, params=params(bundle_sha256=_digest(bundle)), content=bundle,
                             headers=zip_headers(http))
    assert result.status_code == 201, result.text
    sid = result.json()["submission"]["id"]
    await _verify(kit, sid)
    async with kit.session() as session:
        await proof_store.record_human_decision(session, uuid.UUID(sid), decision=ReviewOutcome.APPROVED,
                                                reason_code=code, reviewer="fixture-reviewer")
        await session.commit()
    p = example()
    p.update(synthetic=False, submission_id=sid, task_id="fixture", task_bundle_sha256=TASK_DIGEST,
             final_proof_sha256=proof_hash)
    p["runs"][0]["source_export_unavailable_reason"] = "Local test fixture, no provider export."
    # Synthetic test-only evidence. Production must reject the unmodified synthetic example.
    p["artifacts"][0].update(sha256=proof_hash, content=None, availability="authoritative_submission")
    return account, sid, p


async def reviewer(kit, http):
    account = await sign_in_by_email(kit, http, email="trajectory-reviewer@example.com")
    await _grant_reviewer(kit, account["id"])
    return account


def envelope(package):
    return {"package_json": json.dumps(package), "commercial_election": package["commercial_election"],
            "consent": {"review_terms_version": REVIEW_TERMS_VERSION, "review_terms_sha256": REVIEW_TERMS_SHA256,
                        "assent_statements_sha256": ASSENT_STATEMENTS_SHA256, "accepted": True}}


def review_body(*, decision="APPROVED_MANUAL_PAYMENT", revision=1):
    return {"decision": decision, "expected_revision": revision,
            "note": "Test-only human evidence checks completed.",
            "checklist": {k: True for k in Checklist.model_fields} if decision == "APPROVED_MANUAL_PAYMENT" else None,
            "commercial_clearance": "NOT_CLEARED", "commercial_note": "Review-only election respected.",
            "source_rights_verified": False}


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
                assert (await stranger.post(path, json=envelope(p), headers=same_origin())).status_code == 404
                assert (await owner.post(path, json=envelope(p), headers={"Origin": "https://evil.invalid"})).status_code == 403
                assert (await owner.get("/v1/admin/trajectories")).status_code == 403
                upload = await owner.post(path, json=envelope(p), headers=same_origin())
                assert upload.status_code == 200, upload.text
                assert upload.headers["cache-control"] == "no-store"
                cid = upload.json()["id"]
                assert upload.json()["status"] == "UNDER_REVIEW"
                retry = await owner.post(path, json=envelope(p), headers=same_origin())
                assert retry.json()["id"] == cid and retry.json()["revision"] == 1
                altered = json.loads(json.dumps(p))
                altered["events"][0]["content"] += " edit"
                assert (await owner.post(path, json=envelope(altered), headers=same_origin())).status_code == 409
                await reviewer(kit, admin)
                queue = await admin.get("/v1/admin/trajectories")
                assert queue.status_code == 200, queue.text
                assert "sources" not in queue.json()["items"][0]["commercial_election"]
                assert "sources" not in (await owner.get(path)).json()["claim"]["commercial_election"]
                downloaded = await admin.get(f"/v1/admin/trajectories/{cid}/package")
                assert downloaded.status_code == 200 and downloaded.headers["cache-control"] == "no-store"
                assert downloaded.headers["content-disposition"].startswith("attachment;")
                assert "sandbox" in downloaded.headers["content-security-policy"]
                assert downloaded.headers["x-content-type-options"] == "nosniff"
                async with kit.session() as session:
                    assert await session.scalar(select(func.count()).select_from(TrajectoryAccess)) == 1
                    record = await session.get(TrajectoryClaim, uuid.UUID(cid))
                    assert record.consent["accepted"] and record.consent["account_id"] == account["id"]
                    assert record.consent["review_terms_sha256"] == REVIEW_TERMS_SHA256
                    assert record.proof_policy_snapshot["review_policy_version"] == (await session.get(Submission, uuid.UUID(sid))).review_policy_version
                assert (await stranger.get(f"/v1/admin/trajectories/{cid}/package")).status_code == 403
                decision_path = f"/v1/admin/trajectories/{cid}/decision"
                for note_field in ("note", "commercial_note"):
                    whitespace = review_body()
                    whitespace[note_field] = " " * 20
                    assert (await admin.post(decision_path, json=whitespace, headers=same_origin())).status_code == 400
                missing_checks = review_body()
                missing_checks["checklist"] = None
                assert (await admin.post(decision_path, json=missing_checks, headers=same_origin())).status_code == 400
                cleared = review_body()
                cleared.update(commercial_clearance="CLEARED_CONDITIONAL", source_rights_verified=True)
                assert (await admin.post(decision_path, json=cleared, headers=same_origin())).status_code == 409
                approved = await admin.post(decision_path, json=review_body(), headers=same_origin())
                assert approved.status_code == 200, approved.text
                assert approved.json()["status"] == "APPROVED_MANUAL_PAYMENT"
                assert approved.json()["payment_status"] == "NOT_ISSUED"
                assert approved.json()["license_status"] == "INACTIVE_NO_PAYMENT_OR_ACTIVATION"
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
                    json={"action": "REVOKE_PROOF_CHANGED", "expected_revision": 1, "note": "Proof approval was revoked; release bonus reservation."})
                assert revoked.status_code == 200 and revoked.json()["status"] == "REVOKED_PROOF_CHANGED"
                reopened = await admin.post(f"/v1/admin/trajectories/{cid}/reopen", headers=same_origin(),
                    json={"action": "REOPEN_REVIEW", "expected_revision": 1, "note": "Reopen for a new full review of this proof."})
                assert reopened.status_code == 200 and reopened.json()["status"] == "UNDER_REVIEW"
                rejected = await admin.post(decision_path, headers=same_origin(), json=review_body(decision="REJECTED"))
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
                first = await owner.post(path, json=envelope(p), headers=same_origin())
                cid = first.json()["id"]
                await _grant_reviewer(kit, account["id"])
                decision_path = f"/v1/admin/trajectories/{cid}/decision"
                assert (await owner.post(decision_path, json=review_body(), headers=same_origin())).status_code == 409
                await reviewer(kit, admin)
                result = await admin.post(decision_path, json=review_body(decision="NEEDS_CHANGES"), headers=same_origin())
                assert result.status_code == 200, result.text
                p["events"][0]["content"] += " corrected"
                changed = await owner.post(path, json=envelope(p), headers=same_origin())
                assert changed.json()["revision"] == 2
                assert (await admin.post(decision_path, json=review_body(), headers=same_origin())).status_code == 409
                withdrawn = await owner.post(path + "/withdraw", headers=same_origin())
                assert withdrawn.status_code == 200 and withdrawn.json()["content_available"] is False
                assert (await admin.get(f"/v1/admin/trajectories/{cid}/package")).status_code == 404
                p["events"][0]["content"] += " third"
                third = await owner.post(path, json=envelope(p), headers=same_origin())
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
                    assert record.commercial_election["provenance_expired"]
                rejected = await admin.post(decision_path, json=review_body(revision=3, decision="REJECTED"), headers=same_origin())
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
                assert (await owner.post(path, json=envelope(p), headers=same_origin())).status_code == 503
                kit.app.state.services = kit.services
                p["synthetic"] = True
                assert (await owner.post(path, json=envelope(p), headers=same_origin())).status_code == 409
                p["synthetic"] = False
                p["task_id"] = "wrong-task"
                assert (await owner.post(path, json=envelope(p), headers=same_origin())).status_code == 409
                p["task_id"] = "fixture"
                p["private_reasoning"] = "SECRET_MUST_NOT_BE_ECHOED"
                invalid = await owner.post(path, json=envelope(p), headers=same_origin())
                assert invalid.status_code == 400
                assert "SECRET_MUST_NOT_BE_ECHOED" not in invalid.text
                del p["private_reasoning"]
                oversized = await owner.post(path, content=b"{}", headers={**same_origin(), "content-type": "application/json", "content-length": str(MAX_ENVELOPE_BYTES + 1)})
                assert oversized.status_code == 413
                wrong_format = await owner.post(path, content=b"PKzip", headers={**same_origin(), "content-type": "application/zip"})
                assert wrong_format.status_code == 415
                async with kit.session() as session:
                    current = await proof_store.latest_binding_decision(session, uuid.UUID(sid))
                    await proof_store.correct_human_decision(session, uuid.UUID(sid), supersedes_id=current.id,
                        decision=ReviewOutcome.APPROVED, reason_code="FORMALIZATION_DEFECT_AWARD", reviewer="fixture-correction",
                        notes="Defect award only", notes_public="Defect award only")
                    await session.commit()
                assert (await owner.post(path, json=envelope(p), headers=same_origin())).status_code == 409
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
                    owner.post(path, json=envelope(p), headers=same_origin()),
                    owner.post(path, json=envelope(p), headers=same_origin()))
                assert sorted([first.status_code, second.status_code]) in ([200, 200], [200, 429]), (first.text, second.text)
                if first.status_code != 200:
                    first, second = second, first
                retry = await owner.post(path, json=envelope(p), headers=same_origin())
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


def test_in_flow_conditional_license_records_election_and_separate_rights_clearance():
    async def scenario():
        kit = await kit_config().setup()
        try:
            async with await client(kit) as owner, await client(kit) as admin:
                _, sid, p = await accepted(kit, owner)
                p["commercial_election"].update(
                    election="conditional_grant", purposes=["dataset_redistribution", "evaluation"],
                    explicit_assent=True,
                    sources=[{"source_id": identifier, "rights_status": "unknown",
                              "agreement_or_license": {"value": None, "unknown_reason": "Test unknown"},
                              "evidence_reference": {"value": None, "unknown_reason": "Test unknown"},
                              "allowed_purposes": [], "restrictions": "Synthetic fixture, not an actual grant",
                              "third_party_rights": "Synthetic test data only"}
                             for identifier in ("run:run-1", "artifact:final-proof")])
                path = f"/v1/me/submissions/{sid}/trajectory"
                uploaded = await owner.post(path, json=envelope(p), headers=same_origin())
                assert uploaded.status_code == 200, uploaded.text
                cid = uploaded.json()["id"]
                await reviewer(kit, admin)
                dpath = f"/v1/admin/trajectories/{cid}/decision"
                body = review_body()
                body.update(commercial_clearance="CLEARED_CONDITIONAL", source_rights_verified=True)
                denied = await admin.post(dpath, json=body, headers=same_origin())
                assert denied.status_code == 409
                assert denied.json()["reason_code"] == "TRAJECTORY_RIGHTS_UNCLEARED"
                # Request a new package with actual source disclosures; the original election
                # cannot be silently converted into a broader or active license by the reviewer.
                changed = await admin.post(dpath, json=review_body(decision="NEEDS_CHANGES"), headers=same_origin())
                assert changed.status_code == 200
                for source in p["commercial_election"]["sources"]:
                    source.update(rights_status="confirmed", allowed_purposes=["dataset_redistribution", "evaluation"],
                                  agreement_or_license={"value": "Own synthetic fixture license", "unknown_reason": None},
                                  evidence_reference={"value": "Synthetic fixture authorship record", "unknown_reason": None})
                revised = await owner.post(path, json=envelope(p), headers=same_origin())
                assert revised.status_code == 200 and revised.json()["revision"] == 2
                body["expected_revision"] = 2
                cleared = await admin.post(dpath, json=body, headers=same_origin())
                assert cleared.status_code == 200, cleared.text
                assert cleared.json()["commercial_review"]["decision"] == "CLEARED_CONDITIONAL"
                assert cleared.json()["license_status"] == "INACTIVE_NO_PAYMENT_OR_ACTIVATION"
                assert cleared.json()["payment_status"] == "NOT_ISSUED"
                assert "reviewer_id" not in cleared.json()["commercial_review"]
                withdrawn = await owner.post(path + "/withdraw-commercial", headers=same_origin())
                assert withdrawn.status_code == 200 and withdrawn.json()["commercial_withdrawn_at"]
                assert withdrawn.json()["status"] == "APPROVED_MANUAL_PAYMENT"
                assert withdrawn.json()["commercial_review"] is None
                async with kit.session() as session:
                    record = await session.get(TrajectoryClaim, uuid.UUID(cid))
                    assert record.history[-1]["action"] == "COMMERCIAL_WITHDRAWN"
                    record.created_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=100)
                    record.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
                    await session.commit()
                    from scripts.purge_trajectories import purge
                    await purge(session)
                    await session.refresh(record)
                    assert "restrictions" not in record.commercial_election["sources"][0]
                    assert "record_sha256" in record.commercial_election["sources"][0]
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
                monkeypatch.setattr(router, "MAX_ENVELOPE_BYTES", 1024)
                async def chunks():
                    assert not observed["session"].in_transaction()
                    yield b"x" * 800
                    await asyncio.sleep(0)
                    yield b"x" * 800
                oversized = await owner.post(path, content=chunks(), headers={**same_origin(), "Content-Type": "application/json"})
                assert oversized.status_code == 413 and oversized.json()["reason_code"] == "TRAJECTORY_SIZE"
                assert oversized.headers["cache-control"] == "no-store"
                _, token = await linked_account_with_cli_token(kit)
                for endpoint in (path, f"/v1/admin/trajectories/{uuid.uuid4()}/decision"):
                    refused = await cli.post(endpoint, json={}, headers={**bearer(token), **same_origin()})
                    assert refused.status_code == 403
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
                result = await owner.post(f"/v1/me/submissions/{sid}/trajectory", json=envelope(p), headers=same_origin())
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
                upload = await owner.post(f"/v1/me/submissions/{sid}/trajectory", json=envelope(p), headers=same_origin())
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
