"""The website submission path: one call, one coldkey signature, one credit.

The third way into intake, and it exists because of one fact about browser wallets: Talisman and
the tao.com extension hold **coldkeys only**. A hotkey lives unencrypted on a mining box — that
is the point of the coldkey/hotkey split — and it never reaches the browser. So neither of the
existing paths can be driven from a web page:

* `POST /v1/submissions` wants a hotkey signature over the request digest, and a payment
  reference for a transfer the page cannot make;
* `POST /v1/submissions/intents` + `PUT .../bundle` + `POST .../confirm` also ends in a hotkey
  signature, over 32 raw bytes — which is both the wrong key and a thing a message-signing
  wallet will not render.

What changes here: **the authorising signature is made by a coldkey linked to the account, over a
readable message, the whole attempt is one request, and no hotkey has to be linked.** Everything
else is the credit path unchanged — the credit is held, the bundle is admitted by the same
exact-shape scanner, the bounty is locked by the same serialized quote, and the debit and the
submission are written by the same `intents.confirm` transaction. Reusing that is deliberate: it
is the money path, and a second implementation of it is a second chance to get atomicity wrong.

**The hotkey is declared, not proved, and that is the point of the whole endpoint.** An account
opened with a browser wallet has a coldkey and nothing else; requiring proof of a hotkey would
make this path unusable for exactly the person it exists for. It is safe to leave unproved
because Alpha is held as **stake owned by the coldkey** — nominating a hotkey chooses which
neuron the reward is staked to, not who owns it, and the coldkey it is staked for is the one that
signed. So the declaration cannot misdirect money; the only thing it could misdirect is *credit*,
which is why a hotkey another account has proved control of is refused. See the handler.

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
2. the credential, and the two keys it claims: a caller who may not act as this coldkey or this
   hotkey learns that before the server does catalog work for them;
3. idempotency replay — a retry is answered from durable state without re-uploading;
4. the balance and a first bounty quote, so an account with nothing to spend is refused before
   it uploads 2 MiB;
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
# Not `HOTKEY_NOT_LINKED`, which is the intent path's refusal and means the opposite thing:
# there, a hotkey has to be linked to *this* account. Here it only has to not be linked to
# another one, so the two codes must not be confused for each other by a client.
REASON_HOTKEY_CLAIMED = "HOTKEY_CLAIMED_BY_ANOTHER_ACCOUNT"
# The chain has no owner for it, so nothing can be staked to it. A third distinct code, because
# the fix is a third distinct thing: register the hotkey, or nominate one that exists.
REASON_HOTKEY_NOT_REGISTERED = "HOTKEY_NOT_REGISTERED"
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

    Two-sided, like `assert_fresh_nonce` on the hotkey paths and for the same reason: an
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
    hotkey: Annotated[str, Query(min_length=48, max_length=48)],
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

    **A browser session, not a CLI one.** `CookieWriterDep` refuses a bearer token outright, and
    that costs the CLI nothing: a bearer session is minted by a hotkey and scoped to it, so it
    already has the three-call flow and no coldkey to sign with here. What it buys is that the
    one intake path authorised by a coldkey cannot be driven by a credential read off a mining
    box — the same rule that keeps hotkey linking and the payout destination browser-only.

    **The coldkey must be linked; the hotkey must not be somebody else's.** The two checks are
    deliberately asymmetric because the two keys do different jobs.

    The coldkey is *who authorised the spend and who owns the reward*. It must be linked, because
    a signature proves control of a key and not that the key belongs to this account — without
    the check, anyone who captured a signature could spend their own credits under somebody
    else's authorisation and have the payout follow that key.

    The hotkey is only a *delegation target*: the reward is staked for the coldkey, and the
    hotkey names the neuron it is staked to. It is therefore declared and never proved — a
    browser wallet has no hotkey to sign with, and demanding one would defeat the endpoint. Two
    checks bound the declaration, and neither is a proof of control:

    * no **other account here** has proved control of it, because `ResultRow.hotkey` is published
      and a result is credited to its solver, so an unchecked declaration could steal credit;
    * the **chain knows it**, because the payout extrinsic cannot stake to a hotkey with no owner
      and a submission we cannot pay is worse than one we refuse.

    An unclaimed, registered hotkey is free to nominate, and this submission's reward will be
    staked to it for the coldkey that signed.

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

    # The one key that must be proved: the coldkey is what authorises the spend, and it is where
    # the reward lands. Absent rather than forbidden is not an option — it is the caller's own
    # key, so naming the problem is the whole value of the refusal.
    if not await account_store.owns_wallet(session, principal.account.id, coldkey):
        raise Conflict(
            "link that coldkey to your account before submitting with it",
            reason_code=REASON_WALLET_NOT_LINKED,
        )
    # The hotkey is *not* proved, and deliberately. It is a delegation target: Alpha is held as
    # stake owned by the coldkey, so nominating a hotkey chooses which neuron the reward is
    # staked to, not who owns it. Requiring proof of it would make the whole path unusable for
    # exactly the person it exists for — a browser wallet holds no hotkey to sign with.
    #
    # One thing it must not be is somebody else's *claimed* identity. `ResultRow.hotkey` is
    # published and a result is credited to its solver, so an unchecked declaration would let
    # anyone credit a solved conjecture to a hotkey another account has proved control of.
    # A hotkey nobody has claimed stays free to nominate.
    owner = await account_store.find_by_hotkey(session, hotkey)
    if owner is not None and owner.id != principal.account.id:
        raise Conflict(
            "that hotkey is linked to another account; nominate one of your own",
            reason_code=REASON_HOTKEY_CLAIMED,
        )

    # Answered from durable state, and before the body: a client retrying after a lost response
    # should not have to upload the archive again to learn it already succeeded.
    existing = await submission_store.find_by_idempotency_key(session, hotkey, key)
    if existing is not None:
        # The lookup is keyed by `(hotkey, idempotency_key)`, which is the uniqueness the schema
        # enforces — and the extrinsic path writes rows with the same hotkey and no account at
        # all. A key that names one of those is a genuine collision rather than this caller's
        # earlier attempt, and answering it as a replay would report a submission with no
        # balance to read beside it.
        if existing.account_id != principal.account.id:
            raise Conflict(
                "that idempotency key already names a submission made another way",
                reason_code="IDEMPOTENCY_CONFLICT",
                extra={"idempotency_key": str(key)},
            )
        response.status_code = status.HTTP_200_OK
        return await _confirmed(session, existing, settings=settings, now=now)

    entry = _resolve_task(services, task_id, task_bundle_sha256)

    # Two cheap refusals before a 2 MiB upload is accepted. Neither is authoritative — the quote
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

    # The one chain read on this path, and it is deliberately last among the free checks: it costs
    # a round trip, so everything answerable from local state — the credential, the two hotkey
    # rules, the replay, the task, the balance — is answered first.
    #
    # A declared hotkey is a payout instruction, and `transfer_stake_and_hotkey` cannot stake to a
    # hotkey the chain has never heard of. Refusing here means a mistyped address is caught while
    # the submitter is still looking at it, instead of weeks later as a payout command a human
    # signs and watches fail. Unreachable chain raises `ServiceUnavailable` rather than returning
    # False — see `hotkeys.py` on why those must not collapse into one answer.
    if not await services.hotkeys.is_registered(hotkey):
        raise Conflict(
            "the chain knows no such hotkey; register it before nominating it for payout",
            reason_code=REASON_HOTKEY_NOT_REGISTERED,
            extra={"hotkey": hotkey},
        )

    bundle = await uploaded_bundle(
        request,
        services,
        entry,
        hotkey=hotkey,
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
        hotkey=hotkey,
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
        hotkey=hotkey,
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
        # The digest of the exact bytes that were signed. On the two hotkey paths the stored
        # signature is over the stored request digest itself; here it is over the digest's
        # preimage, so the row still says what was authorised and by which key —
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
        hotkey_signature=signature_bytes,
        manual_review_required=settings.manual_review_enabled,
        review_policy_version=settings.review_policy_version,
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
        hotkey=hotkey,
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
