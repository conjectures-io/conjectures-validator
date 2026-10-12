"""Bonus eligibility and private storage. No path here writes to a payout or reward table."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import defer
from sqlalchemy.exc import IntegrityError

from conjectures_subnet.db.models import Submission
from conjectures_subnet.db.submissions import latest_binding_decision
from conjectures_subnet.db.trajectory_models import TrajectoryClaim, TrajectoryFingerprint
from submission_api.errors import Conflict, NotFound
from submission_api.trajectory_schema import (
    MAX_PACKAGE_BYTES, RETENTION_DAYS, TERMS_VERSION,
)
from submission_api.trajectory_terms import COMMERCIAL_TERMS_SHA256

CLAIM_WINDOW_DAYS = 365
AUDIT_RETENTION_DAYS = 7 * 365


def eligibility_reason(submission, decision, *, cutoff, now, enforce_window=True):
    if submission.created_at < cutoff:
        return "Proof predates the configured offer start."
    if enforce_window and now > submission.created_at + dt.timedelta(days=CLAIM_WINDOW_DAYS):
        return "The one-year claim window has closed."
    if str(submission.verification_status) != "VERIFIED":
        return "Proof has not passed verification."
    if str(submission.manual_review_status) != "APPROVED" or str(submission.reward_status) not in (
        "ELIGIBLE", "REWARDED", "FAILED"
    ):
        return "Proof does not currently qualify for its full bounty."
    if (decision is None or str(decision.kind) != "HUMAN" or str(decision.decision) != "APPROVED"
            or decision.reason_code != "REVIEW_APPROVED"):
        return "A binding human approval for the full bounty is required; defect awards are excluded."
    return None


async def owned_submission(session, submission_id, account_id, *, lock=False):
    query = select(Submission).where(Submission.id == submission_id, Submission.account_id == account_id)
    if lock:
        query = query.with_for_update()
    submission = await session.scalar(query)
    if submission is None:
        raise NotFound("Submission not found")
    return submission


async def claim_for(session, submission_id):
    return await session.scalar(select(TrajectoryClaim).options(defer(TrajectoryClaim.package), defer(TrajectoryClaim.commercial_election)).where(TrajectoryClaim.submission_id == submission_id))


def audit(claim, *, action, actor, now):
    claim.history = [*claim.history, {
        "action": action, "actor": str(actor), "at": now.isoformat(),
        "revision": claim.revision, "package_sha256": "sha256:" + claim.package_digest.hex(),
        "proof_review_decision_id": claim.proof_review_decision_id,
    }]
    claim.updated_at = now


async def accept_package(session, *, submission, decision, prepared, account_id, now):
    package, raw, package_hash, fingerprint, consent = prepared
    if package.commercial_election.terms_sha256 != COMMERCIAL_TERMS_SHA256:
        raise Conflict("Commercial election references different terms; review the current draft.",
                       reason_code="TRAJECTORY_TERMS_CHANGED")
    if package.synthetic:
        raise Conflict("Synthetic examples are never bonus submissions.", reason_code="TRAJECTORY_SYNTHETIC")
    if (package.submission_id != submission.id or package.task_id != submission.task_id
            or package.final_proof_sha256 != "sha256:" + bytes(submission.proof_digest).hex()
            or package.task_bundle_sha256 != "sha256:" + bytes(submission.task_bundle_sha256).hex()):
        raise Conflict("Package linkage does not match this submission.", reason_code="TRAJECTORY_LINK_MISMATCH")
    if package.exported_at > now + dt.timedelta(minutes=5):
        raise Conflict("Export time is in the future.", reason_code="TRAJECTORY_TIME_INVALID")
    if len(raw) > MAX_PACKAGE_BYTES:
        raise Conflict("Canonical package exceeds the size limit.", reason_code="TRAJECTORY_SIZE")
    existing = await claim_for(session, submission.id)
    if existing and existing.package_digest == package_hash:
        return existing  # Exact retry returns the durable record without changing any state.
    if existing and (existing.status not in ("NEEDS_CHANGES", "WITHDRAWN") or existing.revision >= 3):
        raise Conflict("A new package requires a revision request or withdrawal, with at most three versions.",
                       reason_code="TRAJECTORY_ALREADY_SUBMITTED")
    seen = await session.get(TrajectoryFingerprint, fingerprint)
    if seen and (existing is None or seen.claim_id != existing.id):
        raise Conflict("Evidence has already been submitted.", reason_code="TRAJECTORY_DUPLICATE")
    if existing:
        claim = existing
        claim.package = raw
        claim.package_digest = package_hash
        claim.revision += 1
        claim.status = "UNDER_REVIEW"
        claim.proof_review_decision_id = decision.id
        claim.reviewer_id = None
        claim.review_note = None
        claim.checklist = None
        claim.commercial_review = None
        # Revisions do not extend the original retention deadline.
        if claim.expires_at <= now:
            raise Conflict("Review storage has expired.", reason_code="TRAJECTORY_EXPIRED")
    else:
        claim = TrajectoryClaim(
            id=uuid.uuid4(), submission_id=submission.id, account_id=account_id,
            proof_digest=submission.proof_digest, reward_target_id=submission.reward_target_id,
            status="UNDER_REVIEW", amount_usd_cents=50000, terms_version=TERMS_VERSION,
            package=raw, package_digest=package_hash, revision=1,
            proof_review_decision_id=decision.id, history=[], created_at=now, updated_at=now,
            commercial_election=package.commercial_election.model_dump(mode="json"),
            expires_at=now + dt.timedelta(days=RETENTION_DAYS),
            audit_expires_at=now + dt.timedelta(days=AUDIT_RETENTION_DAYS),
        )
        session.add(claim)
    claim.commercial_election = package.commercial_election.model_dump(mode="json")
    claim.consent = {**consent, "account_id": str(account_id), "accepted_at": now.isoformat()}
    claim.proof_policy_snapshot = {"review_policy_version": submission.review_policy_version,
                                   "bounty_policy_version": submission.bounty_policy_version}
    claim.commercial_withdrawn_at = None
    audit(claim, action="UPLOADED", actor=account_id, now=now)
    try:
        await session.flush()
        if not seen:
            session.add(TrajectoryFingerprint(digest=fingerprint, claim_id=claim.id))
        await session.flush()
    except IntegrityError:
        await session.rollback()
        # Do not expose or log SQL parameters: they include the private export.
        raise Conflict("A claim or evidence fingerprint already exists.", reason_code="TRAJECTORY_DUPLICATE") from None
    return claim


def commercial_clearance_reason(election):
    if election.get("provenance_expired"):
        return "Source rights provenance expired; commercial clearance is unavailable."
    if election["election"] != "conditional_grant":
        return "Contributor chose review only."
    purposes = set(election["purposes"])
    if not election["sources"]:
        return "Source rights have not been disclosed."
    for source in election["sources"]:
        if (source["rights_status"] != "confirmed"
                or source["agreement_or_license"]["value"] is None
                or source["evidence_reference"]["value"] is None
                or not purposes <= set(source["allowed_purposes"])):
            return "An unknown, prohibited or insufficient source right prevents clearance."
    return None


async def effective_status(session, claim):
    if claim.status in ("UNDER_REVIEW", "NEEDS_CHANGES") and claim.expires_at <= dt.datetime.now(dt.UTC):
        return "EXPIRED"
    if claim.status != "APPROVED_MANUAL_PAYMENT":
        return claim.status
    submission = await session.get(Submission, claim.submission_id)
    decision = await latest_binding_decision(session, claim.submission_id)
    # Any correction invalidates the exact decision this bonus approval relied on. A future
    # payout adapter must repeat this under locks; the current feature cannot issue funds.
    reason = eligibility_reason(submission, decision, cutoff=submission.created_at,
                                now=dt.datetime.now(dt.UTC), enforce_window=False)
    if (reason or decision.id != claim.proof_review_decision_id
            or claim.proof_policy_snapshot != {"review_policy_version": submission.review_policy_version,
                                               "bounty_policy_version": submission.bounty_policy_version}):
        return "BLOCKED_PROOF_CHANGED"
    return claim.status


async def view(session, claim):
    return {
        "id": str(claim.id), "submission_id": str(claim.submission_id),
        "status": await effective_status(session, claim), "amount_usd_cents": claim.amount_usd_cents,
        "payment_status": "NOT_ISSUED", "revision": claim.revision,
        "package_sha256": "sha256:" + claim.package_digest.hex(), "terms_version": claim.terms_version,
        "review_note": claim.review_note, "created_at": claim.created_at,
        "expires_at": claim.expires_at, "audit_expires_at": claim.audit_expires_at,
        "content_available": await content_available(session, claim),
        "commercial_election": await session.scalar(select(commercial_projection()).where(TrajectoryClaim.id == claim.id)),
        "commercial_review": ({k: v for k, v in claim.commercial_review.items() if k != "reviewer_id"} if claim.commercial_review else None),
        "commercial_withdrawn_at": claim.commercial_withdrawn_at,
        "license_status": "INACTIVE_NO_PAYMENT_OR_ACTIVATION",
    }


def commercial_summary(election):
    # Rights provenance can fill most of a package. Only the private attachment carries it;
    # status/queue responses must stay small and must not echo contributor-supplied source text.
    return {k: election[k] for k in (
        "election", "purposes", "terms_version", "terms_sha256", "explicit_assent"
    )}


def commercial_projection():
    column = TrajectoryClaim.commercial_election
    return func.jsonb_build_object(*[part for k in (
        "election", "purposes", "terms_version", "terms_sha256", "explicit_assent"
    ) for part in (k, column[k])])


async def content_available(session, claim):
    return claim.expires_at > dt.datetime.now(dt.UTC) and bool(await session.scalar(
        select(TrajectoryClaim.package.is_not(None)).where(TrajectoryClaim.id == claim.id)))


async def minimize_election(session, claim):
    election = await session.scalar(select(TrajectoryClaim.commercial_election).where(TrajectoryClaim.id == claim.id))
    if election is None or election.get("provenance_expired"):
        return
    minimized = commercial_summary(election)
    minimized["provenance_expired"] = True
    minimized["sources"] = [{"source_id": source["source_id"], "rights_status": source["rights_status"],
        "record_sha256": "sha256:" + hashlib.sha256(json.dumps(source, sort_keys=True,
            separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()} for source in election["sources"]]
    claim.commercial_election = minimized
