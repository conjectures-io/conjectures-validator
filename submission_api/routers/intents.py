"""The credit-funded submission path: preflight, intent, bundle, confirm.

The alternative to `POST /v1/submissions`, which funds one submission with one finalized
extrinsic. This path spends a credit instead, and splits intake into four calls so that a
miner learns their bundle is admissible *before* anything is charged:

    POST /v1/submissions/preflight              free, no credit, no state
    POST /v1/submissions/intents                holds one credit
    PUT  /v1/submissions/intents/{id}/bundle    admits the bundle, returns the digest to sign
    POST /v1/submissions/intents/{id}/confirm   debits the credit, writes the submission

Why the credit is held at step 2 rather than charged at step 4: without a hold, a miner with
one credit could open any number of intents, upload to all of them, and race the confirms. The
hold is a claim on the balance that either becomes a debit or lapses — see
`conjectures_subnet.db.intents` for why it is not a ledger entry.

Why the server computes the digest at step 3: the client must never choose what it is signing.
The digest covers the proof bytes, the task, and the intent, so a captured signature cannot be
moved to different bytes or a different attempt.

**Preflight is free and therefore rate-limited by nothing but the middleware.** It runs the
full bundle admission, which is bounded work over a body capped at `MAX_BUNDLE_BYTES`, and it
writes nothing. That is deliberate: a paid endpoint that rejects malformed bundles is a bad
deal, and pushing miners to test against the paid path would be worse for both sides.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Path, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from conjectures_subnet.attribution import (
    PublicCredit as PublicCreditValue,
    public_credit,
    public_credit_from_values,
)
from conjectures_subnet.axiom import get_axiom
from conjectures_subnet.db import accounts as account_store
from conjectures_subnet.db import digests
from conjectures_subnet.db import intents as intent_store
from conjectures_subnet.db.errors import RecordConflict
from conjectures_subnet.db.models import TaskMode
from submission_api import schemas_account as schemas
from submission_api.dependencies import (
    PrincipalDep,
    ServicesDep,
    SessionDep,
    WriterDep,
    assert_hotkey_in_scope,
)
from submission_api.errors import (
    ApiError,
    BadRequest,
    Conflict,
    LengthRequired,
    NotFound,
    PayloadTooLarge,
    ServiceUnavailable,
    Unauthorized,
    from_verifier_error,
)
from submission_api.login import REASON_SIGNATURE_INVALID
from submission_api.routers._account import submission_detail, utc
from submission_api.routers.submissions import REASON_SUBMISSIONS_PAUSED, read_body
from submission_api.taskpool import TaskNotAllowed
from verifier.bundle import BUNDLE_MEDIA_TYPE, ProofBundle, admit_proof_bundle
from verifier.errors import VerifierError
from verifier.hashing import canonical_json_bytes, sha256_bytes
from verifier.task_registry import TaskNotAllowed as RegistryTaskNotAllowed

router = APIRouter(prefix="/v1/submissions", tags=["submission"])

REASON_TASK_NOT_ALLOWED = "TASK_NOT_ALLOWED"
REASON_HOTKEY_NOT_LINKED = "HOTKEY_NOT_LINKED"


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PublicCreditRequest(Payload):
    name: str = Field(min_length=1, max_length=128)
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    orcid: str | None = Field(default=None, min_length=19, max_length=19)


class IntentRequest(Payload):
    task_id: str = Field(min_length=1, max_length=255, pattern=r"^[a-z0-9][a-z0-9-]{0,254}$")
    task_bundle_sha256: str = Field(min_length=71, max_length=71)
    hotkey: str = Field(min_length=48, max_length=48)
    public_credit: PublicCreditRequest | None = None


class ConfirmRequest(Payload):
    signature: str = Field(min_length=128, max_length=132)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _signature_bytes(value: str) -> bytes:
    candidate = value.strip().removeprefix("0x").removeprefix("0X").lower()
    try:
        raw = bytes.fromhex(candidate)
    except ValueError as exc:
        raise Unauthorized(
            "signature must be 64 bytes of hex", reason_code=REASON_SIGNATURE_INVALID
        ) from exc
    if len(raw) != 64:
        raise Unauthorized(
            "signature must be 64 bytes of hex", reason_code=REASON_SIGNATURE_INVALID
        )
    return raw


def intent_request_digest(
    *,
    intent_id: uuid.UUID,
    hotkey: str,
    task_id: str,
    task_bundle_sha256: str,
    proof_sha256: str,
    public_credit: PublicCreditValue | None = None,
) -> str:
    """The identity of a credit-funded attempt, and the message the miner signs.

    The same shape as the extrinsic path's `canonical_request_digest` — canonical JSON, sorted
    keys — with `intent_id` where that one has `payment_reference` and `idempotency_key`. The
    intent *is* the idempotency key here: one intent is one attempt.

    Every field a signature must not be transferable across is inside it: the proof bytes (by
    digest), the task, the submitting hotkey, and the specific attempt.
    """
    value = {
        "hotkey": hotkey,
        "intent_id": str(intent_id),
        "proof_sha256": proof_sha256,
        "task_bundle_sha256": task_bundle_sha256,
        "task_id": task_id,
    }
    if public_credit is not None:
        value["public_credit"] = public_credit.to_dict()
    return sha256_bytes(canonical_json_bytes(value))


def _intent(intent) -> schemas.SubmissionIntent:
    return schemas.SubmissionIntent(
        id=intent.id,
        status=str(intent.status),
        hotkey=intent.hotkey,
        public_credit=(
            None
            if (credit := public_credit_from_values(intent)) is None
            else credit.to_dict()
        ),
        task_id=intent.task_id,
        task_bundle_sha256=digests.to_prefixed(intent.task_bundle_sha256),
        credits_held=intent.credits_held,
        credit_price_rao=intent.credit_price_rao,
        proof_sha256=(
            digests.to_prefixed(intent.proof_sha256) if intent.proof_sha256 else None
        ),
        request_digest=(
            digests.to_prefixed(intent.request_digest) if intent.request_digest else None
        ),
        submission_id=intent.submission_id,
        expires_at=utc(intent.expires_at),
        created_at=utc(intent.created_at),
    )


def _resolve_task(services, task_id: str, task_bundle_sha256: str):
    try:
        return services.catalog.resolve(task_id, task_bundle_sha256)
    except (TaskNotAllowed, RegistryTaskNotAllowed) as exc:
        raise NotFound(str(exc), reason_code=REASON_TASK_NOT_ALLOWED) from exc


async def _uploaded_bundle(
    request: Request,
    services,
    entry,
    *,
    hotkey: str,
    content_type: str | None,
    content_length: int | None,
) -> ProofBundle:
    """Read and admit a bundle from the request body.

    The same ordering the extrinsic path uses, and for the same reason: headers and the
    declared length first, so a hostile body is refused at the door rather than buffered and
    then measured.
    """
    settings = services.settings
    if content_type is None or content_type.split(";")[0].strip().lower() != BUNDLE_MEDIA_TYPE:
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
    raw = await read_body(request, content_length, settings.max_bundle_bytes)
    try:
        return admit_proof_bundle(
            raw,
            task_manifest=entry.manifest,
            expected_task_sha256=entry.task_bundle_sha256,
            expected_hotkey=hotkey,
        )
    except VerifierError as exc:
        raise from_verifier_error(exc) from exc


# --- Preflight ---------------------------------------------------------------------------


@router.post(
    "/preflight",
    response_model=schemas.PreflightResult,
    summary="Free static policy check, before a credit is spent",
)
async def preflight(
    request: Request,
    services: ServicesDep,
    task_id: Annotated[str, Header(alias="X-Conjectures-Task-Id")],
    task_sha256: Annotated[str, Header(alias="X-Conjectures-Task-Sha256")],
    hotkey: Annotated[str, Header(alias="X-Conjectures-Hotkey")],
    content_length: Annotated[int | None, Header(alias="Content-Length")] = None,
    content_type: Annotated[str | None, Header(alias="Content-Type")] = None,
) -> schemas.PreflightResult:
    """Run the paid path's bundle admission and report what it says. Charge nothing.

    Unauthenticated on purpose: a miner should be able to check a bundle before creating an
    account. It writes nothing, holds nothing, and the work is bounded by the body cap, so
    there is nothing here worth gating.

    A failure is returned as `ok: false` with the reason code rather than as an error status.
    The request succeeded — the answer is "this bundle would be refused", which is exactly what
    was asked.
    """
    entry = _resolve_task(services, task_id, task_sha256)
    try:
        bundle = await _uploaded_bundle(
            request,
            services,
            entry,
            hotkey=hotkey,
            content_type=content_type,
            content_length=content_length,
        )
    except ApiError as exc:
        # Every failure `_uploaded_bundle` raises is an ApiError — a malformed request, or a
        # bundle the verifier refused, already translated to a reason code. Caught as one type
        # rather than three, and reported in the body: the caller asked "would this be
        # accepted", and "no, because X" is a successful answer to that question.
        line, column = _location(exc.extra)
        return schemas.PreflightResult(
            ok=False,
            reason_code=exc.reason_code,
            detail=exc.detail,
            line=line,
            column=column,
        )

    return schemas.PreflightResult(
        ok=True,
        proof_sha256=bundle.proof.sha256,
        proof_bytes=len(bundle.proof.raw),
    )


def _location(extra) -> tuple[int | None, int | None]:
    """Pull a line and column out of a verifier rejection when it carries one.

    Not every rejection has a location — an archive-level policy failure has no line in the
    submitted source — so both stay null unless the verifier supplied them.
    """
    line = (extra or {}).get("line")
    column = (extra or {}).get("column")
    return (
        line if isinstance(line, int) else None,
        column if isinstance(column, int) else None,
    )


# --- Intents -----------------------------------------------------------------------------


@router.post(
    "/intents",
    response_model=schemas.SubmissionIntent,
    status_code=status.HTTP_201_CREATED,
    summary="Open an intent and hold one credit",
)
async def create_intent(
    payload: IntentRequest,
    principal: WriterDep,
    services: ServicesDep,
    session: SessionDep,
) -> schemas.SubmissionIntent:
    """Hold a credit for one attempt on one task.

    The hotkey must be linked to this account. Otherwise a signed-in account could spend its
    own credit to submit under someone else's identity, and the resulting reward would have a
    disputed owner.

    A **CLI session** must additionally name the hotkey it was minted for. `owns_hotkey` is an
    account-level question, which is the right one for a browser session — the person owns all of
    their keys. It is the wrong one for a bearer token, which was opened by one key on one
    machine: without the scope check, a token read off rig A could spend the account's credits
    and have the submission, and its reward, attributed to rig B.

    Insufficient credit is a 409 carrying the balance, so a client can show what is missing
    without a second request.
    """
    settings = services.settings
    if settings.submissions_paused:
        raise ServiceUnavailable(
            "submissions are paused; see GET /v1/system/status",
            reason_code=REASON_SUBMISSIONS_PAUSED,
        )

    # Before the task is resolved: this is a question about the caller's credential, not about
    # the request's content, and a caller who may not act as this hotkey should be refused
    # without the server first doing catalog work on their behalf.
    assert_hotkey_in_scope(principal, payload.hotkey)

    # Then the content. Parsed before the task is resolved for the same reason — a malformed
    # credit is the caller's error and costs nothing to refuse — but after the credential check,
    # so a caller who may not act as this hotkey learns that first.
    try:
        credit = (
            None
            if payload.public_credit is None
            else public_credit(
                payload.public_credit.name,
                payload.public_credit.url,
                payload.public_credit.orcid,
            )
        )
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc

    entry = _resolve_task(services, payload.task_id, payload.task_bundle_sha256)
    if not await account_store.owns_hotkey(
        session, principal.account.id, payload.hotkey
    ):
        raise Conflict(
            "link that hotkey to your account before submitting under it",
            reason_code=REASON_HOTKEY_NOT_LINKED,
        )

    intent, _ = await intent_store.open_intent(
        session,
        account_id=principal.account.id,
        hotkey=payload.hotkey,
        task_id=entry.task_id,
        task_bundle_sha256=entry.task_bundle_sha256,
        credit_price_rao=settings.payment_amount_rao,
        expires_at=_now() + dt.timedelta(minutes=settings.intent_minutes),
        now=_now(),
        public_credit=credit,
    )
    await session.commit()
    # A held credit is money committed, so the three intent events are the funnel an operator
    # reads: opened, bundle stored, committed. A gap between the first and the last is either
    # abandoned intents or a step that is failing.
    get_axiom().info(
        source="api-intents",
        event_type="intent_opened",
        intent_id=str(intent.id),
        account_id=str(principal.account.id),
        hotkey=payload.hotkey,
        task_id=entry.task_id,
        credit_price_rao=settings.payment_amount_rao,
        expires_in_minutes=settings.intent_minutes,
    )
    return _intent(intent)


@router.put(
    "/intents/{intent_id}/bundle",
    response_model=schemas.IntentBundleResult,
    summary="Upload the bundle and receive the digest to sign",
)
async def upload_bundle(
    intent_id: Annotated[str, Path(min_length=36, max_length=36)],
    request: Request,
    principal: WriterDep,
    services: ServicesDep,
    session: SessionDep,
    content_length: Annotated[int | None, Header(alias="Content-Length")] = None,
    content_type: Annotated[str | None, Header(alias="Content-Type")] = None,
) -> schemas.IntentBundleResult:
    """Admit the bundle against the intent's task, then compute the digest to sign.

    The digest is derived here from what was actually admitted — the bundle's own proof digest,
    not a value the client sent — so the miner signs the bytes the validator holds. Re-uploading
    replaces the bundle and recomputes the digest, invalidating any earlier signature, so a
    miner who uploaded the wrong file does not lose the held credit.
    """
    identifier = _as_uuid(intent_id)
    intent = await intent_store.get_intent(session, identifier, principal.account.id)
    intent_store.assert_usable(intent, now=_now())

    entry = _resolve_task(
        services, intent.task_id, digests.to_prefixed(intent.task_bundle_sha256)
    )
    bundle = await _uploaded_bundle(
        request,
        services,
        entry,
        hotkey=intent.hotkey,
        content_type=content_type,
        content_length=content_length,
    )

    request_digest = intent_request_digest(
        intent_id=intent.id,
        hotkey=intent.hotkey,
        task_id=intent.task_id,
        task_bundle_sha256=entry.task_bundle_sha256,
        proof_sha256=bundle.proof.sha256,
        public_credit=public_credit_from_values(intent),
    )
    await intent_store.attach_bundle(
        session,
        intent,
        proof_content=bundle.proof.raw,
        proof_sha256=bundle.proof.sha256,
        request_digest=request_digest,
        now=_now(),
    )
    await session.commit()
    get_axiom().info(
        source="api-intents",
        event_type="intent_bundle_stored",
        intent_id=str(intent.id),
        account_id=str(principal.account.id),
        hotkey=intent.hotkey,
        task_id=intent.task_id,
        proof_sha256=bundle.proof.sha256,
        proof_bytes=len(bundle.proof.raw),
    )
    return schemas.IntentBundleResult(
        intent=_intent(intent),
        proof_sha256=bundle.proof.sha256,
        proof_bytes=len(bundle.proof.raw),
        request_digest=request_digest,
    )


@router.post(
    "/intents/{intent_id}/confirm",
    response_model=schemas.ConfirmedSubmission,
    status_code=status.HTTP_201_CREATED,
    summary="Debit the credit and write the submission",
)
async def confirm_intent(
    intent_id: Annotated[str, Path(min_length=36, max_length=36)],
    payload: ConfirmRequest,
    principal: WriterDep,
    services: ServicesDep,
    session: SessionDep,
) -> schemas.ConfirmedSubmission:
    """Verify the signature, then spend the credit and record the submission atomically.

    The signature is checked *before* the debit, against the digest the server computed at
    upload time. `intents.confirm` then does the money in one transaction under an account-level
    lock: either the SPEND entry and the submission both exist, or neither does.

    A second confirm of the same intent is a 409 carrying the original submission id rather than
    a second charge — the intent is the idempotency key, and the unique index on
    `credit_ledger.intent_id` would refuse the duplicate debit regardless.
    """
    settings = services.settings
    identifier = _as_uuid(intent_id)
    signature = _signature_bytes(payload.signature)

    intent = await intent_store.get_intent(session, identifier, principal.account.id)
    if intent.request_digest is None:
        raise Conflict(
            "upload a bundle before confirming", reason_code="INTENT_HAS_NO_BUNDLE"
        )

    # The digest is 32 raw bytes, and the hotkey signature is over exactly those bytes — the
    # same convention the extrinsic path uses, so miner tooling signs one kind of thing.
    services.authenticator.verify(
        _signed_request(intent, signature)
    )

    # Preserve the intent endpoint's idempotency contract before consulting live bounty state.
    # In particular, retrying an already-confirmed intent must continue to return the original
    # submission id even if that submission has since won (and therefore closed) the bounty.
    # ``confirm`` repeats this check under the account/intent locks to close the race.
    intent_store.assert_usable(intent, now=_now())

    entry = _resolve_task(
        services, intent.task_id, digests.to_prefixed(intent.task_bundle_sha256)
    )
    # Serialize the remaining-balance quote with every other submission until this transaction
    # commits the debit and submission. The amount returned here is the permanent bounty lock.
    quote = await services.pricing.lock_quote(
        session, reward_target_id=entry.reward_target_id
    )
    if not quote.available and quote.reason in {"ALREADY_SOLVED", "NOT_IN_BOUNTY_POOL"}:
        raise Conflict(
            "this bounty has already been solved",
            reason_code="BOUNTY_CLOSED",
            extra={"reward_target_id": entry.reward_target_id},
        )
    if not quote.available or quote.amount_rao is None or quote.amount_rao <= 0:
        raise Conflict(
            "the bounty treasury has no uncommitted payable balance",
            reason_code="BOUNTY_UNFUNDED",
        )
    try:
        confirmed = await intent_store.confirm(
            session,
            identifier,
            principal.account.id,
            problem_id=entry.problem_id,
            reward_target_id=entry.reward_target_id,
            task_mode=TaskMode(entry.mode),
            hotkey_signature=signature,
            manual_review_required=settings.manual_review_enabled,
            review_policy_version=settings.review_policy_version,
            bounty_amount_rao=quote.amount_rao,
            bounty_policy_version=quote.policy_version,
            bounty_inputs=dict(quote.inputs) if quote.inputs else None,
            now=_now(),
        )
    except RecordConflict:
        await session.rollback()
        raise

    await services.dispatcher.dispatch(session, confirmed.submission, entry.task_dir)
    await session.commit()

    # After the commit that made the debit and the submission atomic, so this is never reported
    # for a spend that rolled back. Two events rather than one: `intent_committed` closes the
    # intent funnel, `submission_accepted` opens the verification one, and the two paths into
    # intake — this one and the extrinsic one in `submissions.py` — share the second.
    get_axiom().info(
        source="api-intents",
        event_type="intent_committed",
        intent_id=str(identifier),
        account_id=str(principal.account.id),
        submission_id=str(confirmed.submission.id),
        credits_available=confirmed.balance.credits_available,
        low_balance=confirmed.balance.low_balance,
    )
    get_axiom().info(
        source="api-intents",
        event_type="submission_accepted",
        submission_id=str(confirmed.submission.id),
        account_id=str(principal.account.id),
        hotkey=intent.hotkey,
        task_id=intent.task_id,
        problem_id=entry.problem_id,
        reward_target_id=entry.reward_target_id,
        task_mode=entry.mode,
        funding="credit-intent",
        bounty_amount_rao=quote.amount_rao,
        bounty_policy_version=quote.policy_version,
        manual_review_required=settings.manual_review_enabled,
    )

    view = await _reload(session, confirmed.submission)
    return schemas.ConfirmedSubmission(
        submission=await submission_detail(session, view),
        credits=schemas.CreditBalance(
            credits_available=confirmed.balance.credits_available,
            balance_rao=confirmed.balance.balance_rao,
            held_rao=confirmed.balance.held_rao,
            remainder_rao=confirmed.balance.remainder_rao,
            credit_price_rao=confirmed.balance.credit_price_rao,
            low_balance=confirmed.balance.low_balance,
        ),
    )


def _signed_request(intent, signature: bytes):
    from submission_api.auth import SignedRequest

    return SignedRequest(
        hotkey=intent.hotkey,
        request_digest=digests.to_prefixed(intent.request_digest),
        signature=signature,
    )


async def _reload(session, submission):
    from conjectures_subnet.db import submissions as submission_store

    return await submission_store.load_view(session, submission)


def _as_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise NotFound("intent not found") from exc


@router.get(
    "/intents/{intent_id}",
    response_model=schemas.SubmissionIntent,
    summary="One intent's state",
)
async def read_intent(
    intent_id: Annotated[str, Path(min_length=36, max_length=36)],
    response: Response,
    principal: PrincipalDep,
    session: SessionDep,
) -> schemas.SubmissionIntent:
    """Not in the Stage 2 contract, but the flow is unusable without it: a client that loses
    the response to `POST /intents` has a held credit and no way to find the intent holding it.
    """
    response.headers["Cache-Control"] = "no-store"
    return _intent(
        await intent_store.get_intent(session, _as_uuid(intent_id), principal.account.id)
    )


__all__ = ["intent_request_digest", "router"]
