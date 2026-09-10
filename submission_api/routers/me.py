"""The account: profile, linked coldkeys, payout destination, credits, and the miner panel.

Everything under `/v1/me` is served only to the signed-in owner of the data. The access control
is in the signature of each handler: `PrincipalDep` for a read, `WriterDep` for a write — the
latter also demanding that a browser session prove where the request was initiated. A
state-changing handler that names `PrincipalDep` is a cross-site request forgery hole, which is
why the two names do not look interchangeable.

There is a third name, `CookieWriterDep`, and it marks the writes a **CLI token may not make**.
Two credentials reach this router: a browser cookie, opened by a coldkey signature or by proving
control of a mailbox, and a CLI bearer token — a long-lived file on a mining machine, readable
by anything that can read the disk. They are not equal evidence of "this is the account
holder", so the changes that decide *who the account is* and *where its money goes* — linking a
coldkey, designating which one submits, setting the payout destination, editing the profile,
declaring or claiming a deposit — require the browser. Left open to a bearer token those
compose into account takeover from one stolen file: link an attacker's coldkey, repoint the
payout, collect. See `require_cookie_writer`.

Reads are open to both, but a bearer session sees a **redacted** account: no email address, no
payout destination, and only the one linked coldkey it is scoped to. See `account_response`.

`GET /v1/me/coldkeys` is the deliberate exception on the read side. It returns the two
addresses and nothing else, to either credential, so a client does not have to fetch the whole
account record to find out which key it signs as.

Ownership is enforced in the query, not after it. Every store call takes the account id and
scopes on it, and a row belonging to someone else is reported **absent** rather than forbidden
— so an identifier cannot be probed for existence by watching which error comes back.

Nothing under this prefix is cacheable. A balance, a ledger page or a submission list is
caller-specific by definition, so every response here sets `no-store`: the public surface's
`Cache-Control: public` would be a cross-account disclosure through any shared cache.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Path, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from conjectures_subnet.axiom import get_axiom
from conjectures_subnet.db import accounts as account_store
from conjectures_subnet.db import credits as credit_store
from conjectures_subnet.db import digests
from conjectures_subnet.db import intents as intent_store
from conjectures_subnet.db import submissions as submission_store
from conjectures_subnet.db.models import (
    AccountSessionKind,
    DepositState,
    LoginChallengeKind,
)
from submission_api import credits as credit_config
from submission_api import login, schemas_account as schemas, sessions
from submission_api.dependencies import (
    CookieWriterDep,
    PrincipalDep,
    ServicesDep,
    SessionDep,
    WriterDep,
)
from submission_api.errors import Conflict, NotFound, TooManyRequests, Unauthorized
from submission_api.pagination import decode_cursor, encode_cursor
from submission_api.routers._account import (
    account_response,
    decode_id_cursor,
    encode_id_cursor,
    page_of,
    session_view,
    submission_detail,
    submission_summary,
    utc,
)
from submission_api.settings import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    RAO_PER_TAO,
    Settings,
)
from verifier.bundle import SS58_ADDRESS

router = APIRouter(prefix="/v1/me", tags=["account"])

REASON_TOO_MANY_CHALLENGES = "TOO_MANY_CHALLENGES"
REASON_WALLET_NOT_LINKED = "WALLET_NOT_LINKED"
REASON_NO_OPEN_DEPOSIT = "NO_OPEN_DEPOSIT"

# Where a confirmed transfer can be looked up. A property of the network rather than of this
# deployment, so it is a constant — and a null link is better than a wrong one.
EXPLORER_TEMPLATE = "https://taostats.io/extrinsic/{reference}"

# The per-purchase ceiling, taken from `credits` rather than restated. Both funding paths and
# the package parser have to agree on this number, and the way they stay agreed is by there
# being one of it.
MAX_DEPOSIT_CREDITS = credit_config.MAX_CREDITS_PER_PURCHASE
UUID_LENGTH = 36

# Upper bound on a nonce echoed back to `POST /wallets`. The value is a `sessions.new_token()`,
# about 43 characters at `TOKEN_BYTES = 32`; this only has to refuse an absurd body before it
# reaches a digest. Deliberately the same generous 256 as `routers/auth.py`'s MAX_TOKEN_LENGTH —
# restated rather than imported, to keep one router from depending on another for a bound.
MAX_NONCE_LENGTH = 256


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProfilePatch(Payload):
    display_name: str | None = Field(default=None, min_length=1, max_length=64)


class ColdkeyChallengeRequest(Payload):
    coldkey: str = Field(min_length=48, max_length=48)


class ColdkeyLinkRequest(Payload):
    coldkey: str = Field(min_length=48, max_length=48)
    # Echoed back, as the CLI session flow does it. The nonce is not the proof — the signature
    # is, and it is checked against the message stored on the row this nonce names — but naming
    # the row is what bounds the attempts against it and what stops one challenge from
    # superseding another. See `accounts.open_challenge_by_nonce`.
    nonce: str = Field(min_length=16, max_length=MAX_NONCE_LENGTH)
    signature: str = Field(min_length=128, max_length=132)


class PayoutRequest(Payload):
    """Where rewards go. One coldkey, and `null` to clear it.

    No signature field, and that is the design rather than an omission — see
    `put_payout_coldkey` for why proof of control would defend nothing here while refusing the
    ordinary destinations.
    """

    coldkey: str | None = Field(default=None, min_length=48, max_length=48)


class SubmissionColdkeyRequest(Payload):
    """Which already-linked coldkey submits. `null` clears the designation.

    Proof lives at `POST /v1/me/wallets`; this only chooses among keys already proved.
    """

    coldkey: str | None = Field(default=None, min_length=48, max_length=48)


class DepositRequest(Payload):
    """Declared in whole credits, not in rao.

    Buying credits is the operation; rao is how it is paid. Accepting an arbitrary rao amount
    would let someone deposit a non-whole number of credits and then ask why their balance shows
    a remainder.
    """

    credits: int = Field(ge=1, le=MAX_DEPOSIT_CREDITS)


class DepositClaimRequest(Payload):
    extrinsic_reference: str = Field(min_length=4, max_length=128)
    coldkey: str = Field(min_length=48, max_length=48)
    signature: str = Field(min_length=128, max_length=132)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _assert_ss58(value: str, field: str) -> str:
    if SS58_ADDRESS.fullmatch(value) is None:
        raise Unauthorized(
            f"{field} is not a valid SS58 address",
            reason_code=login.REASON_SIGNATURE_INVALID,
        )
    return value


def _signature_bytes(value: str) -> bytes:
    candidate = value.strip().removeprefix("0x").removeprefix("0X").lower()
    try:
        raw = bytes.fromhex(candidate)
    except ValueError as exc:
        raise Unauthorized(
            "signature must be 64 bytes of hex",
            reason_code=login.REASON_SIGNATURE_INVALID,
        ) from exc
    if len(raw) != 64:
        raise Unauthorized(
            "signature must be 64 bytes of hex",
            reason_code=login.REASON_SIGNATURE_INVALID,
        )
    return raw


def _as_uuid(value: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        # Absent, not malformed: the identifier space is opaque to the caller, and a distinct
        # error for "not a UUID" tells them nothing they can act on.
        raise NotFound(f"{what} not found") from exc


# --- Sessions ----------------------------------------------------------------------------
# The inventory and the kill switch. Without them a leaked CLI token has no remedy short of
# waiting for it to expire: signing in again deliberately does not revoke bearer sessions (see
# `auth._sign_in`), and `POST /v1/auth/logout` only ever revokes the caller's own row.


@router.get(
    "/sessions",
    response_model=tuple[schemas.SessionView, ...],
    summary="Every live session for this account",
)
async def list_sessions(
    response: Response, principal: PrincipalDep, session: SessionDep
) -> tuple[schemas.SessionView, ...]:
    """Both kinds, newest first, with the caller's own marked.

    A read, so a CLI session may list — a miner should be able to see from the machine they are
    on that a token exists on a machine they no longer recognise. It discloses no credential:
    `session_view` names its fields one at a time precisely so that the two digest columns on
    the row cannot leak into it by accident.
    """
    _no_store(response)
    rows = await account_store.live_sessions_for(
        session, principal.account.id, now=_now()
    )
    return tuple(session_view(row, current_id=principal.session.id) for row in rows)


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke one session",
)
async def revoke_session(
    session_id: Annotated[str, Path(min_length=UUID_LENGTH, max_length=UUID_LENGTH)],
    response: Response,
    principal: WriterDep,
    session: SessionDep,
) -> None:
    """Revoke a session belonging to this account, including the caller's own.

    `WriterDep`, not `CookieWriterDep`: revoking is the safe direction. Letting a CLI token kill
    sessions cannot be used to take an account over, and a miner who suspects a rig is
    compromised should be able to cut it off from the rig next to it without finding a browser.

    Ownership is in the UPDATE's predicate, and a row belonging to someone else answers 404 —
    the same as one that never existed. Anything else would let session ids be probed for
    existence, and they are the identifiers of live credentials.

    Revoking the current session is allowed and is not special-cased: it is `logout` by another
    name, and refusing it would be a rule with no purpose that a client would have to learn.
    """
    _no_store(response)
    revoked = await account_store.revoke_session_for_account(
        session, _as_uuid(session_id, "session"), principal.account.id
    )
    if not revoked:
        raise NotFound("no such session", reason_code="SESSION_NOT_FOUND")
    await session.commit()
    get_axiom().info(
        source="api-me",
        event_type="session_revoked",
        account_id=str(principal.account.id),
        reason="owner_request",
        by_session_kind=str(principal.session.kind),
    )


@router.delete(
    "/sessions",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke every other session",
)
async def revoke_other_sessions(
    response: Response,
    principal: WriterDep,
    session: SessionDep,
    kind: Annotated[
        str | None,
        Query(
            pattern="^(COOKIE|BEARER)$",
            description="Limit to one kind. Omit to revoke both.",
        ),
    ] = None,
) -> None:
    """Sign out everywhere else. The caller's own session survives.

    Keeping the caller alive is what makes this the button people actually want: "something is
    wrong, cut everything else off" should not also log them out of the page they are clicking
    it on. `logout` is how you end your own session.

    `kind` exists because the two credentials fail differently. A miner who suspects one rig is
    compromised wants every CLI token gone and their browser untouched; someone who left a
    session open on a shared machine wants the opposite.
    """
    _no_store(response)
    revoked = await account_store.revoke_all_sessions(
        session,
        principal.account.id,
        kind=AccountSessionKind(kind) if kind else None,
        except_session_id=principal.session.id,
    )
    await session.commit()
    get_axiom().info(
        source="api-me",
        event_type="session_revoked",
        account_id=str(principal.account.id),
        reason="revoke_others",
        revoked=revoked,
        kind=kind or "ALL",
        by_session_kind=str(principal.session.kind),
    )


# --- Profile -----------------------------------------------------------------------------


@router.get("", response_model=schemas.Account, summary="The signed-in account")
async def read_me(
    response: Response, principal: PrincipalDep, session: SessionDep
) -> schemas.Account:
    """Redacted for a CLI session: `coldkey_scope` is what decides, so a bearer caller cannot
    reach the email address or payout keys by asking a different endpoint for the same row."""
    _no_store(response)
    return await account_response(
        session, principal.account, bearer_scope=principal.coldkey_scope
    )


@router.patch("", response_model=schemas.Account, summary="Edit the display name")
async def patch_me(
    payload: ProfilePatch,
    response: Response,
    principal: CookieWriterDep,
    session: SessionDep,
) -> schemas.Account:
    """The only editable field.

    Not the email: changing the address a magic link goes to is a change of credential, and it
    has to be proved by receiving mail at the new address rather than asserted by a session that
    may itself be what is being abused. `/v1/auth/email/request-link` already does exactly that.

    Not the roles either — they are granted out of band and are never client input.
    """
    await account_store.set_display_name(
        session, principal.account, payload.display_name
    )
    await session.commit()
    _no_store(response)
    return await account_response(session, principal.account)


# --- Linked coldkeys, and the two the account designates ---------------------------------


@router.post(
    "/wallets/challenge",
    response_model=schemas.WalletChallenge,
    summary="A nonce for linking another coldkey",
)
async def coldkey_challenge(
    payload: ColdkeyChallengeRequest,
    principal: CookieWriterDep,
    services: ServicesDep,
    session: SessionDep,
) -> schemas.WalletChallenge:
    """Mint a single-use nonce bound to this account and coldkey.

    This is separate from wallet sign-in, so a signature collected while adding a wallet
    cannot be replayed as a login credential. The private key remains in the user's wallet;
    the service stores only the public address and the proof that it was controlled.

    The returned `nonce` must be echoed back to `POST /v1/me/wallets` alongside the signature.
    It names the row the signature is checked against, which is what lets two challenges for
    one coldkey coexist instead of the newer silently invalidating the one being signed.
    """
    settings = services.settings
    coldkey = _assert_ss58(payload.coldkey, "coldkey")
    now = _now()
    issued = await account_store.recent_challenge_count(
        session,
        kind=LoginChallengeKind.COLDKEY_LINK,
        since=now - dt.timedelta(hours=1),
        ss58=coldkey,
    )
    if issued >= settings.challenges_per_hour:
        raise TooManyRequests(
            "too many link challenges for that coldkey; try again later",
            reason_code=REASON_TOO_MANY_CHALLENGES,
        )

    nonce = sessions.new_token()
    expires_at = now + dt.timedelta(minutes=settings.challenge_minutes)
    message = login.coldkey_link_message(
        domain=settings.login_domain,
        address=coldkey,
        nonce=nonce,
        expires_at=expires_at,
    )
    await account_store.create_challenge(
        session,
        kind=LoginChallengeKind.COLDKEY_LINK,
        secret_digest=account_store.digest(nonce),
        expires_at=expires_at,
        account_id=principal.account.id,
        ss58=coldkey,
        message=message,
    )
    await session.commit()
    return schemas.WalletChallenge(nonce=nonce, message=message, expires_at=expires_at)


@router.post(
    "/wallets",
    response_model=schemas.Account,
    status_code=status.HTTP_201_CREATED,
    summary="Link another coldkey by signature",
)
async def link_coldkey(
    payload: ColdkeyLinkRequest,
    principal: CookieWriterDep,
    services: ServicesDep,
    session: SessionDep,
) -> schemas.Account:
    """Attach a coldkey the account proved control of.

    An account may have multiple coldkeys, but a coldkey belongs to exactly one account. There
    is deliberately no rebind or unlink operation: moving a login credential between accounts
    needs a separate recovery policy rather than a silent ownership change.

    The order is the CLI session flow's, for the CLI session flow's reasons:

    1. **Find the challenge by its own nonce**, bounded by the account that minted it, rather
       than taking the newest open row for the address. `accounts.open_challenge_by_nonce`
       explains why "which challenge is current" is the attackable question and "which
       challenge is this" is not.
    2. **Verify the signature over the stored message**, before anything is consumed. The
       message comes off the row and is never rebuilt from the request.
    3. **Count a failed attempt** if it did not verify, and refuse. The challenge survives a
       wrong signature — otherwise a mistyped passphrase costs a whole new challenge — but not
       unboundedly many, or an open challenge would be free sr25519 work.
    4. **Consume, then link.** The conditional UPDATE is what makes two simultaneous
       redemptions of one valid signature link the coldkey once.
    """
    settings = services.settings
    coldkey = _assert_ss58(payload.coldkey, "coldkey")
    signature = _signature_bytes(payload.signature)
    now = _now()

    challenge = await account_store.open_challenge_by_nonce(
        session,
        kind=LoginChallengeKind.COLDKEY_LINK,
        ss58=coldkey,
        secret_digest=account_store.digest(payload.nonce),
        now=now,
        max_attempts=settings.challenge_attempts,
        account_id=principal.account.id,
    )
    if challenge is None or challenge.message is None:
        # One refusal for expired, already-used, out-of-attempts, another account's nonce and
        # never-existed. The caller learns nothing about which, and the remedy is the same for
        # all of them: request a new challenge.
        raise Unauthorized(
            "no open link challenge for that coldkey and nonce; request a new one",
            reason_code=login.REASON_CHALLENGE_INVALID,
        )

    try:
        login.verify_signature(
            address=coldkey, message=challenge.message, signature=signature
        )
    except Unauthorized:
        # Counted and committed before re-raising, so the attempt is recorded even though the
        # request fails. Without the commit the increment would roll back with the response and
        # the ceiling would never be reached.
        await account_store.record_failed_attempt(session, challenge.id)
        await session.commit()
        raise

    consumed = await account_store.consume_challenge(
        session,
        kind=LoginChallengeKind.COLDKEY_LINK,
        secret_digest=bytes(challenge.secret_sha256),
        now=now,
    )
    if consumed is None:
        raise Unauthorized(
            "that challenge has already been used; request a new one",
            reason_code=login.REASON_CHALLENGE_INVALID,
        )

    await account_store.link_wallet(
        session, principal.account, coldkey=coldkey, signature=signature
    )
    await session.commit()
    get_axiom().info(
        source="api-me",
        event_type="wallet_linked",
        account_id=str(principal.account.id),
        kind="coldkey",
        coldkey=coldkey,
    )
    return await account_response(session, principal.account)


@router.get(
    "/coldkeys",
    response_model=schemas.AccountColdkeys,
    summary="The currently linked submission and payout coldkeys",
)
async def get_coldkeys(principal: PrincipalDep) -> schemas.AccountColdkeys:
    """The account's two coldkeys, either of which may be unset.

    One request for the pair, because a client almost always wants both: the account page shows
    them together, and the submit flow needs to know whether a submission key is designated
    before it offers a signing step. Reading them from `GET /v1/me` would work but returns the
    whole account, including its email and every linked wallet — more than a header or a submit
    button needs, and more than a CLI session is shown.

    Both are read straight off the account row and neither is derived from the other. `null`
    means "not set", never "not permitted"; the two `PUT`s below set them, and both are
    browser-only.

    **The asymmetry between them is the thing to understand, and it is not an inconsistency.**
    `submission_coldkey` is proved and is always one of the account's linked wallets, because it
    signs submissions, spends credits, and on the extrinsic path cites a transfer — a key that
    can spend has to be proved. `payout_coldkey` is unproved and need not be linked at all,
    because naming it can only give this account's own money away.

    Available to a CLI session as well as a browser one. `submission_coldkey` is the key that
    token already signs as, and `payout_coldkey` is a destination the CLI cannot change; a token
    that could not read either would have to discover its own signing identity by being
    refused. Note this differs from `GET /v1/me`, which redacts the payout destination for a
    bearer session — that response is the whole account record and a stolen token reading it
    learns the email address too, whereas this one is two addresses and nothing else.
    """
    account = principal.account
    return schemas.AccountColdkeys(
        submission_coldkey=account.submission_coldkey,
        payout_coldkey=account.payout_coldkey,
    )


@router.put(
    "/coldkeys/submission",
    response_model=schemas.Account,
    summary="Designate which linked coldkey this account submits under",
)
async def put_submission_coldkey(
    payload: SubmissionColdkeyRequest,
    principal: CookieWriterDep,
    session: SessionDep,
) -> schemas.Account:
    """Designate one already-linked coldkey as the key this account submits and spends under.

    **This designates; it does not prove.** Proof happened when the key was linked at
    `POST /v1/me/wallets`, which is where the signature over a server nonce lives. Designating
    an unlinked key is refused by `account_submission_coldkey_is_linked`, a composite foreign
    key against `(account_id, coldkey)` — so the check is race-free in a way a read-then-write
    could not be: a caller cannot unlink the wallet between a check and this update.

    `coldkey: null` clears the designation. That does not orphan anything already submitted —
    a submission records the key that signed it — it only stops the intent flow until another is
    designated.

    Browser-only, like every write that changes what the account *is*. A CLI token is scoped to
    one coldkey, and letting it re-point the designation would let a token minted for one key
    arrange for the account's work to be signed as another.
    """
    coldkey = (
        None if payload.coldkey is None else _assert_ss58(payload.coldkey, "coldkey")
    )
    if coldkey is not None and not await account_store.owns_wallet(
        session, principal.account.id, coldkey
    ):
        # Checked here as well as by the foreign key, and only to name the fix. The constraint
        # is the authority; this is what turns "integrity error" into "link it first".
        raise Conflict(
            "link that coldkey to your account before submitting with it",
            reason_code=login.REASON_COLDKEY_NOT_LINKED,
        )
    # Every live bearer token scoped to the key being replaced is revoked. Each of those was
    # minted to submit as that key, and after this it no longer can — so the token's basis is
    # gone and it must stop working now rather than fail confusingly on its next submission.
    # `dependencies.py` re-checks the link on every bearer request, which covers an unlink; a
    # re-designation leaves the wallet linked, so it needs this.
    previous = principal.account.submission_coldkey
    await account_store.set_submission_coldkey(
        session, principal.account, coldkey=coldkey
    )
    if previous is not None and previous != coldkey:
        await account_store.revoke_sessions_for_coldkey(session, previous)
    await session.commit()
    get_axiom().info(
        source="api-me",
        event_type="submission_coldkey_set",
        account_id=str(principal.account.id),
        coldkey=coldkey,
    )
    return await account_response(session, principal.account)


@router.put(
    "/coldkeys/payout",
    response_model=schemas.Account,
    summary="Set where rewards are sent",
)
async def put_payout_coldkey(
    payload: PayoutRequest, principal: CookieWriterDep, session: SessionDep
) -> schemas.Account:
    """One coldkey, and deliberately no proof of control over it.

    **Why no signature, when this is the field a takeover would target.** Proof of control
    would not defend it. An attacker who can reach this endpoint holds a browser session for
    the account, and can equally sign with a key they generated a moment ago — so a signature
    requirement stops nobody and instead refuses the ordinary destinations: a hardware wallet,
    an exchange deposit address, a multisig the account does not solely control. What actually
    defends this field is the credential needed to reach it, which is why it is
    `CookieWriterDep` and out of a CLI token's reach entirely, and why the change is recorded
    on the audit stream below.

    This replaced a rule that required the payout hotkey to be linked to the account. That rule
    bought the appearance of proof — an attacker with a session could link their own key first
    — while making the common legitimate case impossible.

    `coldkey: null` clears it. A reward with no destination is skipped by the payout notifier
    rather than sent anywhere, and is picked up on the next poll once one is set; "awarded,
    waiting for somewhere to send it" is a state the pipeline already has.
    """
    coldkey = (
        None if payload.coldkey is None else _assert_ss58(payload.coldkey, "coldkey")
    )
    previous = principal.account.payout_coldkey
    await account_store.set_payout_coldkey(
        session, principal.account, coldkey=coldkey
    )
    await session.commit()
    # After the commit, and carrying both addresses. This is the field that turns a session
    # compromise into permanent theft of an account's rewards, so where the money used to go
    # has to be recoverable from the audit stream and not only from the row that was overwritten.
    get_axiom().info(
        source="api-me",
        event_type="payout_coldkey_set",
        account_id=str(principal.account.id),
        coldkey=coldkey,
        previous_coldkey=previous,
    )
    return await account_response(session, principal.account)


# --- Credits -----------------------------------------------------------------------------


def _balance(balance: credit_store.CreditBalance) -> schemas.CreditBalance:
    return schemas.CreditBalance(
        credits_available=balance.credits_available,
        balance_rao=balance.balance_rao,
        held_rao=balance.held_rao,
        remainder_rao=balance.remainder_rao,
        credit_price_rao=balance.credit_price_rao,
        low_balance=balance.low_balance,
    )


@router.get(
    "/credits", response_model=schemas.CreditBalance, summary="The credit balance"
)
async def read_credits(
    response: Response,
    principal: PrincipalDep,
    services: ServicesDep,
    session: SessionDep,
) -> schemas.CreditBalance:
    _no_store(response)
    return _balance(
        await credit_store.credit_balance(
            session,
            principal.account.id,
            credit_price_rao=services.settings.payment_amount_rao,
            now=_now(),
        )
    )


@router.get(
    "/credits/ledger",
    response_model=schemas.CursorPage[schemas.CreditLedgerEntry],
    summary="The append-only credit ledger",
)
async def read_ledger(
    response: Response,
    principal: PrincipalDep,
    services: ServicesDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=256)] = None,
) -> schemas.CursorPage[schemas.CreditLedgerEntry]:
    settings = services.settings
    rows = await credit_store.ledger_page(
        session,
        principal.account.id,
        limit=limit + 1,
        after_id=decode_id_cursor(settings, cursor),
    )
    page, more = page_of(list(rows), limit=limit)

    # A SPEND names its intent rather than its submission — see the note on
    # `credit_ledger.intent_id` — so the submission a debit paid for is one hop away.
    submissions = await intent_store.submission_ids_for(
        session, [row.intent_id for row in page if row.intent_id is not None]
    )

    _no_store(response)
    return schemas.CursorPage[schemas.CreditLedgerEntry](
        items=tuple(
            schemas.CreditLedgerEntry(
                id=row.id,
                kind=str(row.kind),
                amount_rao=row.amount_rao,
                credit_price_rao=row.credit_price_rao,
                deposit_id=row.deposit_id,
                submission_id=submissions.get(row.intent_id),
                reason=row.reason,
                created_at=utc(row.created_at),
            )
            for row in page
        ),
        next_cursor=encode_id_cursor(settings, page[-1].id) if more and page else None,
    )


def _deposit(deposit, *, settings: Settings) -> schemas.Deposit:
    return schemas.Deposit(
        id=deposit.id,
        status=str(deposit.status),
        amount_rao=deposit.amount_rao,
        credited_rao=deposit.observed_amount_rao,
        treasury_address=deposit.treasury_address,
        credit_price_rao=deposit.credit_price_rao,
        credits_expected=deposit.amount_rao // deposit.credit_price_rao,
        btcli_command=credit_config.btcli_command(
            treasury=deposit.treasury_address,
            amount_rao=deposit.amount_rao,
            rao_per_tao=RAO_PER_TAO,
        ),
        extrinsic_reference=deposit.extrinsic_reference,
        block=deposit.block,
        failure_reason=deposit.failure_reason,
        expires_at=utc(deposit.expires_at),
        created_at=utc(deposit.created_at),
        updated_at=utc(deposit.updated_at),
    )


@router.post(
    "/deposits",
    response_model=schemas.Deposit,
    status_code=status.HTTP_201_CREATED,
    summary="Declare one credit purchase for a wallet-extension transfer",
)
async def create_deposit(
    payload: DepositRequest,
    principal: CookieWriterDep,
    services: ServicesDep,
    session: SessionDep,
) -> schemas.Deposit:
    """Record the one-credit transfer the browser wallet will submit.

    Nothing is credited here. This records the expectation, so that when a transfer is seen it
    can be checked against a declared amount and recipient rather than credited on trust. What
    is eventually credited is the amount observed on chain, not this one.
    """
    settings = services.settings
    deposit = await credit_store.create_deposit(
        session,
        account_id=principal.account.id,
        amount_rao=payload.credits * settings.payment_amount_rao,
        treasury_address=settings.payment_recipient,
        credit_price_rao=settings.payment_amount_rao,
        expires_at=_now() + dt.timedelta(hours=settings.deposit_hours),
    )
    await session.commit()
    return _deposit(deposit, settings=settings)


@router.get(
    "/deposits/{deposit_id}",
    response_model=schemas.Deposit,
    summary="One deposit's state",
)
async def read_deposit(
    deposit_id: Annotated[str, Path(min_length=UUID_LENGTH, max_length=UUID_LENGTH)],
    response: Response,
    principal: PrincipalDep,
    services: ServicesDep,
    session: SessionDep,
) -> schemas.Deposit:
    deposit = await credit_store.get_deposit(
        session, _as_uuid(deposit_id, "deposit"), principal.account.id
    )
    _no_store(response)
    return _deposit(deposit, settings=services.settings)


@router.post(
    "/deposits/claim",
    response_model=schemas.Deposit,
    summary="Claim a transfer the reconciler did not attribute",
)
async def claim_deposit(
    payload: DepositClaimRequest,
    principal: CookieWriterDep,
    services: ServicesDep,
    session: SessionDep,
) -> schemas.Deposit:
    """Attach a transfer to an open deposit. Issues no credits.

    Two things have to be true before credits exist, and this endpoint can establish only one:

    * the claimant controls the sending coldkey — proved here by the signature;
    * the transfer is finalized, went to the treasury, and is not already spoken for — a chain
      read, needing the finalized-transfer reader this deployment does not yet have.

    So the claim records the reference against the deposit as `SEEN_UNFINALIZED` and stops
    there. Crediting on a caller's assertion is the one outcome that must never be possible, and
    the honest intermediate state is "we can see what you are pointing at, we have not confirmed
    it".
    """
    settings = services.settings
    coldkey = _assert_ss58(payload.coldkey, "coldkey")
    signature = _signature_bytes(payload.signature)

    # The coldkey must already be linked to *this* account. Without it, anyone could claim a
    # stranger's transfer by quoting its publicly visible extrinsic reference.
    owner = await account_store.find_by_coldkey(session, coldkey)
    if owner is None or owner.id != principal.account.id:
        raise Conflict(
            "link that wallet to your account before claiming a transfer from it",
            reason_code=REASON_WALLET_NOT_LINKED,
        )

    # Its own domain-separated prefix, with the extrinsic reference as the nonce: a captured
    # sign-in signature is not a claim, and a claim for one transfer cannot be moved to another.
    login.verify_signature(
        address=coldkey,
        message=login.deposit_claim_message(
            domain=settings.login_domain,
            address=coldkey,
            extrinsic_reference=payload.extrinsic_reference,
        ),
        signature=signature,
    )

    open_deposits = [
        item
        for item in await credit_store.deposits_for(
            session, principal.account.id, limit=20
        )
        if item.status == DepositState.AWAITING_TRANSFER
    ]
    if not open_deposits:
        raise Conflict(
            "no open deposit to attach that transfer to; create one first",
            reason_code=REASON_NO_OPEN_DEPOSIT,
        )

    deposit = await credit_store.mark_seen(
        session,
        open_deposits[0],
        extrinsic_reference=payload.extrinsic_reference,
        sender_coldkey=coldkey,
    )
    await session.commit()
    return _deposit(deposit, settings=settings)


# --- The miner panel ---------------------------------------------------------------------


@router.get(
    "/submissions",
    response_model=schemas.CursorPage[schemas.SubmissionSummary],
    summary="The account's own submissions",
)
async def list_submissions(
    response: Response,
    principal: PrincipalDep,
    services: ServicesDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=256)] = None,
) -> schemas.CursorPage[schemas.SubmissionSummary]:
    settings = services.settings
    after = None
    if cursor:
        position = decode_cursor(settings.cursor_secret, cursor)
        after = (position.created_at, position.id)
    rows = await submission_store.for_account(
        session, principal.account.id, limit=limit + 1, after=after
    )
    page, more = page_of(list(rows), limit=limit)
    _no_store(response)
    return schemas.CursorPage[schemas.SubmissionSummary](
        items=tuple(submission_summary(row) for row in page),
        next_cursor=(
            encode_cursor(
                settings.cursor_secret,
                created_at=page[-1].created_at,
                id=page[-1].id,
            )
            if more and page
            else None
        ),
    )


@router.get(
    "/submissions/{submission_id}",
    response_model=schemas.SubmissionDetail,
    summary="One of the account's submissions, in full",
)
async def read_submission(
    submission_id: Annotated[str, Path(min_length=UUID_LENGTH, max_length=UUID_LENGTH)],
    response: Response,
    principal: PrincipalDep,
    session: SessionDep,
) -> schemas.SubmissionDetail:
    view = await submission_store.get_for_account(
        session, _as_uuid(submission_id, "submission"), principal.account.id
    )
    _no_store(response)
    return await submission_detail(session, view)


@router.get(
    "/submissions/{submission_id}/events",
    response_model=tuple[schemas.SubmissionEvent, ...],
    summary="The submission timeline",
)
async def read_events(
    submission_id: Annotated[str, Path(min_length=UUID_LENGTH, max_length=UUID_LENGTH)],
    response: Response,
    principal: PrincipalDep,
    session: SessionDep,
) -> tuple[schemas.SubmissionEvent, ...]:
    """What the miner sees in the meantime.

    The status fields say where the submission is now; this says how it got there, which is the
    question asked when nothing appears to be happening.
    """
    view = await submission_store.get_for_account(
        session, _as_uuid(submission_id, "submission"), principal.account.id
    )
    events = await intent_store.events_for(session, view.submission.id)
    _no_store(response)
    return tuple(
        schemas.SubmissionEvent(
            id=event.id,
            kind=event.kind,
            detail=event.detail,
            context=event.context,
            actor=event.actor,
            occurred_at=utc(event.occurred_at),
        )
        for event in events
    )


@router.get(
    "/submissions/{submission_id}/report",
    response_model=schemas.OwnerVerificationReport,
    summary="The full verifier report, for the submission's owner",
)
async def read_report(
    submission_id: Annotated[str, Path(min_length=UUID_LENGTH, max_length=UUID_LENGTH)],
    response: Response,
    principal: PrincipalDep,
    session: SessionDep,
) -> schemas.OwnerVerificationReport:
    """The complete report, including `stdout_tail` and `stderr_tail`.

    Nothing is withheld here, unlike the public subset: the output quotes the owner's own proof
    back at them, which is exactly what they need in order to fix it, and is not a disclosure to
    anyone else. The same bytes the coldkey-signature surface already returns to them.
    """
    view = await submission_store.get_for_account(
        session, _as_uuid(submission_id, "submission"), principal.account.id
    )
    run = view.verification
    if run is None or run.report is None or run.report_digest is None:
        raise Conflict(
            "verification has not produced a report yet",
            extra={"verification_status": str(view.submission.verification_status)},
        )
    _no_store(response)
    return schemas.OwnerVerificationReport(
        submission_id=view.submission.id,
        report_sha256=digests.to_prefixed(run.report_digest),
        report=json.loads(bytes(run.report).decode("utf-8")),
    )


@router.get(
    "/rewards",
    response_model=schemas.CursorPage[schemas.RewardItem],
    summary="Payouts, with explorer links",
)
async def list_rewards(
    response: Response,
    principal: PrincipalDep,
    services: ServicesDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=256)] = None,
) -> schemas.CursorPage[schemas.RewardItem]:
    settings = services.settings
    rows = await submission_store.rewards_for_account(
        session,
        principal.account.id,
        limit=limit + 1,
        after_id=decode_id_cursor(settings, cursor),
    )
    page, more = page_of(list(rows), limit=limit)
    _no_store(response)
    return schemas.CursorPage[schemas.RewardItem](
        items=tuple(
            schemas.RewardItem(
                id=event.id,
                submission_id=event.submission_id,
                task_id=task_id,
                status=str(event.status),
                amount_rao=event.amount_rao,
                destination_coldkey=event.destination_coldkey,
                destination_hotkey=event.destination_hotkey,
                extrinsic_reference=event.extrinsic_reference,
                explorer_url=(
                    EXPLORER_TEMPLATE.format(reference=event.extrinsic_reference)
                    if event.extrinsic_reference
                    else None
                ),
                submitted_block=event.submitted_block,
                finalized_block=event.finalized_block,
                failure_reason=event.failure_reason,
                created_at=utc(event.created_at),
                confirmed_at=utc(event.confirmed_at),
            )
            for event, task_id in page
        ),
        next_cursor=(
            encode_id_cursor(settings, page[-1][0].id) if more and page else None
        ),
    )


__all__ = ["router"]
