"""Instruction authorization and chain facts after mandatory-log approval changes."""

import asyncio
import uuid

import pytest
from dataclasses import replace
from datetime import datetime, UTC, timedelta
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, DBAPIError
from conftest_api import postgres_dsn
from test_api_accounts import client, same_origin
from test_api_trajectories import accepted, envelope, reviewer, review_body, kit_config
from test_payout_instructions import command_rows
from test_payout_watcher import observed_stake_transfer
from conjectures_subnet.bounty_policy import TREASURY_CHAT_POLICY
from conjectures_subnet.db.engine import create_db_engine, session_factory
from conjectures_subnet.db.models import Account, Submission, RewardEvent, ReviewOutcome
from conjectures_subnet.db import submissions as proof_store, payouts
from payout_notifier.worker import PayoutNotifier


async def restricted_payout_role_can_authorize_without_reading_exports(kit, sid):
    role = "trajectory_payout_test_" + uuid.uuid4().hex
    async with kit.session() as s:
        await s.execute(text(f"CREATE ROLE {role} NOLOGIN"))
        await s.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
        await s.execute(text(f"GRANT SELECT ON submissions, review_decisions TO {role}"))
        await s.execute(text(f"GRANT SELECT, INSERT ON reward_events TO {role}"))
        await s.execute(text(f"GRANT USAGE ON SEQUENCE reward_events_id_seq TO {role}"))
        await s.execute(text(f"GRANT EXECUTE ON FUNCTION bounty_trajectory_eligible(uuid, text) TO {role}"))
        await s.commit()
    try:
        async with kit.session() as s:
            await s.execute(text(f"SET LOCAL ROLE {role}"))
            with pytest.raises(DBAPIError, match="permission denied"):
                await s.execute(text("SELECT package FROM trajectory_claims"))
            await s.rollback()
        async with kit.session() as s:
            await s.execute(text(f"SET LOCAL ROLE {role}"))
            assert await s.scalar(
                text("SELECT bounty_trajectory_eligible(:sid, 'REVIEW_APPROVED')"),
                {"sid": uuid.UUID(sid)},
            )
            # The real INSERT invokes both the existing amount guard and the new definer guard.
            await s.execute(
                text("""INSERT INTO reward_events (
                submission_id, amount_rao, eligibility_reason, pricing_policy_version, pricing_inputs,
                destination_coldkey, initiated_by, generation_key)
                SELECT id, bounty_amount_rao, 'REVIEW_APPROVED', bounty_policy_version, bounty_inputs,
                    :destination, 'restricted-role-test', 'submission:' || id::text
                FROM submissions WHERE id=:sid"""),
                {"sid": uuid.UUID(sid), "destination": kit.settings.payment_recipient},
            )
            await s.rollback()  # The notifier creates the lasting instruction below.
    finally:
        async with kit.session() as s:
            await s.execute(text(f"DROP OWNED BY {role}"))
            await s.execute(text(f"DROP ROLE {role}"))
            await s.commit()


async def stale_evidence_scenario():
    kit = await kit_config().setup()
    kit.services = replace(
        kit.services, pricing=replace(kit.services.pricing, new_target_policy=TREASURY_CHAT_POLICY)
    )
    kit.app.state.services = kit.services
    engine = create_db_engine(postgres_dsn())
    worker = PayoutNotifier(
        sessions=session_factory(engine), webhook_url="", worker_id="review-repro"
    )
    try:
        async with await client(kit) as owner, await client(kit) as admin:
            account, sid, package = await accepted(kit, owner)
            async with kit.session() as s:
                a = await s.get(Account, uuid.UUID(account["id"]))
                a.payout_coldkey = kit.settings.payment_recipient
                await s.commit()
            await reviewer(kit, admin)
            upload = await owner.post(
                f"/v1/me/submissions/{sid}/trajectory",
                json=envelope(package),
                headers=same_origin(),
            )
            assert upload.status_code == 200, upload.text
            cid = upload.json()["id"]
            reviewed = await admin.post(
                f"/v1/admin/trajectories/{cid}/decision", json=review_body(), headers=same_origin()
            )
            assert reviewed.status_code == 200, reviewed.text
            await restricted_payout_role_can_authorize_without_reading_exports(kit, sid)
            assert worker.seed_reward_events() == 1
            assert worker.seed() == 2
            async with kit.session() as s:
                event = await s.scalar(select(RewardEvent))
                eid, amount, dest = event.id, event.amount_rao, event.destination_coldkey
                d = await proof_store.latest_binding_decision(s, uuid.UUID(sid))
                await proof_store.correct_human_decision(
                    s,
                    uuid.UUID(sid),
                    supersedes_id=d.id,
                    decision=ReviewOutcome.APPROVED,
                    reason_code="REVIEW_APPROVED",
                    reviewer="fixture",
                    notes="Clarify explanation",
                    notes_public="Clarify explanation",
                )
                await s.commit()
            state = await owner.get(f"/v1/me/submissions/{sid}/trajectory")
            assert state.json()["claim"]["status"] == "BLOCKED_PROOF_CHANGED"
            assert command_rows(eid) == []
            assert worker.seed() == 0
            assert worker.claim() is None
            revoked = await admin.post(
                f"/v1/admin/trajectories/{cid}/reopen",
                headers=same_origin(),
                json={
                    "action": "REVOKE_PROOF_CHANGED",
                    "expected_revision": 1,
                    "expected_state_version": 2,
                    "note": "A live instruction must be cancelled before this evidence can be revoked.",
                },
            )
            assert (
                revoked.status_code == 409
                and revoked.json()["reason_code"] == "TRAJECTORY_PAYOUT_IN_FLIGHT"
            )
            # Revising an instruction still rechecks evidence, even if its amount stays fixed.
            async with kit.session() as s:
                with pytest.raises(IntegrityError, match="requires reviewed chat logs"):
                    await s.execute(
                        text("UPDATE reward_events SET amount_rao=amount_rao WHERE id=:id"),
                        {"id": eid},
                    )
                await s.rollback()
            # An explicit same-outcome rebind restores current authorization.
            response = await admin.post(
                f"/v1/admin/trajectories/{cid}/reopen",
                headers=same_origin(),
                json={
                    "action": "REBIND_PROOF_DECISION",
                    "expected_revision": 1,
                    "expected_state_version": 2,
                    "note": "Same proof and full-bounty decision; public explanation corrected.",
                },
            )
            assert response.status_code == 200, response.text
            assert len(command_rows(eid)) == 1
            # A second correction makes it stale again, including while money is in flight.
            async with kit.session() as s:
                d = await proof_store.latest_binding_decision(s, uuid.UUID(sid))
                await proof_store.correct_human_decision(
                    s,
                    uuid.UUID(sid),
                    supersedes_id=d.id,
                    decision=ReviewOutcome.APPROVED,
                    reason_code="REVIEW_APPROVED",
                    reviewer="fixture",
                    notes="Second clarification",
                    notes_public="Second clarification",
                )
                await s.commit()
            assert command_rows(eid) == []
            observed = replace(
                observed_stake_transfer(),
                block_timestamp=datetime.now(UTC),
                amount_rao=amount,
                destination_coldkey=dest,
            )
            async with kit.session() as s:
                update = await payouts.mark_submitted(s, observed)
                assert update is not None and update.changed
                await s.commit()
            async with kit.session() as s:
                update = await payouts.confirm(s, observed)
                assert update is not None and update.changed
                await s.commit()
                assert (await s.get(Submission, uuid.UUID(sid))).reward_status == "REWARDED"
                assert (await s.get(RewardEvent, eid)).chain_observed
            from conjectures_subnet.db.trajectory_models import TrajectoryClaim
            from scripts.purge_trajectories import purge

            async with kit.session() as s:
                claim = await s.get(TrajectoryClaim, uuid.UUID(cid))
                claim.created_at = datetime.now(UTC) - timedelta(days=8 * 365)
                claim.expires_at = claim.created_at + timedelta(days=90)
                claim.audit_expires_at = claim.created_at + timedelta(days=7 * 365)
                await s.commit()
            async with kit.session() as owner_lock:
                await owner_lock.get(Submission, uuid.UUID(sid), with_for_update=True)
                async with kit.session() as purger:
                    assert await purge(purger) == (0, 0)  # Never race a claim mutation's proof lock.
                await owner_lock.rollback()
            async with kit.session() as s:
                assert await purge(s) == (1, 0)
            async with kit.session() as s:
                claim = await s.get(TrajectoryClaim, uuid.UUID(cid))
                assert claim is not None and claim.package is None

    finally:
        engine.dispose()
        await kit.teardown()


@pytest.mark.skipif(postgres_dsn() is None, reason="private database required")
def test_stale_log_binding_hides_commands_without_blocking_chain_facts():
    asyncio.run(stale_evidence_scenario())
