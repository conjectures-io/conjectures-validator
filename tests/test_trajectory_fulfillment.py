"""Private PostgreSQL tests for bonus accounting and revocable, account-bound delivery."""
import asyncio
import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from conftest_api import MINER_COLDKEY, postgres_dsn
from test_api_accounts import client, same_origin, sign_in_by_email
from test_api_trajectories import accepted, as_upload, kit_config, reviewer, review_body
from conjectures_subnet.db import payouts, submissions as proof_store
from conjectures_subnet.db.models import Account, ReviewOutcome, TreasuryPayout
from conjectures_subnet.db.trajectory_fulfillment import TrajectoryExport, TrajectoryPayment
from submission_api.errors import Conflict
from submission_api.trajectory_fulfillment import BUYER_ASSENT, alpha_amount


def test_bonus_conversion_is_integer_ceil_and_bounded():
    assert alpha_amount(25000, Decimal("3")) == 83_333_333_334
    assert alpha_amount(50000, Decimal("100")) == 5_000_000_000
    for price in ("0", "-1", "NaN", "Infinity", "0.0000000000000000001"):
        with pytest.raises(Conflict):
            alpha_amount(25000, price)


async def setup_claim(kit, owner, admin):
    account, sid, package = await accepted(kit, owner)
    response = await owner.post(f"/v1/me/submissions/{sid}/trajectory", **as_upload(package))
    assert response.status_code == 200, response.text
    cid = response.json()["id"]
    admin_account = await reviewer(kit, admin)
    async with kit.session() as session:
        person = await session.get(Account, uuid.UUID(account["id"]))
        person.payout_coldkey = MINER_COLDKEY
        operator = await session.get(Account, uuid.UUID(admin_account["id"]))
        operator.roles = ["MINER", "REVIEWER", "ADMIN"]
        await session.commit()
    approved = await admin.post(f"/v1/admin/trajectories/{cid}/decision", json=review_body(), headers=same_origin())
    assert approved.status_code == 200, approved.text
    return account, sid, approved.json()


@pytest.mark.skipif(postgres_dsn() is None, reason="requires verified private fixture")
def test_bonus_reservation_duplicate_guard_and_finalized_reconciliation():
    async def scenario():
        kit = await kit_config(BOUNTY_POOL_BALANCE_RAO="100000000000").setup()
        try:
            async with await client(kit) as owner, await client(kit) as admin:
                _, sid, claim = await setup_claim(kit, owner, admin)
                path = f"/v1/admin/trajectories/{claim['id']}/payments"
                body = {"expected_state_version": claim["state_version"], "alpha_usd": "1000",
                        "price_source": "Synthetic fixture price", "price_observed_at": dt.datetime.now(dt.UTC).isoformat(),
                        "destination_coldkey": MINER_COLDKEY, "destination_evidence": "Fixture owner account destination verified"}
                assert (await owner.post(path, json=body, headers=same_origin())).status_code == 403
                stale = {**body, "price_observed_at": (dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)).isoformat()}
                assert (await admin.post(path, json=stale, headers=same_origin())).status_code == 409
                response = await admin.post(path, json=body, headers=same_origin())
                assert response.status_code == 200, response.text
                payment = response.json()
                assert payment["status"] == "PREPARED"
                assert response.headers["cache-control"] == "no-store"
                assert (await admin.post(path, json=body, headers=same_origin())).status_code == 409
                pid = payment["id"]
                assert (await admin.get(f"/v1/admin/trajectory-payments/{pid}/instruction")).status_code == 200
                async with kit.session() as session:
                    quote = await kit.services.pricing.quote(session, reward_target_id=kit.services.pricing.reward_target_ids[0])
                    assert int(quote.inputs["committed_bounty_rao"]) >= int(payment["amount_rao"])
                    assert await payouts.oldest_unresolved_at(session,
                        origin_coldkey=payment["origin_coldkey"], origin_hotkey=payment["origin_hotkey"]) is not None
                    row = TreasuryPayout(extrinsic_reference="1000-0", block=1000,
                        block_timestamp=dt.datetime.now(dt.UTC), extrinsic_index=0, event_index=0,
                        origin_coldkey=payment["origin_coldkey"], origin_hotkey=payment["origin_hotkey"],
                        destination_coldkey=MINER_COLDKEY, destination_hotkey=payment["origin_hotkey"],
                        origin_netuid=66, destination_netuid=66, amount_rao=int(payment["amount_rao"]))
                    session.add(row)
                    await session.commit()
                    payout_id = row.id
                async with kit.session() as session:
                    assert await payouts.claim_payout(session, payout_id) is None
                    await session.commit()
                # A later rejection blocks signing but cannot hide an actual finalized transfer.
                async with kit.session() as session:
                    await proof_store.correct_human_decision(session, uuid.UUID(sid),
                        supersedes_id=(await proof_store.latest_binding_decision(session, uuid.UUID(sid))).id,
                        notes_public="Fixture changed approval", decision=ReviewOutcome.REJECTED,
                        reason_code="REVIEW_REJECTED", reviewer="fixture correction", notes="Fixture changed approval")
                    await session.commit()
                assert (await admin.get(f"/v1/admin/trajectory-payments/{pid}/instruction")).status_code == 409
                confirmation = await admin.post(f"/v1/admin/trajectory-payments/{pid}/confirm",
                    json={"treasury_payout_id": payout_id}, headers=same_origin())
                assert confirmation.status_code == 200, confirmation.text
                assert confirmation.json()["status"] == "CONFIRMED"
                retry = await admin.post(f"/v1/admin/trajectory-payments/{pid}/confirm",
                    json={"treasury_payout_id": payout_id}, headers=same_origin())
                assert retry.status_code == 200
                result = await owner.get(f"/v1/me/submissions/{sid}/trajectory")
                assert result.json()["claim"]["payment_status"] == "CONFIRMED"
                async with kit.session() as session:
                    with pytest.raises(DBAPIError):
                        await session.execute(text("UPDATE trajectory_payments SET amount_rao = amount_rao + 1 WHERE id = :id"), {"id": pid})
                    await session.rollback()
                    assert await session.scalar(select(func.count()).select_from(TrajectoryPayment)) == 1
        finally:
            await kit.teardown()
    asyncio.run(scenario())


@pytest.mark.skipif(postgres_dsn() is None, reason="requires verified private fixture")
def test_private_delivery_needs_buyer_assent_and_stops_after_proof_correction():
    async def scenario():
        kit = await kit_config().setup()
        try:
            async with await client(kit) as owner, await client(kit) as admin, await client(kit) as buyer:
                _, sid, claim = await setup_claim(kit, owner, admin)
                buyer_account = await sign_in_by_email(kit, buyer, email="buyer@example.com")
                agreement = "sha256:" + "a" * 64
                response = await admin.post(f"/v1/admin/trajectories/{claim['id']}/deliveries", headers=same_origin(), json={
                    "buyer_id": buyer_account["id"], "agreement_reference": "Private fixture agreement A",
                    "agreement_sha256": agreement, "agreement_prohibits_open_publication_and_requires_takedown": True})
                assert response.status_code == 200, response.text
                did = response.json()["id"]
                path = f"/v1/me/chat-log-deliveries/{did}"
                assert (await owner.get(path + "/package")).status_code == 404
                assert (await buyer.get(path + "/package")).status_code == 409
                assert (await buyer.post(path + "/accept", json={"agreement_sha256": agreement, "assent": BUYER_ASSENT})).status_code == 403
                accepted_response = await buyer.post(path + "/accept", headers=same_origin(), json={"agreement_sha256": agreement, "assent": BUYER_ASSENT})
                assert accepted_response.status_code == 200, accepted_response.text
                downloaded = await buyer.get(path + "/package")
                assert downloaded.status_code == 200 and downloaded.content.startswith(b"PK")
                assert downloaded.headers["cache-control"] == "no-store"
                assert "sandbox" in downloaded.headers["content-security-policy"]
                async with kit.session() as session:
                    assert await session.scalar(select(func.count()).select_from(TrajectoryExport)) == 1
                    await proof_store.correct_human_decision(session, uuid.UUID(sid),
                        supersedes_id=(await proof_store.latest_binding_decision(session, uuid.UUID(sid))).id,
                        notes_public="Fixture changed approval", decision=ReviewOutcome.REJECTED,
                        reason_code="REVIEW_REJECTED", reviewer="fixture correction", notes="Fixture changed approval")
                    await session.commit()
                blocked = await buyer.get(path + "/package")
                assert blocked.status_code == 409 and blocked.headers["cache-control"] == "no-store"
                queue = await admin.get("/v1/admin/trajectory-deliveries")
                assert queue.json()["items"][0]["takedown_required"] is True
                action_path = f"/v1/admin/trajectory-deliveries/{did}"
                revoked = await admin.post(action_path, headers=same_origin(), json={"action": "REVOKE", "reference": "Fixture proof approval revoked"})
                assert revoked.status_code == 200, revoked.text
                completed = await admin.post(action_path, headers=same_origin(), json={"action": "RECORD_TAKEDOWN", "reference": "Fixture buyer confirmed deletion"})
                assert completed.status_code == 200 and completed.json()["takedown_required"] is False
                assert (await buyer.get(path + "/package")).status_code == 409
        finally:
            await kit.teardown()
    asyncio.run(scenario())


@pytest.mark.skipif(postgres_dsn() is None, reason="requires verified private fixture")
def test_bonus_cancellation_needs_chain_check_and_preserves_previous_instruction():
    async def scenario():
        kit = await kit_config(BOUNTY_POOL_BALANCE_RAO="100000000000").setup()
        try:
            async with await client(kit) as owner, await client(kit) as admin:
                _, _, claim = await setup_claim(kit, owner, admin)
                path = f"/v1/admin/trajectories/{claim['id']}/payments"
                body = {"expected_state_version": claim["state_version"], "alpha_usd": "1000",
                        "price_source": "Synthetic fixture price", "price_observed_at": dt.datetime.now(dt.UTC).isoformat(),
                        "destination_coldkey": MINER_COLDKEY, "destination_evidence": "Fixture destination independently checked"}
                excessive = await admin.post(path, headers=same_origin(), json={**body, "alpha_usd": "0.001"})
                assert excessive.status_code == 409
                prepared = await admin.post(path, headers=same_origin(), json=body)
                assert prepared.status_code == 200, prepared.text
                first = prepared.json()
                cancellation = f"/v1/admin/trajectory-payments/{first['id']}/cancel"
                bad = await admin.post(cancellation, headers=same_origin(), json={"note": "Fixture replace unused quote", "chain_evidence": {}})
                assert bad.status_code == 409
                cancelled = await admin.post(cancellation, headers=same_origin(), json={
                    "note": "Fixture replace unused quote", "chain_evidence": {
                        "checked_at": dt.datetime.now(dt.UTC).isoformat(), "network": "finney",
                        "multisig_address": first["origin_coldkey"], "pending_multisig_count": 0,
                        "no_matching_chain_transfer": True, "finalized_block_hash": "0x" + "a" * 64}})
                assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED", cancelled.text
                second = await admin.post(path, headers=same_origin(), json=body)
                assert second.status_code == 200 and second.json()["id"] != first["id"], second.text
                async with kit.session() as session:
                    assert await session.scalar(select(func.count()).select_from(TrajectoryPayment)) == 2
                    with pytest.raises(DBAPIError):
                        await session.execute(text("DELETE FROM trajectory_payments WHERE id = :id"), {"id": first["id"]})
                    await session.rollback()
        finally:
            await kit.teardown()
    asyncio.run(scenario())
