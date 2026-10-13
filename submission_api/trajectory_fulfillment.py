"""Audited manual fulfillment. Callers authenticate administrators and own the transaction."""
import datetime as dt
import uuid
from decimal import Decimal, ROUND_CEILING, localcontext

from sqlalchemy import func, select
from sqlalchemy.orm import defer

from conjectures_subnet.bounty import BOUNTY_RESERVATION_ADVISORY_LOCK
from conjectures_subnet.db.models import Account, Submission, TreasuryPayout, TreasuryPayoutState
from conjectures_subnet.db.trajectory_models import TrajectoryClaim
from conjectures_subnet.db.trajectory_fulfillment import TrajectoryPayment, TrajectoryExport
from submission_api import trajectories as store
from submission_api.errors import Conflict, NotFound

BUYER_ASSENT = "I agree not to publish these chat logs openly and to cease using and delete all copies if their acceptance is revoked."


async def lock_claim(session, claim_id):
    sid = await session.scalar(select(TrajectoryClaim.submission_id).where(TrajectoryClaim.id == claim_id))
    if sid is None:
        raise NotFound("Accepted chat logs not found")
    await session.scalar(select(Submission).where(Submission.id == sid).with_for_update())
    claim = await session.scalar(select(TrajectoryClaim).options(defer(TrajectoryClaim.package))
        .where(TrajectoryClaim.id == claim_id).execution_options(populate_existing=True).with_for_update())
    if claim is None:
        raise NotFound("Accepted chat logs not found")
    return claim


async def require_accepted(session, claim):
    if await store.effective_status(session, claim) not in store.APPROVED or not await store.content_available(session, claim):
        raise Conflict("A current proof approval and accepted, available chat logs are required.",
                       reason_code="TRAJECTORY_FULFILLMENT_BLOCKED")


def alpha_amount(cents, price):
    price = Decimal(price)
    if not price.is_finite() or price <= 0 or price.as_tuple().exponent < -18 or price >= Decimal("1e20"):
        raise Conflict("Alpha price must be positive with at most 18 decimal places.")
    with localcontext() as context:
        context.prec = 80
        amount = int((Decimal(cents) * 10_000_000 / price).to_integral_value(rounding=ROUND_CEILING))
    if not 0 < amount < 2**63:
        raise Conflict("Converted bonus is outside the supported Alpha range.")
    return amount


def payment_view(payment):
    return {"id": str(payment.id), "claim_id": str(payment.claim_id), "status": payment.status,
            "amount_usd_cents": payment.amount_usd_cents, "amount_rao": str(payment.amount_rao),
            "alpha_usd": str(payment.alpha_usd), "price_source": payment.price_source,
            "price_observed_at": payment.price_observed_at, "destination_coldkey": payment.destination_coldkey,
            "origin_coldkey": payment.origin_coldkey, "origin_hotkey": payment.origin_hotkey,
            "netuid": 66, "prepared_at": payment.prepared_at, "completed_at": payment.completed_at,
            "treasury_payout_id": payment.treasury_payout_id}


async def prepare_payment(session, *, claim_id, actor_id, body, services):
    now = dt.datetime.now(dt.UTC)
    if body.price_observed_at.tzinfo is None or not dt.timedelta(0) <= now - body.price_observed_at <= dt.timedelta(minutes=10):
        raise Conflict("Record a price observed in the last ten minutes, with its timezone.")
    if services.settings.bounty_netuid != 66:
        raise Conflict("Historical bonuses require Subnet 66 Alpha.")
    await session.execute(select(func.pg_advisory_xact_lock(BOUNTY_RESERVATION_ADVISORY_LOCK)))
    claim = await lock_claim(session, claim_id)
    await require_accepted(session, claim)
    if claim.amount_usd_cents == 0 or claim.account_id == actor_id:
        raise Conflict("Only another contributor's historical bonus can be prepared.")
    if body.expected_state_version != claim.state_version:
        raise Conflict("Claim changed; refresh before preparing payment.")
    existing = await session.scalar(select(TrajectoryPayment).where(
        TrajectoryPayment.claim_id == claim_id, TrajectoryPayment.status != "CANCELLED"))
    if existing:
        raise Conflict("This claim already has a reserved or confirmed bonus.", reason_code="TRAJECTORY_PAYMENT_EXISTS")
    account = await session.get(Account, claim.account_id, populate_existing=True, with_for_update=True)
    if not account.payout_coldkey or account.payout_coldkey != body.destination_coldkey:
        raise Conflict("Verify the contributor's current account payout destination first.")
    amount = alpha_amount(claim.amount_usd_cents, body.alpha_usd)
    snapshot = await services.pricing.quote_many(session, reward_target_ids=())
    if (amount > snapshot.available_balance_rao or snapshot.netuid != 66
            or snapshot.wallet_coldkey != services.settings.bounty_wallet_coldkey
            or snapshot.wallet_hotkey != services.settings.bounty_wallet_hotkey):
        raise Conflict("Available treasury does not cover this bonus.", reason_code="TREASURY_RESERVED")
    payment = TrajectoryPayment(id=uuid.uuid4(), claim_id=claim_id, prepared_by=actor_id, prepared_at=now,
        claim_state_version=claim.state_version, amount_usd_cents=claim.amount_usd_cents, amount_rao=amount,
        alpha_usd=body.alpha_usd, price_source=body.price_source, price_observed_at=body.price_observed_at,
        destination_coldkey=account.payout_coldkey, origin_coldkey=services.settings.bounty_wallet_coldkey,
        origin_hotkey=services.settings.bounty_wallet_hotkey, destination_evidence=body.destination_evidence,
        status="PREPARED")
    session.add(payment)
    await session.flush()
    return payment


async def require_sendable(session, payment, claim):
    await require_accepted(session, claim)
    account = await session.get(Account, claim.account_id, populate_existing=True)
    if (payment.status != "PREPARED" or payment.claim_state_version != claim.state_version
            or account.payout_coldkey != payment.destination_coldkey
            or dt.datetime.now(dt.UTC) - payment.price_observed_at > dt.timedelta(minutes=10)):
        raise Conflict("Payment is stale, completed, or its approval/destination changed. Do not sign it.",
                       reason_code="TRAJECTORY_PAYMENT_BLOCKED")


async def confirm_payment(session, *, payment_id, payout_id, actor_id):
    # Chain row first, matching the payout reconciler. Record an actual transfer even when
    # approval changed after signing; confirmation is accounting, never new authorization.
    payout = await session.get(TreasuryPayout, payout_id, with_for_update=True)
    initial = await session.get(TrajectoryPayment, payment_id)
    if payout is None or initial is None:
        raise NotFound("Payment or finalized chain observation not found")
    await lock_claim(session, initial.claim_id)
    payment = await session.get(TrajectoryPayment, payment_id, populate_existing=True, with_for_update=True)
    if payment.status == "CONFIRMED" and payment.treasury_payout_id == payout_id:
        return payment
    if (payment.status != "PREPARED" or payout.status != TreasuryPayoutState.UNCLAIMED
            or payout.reward_event_id is not None
            or payout.amount_rao != payment.amount_rao or payout.destination_coldkey != payment.destination_coldkey
            or payout.origin_coldkey != payment.origin_coldkey or payout.origin_hotkey != payment.origin_hotkey
            or payout.destination_hotkey != payment.origin_hotkey
            or payout.origin_netuid != 66 or payout.destination_netuid != 66
            or payout.block_timestamp < payment.prepared_at - dt.timedelta(seconds=60)):
        raise Conflict("Finalized transfer does not match this unpaid bonus.", reason_code="TRAJECTORY_PAYMENT_MISMATCH")
    payout.status = TreasuryPayoutState.DISREGARDED
    payout.note = f"Historical chat-log bonus {payment.id}; accounted in trajectory_payments."
    payment.status = "CONFIRMED"
    payment.treasury_payout_id = payout.id
    payment.completed_at = dt.datetime.now(dt.UTC)
    payment.completed_by = actor_id
    payment.completion_evidence = {"extrinsic_reference": payout.extrinsic_reference, "finalized_block": payout.block}
    await session.flush()
    return payment


async def delivery_view(session, delivery):
    claim = await session.get(TrajectoryClaim, delivery.claim_id, options=[defer(TrajectoryClaim.package)])
    valid = (delivery.revoked_at is None and claim.package_digest == delivery.package_digest
             and claim.state_version == delivery.claim_state_version
             and await store.effective_status(session, claim) in store.APPROVED
             and await store.content_available(session, claim))
    exported = bool(await session.scalar(select(TrajectoryExport.id).where(
        TrajectoryExport.delivery_id == delivery.id).limit(1)))
    return {"id": str(delivery.id), "claim_id": str(delivery.claim_id), "buyer_id": str(delivery.buyer_id),
            "agreement_reference": delivery.agreement_reference,
            "agreement_sha256": "sha256:" + delivery.agreement_sha256.hex(), "required_assent": BUYER_ASSENT,
            "accepted_at": delivery.accepted_at, "revoked_at": delivery.revoked_at,
            "acceptance_available": valid and delivery.accepted_at is None,
            "download_available": valid and delivery.accepted_at is not None,
            "takedown_required": exported and not valid and delivery.takedown_recorded_at is None,
            "takedown_recorded_at": delivery.takedown_recorded_at}
