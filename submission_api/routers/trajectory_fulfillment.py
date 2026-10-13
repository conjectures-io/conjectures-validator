"""Administrative bonus accounting and account-bound buyer delivery; no funds are sent."""
import datetime as dt
import uuid
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import Field, StringConstraints, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from conjectures_subnet.bounty import BOUNTY_RESERVATION_ADVISORY_LOCK
from conjectures_subnet.db.models import ADMIN_ROLE, Account, TreasuryPayout
from conjectures_subnet.db.payout_instructions import validate_chain_evidence
from conjectures_subnet.db.errors import RecordConflict
from conjectures_subnet.db.trajectory_models import TrajectoryClaim
from conjectures_subnet.db.trajectory_fulfillment import TrajectoryPayment, TrajectoryDelivery, TrajectoryExport
from submission_api import trajectory_fulfillment as fulfillment
from submission_api.dependencies import CookieWriterDep, PrincipalDep, ServicesDep, SessionDep, require_role, require_role_writer
from submission_api.errors import BadRequest, Conflict, NotFound
from submission_api.routers.trajectories import bounded_json
from submission_api.sessions import Principal
from submission_api.trajectory_schema import StrictModel

router = APIRouter(prefix="/v1", tags=["chat-log fulfillment"])
Admin = Annotated[Principal, Depends(require_role(ADMIN_ROLE))]
AdminWriter = Annotated[Principal, Depends(require_role_writer(ADMIN_ROLE))]
Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=2000)]
Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[a-f0-9]{64}$")]


async def body_as(request, model):
    try:
        return model.model_validate_json(await bounded_json(request, 16384))
    except ValidationError:
        raise BadRequest("Invalid fulfillment request; check required fields and values.") from None


async def commit(session):
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise Conflict("This claim, delivery, or finalized transfer already has a fulfillment record.") from None


class PreparePayment(StrictModel):
    expected_state_version: Annotated[int, Field(strict=True, gt=0)]
    alpha_usd: Annotated[Decimal, Field(gt=0, max_digits=38, decimal_places=18)]
    price_source: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    price_observed_at: dt.datetime
    destination_coldkey: Annotated[str, StringConstraints(min_length=47, max_length=49)]
    destination_evidence: Note


@router.post("/admin/trajectories/{claim_id}/payments")
async def prepare(claim_id: uuid.UUID, request: Request, principal: AdminWriter, session: SessionDep, services: ServicesDep):
    payment = await fulfillment.prepare_payment(session, claim_id=claim_id, actor_id=principal.account.id,
        body=await body_as(request, PreparePayment), services=services)
    await commit(session)
    return fulfillment.payment_view(payment)


@router.get("/admin/trajectory-payments")
async def payment_queue(principal: Admin, session: SessionDep, after: uuid.UUID | None = None):
    query = select(TrajectoryPayment).order_by(TrajectoryPayment.id).limit(100)
    if after:
        query = query.where(TrajectoryPayment.id > after)
    rows = (await session.scalars(query)).all()
    return {"items": [fulfillment.payment_view(row) for row in rows],
            "next_cursor": str(rows[-1].id) if len(rows) == 100 else None}


@router.get("/admin/trajectory-payments/{payment_id}")
async def payment_status(payment_id: uuid.UUID, principal: Admin, session: SessionDep):
    payment = await session.get(TrajectoryPayment, payment_id)
    if payment is None:
        raise NotFound("Payment not found")
    return fulfillment.payment_view(payment)


@router.get("/admin/trajectory-payments/{payment_id}/instruction")
async def instruction(payment_id: uuid.UUID, principal: Admin, session: SessionDep):
    payment = await session.get(TrajectoryPayment, payment_id)
    if payment is None:
        raise NotFound("Payment not found")
    claim = await fulfillment.lock_claim(session, payment.claim_id)
    payment = await session.get(TrajectoryPayment, payment_id, populate_existing=True)
    await fulfillment.require_sendable(session, payment, claim)
    return {**fulfillment.payment_view(payment), "unsigned": True,
            "valid_until": payment.price_observed_at + dt.timedelta(minutes=10)}


class Confirmation(StrictModel):
    treasury_payout_id: Annotated[int, Field(strict=True, gt=0)]


@router.post("/admin/trajectory-payments/{payment_id}/confirm")
async def confirm(payment_id: uuid.UUID, request: Request, principal: AdminWriter, session: SessionDep):
    body = await body_as(request, Confirmation)
    payment = await fulfillment.confirm_payment(session, payment_id=payment_id,
        payout_id=body.treasury_payout_id, actor_id=principal.account.id)
    await commit(session)
    return fulfillment.payment_view(payment)


class Cancellation(StrictModel):
    note: Note
    chain_evidence: dict


@router.post("/admin/trajectory-payments/{payment_id}/cancel")
async def cancel(payment_id: uuid.UUID, request: Request, principal: AdminWriter, session: SessionDep):
    body = await body_as(request, Cancellation)
    now = dt.datetime.now(dt.UTC)
    try:
        validate_chain_evidence(body.chain_evidence, now=now)
    except RecordConflict as exc:
        raise Conflict(str(exc)) from None
    await session.execute(select(func.pg_advisory_xact_lock(BOUNTY_RESERVATION_ADVISORY_LOCK)))
    payment = await session.get(TrajectoryPayment, payment_id)
    if payment is None:
        raise NotFound("Payment not found")
    await fulfillment.lock_claim(session, payment.claim_id)
    payment = await session.get(TrajectoryPayment, payment_id, populate_existing=True, with_for_update=True)
    if payment.status != "PREPARED" or body.chain_evidence.get("multisig_address") != payment.origin_coldkey:
        raise Conflict("Only an unsigned, unpaid instruction with a matching treasury check can be cancelled.")
    observed = await session.scalar(select(TreasuryPayout.id).where(
        TreasuryPayout.origin_coldkey == payment.origin_coldkey,
        TreasuryPayout.destination_coldkey == payment.destination_coldkey,
        TreasuryPayout.amount_rao == payment.amount_rao,
        TreasuryPayout.block_timestamp >= payment.prepared_at - dt.timedelta(seconds=60)).limit(1))
    if observed:
        raise Conflict("A matching finalized transfer exists; reconcile it before cancelling.")
    payment.status, payment.completed_at, payment.completed_by = "CANCELLED", now, principal.account.id
    payment.completion_evidence = {"note": body.note, "chain_evidence": body.chain_evidence}
    await commit(session)
    return fulfillment.payment_view(payment)


class Grant(StrictModel):
    buyer_id: uuid.UUID
    agreement_reference: Note
    agreement_sha256: Digest
    agreement_prohibits_open_publication_and_requires_takedown: Literal[True]


@router.post("/admin/trajectories/{claim_id}/deliveries")
async def grant(claim_id: uuid.UUID, request: Request, principal: AdminWriter, session: SessionDep):
    body = await body_as(request, Grant)
    claim = await fulfillment.lock_claim(session, claim_id)
    await fulfillment.require_accepted(session, claim)
    if await session.get(Account, body.buyer_id) is None:
        raise NotFound("Buyer account not found")
    delivery = TrajectoryDelivery(id=uuid.uuid4(), claim_id=claim_id, buyer_id=body.buyer_id,
        created_by=principal.account.id, created_at=dt.datetime.now(dt.UTC), package_digest=claim.package_digest,
        claim_state_version=claim.state_version,
        agreement_reference=body.agreement_reference,
        agreement_sha256=bytes.fromhex(body.agreement_sha256.removeprefix("sha256:")))
    session.add(delivery)
    await commit(session)
    return await fulfillment.delivery_view(session, delivery)


@router.get("/admin/trajectory-deliveries")
async def delivery_queue(principal: Admin, session: SessionDep, after: uuid.UUID | None = None):
    query = select(TrajectoryDelivery).order_by(TrajectoryDelivery.id).limit(100)
    if after:
        query = query.where(TrajectoryDelivery.id > after)
    rows = (await session.scalars(query)).all()
    return {"items": [await fulfillment.delivery_view(session, row) for row in rows],
            "next_cursor": str(rows[-1].id) if len(rows) == 100 else None}


class DeliveryAction(StrictModel):
    action: Literal["REVOKE", "RECORD_TAKEDOWN"]
    reference: Note


@router.post("/admin/trajectory-deliveries/{delivery_id}")
async def delivery_action(delivery_id: uuid.UUID, request: Request, principal: AdminWriter, session: SessionDep):
    body = await body_as(request, DeliveryAction)
    initial = await session.get(TrajectoryDelivery, delivery_id)
    if initial is None:
        raise NotFound("Delivery not found")
    await fulfillment.lock_claim(session, initial.claim_id)
    delivery = await session.get(TrajectoryDelivery, delivery_id, populate_existing=True, with_for_update=True)
    now = dt.datetime.now(dt.UTC)
    if body.action == "REVOKE":
        if delivery.revoked_at is not None:
            raise Conflict("Delivery was already revoked.")
        delivery.revoked_at, delivery.revoked_by, delivery.revocation_reason = now, principal.account.id, body.reference
    else:
        if delivery.revoked_at is None or delivery.takedown_recorded_at is not None:
            raise Conflict("Revoke delivery first; an existing takedown record cannot be replaced.")
        delivery.takedown_recorded_at, delivery.takedown_recorded_by = now, principal.account.id
        delivery.takedown_reference = body.reference
    await commit(session)
    return await fulfillment.delivery_view(session, delivery)


async def owned_delivery(session, delivery_id, buyer_id):
    initial = await session.get(TrajectoryDelivery, delivery_id)
    if initial is None or initial.buyer_id != buyer_id:
        raise NotFound("Delivery not found")
    claim = await fulfillment.lock_claim(session, initial.claim_id)
    delivery = await session.get(TrajectoryDelivery, delivery_id, populate_existing=True, with_for_update=True)
    await fulfillment.require_accepted(session, claim)
    if (delivery.revoked_at or delivery.package_digest != claim.package_digest
            or delivery.claim_state_version != claim.state_version):
        raise Conflict("Delivery is revoked or its package changed.")
    return delivery, claim


@router.get("/me/chat-log-deliveries")
async def buyer_deliveries(principal: PrincipalDep, session: SessionDep, after: uuid.UUID | None = None):
    query = select(TrajectoryDelivery).where(TrajectoryDelivery.buyer_id == principal.account.id).order_by(TrajectoryDelivery.id).limit(100)
    if after:
        query = query.where(TrajectoryDelivery.id > after)
    rows = (await session.scalars(query)).all()
    return {"items": [await fulfillment.delivery_view(session, row) for row in rows],
            "next_cursor": str(rows[-1].id) if len(rows) == 100 else None}


class BuyerAcceptance(StrictModel):
    agreement_sha256: Digest
    assent: Literal[fulfillment.BUYER_ASSENT]


@router.post("/me/chat-log-deliveries/{delivery_id}/accept")
async def accept(delivery_id: uuid.UUID, request: Request, principal: CookieWriterDep, session: SessionDep):
    body = await body_as(request, BuyerAcceptance)
    delivery, _ = await owned_delivery(session, delivery_id, principal.account.id)
    if body.agreement_sha256 != "sha256:" + delivery.agreement_sha256.hex():
        raise Conflict("Accept the agreement attached to this delivery.")
    if delivery.accepted_at is None:
        delivery.accepted_at = dt.datetime.now(dt.UTC)
        delivery.assent_text = body.assent
    await commit(session)
    return await fulfillment.delivery_view(session, delivery)


@router.get("/me/chat-log-deliveries/{delivery_id}/package")
async def buyer_download(delivery_id: uuid.UUID, principal: PrincipalDep, session: SessionDep):
    delivery, claim = await owned_delivery(session, delivery_id, principal.account.id)
    if delivery.accepted_at is None:
        raise Conflict("Accept the private delivery agreement before downloading.")
    content = await session.scalar(select(TrajectoryClaim.package).where(TrajectoryClaim.id == claim.id))
    session.add(TrajectoryExport(id=uuid.uuid4(), delivery_id=delivery.id, exported_at=dt.datetime.now(dt.UTC),
        proof_review_decision_id=claim.proof_review_decision_id, claim_state_version=claim.state_version))
    await session.commit()
    return Response(content, media_type="application/zip", headers={
        "Cache-Control": "no-store", "Vary": "Authorization, Cookie", "X-Content-Type-Options": "nosniff",
        "Content-Disposition": f'attachment; filename="chat-logs-{claim.id}.zip"',
        "Content-Security-Policy": "default-src 'none'; sandbox"})
