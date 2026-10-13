"""Session-owned chat-log intake and reviewer-only access to private, untrusted exports."""
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

from conjectures_subnet.db.models import REVIEWER_ROLE, RewardState, Submission
from conjectures_subnet.db.submissions import latest_binding_decision
from conjectures_subnet.db.trajectory_models import TrajectoryAccess, TrajectoryClaim
from submission_api import trajectories as store
from submission_api.dependencies import (
    CookieWriterDep, PrincipalDep, ServicesDep, SessionDep, require_role, require_role_writer,
)
from submission_api.errors import ApiError, BadRequest, Conflict, NotFound, PayloadTooLarge
from submission_api.sessions import Principal
from submission_api.taskpool import TaskNotAllowed
from submission_api.trajectory_schema import (
    DEPOSIT_HOURS, MAX_PACKAGE_BYTES, RETENTION_DAYS, TERMS_VERSION, StrictModel,
)
from submission_api.trajectory_terms import ASSENT_STATEMENTS_SHA256, TERMS_SHA256
from verifier.task_registry import TaskNotAllowed as RegistryTaskNotAllowed
from submission_api.trajectory_intake import (
    MAX_UPLOAD_BYTES, PACKAGE_MEDIA_TYPES, Consent, UploadBudget, check_consent, package_errors, prepare_upload,
)
from submission_api.trajectory_schema import PackageError

router = APIRouter(prefix="/v1", tags=["trajectories"])
Reviewer = Annotated[Principal, Depends(require_role(REVIEWER_ROLE))]
ReviewerWriter = Annotated[Principal, Depends(require_role_writer(REVIEWER_ROLE))]


class PrivateHeadersMiddleware:
    """Cover error responses too; setting headers inside a handler misses rejected requests."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        sensitive = path.startswith(("/v1/admin/trajectories", "/v1/admin/trajectory-", "/v1/me/chat-log-deliveries", "/v1/me/chat-log-deposits")) or (
            path.startswith("/v1/me/submissions/") and "/trajectory" in path) or (
            path.startswith(("/v1/me/submissions/", "/v1/admin/reviews/", "/v1/results/")) and "/pdfs" in path)
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


NOT_OPEN = "Chat-log intake is not open."


async def opened(services, session):
    """Intake needs both the deployment flag and the permanent opening record."""
    cutoff = await store.opened_at(session) if services.settings.trajectory_bonus_enabled else None
    if cutoff is None:
        raise ApiError(NOT_OPEN, status_code=503, reason_code="TRAJECTORY_DISABLED")
    return cutoff


async def bounded_json(request, limit, media_types=("application/json",)):
    if request.headers.get("content-type", "").split(";")[0].strip().lower() not in media_types:
        raise ApiError("Send a UTF-8 application/json file." if media_types == ("application/json",)
                       else "Send the chat-log package as a .zip file.", status_code=415, reason_code="TRAJECTORY_FORMAT")
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
async def offer(services: ServicesDep, session: SessionDep, response: Response):
    private(response)
    start_at = await store.opened_at(session)
    return {
        "enabled": services.settings.trajectory_bonus_enabled and start_at is not None,
        "legacy_bonus_usd_cents": {"erdos": 50000, "other": 25000},
        "new_bounty_share": {"numerator": services.settings.bounty_constant_numerator,
                             "denominator": services.settings.bounty_constant_denominator,
                             "maximum_numerator": services.settings.bounty_max_share_numerator,
                             "maximum_denominator": services.settings.bounty_max_share_denominator,
                             "ramp_seconds": services.settings.bounty_ramp_seconds,
                             "basis": "available_treasury"},
        "new_bounties_require_chat_logs": True, "logs_due": "BEFORE_PROOF_SUBMISSION",
        "deposit_hours": DEPOSIT_HOURS, "license": "REQUIRED_SALE_LICENSE_ON_ACCEPTANCE",
        "start_at": start_at,
        "claim_window_days": store.CLAIM_WINDOW_DAYS, "max_package_bytes": MAX_PACKAGE_BYTES,
        "retention_days": RETENTION_DAYS, "audit_retention_days": store.AUDIT_RETENTION_DAYS,
        "terms_version": TERMS_VERSION, "terms_sha256": TERMS_SHA256,
        "assent_statements_sha256": ASSENT_STATEMENTS_SHA256,
    }


def required(submission):
    return submission.bounty_policy_version == store.TREASURY_CHAT_POLICY


FIXED_AT_SUBMISSION = "Logs for this bounty were due with the proof and cannot be added or replaced."


@router.get("/me/submissions/{submission_id}/trajectory")
async def status(submission_id: uuid.UUID, principal: PrincipalDep, services: ServicesDep,
                 session: SessionDep, response: Response):
    private(response)
    submission = await store.owned_submission(session, submission_id, principal.account.id)
    claim = await store.claim_for(session, submission_id)
    decision = await latest_binding_decision(session, submission_id)
    start_at = await store.opened_at(session) if services.settings.trajectory_bonus_enabled else None
    if required(submission):
        reason = FIXED_AT_SUBMISSION
    elif start_at is not None:
        reason = store.eligibility_reason(submission, decision, cutoff=claim.offer_start_at if claim else start_at,
                                          now=dt.datetime.now(dt.UTC), enforce_window=claim is None)
    else:
        reason = NOT_OPEN
    amount = claim.amount_usd_cents if claim else (0 if required(submission) else None)
    if reason is None and claim is None:
        try:
            amount = await anyio.to_thread.run_sync(store.amount_for, services, submission)
        except Conflict as error:
            reason = str(error)
    return {"eligible": reason is None, "eligibility_reason": reason,
            "amount_usd_cents": amount, "chat_logs_required": required(submission),
            "claim": await store.view(session, claim) if claim else None}


TermsVersion = Annotated[str, Query(max_length=64)]
TermsDigest = Annotated[str, Query(max_length=71)]


def consent_from(terms_version, terms_sha256, assent_statements_sha256, accepted) -> dict:
    """Consent rides in the query and is checked before any byte of the package is read."""
    return check_consent(Consent(terms_version=terms_version, terms_sha256=terms_sha256,
                                 assent_statements_sha256=assent_statements_sha256, accepted=accepted))


async def received_package(request, consent, expected_state_version=None):
    """Bounded receipt of the zip and its checks, inside the caller's upload budget, with no open transaction."""
    try:
        with anyio.fail_after(120):
            raw = await bounded_json(request, MAX_UPLOAD_BYTES, PACKAGE_MEDIA_TYPES)
        return await anyio.to_thread.run_sync(prepare_upload, raw, consent, expected_state_version)
    except TimeoutError:
        raise ApiError("Upload timed out; retry with a complete file.", status_code=408,
                       reason_code="TRAJECTORY_TIMEOUT") from None
    except PackageError as error:
        raise BadRequest("Invalid chat-log package; check the indicated files and lines against the instructions.",
                         reason_code="TRAJECTORY_INVALID", extra={"validation_errors": package_errors(error)}) from None


@router.post("/me/chat-log-deposits")
async def deposit(request: Request, principal: CookieWriterDep, services: ServicesDep,
                  session: SessionDep, response: Response, terms_version: TermsVersion,
                  terms_sha256: TermsDigest, assent_statements_sha256: TermsDigest, accepted: bool = False):
    """Logs for a new bounty, uploaded before the proof. The proof must follow within 24 hours.

    The request body is the .zip package; consent rides in the query.
    """
    private(response)
    await opened(services, session)
    consent = consent_from(terms_version, terms_sha256, assent_statements_sha256, accepted)
    account_id = principal.account.id
    await session.rollback()
    if not hasattr(request.app.state, "trajectory_upload_budget"):
        request.app.state.trajectory_upload_budget = UploadBudget()
    async with request.app.state.trajectory_upload_budget.hold(account_id):
        prepared = await received_package(request, consent)
        package = prepared[0].manifest
        try:
            entry = services.catalog.resolve(package.task_id, package.task_bundle_sha256)
        except (TaskNotAllowed, RegistryTaskNotAllowed, ValueError):
            raise Conflict("The package names a task that is not open for submissions.",
                           reason_code="TRAJECTORY_LINK_MISMATCH") from None
        quote = await services.pricing.quote(session, reward_target_id=entry.reward_target_id)
        if quote.policy_version != store.TREASURY_CHAT_POLICY:
            raise Conflict("This bounty does not take logs with the proof; earlier proofs use the bonus upload.",
                           reason_code="TRAJECTORY_INELIGIBLE")
        if not quote.available:
            raise Conflict("This bounty is not accepting submissions.", reason_code="TRAJECTORY_INELIGIBLE",
                           extra={"bounty_reason": quote.reason})
        claim = await store.deposit_package(session, entry=entry, prepared=prepared, account_id=account_id,
                                            now=dt.datetime.now(dt.UTC),
                                            review_policy_version=services.settings.review_policy_version)
        await session.commit()
        return await store.view(session, claim)


@router.post("/me/submissions/{submission_id}/trajectory")
async def upload(submission_id: uuid.UUID, request: Request, principal: CookieWriterDep,
                 services: ServicesDep, session: SessionDep, response: Response, terms_version: TermsVersion,
                 terms_sha256: TermsDigest, assent_statements_sha256: TermsDigest, accepted: bool = False,
                 expected_state_version: Annotated[int | None, Query(ge=1, le=2147483647)] = None):
    """Bonus logs for a proof submitted before the program began. The body is the .zip package."""
    private(response)
    start_at = await opened(services, session)
    consent = consent_from(terms_version, terms_sha256, assent_statements_sha256, accepted)
    account_id = principal.account.id  # rollback expires ORM objects; retain only this immutable ID.
    submission = await store.owned_submission(session, submission_id, account_id)
    if required(submission):
        raise Conflict(FIXED_AT_SUBMISSION, reason_code="CHAT_LOGS_FIXED_AT_SUBMISSION")
    decision = await latest_binding_decision(session, submission_id)
    existing = await store.claim_for(session, submission_id)
    reason = store.eligibility_reason(submission, decision, cutoff=existing.offer_start_at if existing else start_at,
                                      now=dt.datetime.now(dt.UTC), enforce_window=existing is None)
    if reason:
        raise Conflict(reason, reason_code="TRAJECTORY_INELIGIBLE")
    # Release the auth/precheck transaction and its connection before accepting a slow body.
    await session.rollback()
    if not hasattr(request.app.state, "trajectory_upload_budget"):
        request.app.state.trajectory_upload_budget = UploadBudget()
    async with request.app.state.trajectory_upload_budget.hold(account_id):
        prepared = await received_package(request, consent, expected_state_version)
        # Recheck under the proof correction lock after all untrusted parsing and hashing.
        submission = await store.owned_submission(session, submission_id, account_id, lock=True)
        decision = await latest_binding_decision(session, submission_id)
        now = dt.datetime.now(dt.UTC)
        existing = await store.claim_for(session, submission_id)
        offer_start_at = existing.offer_start_at if existing else start_at
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
    await store.owned_submission(session, submission_id, principal.account.id)
    await store.lock_payouts(session, submission_id)
    submission = await store.owned_submission(session, submission_id, principal.account.id, lock=True)
    claim = await store.claim_for(session, submission_id)
    if claim is None:
        raise NotFound("Chat logs not found")
    check_version(claim, expected)
    if claim.status not in ("UNDER_REVIEW", "NEEDS_CHANGES", "WITHDRAWN"):
        raise Conflict("Only logs awaiting review can be withdrawn; contact support about a decided claim.")
    if claim.status != "WITHDRAWN":
        now = dt.datetime.now(dt.UTC)
        forfeited = None
        if claim.amount_usd_cents == 0:
            # For a new bounty this is final: the bounty is given up and the target reopens.
            if store.verification_pending(submission):
                raise Conflict("Wait until the proof's automatic verification finishes, then withdraw.",
                               reason_code="TRAJECTORY_VERIFICATION_PENDING")
            forfeited = await store.forfeit_bounty(session, submission, reviewer=f"chat-log-withdrawal:{principal.account.id}",
                                                   notes="Contributor withdrew the chat logs this bounty requires.")
        claim.status = "WITHDRAWN"
        claim.package = None
        store.audit(claim, action="WITHDRAWN", actor=principal.account.id, now=now,
                    extra={"bounty_forfeited": True, "forfeit_decision_id": forfeited.id if forfeited else None}
                    if claim.amount_usd_cents == 0 else None)
        await session.commit()
    return await store.view(session, claim)


@router.get("/admin/trajectories")
async def queue(principal: Reviewer, session: SessionDep, response: Response,
                offset: Annotated[int, Query(ge=0, le=100000)] = 0):
    private(response)
    rows = (await session.scalars(select(TrajectoryClaim).options(defer(TrajectoryClaim.package))
        .where(TrajectoryClaim.submission_id.is_not(None))  # Unsubmitted deposits are not reviewable.
        .order_by(TrajectoryClaim.created_at.desc(), TrajectoryClaim.id)
        .offset(offset).limit(51))).all()
    return {"items": [await metadata_view(session, r) for r in rows[:50]],
            "next_offset": offset + 50 if len(rows) > 50 else None}


async def metadata_view(session, claim):
    # Do not lazy-load private bytea data to build a queue or status response.
    return {"id": str(claim.id), "submission_id": str(claim.submission_id),
            "status": await store.effective_status(session, claim), "revision": claim.revision, "state_version": claim.state_version,
            "amount_usd_cents": claim.amount_usd_cents,
            "chat_logs_required": claim.amount_usd_cents == 0,
            "review_note": claim.review_note, "created_at": claim.created_at, "expires_at": claim.expires_at,
            "license_status": store.license_status(claim),
            "package_sha256": "sha256:" + claim.package_digest.hex()}


@router.get("/admin/trajectories/{claim_id}/package")
async def download(claim_id: uuid.UUID, principal: Reviewer, session: SessionDep):
    claim = await session.get(TrajectoryClaim, claim_id)
    if claim is None or claim.submission_id is None or not await store.content_available(session, claim):
        raise NotFound("Chat-log content is unavailable or expired")
    content = claim.package
    session.add(TrajectoryAccess(id=uuid.uuid4(), claim_id=claim.id, actor_id=principal.account.id,
                                 accessed_at=dt.datetime.now(dt.UTC)))
    await session.commit()  # Do not release content unless the access record is durable.
    return Response(content, media_type="application/zip", headers={
        "Cache-Control": "no-store", "Vary": "Authorization, Cookie",
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": f'attachment; filename="chat-logs-{claim.id}.zip"',
        "Content-Security-Policy": "default-src 'none'; sandbox",
    })


class Checklist(StrictModel):
    # The acceptance standard: the original chat that produced this proof, not a recreation.
    original_chat_that_produced_the_proof: Literal[True]
    contributor_owns_proof_and_logs_safe_to_sell: Literal[True]


ReviewNote = Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=2000)]


class Decision(StateVersion):
    decision: Literal["APPROVED_MANUAL_PAYMENT", "NEEDS_CHANGES", "REJECTED"]
    expected_revision: Annotated[int, Field(strict=True, ge=1, le=3)]
    note: ReviewNote
    checklist: Checklist | None


async def locked_claim(session, claim_id):
    claim = await session.scalar(select(TrajectoryClaim).options(defer(TrajectoryClaim.package))
                                 .where(TrajectoryClaim.id == claim_id))
    if claim is None or claim.submission_id is None:
        raise NotFound("Chat logs not found")
    # Same lock order as intake and the proof correction route: submission, then claim.
    submission = await session.scalar(select(Submission).where(Submission.id == claim.submission_id)
                                      .execution_options(populate_existing=True).with_for_update())
    claim = await session.scalar(select(TrajectoryClaim).options(defer(TrajectoryClaim.package))
        .where(TrajectoryClaim.id == claim_id).execution_options(populate_existing=True).with_for_update())
    return submission, claim


@router.post("/admin/trajectories/{claim_id}/decision")
async def decide(claim_id: uuid.UUID, request: Request, principal: ReviewerWriter,
                 services: ServicesDep, session: SessionDep, response: Response):
    private(response)
    raw = await bounded_json(request, 16384)
    try:
        body = Decision.model_validate_json(raw)
    except ValidationError:
        raise BadRequest("Invalid review decision; use the required checklist and a concise owner-visible note.") from None
    first = await session.scalar(select(TrajectoryClaim.submission_id).where(TrajectoryClaim.id == claim_id))
    if first is not None and body.decision == "REJECTED":
        await store.lock_payouts(session, first)  # A forfeiture below may correct the proof decision.
    submission, claim = await locked_claim(session, claim_id)
    check_version(claim, body.expected_state_version)
    if principal.account.id == claim.account_id:
        raise Conflict("A contributor cannot review their own chat logs.")
    reviewable = claim.status == "UNDER_REVIEW" or (body.decision == "REJECTED" and claim.status in ("EXPIRED", "NEEDS_CHANGES"))
    if not reviewable or claim.revision != body.expected_revision:
        raise Conflict("This review is stale or already decided.", reason_code="TRAJECTORY_STALE_REVIEW")
    if body.decision == "NEEDS_CHANGES" and claim.amount_usd_cents == 0:
        raise Conflict(FIXED_AT_SUBMISSION + " Approve or reject them.", reason_code="CHAT_LOGS_FIXED_AT_SUBMISSION")
    if body.decision == "REJECTED" and claim.amount_usd_cents == 0 and store.verification_pending(submission):
        raise Conflict("Decide these logs once the proof's automatic verification finishes.",
                       reason_code="TRAJECTORY_VERIFICATION_PENDING")
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
            raise BadRequest("Approval requires every acceptance check.")
        claim.proof_review_decision_id = proof_decision.id
        claim.proof_policy_snapshot = {"review_policy_version": submission.review_policy_version,
                                       "bounty_policy_version": submission.bounty_policy_version}
    extra = None
    if body.decision == "REJECTED" and claim.amount_usd_cents == 0:
        forfeited = await store.forfeit_bounty(session, submission, reviewer=f"chat-log-review:{principal.account.id}",
                                               notes=body.note.strip())
        extra = {"bounty_forfeited": True, "forfeit_decision_id": forfeited.id if forfeited else None}
    claim.status = ("APPROVED_BOUNTY_EVIDENCE" if body.decision == "APPROVED_MANUAL_PAYMENT"
                    and claim.amount_usd_cents == 0 else body.decision)
    claim.reviewer_id = principal.account.id
    claim.review_note = body.note.strip()
    claim.checklist = body.checklist.model_dump() if body.checklist else None
    store.audit(claim, action=claim.status, actor=principal.account.id, now=now, extra=extra)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise Conflict("This reward target already has accepted chat logs.", reason_code="TRAJECTORY_DUPLICATE") from None
    return await store.view(session, claim)


class Reopen(StateVersion):
    expected_revision: Annotated[int, Field(strict=True, ge=1, le=3)]
    action: Literal["REVOKE_PROOF_CHANGED", "REOPEN_REVIEW", "REBIND_PROOF_DECISION"]
    note: ReviewNote


async def target_reassigned(session, claim):
    """A rejected new-bounty log reopened the target; a competing proof that has since qualified keeps it."""
    competitor = (select(Submission.id).where(
        Submission.reward_target_id == claim.reward_target_id, Submission.id != claim.submission_id,
        Submission.reward_status != RewardState.INELIGIBLE,
        ~select(TrajectoryClaim.id).where(TrajectoryClaim.submission_id == Submission.id,
                                          TrajectoryClaim.status.in_(store.FORFEITED)).exists()).limit(1))
    return await session.scalar(competitor) is not None


@router.post("/admin/trajectories/{claim_id}/reopen")
async def reopen(claim_id: uuid.UUID, request: Request, principal: ReviewerWriter,
                 session: SessionDep, response: Response):
    private(response)
    try:
        body = Reopen.model_validate_json(await bounded_json(request, 16384))
    except ValidationError:
        raise BadRequest("Invalid reopen request; include a concise owner-visible explanation.") from None
    claim = await session.scalar(select(TrajectoryClaim).options(defer(TrajectoryClaim.package))
                                 .where(TrajectoryClaim.id == claim_id))
    if claim is None or claim.submission_id is None:
        raise NotFound("Chat logs not found")
    if body.action == "REVOKE_PROOF_CHANGED" and claim.amount_usd_cents == 0:
        # Match the event -> submission lock order used by cancellation/reconciliation.
        from conjectures_subnet.db.submissions import _reward_event_exists
        if await _reward_event_exists(session, claim.submission_id):
            raise Conflict("Cancel the unsigned payout instruction before revoking required evidence; transfers already in flight cannot be revoked.",
                           reason_code="TRAJECTORY_PAYOUT_IN_FLIGHT")
    submission, claim = await locked_claim(session, claim_id)
    check_version(claim, body.expected_state_version)
    if principal.account.id == claim.account_id or claim.revision != body.expected_revision:
        raise Conflict("Self review or stale revision is not allowed.")
    now = dt.datetime.now(dt.UTC)
    if body.action == "REBIND_PROOF_DECISION":
        current = await latest_binding_decision(session, submission.id)
        snapshot = {"review_policy_version": submission.review_policy_version,
                    "bounty_policy_version": submission.bounty_policy_version}
        reason = store.eligibility_reason(submission, current, cutoff=claim.offer_start_at,
                                          now=now, enforce_window=False)
        if (claim.status not in store.APPROVED or reason or
                snapshot != claim.proof_policy_snapshot or current.id == claim.proof_review_decision_id or
                not await store.unchanged_approval_chain(session, submission.id, current, claim.proof_review_decision_id)):
            raise Conflict("Rebinding requires an unchanged policy and a current full-bounty human approval.",
                           reason_code="TRAJECTORY_REBIND_INELIGIBLE")
        previous = claim.proof_review_decision_id
        claim.proof_review_decision_id = current.id
        store.audit(claim, action=body.action, actor=principal.account.id, now=now,
                    extra={"previous_proof_review_decision_id": previous, "note": body.note})
        await session.commit()
        return await store.view(session, claim)
    if body.action == "REVOKE_PROOF_CHANGED":
        if await store.effective_status(session, claim) != "BLOCKED_PROOF_CHANGED":
            raise Conflict("Only accepted logs blocked by a changed proof decision can be revoked here.")
        claim.status = "REVOKED_PROOF_CHANGED"  # Releases the target reservation and the sale license.
        claim.expires_at = store.deletion_after_revocation(claim, now)
    else:
        if claim.status not in ("REVOKED_PROOF_CHANGED", "REJECTED"):
            raise Conflict("Only a rejected or revoked claim can be reopened.")
        if not await store.content_available(session, claim):
            raise Conflict("Expired content cannot be reopened; the retention deadline is final.")
        if claim.status == "REJECTED" and claim.amount_usd_cents == 0 and await target_reassigned(session, claim):
            raise Conflict("Another proof for this target has qualified since these logs were rejected; the rejection is final.",
                           reason_code="TRAJECTORY_TARGET_REASSIGNED")
        claim.status = "UNDER_REVIEW"
    claim.review_note = body.note
    claim.checklist = None
    claim.reviewer_id = principal.account.id
    store.audit(claim, action=body.action, actor=principal.account.id, now=now)
    await session.commit()
    return await store.view(session, claim)
