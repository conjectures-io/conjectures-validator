"""Sign in, sign out, and read the current session.

Two ways in — a magic link to an email address, and a signature from a coldkey — and both
end in the same place: a session row and an HttpOnly cookie. See
`submission_api/sessions.py` for the cookie and CSRF design and
`submission_api/login.py` for the signed messages.

Four things here are security decisions rather than conveniences:

* **`request-link` always answers 202.** Whether an account exists for an address is not
  disclosed, so this endpoint cannot be used to enumerate who has signed up. The response
  is identical for a known address, an unknown one, and one that is rate-limited.
* **Verification is single-use and atomic.** `consume_challenge` claims the row in one
  conditional UPDATE, so a forwarded email or a double-clicked link logs in once.
* **A new session is issued on every sign-in, and any existing one is revoked.** Reusing a
  session across a re-authentication would let a session established before an email was
  verified survive the change in what that account can reach.
* **Per-address rate limits, on top of the per-IP limiter.** Mailing a link is an action
  taken against someone else's mailbox: what has to be bounded is requests per address, not
  requests per requester, and the IP limiter cannot see that.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Header, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from conjectures_subnet.db import accounts as account_store
from conjectures_subnet.db.models import Account, LoginChallengeKind
from submission_api import login, mail, schemas_account as account_schemas, sessions
from submission_api.dependencies import (
    OptionalPrincipalDep,
    ServicesDep,
    SessionDep,
    WriterDep,
)
from submission_api.errors import ServiceUnavailable, TooManyRequests, Unauthorized
from submission_api.routers._account import account_response
from submission_api.settings import Settings
from verifier.bundle import SS58_ADDRESS

router = APIRouter(prefix="/v1/auth", tags=["auth"])

MAX_SIGNATURE_HEX = 132  # 64 bytes, hex, with an optional 0x prefix
MAX_TOKEN_LENGTH = 256

# Python's `re` has no POSIX classes, so this is the `\S`-based equivalent of the
# `account_email_shape` CHECK in V003. Kept deliberately parallel to it.
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

REASON_TOO_MANY_CHALLENGES = "TOO_MANY_CHALLENGES"


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmailLinkRequest(Payload):
    # The same shape the `account_email_shape` CHECK enforces, so an address this endpoint
    # accepts is one the column will store. Pydantic's `EmailStr` would be stricter, but it
    # needs the `email-validator` package, and `requirements-service.lock` is a deliberately
    # curated set — a regex identical to the database's is the honest trade here.
    email: str = Field(min_length=3, max_length=254, pattern=EMAIL_PATTERN)


class EmailVerifyRequest(Payload):
    token: str = Field(min_length=16, max_length=MAX_TOKEN_LENGTH)


class WalletChallengeRequest(Payload):
    address: str = Field(min_length=48, max_length=48)


class WalletVerifyRequest(Payload):
    address: str = Field(min_length=48, max_length=48)
    signature: str = Field(min_length=128, max_length=MAX_SIGNATURE_HEX)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _set_session_cookies(
    response: Response, issued: sessions.IssuedSession, settings: Settings
) -> None:
    max_age = settings.session_days * 24 * 60 * 60
    secure = settings.production
    # Appended rather than assigned: two Set-Cookie headers, not one overwriting the other.
    response.headers.append(
        "Set-Cookie", sessions.session_cookie(issued.token, max_age=max_age, secure=secure)
    )
    response.headers.append(
        "Set-Cookie", sessions.csrf_cookie(issued.csrf_token, max_age=max_age, secure=secure)
    )


async def _sign_in(
    session,
    response: Response,
    account: Account,
    settings: Settings,
    *,
    user_agent: str | None,
    source_ip: str | None,
) -> account_schemas.SessionEnvelope:
    """Issue a fresh session, retiring every earlier one for this account.

    Revoking the old sessions is what makes a sign-in a clean boundary: whatever the account
    could reach before, the only live credential afterwards is the one just handed out.
    """
    await account_store.revoke_all_sessions(session, account.id)
    issued = await sessions.issue(
        session,
        account,
        now=_now(),
        lifetime=dt.timedelta(days=settings.session_days),
        user_agent=user_agent,
        source_ip=source_ip,
    )
    await session.commit()
    _set_session_cookies(response, issued, settings)
    return account_schemas.SessionEnvelope(
        account=await account_response(session, account)
    )


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _normalise_signature(value: str) -> bytes:
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


def _assert_ss58(value: str) -> str:
    if SS58_ADDRESS.fullmatch(value) is None:
        raise Unauthorized(
            "address is not a valid SS58 address",
            reason_code=login.REASON_SIGNATURE_INVALID,
        )
    return value


# --- Reading the session -----------------------------------------------------------------


@router.get(
    "/session",
    response_model=account_schemas.SessionEnvelope,
    summary="The current account, or 401",
)
async def read_session(
    principal: OptionalPrincipalDep, session: SessionDep
) -> account_schemas.SessionEnvelope:
    """What the website calls on load to decide whether to show a sign-in page.

    401 for an absent or expired session, which is what the contract specifies, rather than
    200 with a null account: the status code is the signal, and a client should not have to
    inspect a body to learn it is anonymous.
    """
    if principal is None:
        raise Unauthorized(
            "not signed in", reason_code=sessions.REASON_NOT_AUTHENTICATED
        )
    return account_schemas.SessionEnvelope(
        account=await account_response(session, principal.account)
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Sign out")
async def logout(
    response: Response,
    principal: WriterDep,
    session: SessionDep,
    services: ServicesDep,
) -> None:
    """Revoke the session row and clear both cookies.

    A write, so it takes `WriterDep` and its CSRF check: a cross-site page being able to log
    someone out is a real, if minor, nuisance attack, and the check costs nothing here.

    Revoking server-side is the point. Clearing the cookie alone would leave a credential
    that still works if it was captured.
    """
    await account_store.revoke_session(session, principal.session.id)
    await session.commit()
    for cookie in sessions.cleared_cookies(secure=services.settings.production):
        response.headers.append("Set-Cookie", cookie)


# --- Email magic link --------------------------------------------------------------------


@router.post(
    "/email/request-link",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Mail a single-use sign-in link",
)
async def request_email_link(
    payload: EmailLinkRequest,
    services: ServicesDep,
    session: SessionDep,
) -> Response:
    """Always 202, whatever happened.

    A different answer for a known address than for an unknown one would make this an
    account-enumeration oracle, and the address is the one thing an attacker can vary freely.
    So the response is identical for a delivered link, an address with no account, and an
    address that has asked too often.

    The rate limit is per address rather than per caller: the cost being controlled is mail
    sent to someone else's mailbox.
    """
    settings = services.settings
    now = _now()
    sent = await account_store.recent_challenge_count(
        session,
        kind=LoginChallengeKind.EMAIL,
        since=now - dt.timedelta(hours=1),
        email=payload.email,
    )
    if sent < settings.email_links_per_hour:
        token = sessions.new_token()
        await account_store.create_challenge(
            session,
            kind=LoginChallengeKind.EMAIL,
            secret_digest=account_store.digest(token),
            expires_at=now + dt.timedelta(minutes=settings.email_link_minutes),
            email=payload.email,
        )
        await session.commit()
        try:
            await services.mail.send_login_link(
                email=payload.email,
                link=mail.magic_link(
                    base_url=settings.website_base_url, token=token
                ),
                expires_in_minutes=settings.email_link_minutes,
            )
        except ServiceUnavailable:
            # No mail transport on this deployment. Surfaced, because a caller who is never
            # going to receive a link should not be told to check their inbox — and unlike
            # the existence of an account, this is not a fact about anyone.
            raise
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post(
    "/email/verify",
    response_model=account_schemas.SessionEnvelope,
    summary="Exchange a link token for a session",
)
async def verify_email(
    payload: EmailVerifyRequest,
    request: Request,
    response: Response,
    services: ServicesDep,
    session: SessionDep,
    user_agent: Annotated[str | None, Header(alias="User-Agent")] = None,
) -> account_schemas.SessionEnvelope:
    """Consume the token and sign in, creating the account on first use.

    A magic link is signup and sign-in at once: an address that verifies a token has
    demonstrably received mail at that address, which is the whole of what an email account
    proves. There is nothing further to ask for.
    """
    now = _now()
    challenge = await account_store.consume_challenge(
        session,
        kind=LoginChallengeKind.EMAIL,
        secret_digest=account_store.digest(payload.token),
        now=now,
    )
    if challenge is None or not challenge.email:
        # One refusal for expired, already-used, and never-existed. A caller learns nothing
        # about which, and there is nothing they could act on differently.
        raise Unauthorized(
            "that sign-in link is not valid; request a new one",
            reason_code=login.REASON_CHALLENGE_INVALID,
        )

    account = await account_store.find_by_email(session, challenge.email)
    if account is None:
        account = await account_store.create_account(
            session, email=challenge.email, email_verified=True
        )
    elif not account.email_verified:
        # Reaching the mailbox is the proof, so verifying it here rather than requiring a
        # second step: a wallet-first account that later adds an email confirms it by using
        # a link exactly like this one.
        account.email_verified = True
        await session.flush()

    return await _sign_in(
        session,
        response,
        account,
        services.settings,
        user_agent=user_agent,
        source_ip=_client_ip(request),
    )


# --- Wallet sign-in ----------------------------------------------------------------------


@router.post(
    "/wallet/challenge",
    response_model=account_schemas.WalletChallenge,
    summary="A nonce and the exact message to sign",
)
async def wallet_challenge(
    payload: WalletChallengeRequest,
    services: ServicesDep,
    session: SessionDep,
) -> account_schemas.WalletChallenge:
    """Mint a single-use nonce for a coldkey.

    The message is domain-separated with the `conjectures-login-v1` prefix and pins the
    address, the nonce and the expiry, so a signature over it cannot be replayed into the
    hotkey-link flow, into another deployment, or for another address. It is stored verbatim
    and verified verbatim.
    """
    settings = services.settings
    address = _assert_ss58(payload.address)
    now = _now()
    issued = await account_store.recent_challenge_count(
        session,
        kind=LoginChallengeKind.WALLET,
        since=now - dt.timedelta(hours=1),
        ss58=address,
    )
    if issued >= settings.challenges_per_hour:
        raise TooManyRequests(
            "too many sign-in challenges for that address; try again later",
            reason_code=REASON_TOO_MANY_CHALLENGES,
        )

    nonce = sessions.new_token()
    expires_at = now + dt.timedelta(minutes=settings.challenge_minutes)
    message = login.login_message(
        domain=settings.login_domain,
        address=address,
        nonce=nonce,
        expires_at=expires_at,
    )
    await account_store.create_challenge(
        session,
        kind=LoginChallengeKind.WALLET,
        secret_digest=account_store.digest(nonce),
        expires_at=expires_at,
        ss58=address,
        message=message,
    )
    await session.commit()
    return account_schemas.WalletChallenge(
        nonce=nonce, message=message, expires_at=expires_at
    )


@router.post(
    "/wallet/verify",
    response_model=account_schemas.SessionEnvelope,
    summary="Verify the signature and open a session",
)
async def verify_wallet(
    payload: WalletVerifyRequest,
    request: Request,
    response: Response,
    services: ServicesDep,
    session: SessionDep,
    user_agent: Annotated[str | None, Header(alias="User-Agent")] = None,
) -> account_schemas.SessionEnvelope:
    """Check the signature over the stored message, then sign in.

    The nonce is not in the request: it is recovered from the *signature* being valid over
    the message this server stored. So the client cannot present a message of its own
    choosing — there is exactly one unconsumed challenge per address per nonce, and the
    message that goes into verification comes from the row.
    """
    address = _assert_ss58(payload.address)
    signature = _normalise_signature(payload.signature)
    now = _now()

    challenge = await account_store.latest_open_challenge(
        session, kind=LoginChallengeKind.WALLET, ss58=address, now=now
    )
    if challenge is None or challenge.message is None:
        raise Unauthorized(
            "no open sign-in challenge for that address; request a new one",
            reason_code=login.REASON_CHALLENGE_INVALID,
        )

    # Verified before the challenge is consumed, so a wrong signature does not burn the
    # nonce — otherwise one bad request would force the user to start over, and an attacker
    # could grief a known address by spamming invalid signatures.
    login.verify_signature(
        address=address, message=challenge.message, signature=signature
    )

    consumed = await account_store.consume_challenge(
        session,
        kind=LoginChallengeKind.WALLET,
        secret_digest=bytes(challenge.secret_sha256),
        now=now,
    )
    if consumed is None:
        # Lost a race with another request presenting the same valid signature.
        raise Unauthorized(
            "that challenge has already been used; request a new one",
            reason_code=login.REASON_CHALLENGE_INVALID,
        )

    account = await account_store.find_by_coldkey(session, address)
    if account is None:
        account = await account_store.create_account(session)
        await account_store.link_wallet(
            session, account, coldkey=address, signature=signature
        )

    return await _sign_in(
        session,
        response,
        account,
        services.settings,
        user_agent=user_agent,
        source_ip=_client_ip(request),
    )


__all__ = ["router"]
