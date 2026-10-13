"""Chat-log eligibility and private storage. No path here writes to a payout or reward table.

New bounties: logs are deposited before the proof and bound to it in the submission transaction.
Earlier proofs: logs are uploaded afterwards, but the chat must show the proof before submission.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import defer
from sqlalchemy.exc import IntegrityError

from conjectures_subnet.db.models import (
    ReviewDecision, ReviewOutcome, RewardEvent, Submission, VerificationState,
)
from conjectures_subnet.bounty_policy import TREASURY_CHAT_POLICY, legacy_chat_bonus_cents
from verifier.task_store import TaskVersionStore
from verifier.task_versions import is_v2_task_id
from verifier.errors import VerifierError
from conjectures_subnet.db.submissions import (
    correct_human_decision, latest_binding_decision, record_human_decision,
)
from conjectures_subnet.db.trajectory_models import ChatLogProgram, TrajectoryClaim, TrajectoryFingerprint
from submission_api.errors import Conflict, NotFound
from submission_api.trajectory_schema import (
    DEPOSIT_HOURS, MAX_PACKAGE_BYTES, RETENTION_DAYS, TERMS_VERSION, proof_first_seen,
)

CLAIM_WINDOW_DAYS = 365
AUDIT_RETENTION_DAYS = 7 * 365
APPROVED = ("APPROVED_MANUAL_PAYMENT", "APPROVED_BOUNTY_EVIDENCE")
# Final required-log outcomes that give up the bounty and reopen the target to other solvers.
FORFEITED = ("REJECTED", "WITHDRAWN")
CHAT_LOG_REQUIRED = "CHAT_LOG_REQUIRED"
# Written by this module, never offered to reviewers, like PROBLEM_ALREADY_AWARDED.
FORFEIT_CODE = "CHAT_LOGS_NOT_ACCEPTED"
FORFEIT_PUBLIC = ("This bounty required chat logs submitted with the proof and accepted in review. "
                  "They were not accepted, so this proof does not receive the bounty.")


def eligibility_reason(submission, decision, *, cutoff, now, enforce_window=True):
    required = getattr(submission, "bounty_policy_version", None) == TREASURY_CHAT_POLICY
    if enforce_window and not required:
        if submission.created_at >= cutoff:
            return "The retrospective bonus covers proofs submitted before the program cutoff."
        if now > cutoff + dt.timedelta(days=CLAIM_WINDOW_DAYS):
            return "The one-year retrospective claim window has closed."
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


async def unchanged_approval_chain(session, submission_id, current, original_id):
    """Only explanation corrections may reuse a completed private evidence review."""
    decision = current
    seen = set()
    while decision is not None and decision.id not in seen:
        seen.add(decision.id)
        if (decision.submission_id != submission_id or str(decision.kind) != "HUMAN"
                or str(decision.decision) != "APPROVED" or decision.reason_code != "REVIEW_APPROVED"):
            return False
        if decision.id == original_id:
            return True
        decision = await session.get(ReviewDecision, decision.supersedes_id) if decision.supersedes_id else None
    return False


async def opened_at(session):
    """When intake opened, or None. Stamped once by scripts/open_chat_log_intake.py; never moves."""
    return await session.scalar(select(ChatLogProgram.opened_at).where(ChatLogProgram.id == 1))


async def owned_submission(session, submission_id, account_id, *, lock=False):
    query = select(Submission).where(Submission.id == submission_id, Submission.account_id == account_id)
    if lock:
        # Refresh any earlier unlocked read: verification and review may have moved since.
        query = query.with_for_update().execution_options(populate_existing=True)
    submission = await session.scalar(query)
    if submission is None:
        raise NotFound("Submission not found")
    return submission


async def claim_for(session, submission_id):
    return await session.scalar(select(TrajectoryClaim).options(defer(TrajectoryClaim.package)).where(TrajectoryClaim.submission_id == submission_id))


def record_digest(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                                ensure_ascii=False).encode()).hexdigest()


def audit(claim, *, action, actor, now, extra=None):
    claim.state_version += 1
    provenance = {}
    if action in ("UPLOADED", "DEPOSITED", "SUBMITTED_WITH_PROOF", *APPROVED):
        provenance = {"consent_sha256": record_digest(claim.consent),
                      "proof_policy_sha256": record_digest(claim.proof_policy_snapshot)}
    claim.history = [*claim.history, {
        "action": action, "actor": str(actor), "at": now.isoformat(),
        "revision": claim.revision, "state_version": claim.state_version,
        **provenance, **(extra or {}), "package_sha256": "sha256:" + claim.package_digest.hex(),
        "proof_review_decision_id": claim.proof_review_decision_id,
    }]
    claim.updated_at = now


def check_package(package, raw, *, task_id, task_bundle_sha256, proof_digest, submission_id, now):
    if package.synthetic:
        raise Conflict("Synthetic examples are never accepted as chat logs.", reason_code="TRAJECTORY_SYNTHETIC")
    if (package.submission_id != submission_id or package.task_id != task_id
            or package.final_proof_sha256 != "sha256:" + bytes(proof_digest).hex()
            or package.task_bundle_sha256 != "sha256:" + bytes(task_bundle_sha256).hex()):
        raise Conflict("Package linkage does not match this proof." + (
            " Leave submission_id empty: logs for a new bounty are uploaded before the proof." if submission_id is None else ""),
            reason_code="TRAJECTORY_LINK_MISMATCH")
    if package.exported_at > now + dt.timedelta(minutes=5):
        raise Conflict("Export time is in the future.", reason_code="TRAJECTORY_TIME_INVALID")
    if len(raw) > MAX_PACKAGE_BYTES:
        raise Conflict("Canonical package exceeds the size limit.", reason_code="TRAJECTORY_SIZE")


async def store_fingerprint(session, claim, fingerprint, seen):
    try:
        await session.flush()
        if not seen:
            session.add(TrajectoryFingerprint(digest=fingerprint, claim_id=claim.id))
        await session.flush()
    except IntegrityError:
        await session.rollback()
        # Do not expose or log SQL parameters: they include the private export.
        raise Conflict("A claim or evidence fingerprint already exists.", reason_code="TRAJECTORY_DUPLICATE") from None


async def accept_package(session, *, submission, decision, prepared, account_id, now, amount_usd_cents, offer_start_at):
    """Logs for a proof submitted before the program: uploaded later, but must predate it."""
    package, raw, package_hash, fingerprint, consent, expected_state_version = prepared
    check_package(package, raw, task_id=submission.task_id, task_bundle_sha256=submission.task_bundle_sha256,
                  proof_digest=submission.proof_digest, submission_id=submission.id, now=now)
    if proof_first_seen(package) >= submission.created_at:
        raise Conflict("These need to be the original logs: the chat must show the final proof before it was submitted.",
                       reason_code="TRAJECTORY_AFTER_THE_FACT")
    existing = await claim_for(session, submission.id)
    if existing:
        # The initial request may be retried without a token only while its original state
        # remains untouched. Later revisions must present the current state token.
        if expected_state_version != existing.state_version and not (
                expected_state_version is None and existing.state_version == 1):
            raise Conflict("Claim changed; refresh before resubmitting.", reason_code="TRAJECTORY_STALE_REVIEW")
        if existing.package_digest == package_hash:
            if existing.status == "UNDER_REVIEW" and await content_available(session, existing):
                return existing
            if existing.status == "NEEDS_CHANGES":
                raise Conflict("The package is unchanged; address the requested changes first.",
                               reason_code="TRAJECTORY_UNCHANGED")
            # A withdrawn identical package is a fresh revision with renewed explicit consent.
    elif expected_state_version is not None:
        raise Conflict("Claim changed; refresh before submitting.", reason_code="TRAJECTORY_STALE_REVIEW")
    if existing and (existing.status not in ("NEEDS_CHANGES", "WITHDRAWN") or existing.revision >= 3):
        raise Conflict("A new package requires a revision request or withdrawal, with at most three versions.",
                       reason_code="TRAJECTORY_ALREADY_SUBMITTED")
    seen = await session.get(TrajectoryFingerprint, fingerprint)
    if seen and (existing is None or seen.claim_id != existing.id):
        raise Conflict("Evidence has already been submitted.", reason_code="TRAJECTORY_DUPLICATE")
    previous_terms_version = existing.terms_version if existing else None
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
        # Revisions do not extend the original retention deadline.
        if claim.expires_at <= now:
            raise Conflict("Review storage has expired.", reason_code="TRAJECTORY_EXPIRED")
    else:
        claim = TrajectoryClaim(
            id=uuid.uuid4(), submission_id=submission.id, account_id=account_id,
            proof_digest=submission.proof_digest, reward_target_id=submission.reward_target_id,
            task_bundle_sha256=submission.task_bundle_sha256,
            status="UNDER_REVIEW", amount_usd_cents=amount_usd_cents, terms_version=TERMS_VERSION,
            package=raw, package_digest=package_hash, revision=1, state_version=0, offer_start_at=offer_start_at,
            proof_review_decision_id=decision.id, history=[], created_at=now, updated_at=now,
            expires_at=now + dt.timedelta(days=RETENTION_DAYS),
            audit_expires_at=now + dt.timedelta(days=AUDIT_RETENTION_DAYS),
        )
        session.add(claim)
    claim.terms_version = TERMS_VERSION  # New revision requires fresh assent to these exact terms.
    claim.consent = {**consent, "account_id": str(account_id), "accepted_at": now.isoformat()}
    claim.proof_policy_snapshot = {"review_policy_version": submission.review_policy_version,
                                   "bounty_policy_version": submission.bounty_policy_version}
    audit(claim, action="UPLOADED", actor=account_id, now=now,
          extra={"previous_terms_version": previous_terms_version, "terms_version": TERMS_VERSION})
    await store_fingerprint(session, claim, fingerprint, seen)
    return claim


async def deposit_package(session, *, entry, prepared, account_id, now, review_policy_version):
    """Logs for a new bounty, received before the proof they must accompany exists."""
    package, raw, package_hash, fingerprint, consent, _ = prepared
    proof_digest = bytes.fromhex(package.final_proof_sha256.removeprefix("sha256:"))
    task_bundle = bytes.fromhex(entry.task_bundle_sha256.removeprefix("sha256:"))
    check_package(package, raw, task_id=entry.task_id, task_bundle_sha256=task_bundle,
                  proof_digest=proof_digest, submission_id=None, now=now)
    existing = await session.scalar(select(TrajectoryClaim).options(defer(TrajectoryClaim.package))
                                    .where(TrajectoryClaim.proof_digest == proof_digest).with_for_update())
    if existing is not None:
        if existing.status != "DEPOSITED":
            raise Conflict("Logs for this proof were already submitted with it.",
                           reason_code="TRAJECTORY_ALREADY_SUBMITTED")
        live = existing.expires_at > now
        if existing.account_id != account_id and live:
            raise Conflict("Evidence has already been submitted.", reason_code="TRAJECTORY_DUPLICATE")
        if live and existing.package_digest == package_hash and existing.reward_target_id == entry.reward_target_id:
            return existing  # Exact retry.
        # Until the proof is submitted, a fresh upload replaces an unused deposit.
        await session.delete(existing)
        await session.flush()
    seen = await session.get(TrajectoryFingerprint, fingerprint)
    if seen:
        raise Conflict("Evidence has already been submitted.", reason_code="TRAJECTORY_DUPLICATE")
    claim = TrajectoryClaim(
        id=uuid.uuid4(), submission_id=None, account_id=account_id, proof_digest=proof_digest,
        reward_target_id=entry.reward_target_id, task_bundle_sha256=task_bundle, status="DEPOSITED",
        amount_usd_cents=0, terms_version=TERMS_VERSION, package=raw, package_digest=package_hash,
        revision=1, state_version=0, offer_start_at=now, proof_review_decision_id=None, history=[],
        consent={**consent, "account_id": str(account_id), "accepted_at": now.isoformat()},
        proof_policy_snapshot={"review_policy_version": review_policy_version,
                               "bounty_policy_version": TREASURY_CHAT_POLICY},
        created_at=now, updated_at=now, expires_at=now + dt.timedelta(hours=DEPOSIT_HOURS),
        audit_expires_at=now + dt.timedelta(days=AUDIT_RETENTION_DAYS),
    )
    session.add(claim)
    audit(claim, action="DEPOSITED", actor=account_id, now=now, extra={"terms_version": TERMS_VERSION})
    await store_fingerprint(session, claim, fingerprint, seen)
    return claim


def live_deposit(*, account_id, reward_target_id, now):
    return select(TrajectoryClaim).options(defer(TrajectoryClaim.package)).where(
        TrajectoryClaim.account_id == account_id, TrajectoryClaim.reward_target_id == reward_target_id,
        TrajectoryClaim.status == "DEPOSITED", TrajectoryClaim.submission_id.is_(None),
        TrajectoryClaim.expires_at > now)


def missing_logs():
    return Conflict("This bounty needs its chat logs uploaded before the proof. Upload the logs for "
                    "this exact proof, then submit it within 24 hours.", reason_code=CHAT_LOG_REQUIRED)


async def require_deposit(session, *, account_id, entry, proof_digest, now):
    """Lock the logs this proof must carry; refuse before any credit is spent."""
    if isinstance(proof_digest, str):
        proof_digest = bytes.fromhex(proof_digest.removeprefix("sha256:"))
    claim = await session.scalar(live_deposit(account_id=account_id, reward_target_id=entry.reward_target_id, now=now)
        .where(TrajectoryClaim.proof_digest == proof_digest,
               TrajectoryClaim.task_bundle_sha256 == bytes.fromhex(entry.task_bundle_sha256.removeprefix("sha256:")))
        .with_for_update())
    if claim is None:
        raise missing_logs()
    return claim


async def bind_deposit(session, claim, submission, now):
    claim.submission_id = submission.id
    claim.status = "UNDER_REVIEW"
    claim.expires_at = claim.created_at + dt.timedelta(days=RETENTION_DAYS)
    claim.proof_policy_snapshot = {"review_policy_version": submission.review_policy_version,
                                   "bounty_policy_version": submission.bounty_policy_version}
    audit(claim, action="SUBMITTED_WITH_PROOF", actor=claim.account_id, now=now,
          extra={"submission_id": str(submission.id)})
    await session.flush()


async def effective_status(session, claim):
    if claim.status in ("DEPOSITED", "UNDER_REVIEW", "NEEDS_CHANGES") and claim.expires_at <= dt.datetime.now(dt.UTC):
        return "EXPIRED"
    if claim.status not in APPROVED:
        return claim.status
    submission = await session.get(Submission, claim.submission_id)
    decision = await latest_binding_decision(session, claim.submission_id)
    # Any correction invalidates the exact decision this bonus approval relied on. A future
    # payout adapter must repeat this under locks; the current feature cannot issue funds.
    reason = eligibility_reason(submission, decision, cutoff=claim.offer_start_at,
                                now=dt.datetime.now(dt.UTC), enforce_window=False)
    if (reason or decision.id != claim.proof_review_decision_id
            or claim.proof_policy_snapshot != {"review_policy_version": submission.review_policy_version,
                                               "bounty_policy_version": submission.bounty_policy_version}):
        return "BLOCKED_PROOF_CHANGED"
    return claim.status


async def view(session, claim):
    return {
        "id": str(claim.id), "submission_id": str(claim.submission_id) if claim.submission_id else None,
        "status": await effective_status(session, claim), "amount_usd_cents": claim.amount_usd_cents,
        "payment_status": "NOT_APPLICABLE" if claim.amount_usd_cents == 0 else "NOT_ISSUED",
        "chat_logs_required": claim.amount_usd_cents == 0, "revision": claim.revision,
        "state_version": claim.state_version, "offer_start_at": claim.offer_start_at,
        "package_sha256": "sha256:" + claim.package_digest.hex(), "terms_version": claim.terms_version,
        "review_note": claim.review_note, "created_at": claim.created_at,
        "expires_at": claim.expires_at, "audit_expires_at": claim.audit_expires_at,
        "content_available": await content_available(session, claim),
        "license_status": license_status(claim),
    }


def license_status(claim):
    # Granted on upload, effective on acceptance. No sale or delivery system is part of this code.
    return "LICENSED_FOR_SALE" if claim.status in APPROVED else "NOT_LICENSED"


async def content_available(session, claim):
    # Accepted logs are kept for sale; everything else expires on schedule.
    return (claim.status in APPROVED or claim.expires_at > dt.datetime.now(dt.UTC)) and bool(await session.scalar(
        select(TrajectoryClaim.package.is_not(None)).where(TrajectoryClaim.id == claim.id)))


def deletion_after_revocation(claim, now):
    """A revoked acceptance is no longer licensed; its content follows the normal deletion window."""
    return min(max(claim.expires_at, now + dt.timedelta(days=RETENTION_DAYS)),
               claim.audit_expires_at - dt.timedelta(seconds=1))


async def lock_payouts(session, submission_id):
    """Take payout rows before the submission, matching reconciliation and review corrections."""
    await session.execute(select(RewardEvent.id).where(RewardEvent.submission_id == submission_id)
                          .order_by(RewardEvent.id).with_for_update())


def verification_pending(submission):
    # A binding decision can only be recorded on a verified proof; verification is automatic.
    return submission.verification_status not in (VerificationState.VERIFIED, VerificationState.REJECTED)


async def forfeit_bounty(session, submission, *, reviewer, notes):
    """Required logs finally failed: record the binding rejection that releases the target.

    Uses the ordinary decision functions, so the reward hold, treasury reservation and payout
    guards move exactly as for any rejected proof. A defect award never needed logs and a
    proof that is already rejected holds nothing, so neither changes.
    """
    if submission.verification_status != VerificationState.VERIFIED:
        return None
    current = await latest_binding_decision(session, submission.id)
    if current is None:
        return await record_human_decision(session, submission.id, decision=ReviewOutcome.REJECTED,
                                           reason_code=FORFEIT_CODE, reviewer=reviewer, notes=notes,
                                           notes_public=FORFEIT_PUBLIC)
    if str(current.decision) == "APPROVED" and current.reason_code != "FORMALIZATION_DEFECT_AWARD":
        return await correct_human_decision(session, submission.id, supersedes_id=current.id,
                                            decision=ReviewOutcome.REJECTED, reason_code=FORFEIT_CODE,
                                            reviewer=reviewer, notes=notes, notes_public=FORFEIT_PUBLIC)
    return None


def amount_for(services, submission):
    """Resolve old and retired tasks by their immutable bundle, never user-supplied family."""
    if submission.bounty_policy_version == TREASURY_CHAT_POLICY:
        return 0
    entry = services.catalog.entries.get(submission.task_id)
    expected = "sha256:" + bytes(submission.task_bundle_sha256).hex()
    if entry is not None and entry.task_bundle_sha256 == expected:
        return legacy_chat_bonus_cents(entry.manifest.source_module)
    if is_v2_task_id(submission.task_id):
        try:
            bundle = TaskVersionStore(services.settings.task_pool_root.parent / "versions").load(
                submission.task_id, expected_sha256=expected)
        except (VerifierError, OSError):
            bundle = None  # Missing/corrupt archives cannot establish the historical tier.
        if bundle is not None:
            return legacy_chat_bonus_cents(bundle.manifest.source_module)
    raise Conflict("The original task bundle is required to classify this historical bonus.",
                   reason_code="TRAJECTORY_SOURCE_UNAVAILABLE")
