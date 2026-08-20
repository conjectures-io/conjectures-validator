"""The miner-facing submission surface.

The bundle arrives as the raw request body with `Content-Type: application/zip`; every scalar
travels in a header. That keeps one content-addressed artifact on the wire, needs no multipart
parser, and lets the handler reject a request before reading a single body byte.

Ordering here is a security and cost property, not a style choice:

1. headers, then declared length — a hostile 500 MB body is refused at the door rather than
   buffered and then measured;
2. idempotency replay — an exact retry is answered from durable state without re-uploading;
3. the body, streamed under a running cap, and the bundle admitted by the exact-shape scanner;
4. the hotkey signature over the canonical request digest, which covers the proof digest;
5. the payment, confirmed against finalized chain state.

Payment is confirmed last among the checks but before any write, because the schema makes it a
precondition: a submission row exists only for a transfer already confirmed. Anything refused
along the way creates no submission and is recorded in `api_rejection_log`, which is the only
trace a miner who paid and was turned away would otherwise leave.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal, Self

from fastapi import APIRouter, Header, Path, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from conjectures_subnet.attribution import (
    decode_public_credit_header,
    public_credit_from_values,
)
from conjectures_subnet.axiom import Severity, get_axiom
from conjectures_subnet.db import digests
from conjectures_subnet.db import submissions as store
from conjectures_subnet.db.errors import DatabaseError
from conjectures_subnet.db.models import (
    ManualReviewState,
    PayoutState,
    RewardEvent,
    Submission,
    VerificationRun,
    VerificationState,
)
from submission_api import schemas
from submission_api.auth import (
    SignedRequest,
    assert_fresh_nonce,
    assert_valid_hotkey,
    normalise_signature,
)
from submission_api.dependencies import Services, ServicesDep, SessionDep
from submission_api.errors import (
    REASON_TASK_NOT_ALLOWED,
    ApiError,
    BadRequest,
    Conflict,
    LengthRequired,
    NotFound,
    PayloadTooLarge,
    ServiceUnavailable,
    from_database_error,
    from_verifier_error,
)
from submission_api.taostats import amount_usd
from verifier.bundle import BUNDLE_MEDIA_TYPE, ProofBundle, admit_proof_bundle
from verifier.errors import VerifierError
from verifier.hashing import is_sha256, sha256_bytes
from verifier.task_registry import TaskNotAllowed

router = APIRouter(prefix="/v1/submissions", tags=["submissions"])

PAYMENT_REFERENCE = re.compile(r"^[A-Za-z0-9:_.#-]{4,128}$")
TASK_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,254}$")

REASON_SUBMISSIONS_PAUSED = "SUBMISSIONS_PAUSED"
REASON_BOUNTY_CLOSED = "BOUNTY_CLOSED"
REASON_BOUNTY_UNFUNDED = "BOUNTY_UNFUNDED"


# --- request parsing -----------------------------------------------------------------


def _require_uuid(raw: str, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw.strip())
    except (ValueError, AttributeError) as exc:
        raise BadRequest(f"{field} must be a UUID") from exc


def _require_nonce(raw: str) -> int:
    if not raw.isdigit() or len(raw) > 20:
        raise BadRequest(
            "X-Conjectures-Timestamp must be milliseconds since the Unix epoch"
        )
    return int(raw)


def _require_pattern(value: str, pattern: re.Pattern[str], field: str) -> str:
    if pattern.fullmatch(value) is None:
        raise BadRequest(f"{field} is malformed")
    return value


def _require_digest(value: str, field: str) -> str:
    if not is_sha256(value):
        raise BadRequest(f"{field} must be a lowercase sha256: digest")
    return value


async def read_body(request: Request, declared_length: int, limit: int) -> bytes:
    """Stream the body with a running cap so an oversized upload dies mid-flight."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise PayloadTooLarge(
                f"submission bundle exceeds the {limit}-byte limit",
                extra={"max_bundle_bytes": limit},
            )
        chunks.append(chunk)
    if total != declared_length:
        raise BadRequest("request body length does not match Content-Length")
    return b"".join(chunks)


# --- response shaping ----------------------------------------------------------------


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _verification(
    run: VerificationRun | None, submission: Submission
) -> schemas.VerificationStatus:
    if run is None:
        return schemas.VerificationStatus(status=str(submission.verification_status))
    return schemas.VerificationStatus(
        status=str(submission.verification_status),
        attempt=run.id,
        accepted=run.accepted,
        reason_code=run.reason_code,
        stage=run.stage,
        sandbox_mode=run.sandbox_mode,
        report_available=run.report is not None,
        started_at=_utc(run.started_at),
        finished_at=_utc(run.finished_at),
    )


async def _status(
    view: store.SubmissionView,
    *,
    services: Services,
    session: AsyncSession,
) -> schemas.SubmissionStatus:
    submission = view.submission
    payout = (
        await session.execute(
            select(RewardEvent)
            .where(
                RewardEvent.submission_id == submission.id,
                RewardEvent.chain_observed.is_(True),
                RewardEvent.status.in_(
                    (PayoutState.SUBMITTED, PayoutState.CONFIRMED)
                ),
            )
            .order_by(RewardEvent.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    alpha_usd = await services.bounty_usd.alpha_usd()
    if payout is None:
        if submission.bounty_locked_at is None:
            # Grandfathered submissions retain the payout-time pricing contract under which they
            # were accepted. They are the only rows for which a status read remains live-priced.
            live = await services.pricing.quote(
                session,
                reward_target_id=submission.reward_target_id,
                claimant_id=submission.id,
            )
            bounty = schemas.BountyQuote(
                amount_rao=live.amount_rao,
                amount_usd=amount_usd(live.amount_rao, alpha_usd=alpha_usd),
                policy_version=live.policy_version,
                available=live.available,
                reason=live.reason,
                as_of=live.as_of,
                locked=False,
            )
        else:
            released = (
                submission.verification_status == VerificationState.REJECTED
                or submission.manual_review_status == ManualReviewState.REJECTED
            )
            bounty = schemas.BountyQuote(
                amount_rao=submission.bounty_amount_rao,
                amount_usd=amount_usd(
                    submission.bounty_amount_rao, alpha_usd=alpha_usd
                ),
                policy_version=submission.bounty_policy_version,
                available=not released,
                reason="LOCK_RELEASED" if released else "LOCKED_AT_SUBMISSION",
                as_of=submission.bounty_locked_at,
                locked=True,
            )
    else:
        bounty = schemas.BountyQuote(
            amount_rao=payout.amount_rao,
            amount_usd=amount_usd(payout.amount_rao, alpha_usd=alpha_usd),
            policy_version=payout.pricing_policy_version,
            available=payout.status != PayoutState.CONFIRMED,
            reason=(
                "PAID" if payout.status == PayoutState.CONFIRMED else "PAYOUT_IN_PROGRESS"
            ),
            as_of=payout.confirmed_at or payout.created_at,
            locked=True,
        )
    return schemas.SubmissionStatus(
        submission_id=submission.id,
        hotkey=submission.hotkey,
        public_credit=(
            None
            if (credit := public_credit_from_values(submission)) is None
            else credit.to_dict()
        ),
        task_id=submission.task_id,
        task_bundle_sha256=digests.to_prefixed(submission.task_bundle_sha256),
        proof_sha256=digests.to_prefixed(submission.proof_digest),
        request_digest=digests.to_prefixed(submission.request_digest),
        verification_status=str(submission.verification_status),
        manual_review_status=str(submission.manual_review_status),
        reward_status=str(submission.reward_status),
        failure_reason=submission.failure_reason,
        manual_review_required=submission.manual_review_required,
        review_policy_version=submission.review_policy_version,
        payment=schemas.PaymentRecord(
            reference=submission.payment_reference,
            sender=submission.payment_sender,
            amount_rao=submission.payment_amount_rao,
            block=submission.payment_block,
        ),
        bounty=bounty,
        verification=_verification(view.verification, submission),
        created_at=_utc(submission.created_at),
        updated_at=_utc(submission.updated_at),
    )


# --- intake --------------------------------------------------------------------------


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=schemas.SubmissionStatus,
    summary="Create one paid Lean-proof submission",
)
async def create_submission(
    request: Request,
    response: Response,
    services: ServicesDep,
    session: SessionDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    hotkey: Annotated[str, Header(alias="X-Conjectures-Hotkey")],
    timestamp: Annotated[str, Header(alias="X-Conjectures-Timestamp")],
    signature: Annotated[str, Header(alias="X-Conjectures-Signature")],
    task_id: Annotated[str, Header(alias="X-Conjectures-Task-Id")],
    task_sha256: Annotated[str, Header(alias="X-Conjectures-Task-Sha256")],
    proof_sha256: Annotated[str, Header(alias="X-Conjectures-Proof-Sha256")],
    payment_reference: Annotated[str, Header(alias="X-Conjectures-Payment-Ref")],
    public_credit_header: Annotated[
        str | None, Header(alias="X-Conjectures-Public-Credit")
    ] = None,
    content_length: Annotated[int | None, Header(alias="Content-Length")] = None,
    content_type: Annotated[str | None, Header(alias="Content-Type")] = None,
    user_agent: Annotated[str | None, Header(alias="User-Agent")] = None,
) -> schemas.SubmissionStatus:
    settings = services.settings
    audit = _Audit(
        session=session,
        source_ip=request.client.host if request.client else None,
        user_agent=user_agent,
        hotkey_claimed=hotkey,
        idempotency_key=idempotency_key,
        task_id=task_id,
        task_bundle_sha256=task_sha256,
        payment_reference=payment_reference,
    )

    async with audit:
        # Refused before the body is read and before any payment is confirmed. A pause is what
        # `/v1/system/status` reports as `submissions_open: false` and what the weekly pin
        # rotation depends on: the drain cannot complete if new work keeps arriving. 503 with
        # `Retry-After` rather than 403 — the miner did nothing wrong and should come back.
        if settings.submissions_paused:
            raise ServiceUnavailable(
                "submissions are paused; see GET /v1/system/status",
                reason_code=REASON_SUBMISSIONS_PAUSED,
            )
        if (
            content_type is None
            or content_type.split(";")[0].strip().lower() != BUNDLE_MEDIA_TYPE
        ):
            raise BadRequest(f"Content-Type must be {BUNDLE_MEDIA_TYPE}")
        if content_length is None:
            raise LengthRequired("Content-Length is required for a submission bundle")
        if content_length <= 0:
            raise BadRequest("submission bundle must not be empty")
        if content_length > settings.max_bundle_bytes:
            raise PayloadTooLarge(
                f"submission bundle exceeds the {settings.max_bundle_bytes}-byte limit",
                extra={"max_bundle_bytes": settings.max_bundle_bytes},
            )

        key = _require_uuid(idempotency_key, "Idempotency-Key")
        payment_reference = _require_pattern(
            payment_reference, PAYMENT_REFERENCE, "X-Conjectures-Payment-Ref"
        )
        task_id = _require_pattern(task_id, TASK_ID, "X-Conjectures-Task-Id")
        task_sha256 = _require_digest(task_sha256, "X-Conjectures-Task-Sha256")
        declared_proof = _require_digest(proof_sha256, "X-Conjectures-Proof-Sha256")
        try:
            credit = decode_public_credit_header(public_credit_header)
        except ValueError as exc:
            raise BadRequest(str(exc)) from exc
        nonce_ms = _require_nonce(timestamp)
        miner = assert_valid_hotkey(hotkey)
        audit.proof_digest = declared_proof

        try:
            entry = services.catalog.resolve(task_id, task_sha256)
            task_mode = store.TaskMode(entry.mode)
        except (TaskNotAllowed, ValueError) as exc:
            raise NotFound(str(exc), reason_code=REASON_TASK_NOT_ALLOWED) from exc

        request_digest = store.canonical_request_digest(
            hotkey=miner,
            task_id=task_id,
            task_bundle_sha256=task_sha256,
            proof_sha256=declared_proof,
            payment_reference=payment_reference,
            idempotency_key=str(key),
            public_credit=credit,
        )
        audit.request_digest = request_digest

        # An exact replay is answered from durable state; the body is not needed.
        existing = await store.find_by_idempotency_key(session, miner, key)
        if existing is not None:
            if bytes(existing.request_digest) != digests.to_bytes(request_digest):
                raise from_database_error(_idempotency_conflict(str(key)))
            audit.disarm()
            response.status_code = status.HTTP_200_OK
            return await _status(
                await store.load_view(session, existing),
                services=services,
                session=session,
            )

        # Checked before reading the bundle and before confirming payment. A target with an
        # existing successful claim is no longer a bounty; accepting another paid attempt would
        # charge a miner for work that cannot win. This first quote is a cheap early refusal; the
        # serialized quote immediately before payment is the amount actually locked.
        quote = await services.pricing.quote(
            session, reward_target_id=entry.reward_target_id
        )
        _assert_payable_quote(quote, entry.reward_target_id)

        raw = await read_body(request, content_length, settings.max_bundle_bytes)
        try:
            bundle: ProofBundle = admit_proof_bundle(
                raw,
                task_manifest=entry.manifest,
                expected_task_sha256=entry.task_bundle_sha256,
                expected_hotkey=miner,
            )
        except VerifierError as exc:
            audit.proof_byte_length = len(raw)
            raise from_verifier_error(exc) from exc
        audit.proof_byte_length = len(bundle.proof.raw)

        if bundle.proof.sha256 != declared_proof:
            raise BadRequest(
                "archived proof digest does not match X-Conjectures-Proof-Sha256",
                extra={"proof_sha256": bundle.proof.sha256},
            )

        services.authenticator.verify(
            SignedRequest(
                hotkey=miner,
                request_digest=request_digest,
                signature=normalise_signature(signature),
            )
        )
        assert_fresh_nonce(nonce_ms, settings.nonce_window_seconds)

        # This transaction-level lock serializes the remaining-balance calculation with other
        # submissions. It is held through the submission INSERT and commit, making this quote the
        # immutable amount-of-record rather than another display estimate.
        quote = await services.pricing.lock_quote(
            session, reward_target_id=entry.reward_target_id
        )
        _assert_payable_quote(quote, entry.reward_target_id)

        # Payment last, and before any write: the schema has no unpaid state.
        try:
            payment = await services.payments.confirm(
                reference=payment_reference, hotkey=miner
            )
        except ApiError as exc:
            # Recorded here rather than left to `_Audit`, which sees the same failure as a generic
            # `submission_rejected`. The distinction matters: a refused payment can mean the
            # miner cited a bad reference, or it can mean the chain reader cannot see finalized
            # state — and only one of those is the validator's problem. `severity` follows the
            # status, so a `503` from an unreachable node is an error and a `402` is a warning.
            get_axiom().emit(
                severity=Severity.ERROR if exc.status_code >= 500 else Severity.WARNING,
                source="api-payments",
                event_type="payment_rejected",
                hotkey=miner,
                payment_reference=payment_reference,
                reason_code=exc.reason_code,
                http_status=exc.status_code,
                verifier=settings.payment_verifier,
            )
            raise
        get_axiom().info(
            source="api-payments",
            event_type="payment_accepted",
            hotkey=miner,
            payment_reference=payment.reference,
            payment_sender=payment.sender,
            amount_rao=payment.amount_rao,
            block=payment.block,
            verifier=settings.payment_verifier,
        )

        view = await store.create_submission(
            session,
            store.NewSubmission(
                hotkey=miner,
                idempotency_key=key,
                request_digest=request_digest,
                task_id=task_id,
                task_bundle_sha256=entry.task_bundle_sha256,
                problem_id=entry.problem_id,
                reward_target_id=entry.reward_target_id,
                task_mode=task_mode,
                proof_content=bundle.proof.raw,
                proof_sha256=bundle.proof.sha256,
                payment_reference=payment.reference,
                payment_sender=payment.sender,
                payment_amount_rao=payment.amount_rao,
                payment_block=payment.block,
                hotkey_signature=normalise_signature(signature),
                manual_review_required=settings.manual_review_enabled,
                review_policy_version=settings.review_policy_version,
                bounty_amount_rao=quote.amount_rao,
                bounty_policy_version=quote.policy_version,
                bounty_inputs=quote.inputs,
                public_credit_name=None if credit is None else credit.name,
                public_credit_url=None if credit is None else credit.url,
                public_credit_orcid=None if credit is None else credit.orcid,
            ),
        )
        if view.replayed:
            audit.disarm()
            response.status_code = status.HTTP_200_OK
            return await _status(view, services=services, session=session)

        # Spend the transfer, so it cannot also buy credits. In the same transaction as the
        # submission above, and after the replay check so an idempotent retry is not refused as a
        # double spend.
        #
        # Both funding paths reach the same treasury address and now share one canonical reference
        # format, so without this a miner could pay once, cite it here, and have the deposit
        # watcher credit the same transfer to their account as well. `chain_transfers` is the only
        # table that can arbitrate: its reference is unique and both sides take the row FOR UPDATE.
        # A conflict rolls this whole request back, submission included, which is correct — the
        # money bought something else.
        await services.payments.spend(
            session, payment, submission_id=view.submission.id
        )

        # The submission is now queued for verification by virtue of
        # verification_status = 'UNVERIFIED'; the worker claims it from that partial index.
        await services.dispatcher.dispatch(session, view.submission, entry.task_dir)
        refreshed = await store.load_view(session, view.submission)
        await session.commit()
        # After the commit that made the submission and the spend atomic. Same event type as the
        # intent path emits, distinguished by `funding`, so "how many submissions did we take" is
        # one query across both ways in.
        get_axiom().info(
            source="api-submissions",
            event_type="submission_accepted",
            submission_id=str(view.submission.id),
            hotkey=miner,
            task_id=task_id,
            problem_id=entry.problem_id,
            reward_target_id=entry.reward_target_id,
            task_mode=str(task_mode),
            funding="chain-extrinsic",
            payment_reference=payment.reference,
            payment_amount_rao=payment.amount_rao,
            proof_sha256=bundle.proof.sha256,
            proof_bytes=len(bundle.proof.raw),
            bounty_amount_rao=quote.amount_rao,
            bounty_policy_version=quote.policy_version,
            manual_review_required=settings.manual_review_enabled,
        )
        audit.disarm()
        return await _status(
            refreshed, services=services, session=session
        )


def _assert_payable_quote(quote, reward_target_id: str) -> None:
    if not quote.available and quote.reason in {"ALREADY_SOLVED", "NOT_IN_BOUNTY_POOL"}:
        raise Conflict(
            "this bounty has already been solved",
            reason_code=REASON_BOUNTY_CLOSED,
            extra={"reward_target_id": reward_target_id},
        )
    if not quote.available or quote.amount_rao is None or quote.amount_rao <= 0:
        raise ServiceUnavailable(
            "the bounty treasury has no uncommitted payable balance",
            reason_code=REASON_BOUNTY_UNFUNDED,
        )


def _idempotency_conflict(key: str) -> DatabaseError:
    from conjectures_subnet.db.errors import IdempotencyConflict

    return IdempotencyConflict(
        "idempotency key was already used with different submission data",
        idempotency_key=key,
    )


class _Audit:
    """Record any refusal in `api_rejection_log`, then re-raise.

    Intake is payment-gated, so a refused request creates no submission. Without this the only
    record of a miner who paid and was turned away would be a log line. `disarm()` is called
    on the success path; anything else that leaves the block is logged.

    The write uses a fresh transaction because the failing one is rolled back first — a
    refusal must still be recorded even when it was a constraint violation that caused it.
    """

    def __init__(
        self,
        *,
        session: AsyncSession,
        source_ip: str | None,
        user_agent: str | None,
        hotkey_claimed: str | None,
        idempotency_key: str | None,
        task_id: str | None,
        task_bundle_sha256: str | None,
        payment_reference: str | None,
    ) -> None:
        self._session = session
        self._armed = True
        self.source_ip = source_ip
        self.user_agent = user_agent
        self.hotkey_claimed = hotkey_claimed
        self.idempotency_key = idempotency_key
        self.task_id = task_id
        self.task_bundle_sha256 = task_bundle_sha256
        self.payment_reference = payment_reference
        self.proof_digest: str | None = None
        self.proof_byte_length: int | None = None
        self.request_digest: str | None = None

    def disarm(self) -> None:
        self._armed = False

    async def __aenter__(self) -> Self:
        return self

    # Literal[False], not bool: a plain `bool` means "may suppress the exception", which
    # makes every `raise` inside the block look like it falls through to the end of the
    # handler. This context manager records and re-raises, never swallows.
    async def __aexit__(self, exc_type, exc, _traceback) -> Literal[False]:
        if exc is None or not self._armed:
            return False
        problem = exc if isinstance(exc, ApiError) else None
        if isinstance(exc, DatabaseError):
            problem = from_database_error(exc)
        if isinstance(exc, VerifierError):
            problem = from_verifier_error(exc)
        if problem is None:
            return False
        try:
            await self._session.rollback()
            await store.log_rejection(
                self._session,
                reason_code=problem.reason_code,
                http_status=problem.status_code,
                hotkey_claimed=self.hotkey_claimed,
                idempotency_key=self.idempotency_key,
                task_id=self.task_id,
                task_bundle_sha256=_bare_hex(self.task_bundle_sha256),
                proof_digest=_bare_hex(self.proof_digest),
                proof_byte_length=self.proof_byte_length,
                request_digest=_bare_hex(self.request_digest),
                payment_reference=self.payment_reference,
                source_ip=self.source_ip,
                user_agent=self.user_agent,
                detail=dict(problem.extra) or None,
            )
            await self._session.commit()
        except Exception:  # noqa: BLE001 - telemetry must never mask the real refusal
            await self._session.rollback()
        # Outside the try: an Axiom emit is a queue put that cannot raise, and the event should be
        # recorded whether or not the `api_rejection_log` write survived — the case where the
        # database refused the row is precisely when the event is the only trace left.
        #
        # `warning`, matching the severity the request event gets for the same 4xx. A refused
        # submission is usually the miner's bundle to fix; what makes it worth recording is that a
        # payment-gated refusal may mean somebody paid and got nothing.
        get_axiom().warn(
            source="api-submissions",
            event_type="submission_rejected",
            reason_code=problem.reason_code,
            http_status=problem.status_code,
            hotkey=self.hotkey_claimed,
            task_id=self.task_id,
            payment_reference=self.payment_reference,
            proof_digest=_bare_hex(self.proof_digest),
            proof_byte_length=self.proof_byte_length,
        )
        # Re-raise the original: the handler's own exception handlers shape the response.
        return False


def _bare_hex(value: str | None) -> str | None:
    """The rejection log stores digests as unvalidated text, so keep whatever arrived."""
    if value is None:
        return None
    return value.removeprefix("sha256:")


# --- reads ---------------------------------------------------------------------------


def _read_authentication(
    services: Services, submission_id: str, hotkey: str, timestamp: str, signature: str
) -> str:
    """Authenticate a status read.

    The signed message is the digest of the submission id, so a read signature can never be
    replayed as a submission — the intake digest covers six fields and can never equal it.
    """
    miner = assert_valid_hotkey(hotkey)
    digest = sha256_bytes(
        f"conjectures-read-v1:{miner}:{submission_id}".encode()
    )
    assert_fresh_nonce(
        _require_nonce(timestamp), services.settings.nonce_window_seconds
    )
    services.authenticator.verify(
        SignedRequest(
            hotkey=miner,
            request_digest=digest,
            signature=normalise_signature(signature),
        )
    )
    return miner


@router.get(
    "/{submission_id}",
    response_model=schemas.SubmissionStatus,
    summary="Read verification, review, and reward state",
)
async def read_submission(
    submission_id: Annotated[uuid.UUID, Path()],
    services: ServicesDep,
    session: SessionDep,
    hotkey: Annotated[str, Header(alias="X-Conjectures-Hotkey")],
    timestamp: Annotated[str, Header(alias="X-Conjectures-Timestamp")],
    signature: Annotated[str, Header(alias="X-Conjectures-Signature")],
) -> schemas.SubmissionStatus:
    miner = _read_authentication(
        services, str(submission_id), hotkey, timestamp, signature
    )
    return await _status(
        await store.get_for_miner(session, submission_id, miner),
        services=services,
        session=session,
    )


@router.get(
    "/{submission_id}/report",
    response_model=schemas.VerificationReportResponse,
    summary="Read the immutable verifier report",
)
async def read_report(
    submission_id: Annotated[uuid.UUID, Path()],
    services: ServicesDep,
    session: SessionDep,
    hotkey: Annotated[str, Header(alias="X-Conjectures-Hotkey")],
    timestamp: Annotated[str, Header(alias="X-Conjectures-Timestamp")],
    signature: Annotated[str, Header(alias="X-Conjectures-Signature")],
) -> schemas.VerificationReportResponse:
    import json

    miner = _read_authentication(
        services, str(submission_id), hotkey, timestamp, signature
    )
    view = await store.get_for_miner(session, submission_id, miner)
    run = view.verification
    if run is None or run.report is None or run.report_digest is None:
        raise Conflict(
            "verification has not produced a report yet",
            extra={"verification_status": str(view.submission.verification_status)},
        )
    return schemas.VerificationReportResponse(
        submission_id=view.submission.id,
        report_sha256=digests.to_prefixed(run.report_digest),
        report=json.loads(bytes(run.report).decode("utf-8")),
    )
