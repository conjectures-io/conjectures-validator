"""The website submission path: one call, one coldkey signature, one credit.

The third way into intake. Every path now ends in a coldkey signature — V035 retired the miner
hotkey entirely — so what distinguishes this one is no longer *which key* signs but **what it
signs and how many calls it takes**:

* `POST /v1/submissions` wants a signature over 32 raw bytes of request digest, plus a payment
  reference for a transfer the page cannot make;
* `POST /v1/submissions/intents` + `PUT .../bundle` + `POST .../confirm` also ends in a
  signature over 32 raw bytes — which a message-signing wallet will not render, and which a
  person cannot meaningfully approve.

What changes here: **the authorising signature is over a readable message and the whole attempt
is one request.** Everything else is the credit path unchanged — the credit is held, the bundle
is admitted by the same exact-shape scanner, the bounty is locked by the same serialized quote,
and the debit and the submission are written by the same `intents.confirm` transaction. Reusing
that is deliberate: it is the money path, and a second implementation of it is a second chance
to get atomicity wrong.

This endpoint used to take a second, *declared* key: a payout hotkey it never proved, because a
payout ran `transfer_stake_and_hotkey` and had to name a stake position. Two checks bounded that
declaration without proving control of it, and a chain read backed one of them. A payout now
hands the destination coldkey ownership of alpha that never leaves the validator's own hotkey,
so there is nothing to declare, nothing to check, and no chain read on this path at all.

Why one call rather than three. The three-call flow exists so the server can compute the digest
the client signs *after* seeing the bundle. Here the client signs first, so the ordering is
inverted — and the property is recovered a different way: the message names the digest of the
archive, and the server rebuilds the message from **the bytes it actually read** and **the task
digest from its own allowlist**, never from what the request claimed. A caller who understated
either signs one message and is checked against another. See `login.web_submission_message`.

**Every scalar travels in the query string, not in a header, and that is not a style choice.**
`CORS_REQUEST_HEADERS` in `settings.py` deliberately allowlists no `X-Conjectures-*` header —
that omission is what keeps `POST /v1/submissions` unreachable from a browser even on an
allowlisted origin, and widening it for this endpoint would undo it for that one. A query
parameter needs no preflight grant, and `Content-Type: application/zip` is already allowed. The
body therefore stays exactly what it is on the other two paths: one content-addressed artifact,
read under a running byte cap, with no form parser in the way.

Ordering here is a security and cost property, the same as on the extrinsic path:

1. the pause, then the query's own shape — nothing that costs a query is done for a request
   that cannot be well-formed;
2. the credential and the key it claims: a caller who may not act as this coldkey learns that
   before the server does catalog work for them;
3. idempotency replay — a retry is answered from durable state without re-uploading;
4. the balance and a first bounty quote, so an account with nothing to spend is refused before
   it uploads up to 12 MiB;
5. the declared type and length, then the body streamed under a running cap, and the bundle
   admitted by the exact-shape scanner — all of it inside `intents.uploaded_bundle`, so a
   hostile 500 MB body is refused on its declaration rather than buffered and then measured;
6. the coldkey signature over the rebuilt message;
7. the credit hold, the serialized bounty quote, and the debit-plus-submission transaction.

**No step before 5 reads a single body byte**, which is what makes the cheap refusals above it
cheap rather than merely early. Nothing is charged before step 7, and step 7 is one transaction:
either the SPEND entry and the submission both exist, or neither does.
"""

from __future__ import annotations

from verifier.task_policy import review_policy_for_track

import datetime as dt
import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request, Response, status

from conjectures_subnet.attribution import decode_public_credit_header
from conjectures_subnet.axiom import get_axiom
from conjectures_subnet.db import accounts as account_store
from conjectures_subnet.db import credits as credit_store
from conjectures_subnet.db import intents as intent_store
from conjectures_subnet.db import submissions as submission_store
from conjectures_subnet.db.models import TaskMode
from submission_api import schemas_account as schemas
from submission_api.auth import normalise_signature
from submission_api.dependencies import CookieWriterDep, ServicesDep, SessionDep
from submission_api.errors import (
    BadRequest,
    Conflict,
    NotFound,
    ServiceUnavailable,
    Unauthorized,
)
from submission_api.login import verify_signature, web_submission_message
from submission_api.routers._account import submission_detail
from submission_api.routers.intents import uploaded_bundle
from submission_api.routers.submissions import REASON_SUBMISSIONS_PAUSED
from submission_api.taskpool import TaskNotAllowed
from verifier.hashing import is_sha256, sha256_bytes
from verifier.task_registry import TaskNotAllowed as RegistryTaskNotAllowed

router = APIRouter(prefix="/v1/submissions", tags=["submission"])

WEB_PATH = "/v1/submissions/web"

REASON_TASK_NOT_ALLOWED = "TASK_NOT_ALLOWED"
REASON_WALLET_NOT_LINKED = "WALLET_NOT_LINKED"
# HOTKEY_CLAIMED_BY_ANOTHER_ACCOUNT and HOTKEY_NOT_REGISTERED were here until V035. Both bounded
# a declared payout hotkey that no longer exists, so both are retired rather than renamed — a
# client still handling them will simply never see them again.
REASON_BUNDLE_DIGEST_MISMATCH = "BUNDLE_DIGEST_MISMATCH"
REASON_AUTHORISATION_EXPIRED = "AUTHORISATION_EXPIRED"
REASON_AUTHORISATION_WINDOW = "AUTHORISATION_WINDOW_TOO_LONG"

TASK_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,254}$")
# Exactly one spelling of an instant, to the second, UTC, `Z`. The message is verified by
# rebuilding it, so a second accepted spelling would be a second message for the same moment
# and a signature that fails for no reason the caller can see. Fractional seconds are refused
# for the same reason rather than truncated.
EXPIRES_AT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _require_uuid(raw: str, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw.strip())
    except (ValueError, AttributeError) as exc:
        raise BadRequest(f"{field} must be a UUID") from exc


def _require_digest(value: str, field: str) -> str:
    if not is_sha256(value):
        raise BadRequest(f"{field} must be a lowercase sha256: digest")
    return value


def _require_expiry(raw: str, *, now: dt.datetime, max_minutes: int) -> dt.datetime:
    """Parse the authorisation expiry and bound how long it may be good for.

    Two-sided, like `assert_fresh_nonce` on the digest-signing paths and for the same reason: an
    expiry already past is useless, and one far in the future would let a page mint a
    long-lived reusable authorisation for the account's credits. The ceiling is
    `INTENT_MINUTES`, which is already the answer to "how long may one attempt stay live".
    """
    if EXPIRES_AT.fullmatch(raw) is None:
        raise BadRequest(
            "expires_at must be a UTC instant to the second, as 2026-08-21T10:00:00Z"
        )
    expires_at = dt.datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.UTC)
    if expires_at <= now:
        raise Unauthorized(
            "this authorisation has expired; sign a new one",
            reason_code=REASON_AUTHORISATION_EXPIRED,
        )
    if expires_at - now > dt.timedelta(minutes=max_minutes):
        raise BadRequest(
            f"expires_at must be at most {max_minutes} minutes from now",
            reason_code=REASON_AUTHORISATION_WINDOW,
            extra={"max_minutes": max_minutes},
        )
    return expires_at


def _resolve_task(services, task_id: str, task_bundle_sha256: str):
    try:
        return services.catalog.resolve(task_id, task_bundle_sha256)
    except (TaskNotAllowed, RegistryTaskNotAllowed, ValueError) as exc:
        raise NotFound(str(exc), reason_code=REASON_TASK_NOT_ALLOWED) from exc


def _assert_payable(quote, reward_target_id: str) -> None:
    """The same two refusals the intent path makes, with the same codes.

    Spelled out here rather than shared with `submissions._assert_payable_quote`, which answers
    `503` where the credit path answers `409`. This endpoint replaces the three-call flow for a
    browser, so it has to answer what that flow answers — a frontend handling one of them must
    not need a second branch for the other.
    """
    if not quote.available and quote.reason in {"ALREADY_SOLVED", "NOT_IN_BOUNTY_POOL"}:
        raise Conflict(
            "this bounty has already been solved",
            reason_code="BOUNTY_CLOSED",
            extra={"reward_target_id": reward_target_id},
        )
    if not quote.available or quote.amount_rao is None or quote.amount_rao <= 0:
        raise Conflict(
            "the bounty treasury has no uncommitted payable balance",
            reason_code="BOUNTY_UNFUNDED",
        )


@router.post(
    "/web",
    response_model=schemas.ConfirmedSubmission,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a bundle from the website, authorised by a linked coldkey",
)
async def create_web_submission(
    request: Request,
    response: Response,
    principal: CookieWriterDep,
    services: ServicesDep,
    session: SessionDep,
    task_id: Annotated[str, Query(min_length=1, max_length=255)],
    task_bundle_sha256: Annotated[str, Query(min_length=71, max_length=71)],
    coldkey: Annotated[str, Query(min_length=48, max_length=48)],
    bundle_sha256: Annotated[str, Query(min_length=71, max_length=71)],
    idempotency_key: Annotated[str, Query(min_length=36, max_length=36)],
    expires_at: Annotated[str, Query(min_length=20, max_length=20)],
    signature: Annotated[str, Query(min_length=128, max_length=132)],
    public_credit: Annotated[str | None, Query(max_length=4096)] = None,
    content_length: Annotated[int | None, Header(alias="Content-Length")] = None,
    content_type: Annotated[str | None, Header(alias="Content-Type")] = None,
) -> schemas.ConfirmedSubmission:
    """Hold a credit, admit the bundle, verify the coldkey signature, and submit — atomically.

    **A browser session, not a CLI one.** `CookieWriterDep` refuses a bearer token outright,
    and that costs the CLI nothing: it already has the three-call intent flow, and it holds a
    scoped token rather than a key it could sign this message with. What it buys is that the
    one-call intake path cannot be driven by a credential read off a mining box — the same rule
    that keeps coldkey linking and the payout destination browser-only.

    **One key, and it must be linked.** V035 removed the second. This endpoint used to take a
    declared `hotkey` as well, because a payout ran `transfer_stake_and_hotkey` and had to name
    a stake position; that meant an address the submitter did not prove, bounded by two checks
    that were not proofs of control — that no other account had claimed it, and that the chain
    knew it. A payout now hands the destination coldkey ownership of alpha that stays staked at
    the validator's own hotkey, so there is nothing to declare. Both checks, the chain read
    behind the second, and their two reason codes are gone.

    The coldkey that remains is *who authorised the spend*. It must be linked, because a
    signature proves control of a key and not that the key belongs to this account — without
    the check, anyone who captured a signature could spend their own credits under somebody
    else's authorisation.

    Note it need not be the account's designated `submission_coldkey`. Any linked wallet may
    authorise from a browser: the designation exists to tell the *intent* flow which key to
    expect a signature from when nothing in the request names one, and here the request names
    one and proves it.

    A replay of an already-accepted `idempotency_key` answers `200` with the original
    submission, before the body is read. Two identical requests racing past that check leave one
    `201` and one `409 IDEMPOTENCY_CONFLICT`; the loser's credit hold rolls back with its
    transaction, so nothing is charged twice either way.
    """
    settings = services.settings
    now = _now()

    # First, and before the body: a pause is what `/v1/system/status` reports as
    # `submissions_open: false`, and the weekly pin rotation depends on the drain completing.
    if settings.submissions_paused:
        raise ServiceUnavailable(
            "submissions are paused; see GET /v1/system/status",
            reason_code=REASON_SUBMISSIONS_PAUSED,
        )

    key = _require_uuid(idempotency_key, "idempotency_key")
    task_bundle_sha256 = _require_digest(task_bundle_sha256, "task_bundle_sha256")
    declared_bundle = _require_digest(bundle_sha256, "bundle_sha256")
    if TASK_ID.fullmatch(task_id) is None:
        raise BadRequest("task_id is malformed")
    signed_until = _require_expiry(
        expires_at, now=now, max_minutes=settings.intent_minutes
    )
    signature_bytes = normalise_signature(signature)
    try:
        credit = decode_public_credit_header(public_credit)
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc

    # The one key, and it must be proved. Absent rather than forbidden is not an option — it is
    # the caller's own key, so naming the problem is the whole value of the refusal.
    if not await account_store.owns_wallet(session, principal.account.id, coldkey):
        raise Conflict(
            "link that coldkey to your account before submitting with it",
            reason_code=REASON_WALLET_NOT_LINKED,
        )

    # Answered from durable state, and before the body: a client retrying after a lost response
    # should not have to upload the archive again to learn it already succeeded.
    #
    # Scoped by account rather than by the signing key, mirroring
    # `submissions_session_idempotency_unique`. It was keyed by the declared hotkey before V035,
    # which needed the cross-account guard below because the extrinsic path wrote rows under the
    # same hotkey with no account at all. Keyed by the account there is nothing to disambiguate:
    # the index this mirrors is per-account, and an extrinsic row can never match it.
    existing = await submission_store.find_session_submission_by_idempotency_key(
        session, principal.account.id, key
    )
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return await _confirmed(session, existing, settings=settings, now=now)

    entry = _resolve_task(services, task_id, task_bundle_sha256)

    # Two cheap refusals before an upload is accepted. Neither is authoritative — the quote
    # that counts is taken under a lock below, and the balance is re-read under the account lock
    # by `open_intent` — but an account with nothing to spend, or a target that is no longer a
    # bounty, should not be asked to send its bundle first.
    _assert_payable(
        await services.pricing.quote(session, reward_target_id=entry.reward_target_id),
        entry.reward_target_id,
    )
    balance = await credit_store.credit_balance(
        session,
        principal.account.id,
        credit_price_rao=settings.payment_amount_rao,
        now=now,
    )
    if balance.credits_available < 1:
        raise Conflict(
            "not enough credits for another verification attempt",
            reason_code=intent_store.REASON_INSUFFICIENT_CREDITS,
            extra={"credits_available": balance.credits_available, "credits_required": 1},
        )

    # There is no chain read left on this path. V035 removed the last one — a declared payout
    # hotkey had to be checked against `SubtensorModule.Owner`, because the payout extrinsic
    # could not stake to an address nobody owned and a submission we could not pay was worse
    # than one we refused. With no declared hotkey, every check on this path is answerable from
    # local state, and the endpoint no longer has an outage mode that belongs to the chain.

    bundle = await uploaded_bundle(
        request,
        services,
        entry,
        signer=coldkey,
        content_type=content_type,
        content_length=content_length,
    )
    # Reported as its own mismatch rather than left to fail as a bad signature. The signature
    # check below would catch it — the rebuilt message carries the digest of what arrived — but
    # "the archive is not the one you signed for" is a fixable answer and `SIGNATURE_INVALID` is
    # not. The same reason `X-Conjectures-Proof-Sha256` is compared on the extrinsic path.
    if bundle.sha256 != declared_bundle:
        raise BadRequest(
            "the uploaded archive does not match bundle_sha256",
            reason_code=REASON_BUNDLE_DIGEST_MISMATCH,
            extra={"bundle_sha256": bundle.sha256},
        )

    # Rebuilt from what the server holds: the digest of the bytes it just read, and the task
    # digest from its own allowlist entry. Never from the query, which is only ever checked
    # against this.
    message = web_submission_message(
        domain=settings.login_domain,
        address=coldkey,
        task_id=entry.task_id,
        task_bundle_sha256=entry.task_bundle_sha256,
        bundle_sha256=bundle.sha256,
        idempotency_key=str(key),
        expires_at=signed_until,
    )
    try:
        verify_signature(address=coldkey, message=message, signature=signature_bytes)
    except Unauthorized as exc:
        # Echo the exact bytes the server rebuilt. `SIGNATURE_INVALID` on its own is
        # undiagnosable on this path in a way it is not on the others: everywhere else the server
        # minted the message and the client signed what it was given, so a failure means a bad
        # key. Here the client *built* the message, so a failure usually means the two strings
        # differ by a character — most often the `domain:` line — and a diff answers it in
        # seconds where a bare reason code sends someone re-reading their wallet integration.
        #
        # Nothing here is a secret or an oracle. Every line is a value this caller just sent, or
        # `signing_domain`, which `GET /v1/catalog/submission-terms` serves unauthenticated. The
        # signature is not echoed, and knowing the message does not help forge one.
        raise Unauthorized(
            exc.detail,
            reason_code=exc.reason_code,
            extra={**exc.extra, "expected_message": message},
        ) from exc

    # Money from here down, and nothing before this point has touched it.
    intent, _ = await intent_store.open_intent(
        session,
        account_id=principal.account.id,
        task_id=entry.task_id,
        task_bundle_sha256=entry.task_bundle_sha256,
        credit_price_rao=settings.payment_amount_rao,
        expires_at=now + dt.timedelta(minutes=settings.intent_minutes),
        now=now,
        public_credit=credit,
        signer_coldkey=coldkey,
    )
    # The intent is the row the schema requires behind a credit-funded submission — see
    # `submission_credit_path_is_complete` — and it is also what carries the proof bytes into
    # the confirming transaction. Opened and confirmed inside this one request, so it is never
    # a state a client has to manage; it stays visible in the ledger as the SPEND's intent.
    await intent_store.attach_bundle(
        session,
        intent,
        proof_content=bundle.proof.raw,
        proof_sha256=bundle.proof.sha256,
        # The digest of the exact bytes that were signed. On the extrinsic and intent paths the
        # stored signature is over the stored request digest itself; here it is over the
        # digest's preimage, so the row still says what was authorised and by which key —
        # `signer_coldkey` names the key, and the accepted event records the message verbatim.
        request_digest=sha256_bytes(message.encode("utf-8")),
        now=now,
    )

    # Serializes the remaining-balance calculation with every other submission until this
    # transaction commits. The amount returned is the permanent bounty lock, not an estimate.
    quote = await services.pricing.lock_quote(
        session, reward_target_id=entry.reward_target_id
    )
    _assert_payable(quote, entry.reward_target_id)

    confirmed = await intent_store.confirm(
        session,
        intent.id,
        principal.account.id,
        problem_id=entry.problem_id,
        reward_target_id=entry.reward_target_id,
        task_mode=TaskMode(entry.mode),
        signer_signature=signature_bytes,
        manual_review_required=settings.manual_review_enabled,
        review_policy_version=review_policy_for_track(entry.manifest.track, settings.review_policy_version),
        bounty_amount_rao=quote.amount_rao,
        bounty_policy_version=quote.policy_version,
        bounty_inputs=dict(quote.inputs) if quote.inputs else None,
        now=now,
        # The intent was minted by this request, so it is no idempotency handle for a client
        # that has to retry. The key it chose is, and `submissions_idempotency_unique` is what
        # makes the retry a conflict rather than a second charge.
        idempotency_key=key,
    )
    # Append-only, and the only place the exact authorised bytes are kept. `request_digest` on
    # the submission is this message's digest; a reader checking the stored signature years from
    # now needs the preimage, and reconstructing it would mean trusting a formatter to have not
    # changed. `signature` is not recorded here — it is on the submission row.
    await intent_store.record_event(
        session,
        confirmed.submission.id,
        kind="AUTHORISED_BY_COLDKEY",
        detail="Submitted from the website and signed by a linked coldkey.",
        context={"signer_coldkey": coldkey, "signed_message": message},
    )

    await services.dispatcher.dispatch(session, confirmed.submission, entry.task_dir)
    await session.commit()

    # After the commit that made the debit and the submission atomic. Same event type both other
    # paths emit, distinguished by `funding`, so "how many submissions did we take" stays one
    # query across all three ways in.
    get_axiom().info(
        source="api-intents",
        event_type="submission_accepted",
        submission_id=str(confirmed.submission.id),
        account_id=str(principal.account.id),
        signer_coldkey=coldkey,
        task_id=entry.task_id,
        problem_id=entry.problem_id,
        reward_target_id=entry.reward_target_id,
        task_mode=entry.mode,
        funding="credit-web",
        proof_sha256=bundle.proof.sha256,
        proof_bytes=len(bundle.proof.raw),
        bounty_amount_rao=quote.amount_rao,
        bounty_policy_version=quote.policy_version,
        manual_review_required=settings.manual_review_enabled,
    )

    view = await submission_store.load_view(session, confirmed.submission)
    return schemas.ConfirmedSubmission(
        submission=await submission_detail(session, view),
        credits=_balance(confirmed.balance),
    )


@router.post(
    "/session",
    response_model=schemas.ConfirmedSubmission,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a bundle authorised by the browser session alone, with no Bittensor key",
)
async def create_session_submission(
    request: Request,
    response: Response,
    principal: CookieWriterDep,
    services: ServicesDep,
    session: SessionDep,
    task_id: Annotated[str, Query(min_length=1, max_length=255)],
    task_bundle_sha256: Annotated[str, Query(min_length=71, max_length=71)],
    bundle_sha256: Annotated[str, Query(min_length=71, max_length=71)],
    idempotency_key: Annotated[str, Query(min_length=36, max_length=36)],
    public_credit: Annotated[str | None, Query(max_length=4096)] = None,
    content_length: Annotated[int | None, Header(alias="Content-Length")] = None,
    content_type: Annotated[str | None, Header(alias="Content-Type")] = None,
) -> schemas.ConfirmedSubmission:
    """The fourth way in: a credit, a bundle, and a signed-in browser. No key of any kind.

    **What authorises this is the session**, which is why it is `CookieWriterDep` and not
    `WriterDep`: an account whose key is on a mining box has the three-call flow already. The
    account that spent the credit is the account that submitted, and the schema says so —
    `submission_authorised_exactly_once` requires an account and a credit on exactly the rows
    that name no key.

    **It claims no identity, and cannot borrow one.** `signer_coldkey` is null on the row, and
    `admit_proof_bundle` is called with `expected_signer=None`, which *refuses* a manifest naming
    a miner rather than ignoring it. Without that, anyone could publish a solved conjecture under
    somebody else's address, because the solver identity is what credits a result.

    **A reward it wins waits rather than misfires.** `payout_notifier` resolves its destination
    from `Account.payout_coldkey` and skips a row where that is null, so this submission is
    simply not paid until its account sets one — at which point the next poll picks it up.
    Nothing here needs to know that; it is worth stating because the alternative people assume
    is that the payout crashes.

    One step the coldkey path has is absent, because there is no key: nothing verifies a
    signature. Neither path touches an external service any more — V035 removed the chain read
    that used to make the coldkey path the exception.
    """
    settings = services.settings
    now = _now()

    if settings.submissions_paused:
        raise ServiceUnavailable(
            "submissions are paused; see GET /v1/system/status",
            reason_code=REASON_SUBMISSIONS_PAUSED,
        )

    key = _require_uuid(idempotency_key, "idempotency_key")
    task_bundle_sha256 = _require_digest(task_bundle_sha256, "task_bundle_sha256")
    declared_bundle = _require_digest(bundle_sha256, "bundle_sha256")
    if TASK_ID.fullmatch(task_id) is None:
        raise BadRequest("task_id is malformed")
    try:
        credit = decode_public_credit_header(public_credit)
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc

    # Scoped to the account, mirroring the partial unique index that enforces it. The
    # `(signer_coldkey, idempotency_key)` index the extrinsic path uses does not constrain these
    # rows at all — they have no signer, and PostgreSQL treats NULLs as distinct — so the lookup
    # and the index have to agree on the same key or a retry would buy a second attempt.
    existing = await submission_store.find_session_submission_by_idempotency_key(
        session, principal.account.id, key
    )
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return await _confirmed(session, existing, settings=settings, now=now)

    entry = _resolve_task(services, task_id, task_bundle_sha256)

    _assert_payable(
        await services.pricing.quote(session, reward_target_id=entry.reward_target_id),
        entry.reward_target_id,
    )
    balance = await credit_store.credit_balance(
        session,
        principal.account.id,
        credit_price_rao=settings.payment_amount_rao,
        now=now,
    )
    if balance.credits_available < 1:
        raise Conflict(
            "not enough credits for another verification attempt",
            reason_code=intent_store.REASON_INSUFFICIENT_CREDITS,
            extra={"credits_available": balance.credits_available, "credits_required": 1},
        )

    bundle = await uploaded_bundle(
        request,
        services,
        entry,
        signer=None,
        content_type=content_type,
        content_length=content_length,
    )
    if bundle.sha256 != declared_bundle:
        raise BadRequest(
            "the uploaded archive does not match bundle_sha256",
            reason_code=REASON_BUNDLE_DIGEST_MISMATCH,
            extra={"bundle_sha256": bundle.sha256},
        )

    # Money from here down, and nothing before this point has touched it.
    intent, _ = await intent_store.open_intent(
        session,
        account_id=principal.account.id,
        signer_coldkey=None,
        task_id=entry.task_id,
        task_bundle_sha256=entry.task_bundle_sha256,
        credit_price_rao=settings.payment_amount_rao,
        expires_at=now + dt.timedelta(minutes=settings.intent_minutes),
        now=now,
        public_credit=credit,
    )
    await intent_store.attach_bundle(
        session,
        intent,
        proof_content=bundle.proof.raw,
        proof_sha256=bundle.proof.sha256,
        # The canonical request, which is what this column has always meant. On the key-signed
        # paths the same value doubles as the bytes that were signed; here it keeps only its
        # first job, telling a replay from a conflict.
        request_digest=submission_store.session_request_digest(
            account_id=str(principal.account.id),
            task_id=entry.task_id,
            task_bundle_sha256=entry.task_bundle_sha256,
            proof_sha256=bundle.proof.sha256,
            idempotency_key=str(key),
            public_credit=credit,
        ),
        now=now,
    )

    quote = await services.pricing.lock_quote(
        session, reward_target_id=entry.reward_target_id
    )
    _assert_payable(quote, entry.reward_target_id)

    confirmed = await intent_store.confirm(
        session,
        intent.id,
        principal.account.id,
        problem_id=entry.problem_id,
        reward_target_id=entry.reward_target_id,
        task_mode=TaskMode(entry.mode),
        signer_signature=None,
        manual_review_required=settings.manual_review_enabled,
        review_policy_version=review_policy_for_track(entry.manifest.track, settings.review_policy_version),
        bounty_amount_rao=quote.amount_rao,
        bounty_policy_version=quote.policy_version,
        bounty_inputs=dict(quote.inputs) if quote.inputs else None,
        now=now,
        idempotency_key=key,
    )
    await intent_store.record_event(
        session,
        confirmed.submission.id,
        kind="AUTHORISED_BY_SESSION",
        detail="Submitted from the website and authorised by the account session.",
        context={"account_id": str(principal.account.id)},
    )

    await services.dispatcher.dispatch(session, confirmed.submission, entry.task_dir)
    await session.commit()

    get_axiom().info(
        source="api-intents",
        event_type="submission_accepted",
        submission_id=str(confirmed.submission.id),
        account_id=str(principal.account.id),
        signer_coldkey=None,
        task_id=entry.task_id,
        problem_id=entry.problem_id,
        reward_target_id=entry.reward_target_id,
        task_mode=entry.mode,
        funding="credit-session",
        proof_sha256=bundle.proof.sha256,
        proof_bytes=len(bundle.proof.raw),
        bounty_amount_rao=quote.amount_rao,
        bounty_policy_version=quote.policy_version,
        manual_review_required=settings.manual_review_enabled,
    )

    view = await submission_store.load_view(session, confirmed.submission)
    return schemas.ConfirmedSubmission(
        submission=await submission_detail(session, view),
        credits=_balance(confirmed.balance),
    )


async def _confirmed(
    session, submission, *, settings, now: dt.datetime
) -> schemas.ConfirmedSubmission:
    """The replay answer: the original submission, and the balance as it stands now.

    The balance is read fresh rather than reconstructed as it was at the time. It is a live
    figure everywhere else it appears, and a stale one here would be a different lie for every
    retry.
    """
    view = await submission_store.load_view(session, submission)
    balance = await credit_store.credit_balance(
        session,
        submission.account_id,
        credit_price_rao=settings.payment_amount_rao,
        now=now,
    )
    return schemas.ConfirmedSubmission(
        submission=await submission_detail(session, view),
        credits=_balance(balance),
    )


def _balance(balance) -> schemas.CreditBalance:
    return schemas.CreditBalance(
        credits_available=balance.credits_available,
        balance_rao=balance.balance_rao,
        held_rao=balance.held_rao,
        remainder_rao=balance.remainder_rao,
        credit_price_rao=balance.credit_price_rao,
        low_balance=balance.low_balance,
    )


__all__ = ["WEB_PATH", "router"]
