"""Instruction authorization and chain facts after mandatory-log approval changes."""

import asyncio
import uuid

import pytest
from dataclasses import replace
from datetime import datetime, UTC
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
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

    finally:
        engine.dispose()
        await kit.teardown()


@pytest.mark.skipif(postgres_dsn() is None, reason="private database required")
def test_stale_log_binding_hides_commands_without_blocking_chain_facts():
    asyncio.run(stale_evidence_scenario())
