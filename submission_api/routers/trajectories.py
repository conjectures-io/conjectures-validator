"""Session-owned intake and reviewer-only access to private, untrusted trajectory exports."""
from __future__ import annotations

import datetime as dt
import uuid

import anyio
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import Field, StringConstraints, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import defer

from conjectures_subnet.db.models import REVIEWER_ROLE, Submission
from conjectures_subnet.db.submissions import latest_binding_decision
from conjectures_subnet.db.trajectory_models import TrajectoryAccess, TrajectoryClaim
from submission_api import trajectories as store
from submission_api.dependencies import (
    CookieWriterDep, PrincipalDep, ServicesDep, SessionDep, require_role, require_role_writer,
)
from submission_api.errors import ApiError, BadRequest, Conflict, NotFound, PayloadTooLarge
from submission_api.sessions import Principal
from submission_api.trajectory_schema import (
    MAX_PACKAGE_BYTES, RETENTION_DAYS, TERMS_VERSION, StrictModel,
)
from submission_api.trajectory_terms import (
    ASSENT_STATEMENTS_SHA256, COMMERCIAL_TERMS_SHA256, COMMERCIAL_TERMS_VERSION,
    REVIEW_TERMS_SHA256, REVIEW_TERMS_VERSION,
)
from submission_api.trajectory_intake import MAX_ENVELOPE_BYTES, UploadBudget, prepare_upload, safe_errors

router = APIRouter(prefix="/v1", tags=["trajectories"])
Reviewer = Annotated[Principal, Depends(require_role(REVIEWER_ROLE))]
ReviewerWriter = Annotated[Principal, Depends(require_role_writer(REVIEWER_ROLE))]


class PrivateHeadersMiddleware:
    """Cover error responses too; setting headers inside a handler misses rejected requests."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        sensitive = path.startswith("/v1/admin/trajectories") or (
            path.startswith("/v1/me/submissions/") and "/trajectory" in path)
        if scope["type"] != "http" or not sensitive:
            return await self.app(scope, receive, send)

        async def private_send(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", [])
                           if k.lower() not in (b"cache-control", b"vary")]
                message = {**message, "headers": [*headers, (b"cache-control", b"no-store"),
                                                  (b"vary", b"Authorization, Cookie, Origin")]}
            await send(message)
        await self.app(scope, receive, private_send)


def private(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Vary"] = "Authorization, Cookie"


def enabled(services):
    if not services.settings.trajectory_bonus_enabled:
        raise ApiError("Trajectory bonus intake is not open.", status_code=503, reason_code="TRAJECTORY_DISABLED")


def cutoff(services):
    return dt.datetime.fromisoformat(services.settings.trajectory_bonus_start_at.replace("Z", "+00:00"))


async def bounded_json(request, limit):
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise ApiError("Send a UTF-8 application/json file.", status_code=415, reason_code="TRAJECTORY_FORMAT")
    if request.headers.get("content-encoding", "identity") != "identity":
        raise BadRequest("Compressed bodies are not accepted.")
    length = request.headers.get("content-length")
    if length is not None:
        try:
            declared = int(length)
        except ValueError:
            raise BadRequest("Invalid Content-Length") from None
        if declared < 0:
            raise BadRequest("Invalid Content-Length")
        if declared > limit:
            raise PayloadTooLarge("Trajectory body exceeds the byte limit.", reason_code="TRAJECTORY_SIZE")
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > limit:
            raise PayloadTooLarge("Trajectory body exceeds the byte limit.", reason_code="TRAJECTORY_SIZE")
        raw.extend(chunk)
    if length is not None and len(raw) != declared:
        raise BadRequest("Body length does not match Content-Length")
    return bytes(raw)


@router.get("/trajectory-offer")
async def offer(services: ServicesDep, response: Response):
    private(response)
    return {
        "enabled": services.settings.trajectory_bonus_enabled,
        "legacy_bonus_usd_cents": {"erdos": 50000, "green": 50000, "other": 25000},
        "new_bounty_share": {"numerator": 1, "denominator": 40, "basis": "gross_treasury"},
        "new_bounties_require_chat_logs": True, "terms_version": TERMS_VERSION,
        "start_at": services.settings.trajectory_bonus_start_at or None,
        "claim_window_days": store.CLAIM_WINDOW_DAYS, "max_package_bytes": MAX_PACKAGE_BYTES,
        "retention_days": RETENTION_DAYS, "audit_retention_days": store.AUDIT_RETENTION_DAYS,
        "payment_mode": "HISTORICAL_MANUAL_BONUS_OR_REQUIRED_BOUNTY_AUTHORIZATION",
        "required_evidence_audit_retention": "WHILE_REWARD_EVENT_REFERENCES_PROOF",
        "commercial_terms_version": COMMERCIAL_TERMS_VERSION,
        "commercial_terms_sha256": COMMERCIAL_TERMS_SHA256,
        "review_terms_version": REVIEW_TERMS_VERSION, "review_terms_sha256": REVIEW_TERMS_SHA256,
        "assent_statements_sha256": ASSENT_STATEMENTS_SHA256,
    }


@router.get("/me/submissions/{submission_id}/trajectory")
async def status(submission_id: uuid.UUID, principal: PrincipalDep, services: ServicesDep,
                 session: SessionDep, response: Response):
    private(response)
    submission = await store.owned_submission(session, submission_id, principal.account.id)
    claim = await store.claim_for(session, submission_id)
    decision = await latest_binding_decision(session, submission_id)
    reason = (store.eligibility_reason(submission, decision, cutoff=claim.offer_start_at if claim else cutoff(services),
                                       now=dt.datetime.now(dt.UTC), enforce_window=claim is None)
              if services.settings.trajectory_bonus_enabled else "Trajectory bonus intake is not open.")
    amount = claim.amount_usd_cents if claim else None
    if reason is None and claim is None:
        try:
            amount = await anyio.to_thread.run_sync(store.amount_for, services, submission)
        except Conflict as error:
            reason = str(error)
    return {"eligible": reason is None, "eligibility_reason": reason,
            "amount_usd_cents": amount,
            "chat_logs_required": submission.bounty_policy_version == store.TREASURY_CHAT_POLICY,
            "claim": await store.view(session, claim) if claim else None}


@router.post("/me/submissions/{submission_id}/trajectory")
async def upload(submission_id: uuid.UUID, request: Request, principal: CookieWriterDep,
                 services: ServicesDep, session: SessionDep, response: Response):
    private(response)
    enabled(services)
    account_id = principal.account.id  # rollback expires ORM objects; retain only this immutable ID.
    submission = await store.owned_submission(session, submission_id, account_id)
    decision = await latest_binding_decision(session, submission_id)
    existing = await store.claim_for(session, submission_id)
    reason = store.eligibility_reason(submission, decision, cutoff=existing.offer_start_at if existing else cutoff(services),
                                      now=dt.datetime.now(dt.UTC), enforce_window=existing is None)
    if reason:
        raise Conflict(reason, reason_code="TRAJECTORY_INELIGIBLE")
    # Release the auth/precheck transaction and its connection before accepting a slow body.
    await session.rollback()
    if not hasattr(request.app.state, "trajectory_upload_budget"):
        request.app.state.trajectory_upload_budget = UploadBudget()
    async with request.app.state.trajectory_upload_budget.hold(account_id):
        try:
            with anyio.fail_after(60):
                raw = await bounded_json(request, MAX_ENVELOPE_BYTES)
            prepared = await anyio.to_thread.run_sync(prepare_upload, raw)
        except TimeoutError:
            raise ApiError("Upload timed out; retry with a complete file.", status_code=408,
                           reason_code="TRAJECTORY_TIMEOUT") from None
        except ValidationError as error:
            raise BadRequest("Invalid trajectory package; check the indicated fields against the schema.",
                             reason_code="TRAJECTORY_INVALID", extra={"validation_errors": safe_errors(error)}) from None
        except (ValueError, UnicodeError, RecursionError):
            raise BadRequest("Invalid trajectory package or consent. Check the current terms, schema, hashes, chronology and limits.",
                             reason_code="TRAJECTORY_INVALID") from None
        # Recheck under the proof correction lock after all untrusted parsing and hashing.
        submission = await store.owned_submission(session, submission_id, account_id, lock=True)
        decision = await latest_binding_decision(session, submission_id)
        now = dt.datetime.now(dt.UTC)
        existing = await store.claim_for(session, submission_id)
        offer_start_at = existing.offer_start_at if existing else cutoff(services)
        reason = store.eligibility_reason(submission, decision, cutoff=offer_start_at, now=now,
                                          enforce_window=existing is None)
        if reason:
            raise Conflict(reason, reason_code="TRAJECTORY_INELIGIBLE")
        claim = await store.accept_package(session, submission=submission, decision=decision,
                                           prepared=prepared, account_id=account_id, now=now,
                                           amount_usd_cents=(existing.amount_usd_cents if existing else
                                               await anyio.to_thread.run_sync(store.amount_for, services, submission)),
                                           offer_start_at=offer_start_at)
        await session.commit()
        return await store.view(session, claim)


class StateVersion(StrictModel):
    expected_state_version: Annotated[int, Field(strict=True, ge=1, le=2147483647)]


async def mutation_version(request):
    try:
        return StateVersion.model_validate_json(await bounded_json(request, 4096)).expected_state_version
    except ValidationError:
        raise BadRequest("Include the current claim state version; refresh before retrying.") from None


def check_version(claim, expected):
    if claim.state_version != expected:
        raise Conflict("This claim changed; refresh before retrying.", reason_code="TRAJECTORY_STALE_REVIEW")


@router.post("/me/submissions/{submission_id}/trajectory/withdraw")
async def withdraw(submission_id: uuid.UUID, request: Request, principal: CookieWriterDep, session: SessionDep, response: Response):
    private(response)
    expected = await mutation_version(request)
    await store.owned_submission(session, submission_id, principal.account.id, lock=True)
    claim = await store.claim_for(session, submission_id)
    if claim is None:
        raise NotFound("Trajectory not found")
    check_version(claim, expected)
    if claim.status not in ("UNDER_REVIEW", "NEEDS_CHANGES", "WITHDRAWN"):
        raise Conflict("Only a pending trajectory can be withdrawn; contact support about a decided claim.")
    if claim.status != "WITHDRAWN":
        claim.status = "WITHDRAWN"
        claim.package = None
        claim.commercial_withdrawn_at = dt.datetime.now(dt.UTC)
        await store.minimize_election(session, claim)
        store.audit(claim, action="WITHDRAWN", actor=principal.account.id, now=dt.datetime.now(dt.UTC))
        await session.commit()
    return await store.view(session, claim)


@router.get("/admin/trajectories")
async def queue(principal: Reviewer, session: SessionDep, response: Response,
                offset: Annotated[int, Query(ge=0, le=100000)] = 0):
    private(response)
    election = store.commercial_projection()
    rows = (await session.execute(select(TrajectoryClaim, election).options(
        defer(TrajectoryClaim.package), defer(TrajectoryClaim.commercial_election))
        .order_by(TrajectoryClaim.created_at.desc(), TrajectoryClaim.id)
        .offset(offset).limit(51))).all()
    return {"items": [await metadata_view(session, r, e) for r, e in rows[:50]],
            "next_offset": offset + 50 if len(rows) > 50 else None}


async def metadata_view(session, claim, election):
    # Do not lazy-load private bytea data to build a queue or status response.
    return {"id": str(claim.id), "submission_id": str(claim.submission_id),
            "status": await store.effective_status(session, claim), "revision": claim.revision, "state_version": claim.state_version,
            "amount_usd_cents": claim.amount_usd_cents,
            "chat_logs_required": claim.amount_usd_cents == 0,
            "review_note": claim.review_note, "expires_at": claim.expires_at,
            "commercial_election": election, "commercial_review": claim.commercial_review,
            "commercial_withdrawn_at": claim.commercial_withdrawn_at,
            "license_status": "INACTIVE_NO_PAYMENT_OR_ACTIVATION",
            "package_sha256": "sha256:" + claim.package_digest.hex()}


@router.get("/admin/trajectories/{claim_id}/package")
async def download(claim_id: uuid.UUID, principal: Reviewer, session: SessionDep):
    claim = await session.get(TrajectoryClaim, claim_id)
    if claim is None or claim.package is None or claim.expires_at <= dt.datetime.now(dt.UTC):
        raise NotFound("Trajectory content is unavailable or expired")
    content = claim.package
    session.add(TrajectoryAccess(id=uuid.uuid4(), claim_id=claim.id, actor_id=principal.account.id,
                                 accessed_at=dt.datetime.now(dt.UTC)))
    await session.commit()  # Do not release content unless the access record is durable.
    return Response(content, media_type="application/json", headers={
        "Cache-Control": "no-store", "Vary": "Authorization, Cookie",
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": f'attachment; filename="trajectory-{claim.id}.json"',
        "Content-Security-Policy": "default-src 'none'; sandbox",
    })


class Checklist(StrictModel):
    authentic_export_and_provenance: Literal[True]
    complete_visible_history: Literal[True]
    tools_failures_revisions_accounted_for: Literal[True]
    proof_and_pins_reproduce: Literal[True]
    gaps_and_redactions_do_not_prevent_acceptance: Literal[True]
    ownership_consent_and_privacy: Literal[True]
    full_bounty_under_recorded_policy_not_disqualified_or_defect: Literal[True]
    duplicate_and_fabrication_checks: Literal[True]
    review_only_rights_sufficient: Literal[True]


ReviewNote = Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=2000)]


class Decision(StateVersion):
    decision: Literal["APPROVED_MANUAL_PAYMENT", "NEEDS_CHANGES", "REJECTED"]
    expected_revision: Annotated[int, Field(strict=True, ge=1, le=3)]
    note: ReviewNote
    checklist: Checklist | None
    commercial_clearance: Literal["NOT_CLEARED", "CLEARED_CONDITIONAL"]
    commercial_note: ReviewNote
    source_rights_verified: bool = Field(strict=True)


@router.post("/admin/trajectories/{claim_id}/decision")
async def decide(claim_id: uuid.UUID, request: Request, principal: ReviewerWriter,
                 services: ServicesDep, session: SessionDep, response: Response):
    private(response)
    raw = await bounded_json(request, 16384)
    try:
        body = Decision.model_validate_json(raw)
    except ValidationError:
        raise BadRequest("Invalid review decision; use the required checklist and a concise owner-visible note.") from None
    claim = await session.scalar(select(TrajectoryClaim).options(defer(TrajectoryClaim.package),
        defer(TrajectoryClaim.commercial_election)).where(TrajectoryClaim.id == claim_id))
    if claim is None:
        raise NotFound("Trajectory not found")
    # Same lock order as intake and the proof correction route: submission, then claim.
    submission = await session.scalar(select(Submission).where(Submission.id == claim.submission_id).with_for_update())
    claim = await session.scalar(select(TrajectoryClaim).options(defer(TrajectoryClaim.package),
        defer(TrajectoryClaim.commercial_election)).where(TrajectoryClaim.id == claim_id)
        .execution_options(populate_existing=True).with_for_update())
    check_version(claim, body.expected_state_version)
    if principal.account.id == claim.account_id:
        raise Conflict("A contributor cannot review their own trajectory.")
    reviewable = claim.status == "UNDER_REVIEW" or (body.decision == "REJECTED" and claim.status in ("EXPIRED", "NEEDS_CHANGES"))
    if not reviewable or claim.revision != body.expected_revision:
        raise Conflict("This review is stale or already decided.", reason_code="TRAJECTORY_STALE_REVIEW")
    now = dt.datetime.now(dt.UTC)
    if body.decision != "REJECTED" and not await store.content_available(session, claim):
        raise Conflict("Content expired; approval is unavailable.", reason_code="TRAJECTORY_EXPIRED")
    proof_decision = await latest_binding_decision(session, submission.id)
    if body.decision == "APPROVED_MANUAL_PAYMENT":
        reason = store.eligibility_reason(submission, proof_decision, cutoff=claim.offer_start_at, now=now,
                                          enforce_window=False)
        if reason:
            raise Conflict(reason, reason_code="TRAJECTORY_INELIGIBLE")
        if body.checklist is None:
            raise BadRequest("Approval requires every human acceptance check.")
        claim.proof_review_decision_id = proof_decision.id
        claim.proof_policy_snapshot = {"review_policy_version": submission.review_policy_version,
                                       "bounty_policy_version": submission.bounty_policy_version}
    if body.commercial_clearance == "CLEARED_CONDITIONAL":
        if claim.commercial_withdrawn_at is not None:
            raise Conflict("Commercial election was withdrawn.", reason_code="TRAJECTORY_RIGHTS_WITHDRAWN")
        election = await session.scalar(select(TrajectoryClaim.commercial_election).where(TrajectoryClaim.id == claim_id))
        reason = store.commercial_clearance_reason(election)
        if reason or not body.source_rights_verified or body.decision != "APPROVED_MANUAL_PAYMENT":
            raise Conflict(reason or "Conditional rights clearance needs an approved package and verified source rights.",
                           reason_code="TRAJECTORY_RIGHTS_UNCLEARED")
    claim.status = ("APPROVED_BOUNTY_EVIDENCE" if body.decision == "APPROVED_MANUAL_PAYMENT"
                    and claim.amount_usd_cents == 0 else body.decision)
    claim.reviewer_id = principal.account.id
    claim.review_note = body.note.strip()
    claim.checklist = body.checklist.model_dump() if body.checklist else None
    claim.commercial_review = {"decision": body.commercial_clearance,
                               "note": body.commercial_note.strip(),
                               "source_rights_verified": body.source_rights_verified,
                               "reviewer_id": str(principal.account.id), "reviewed_at": now.isoformat(),
                               "license_status": "INACTIVE_NO_PAYMENT_OR_ACTIVATION"}
    store.audit(claim, action=claim.status, actor=principal.account.id, now=now)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise Conflict("This reward target already has an approved trajectory bonus.", reason_code="TRAJECTORY_DUPLICATE") from None
    return await store.view(session, claim)


@router.post("/me/submissions/{submission_id}/trajectory/withdraw-commercial")
async def withdraw_commercial(submission_id: uuid.UUID, request: Request, principal: CookieWriterDep,
                              session: SessionDep, response: Response):
    private(response)
    expected = await mutation_version(request)
    await store.owned_submission(session, submission_id, principal.account.id, lock=True)
    claim = await store.claim_for(session, submission_id)
    if claim is None:
        raise NotFound("Trajectory not found")
    check_version(claim, expected)
    # No activation route exists. Every current election is therefore withdrawable,
    # independently of the bonus claim's status or whether intake is paused.
    if claim.commercial_withdrawn_at is None:
        now = dt.datetime.now(dt.UTC)
        claim.commercial_withdrawn_at = now
        claim.commercial_review = None
        store.audit(claim, action="COMMERCIAL_WITHDRAWN", actor=principal.account.id, now=now)
        await session.commit()
    return await store.view(session, claim)


class Reopen(StateVersion):
    expected_revision: Annotated[int, Field(strict=True, ge=1, le=3)]
    action: Literal["REVOKE_PROOF_CHANGED", "REOPEN_REVIEW", "REBIND_PROOF_DECISION"]
    note: ReviewNote


@router.post("/admin/trajectories/{claim_id}/reopen")
async def reopen(claim_id: uuid.UUID, request: Request, principal: ReviewerWriter,
                 session: SessionDep, response: Response):
    private(response)
    try:
        body = Reopen.model_validate_json(await bounded_json(request, 16384))
    except ValidationError:
        raise BadRequest("Invalid reopen request; include a concise owner-visible explanation.") from None
    claim = await session.scalar(select(TrajectoryClaim).options(defer(TrajectoryClaim.package),
        defer(TrajectoryClaim.commercial_election)).where(TrajectoryClaim.id == claim_id))
    if claim is None:
        raise NotFound("Trajectory not found")
    if body.action == "REVOKE_PROOF_CHANGED" and claim.amount_usd_cents == 0:
        # Match the event -> submission lock order used by cancellation/reconciliation.
        from conjectures_subnet.db.submissions import _reward_event_exists
        if await _reward_event_exists(session, claim.submission_id):
            raise Conflict("Cancel the unsigned payout instruction before revoking required evidence; transfers already in flight cannot be revoked.",
                           reason_code="TRAJECTORY_PAYOUT_IN_FLIGHT")
    submission = await session.scalar(select(Submission).where(Submission.id == claim.submission_id).with_for_update())
    claim = await session.scalar(select(TrajectoryClaim).options(defer(TrajectoryClaim.package),
        defer(TrajectoryClaim.commercial_election)).where(TrajectoryClaim.id == claim_id)
        .execution_options(populate_existing=True).with_for_update())
    check_version(claim, body.expected_state_version)
    if principal.account.id == claim.account_id or claim.revision != body.expected_revision:
        raise Conflict("Self review or stale revision is not allowed.")
    if body.action == "REBIND_PROOF_DECISION":
        current = await latest_binding_decision(session, submission.id)
        snapshot = {"review_policy_version": submission.review_policy_version,
                    "bounty_policy_version": submission.bounty_policy_version}
        reason = store.eligibility_reason(submission, current, cutoff=claim.offer_start_at,
                                          now=dt.datetime.now(dt.UTC), enforce_window=False)
        if (claim.status not in ("APPROVED_MANUAL_PAYMENT", "APPROVED_BOUNTY_EVIDENCE") or reason or
                snapshot != claim.proof_policy_snapshot or current.id == claim.proof_review_decision_id or
                not await store.unchanged_approval_chain(session, submission.id, current, claim.proof_review_decision_id)):
            raise Conflict("Rebinding requires an unchanged policy and a current full-bounty human approval.",
                           reason_code="TRAJECTORY_REBIND_INELIGIBLE")
        previous = claim.proof_review_decision_id
        claim.proof_review_decision_id = current.id
        store.audit(claim, action=body.action, actor=principal.account.id, now=dt.datetime.now(dt.UTC),
                    extra={"previous_proof_review_decision_id": previous, "note": body.note})
        await session.commit()
        return await store.view(session, claim)
    if body.action == "REVOKE_PROOF_CHANGED":
        if await store.effective_status(session, claim) != "BLOCKED_PROOF_CHANGED":
            raise Conflict("Only a bonus blocked by a changed proof decision can be revoked here.")
        claim.status = "REVOKED_PROOF_CHANGED"  # Releases the unique approved-target reservation.
    else:
        if claim.status not in ("REVOKED_PROOF_CHANGED", "REJECTED"):
            raise Conflict("Only a rejected or revoked claim can be reopened.")
        if not await store.content_available(session, claim):
            raise Conflict("Expired content cannot be reopened; the retention deadline is final.")
        claim.status = "UNDER_REVIEW"
    claim.review_note = body.note
    claim.checklist = None
    claim.commercial_review = None
    claim.reviewer_id = principal.account.id
    store.audit(claim, action=body.action, actor=principal.account.id, now=dt.datetime.now(dt.UTC))
    await session.commit()
    return await store.view(session, claim)
