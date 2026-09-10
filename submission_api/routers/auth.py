"""Sign in, sign out, and read the current session.

Five ways in. Four are for a browser and end in an HttpOnly cookie: a Google identity, a magic
link to an email address, an email address with a password, and a signature from a coldkey. The
fifth is for the miner CLI and ends in a bearer token: a signature from a coldkey that has already
been linked to an account in the browser. See `submission_api/sessions.py` for the two credentials
and why only one of them has to prove where a write was initiated,
`submission_api/origin_policy.py` for how it proves it, `submission_api/login.py` for the signed
messages, and `submission_api/passwords.py` for the hashing.

The password flows have their own section below, with the reasoning specific to them. Eight
things across the whole router are security decisions rather than conveniences:

* **`request-link` always answers 202.** Whether an account exists for an address is not
  disclosed, so this endpoint cannot be used to enumerate who has signed up. The response
  is identical for a known address, an unknown one, and one that is rate-limited.
* **Verification is single-use and atomic.** `consume_challenge` claims the row in one
  conditional UPDATE, so a forwarded email or a double-clicked link logs in once.
* **A new browser session is issued on every sign-in, and any existing *browser* session is
  revoked.** Reusing a session across a re-authentication would let a session established
  before an email was verified survive the change in what that account can reach. CLI tokens
  are deliberately out of that scope — see `_sign_in`.
* **Google subjects, not emails, identify Google users.** A matching email never silently
  combines accounts; the existing account must authenticate and explicitly link Google.
* **Per-address rate limits, on top of the per-IP limiter.** Mailing a link is an action
  taken against someone else's mailbox: what has to be bounded is requests per address, not
  requests per requester, and the IP limiter cannot see that.
* **Signatures are checked before nonces are consumed, and before anything is disclosed.** A
  wrong signature must not burn a challenge, or an attacker could grief a known address by
  sending garbage; and nothing about an account may be revealed to a caller who has not yet
  proved control of the key. The per-challenge attempt ceiling is what bounds the first choice.
* **Every response here is `no-store`.** All of them are caller-dependent, and one of them
  contains a live credential.
* **No account exists until a mailbox answers.** Registration writes a pending challenge, not
  an unverified `accounts` row, so nobody can squat an address they do not control. See the
  email-and-password section.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import math
import secrets
import time
from asyncio import to_thread
from typing import Annotated
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, Header, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from conjectures_subnet.axiom import get_axiom
from conjectures_subnet.db import accounts as account_store
from conjectures_subnet.db.errors import RecordConflict
from conjectures_subnet.db.models import (
    MAILED_CHALLENGE_KINDS,
    MINER_ROLE,
    Account,
    AccountSessionKind,
    LoginChallengeKind,
)
from submission_api import (
    login,
    mail,
    passwords,
    schemas_account as account_schemas,
    sessions,
)
from submission_api.dependencies import (
    CookieWriterDep,
    OptionalPrincipalDep,
    ServicesDep,
    SessionDep,
    WriterDep,
)
from submission_api.errors import (
    BadRequest,
    Conflict,
    Forbidden,
    ServiceUnavailable,
    TooManyRequests,
    Unauthorized,
)
from submission_api.google_identity import GOOGLE_PROVIDER, GoogleIdentity
from submission_api.middleware import client_address
from submission_api.routers._account import account_response, session_envelope
from submission_api.settings import Settings
from verifier.bundle import SS58_ADDRESS

router = APIRouter(prefix="/v1/auth", tags=["auth"])

MAX_SIGNATURE_HEX = 132  # 64 bytes, hex, with an optional 0x prefix
MAX_TOKEN_LENGTH = 256
MAX_GOOGLE_CREDENTIAL_LENGTH = 16_384
MAX_GOOGLE_CALLBACK_BYTES = 24_000
GOOGLE_CSRF_COOKIE = "g_csrf_token"

REASON_GOOGLE_CSRF_INVALID = "GOOGLE_CSRF_INVALID"
REASON_GOOGLE_ACCOUNT_LINK_REQUIRED = "GOOGLE_ACCOUNT_LINK_REQUIRED"
REASON_GOOGLE_IDENTITY_ALREADY_LINKED = "GOOGLE_IDENTITY_ALREADY_LINKED"
REASON_GOOGLE_PROVIDER_ALREADY_LINKED = "GOOGLE_PROVIDER_ALREADY_LINKED"

# Python's `re` has no POSIX classes, so this is the `\S`-based equivalent of the
# `account_email_shape` CHECK in V003. Kept deliberately parallel to it.
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

REASON_TOO_MANY_CHALLENGES = "TOO_MANY_CHALLENGES"

# The password flows. Four codes, and the split between them is the disclosure boundary:
# PASSWORD_REJECTED is about the password the caller just typed and says nothing about any
# account, while PASSWORD_INVALID covers a wrong password, an address with no account, and an
# account with no password set — three situations a caller must not be able to tell apart.
REASON_PASSWORD_REJECTED = "PASSWORD_REJECTED"
REASON_PASSWORD_INVALID = "PASSWORD_INVALID"
REASON_PASSWORD_ATTEMPTS_EXCEEDED = "PASSWORD_ATTEMPTS_EXCEEDED"
REASON_EMAIL_IN_USE = "EMAIL_IN_USE"

# Bounds the request, not the policy. `passwords.assert_acceptable` owns the rules and produces
# a message a person can act on; this only stops an unauthenticated caller from posting a
# megabyte to be normalised and hashed. Well above `passwords.MAX_LENGTH`, so a password that
# is merely too long is refused by policy with an explanation rather than by schema with a 422.
MAX_PASSWORD_FIELD = 512


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


class PasswordRegisterRequest(Payload):
    email: str = Field(min_length=3, max_length=254, pattern=EMAIL_PATTERN)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_FIELD)


class PasswordLoginRequest(Payload):
    email: str = Field(min_length=3, max_length=254, pattern=EMAIL_PATTERN)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_FIELD)


class PasswordEmailRequest(Payload):
    email: str = Field(min_length=3, max_length=254, pattern=EMAIL_PATTERN)


class PasswordTokenRequest(Payload):
    token: str = Field(min_length=16, max_length=MAX_TOKEN_LENGTH)


class PasswordResetRequest(Payload):
    token: str = Field(min_length=16, max_length=MAX_TOKEN_LENGTH)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_FIELD)


class WalletChallengeRequest(Payload):
    address: str = Field(min_length=48, max_length=48)


class WalletVerifyRequest(Payload):
    address: str = Field(min_length=48, max_length=48)
    signature: str = Field(min_length=128, max_length=MAX_SIGNATURE_HEX)


class GoogleCredentialRequest(Payload):
    credential: str = Field(min_length=100, max_length=MAX_GOOGLE_CREDENTIAL_LENGTH)


class CliChallengeRequest(Payload):
    address: str = Field(
        min_length=48, max_length=48, description="The coldkey that will sign"
    )


class CliVerifyRequest(Payload):
    address: str = Field(min_length=48, max_length=48)
    # Echoed back, unlike the coldkey flow. The nonce is not the proof — the signature is, and
    # it is checked against the message stored on the row this nonce names — but naming the row
    # is what stops one caller's challenge from superseding another's. See
    # `accounts.open_challenge_by_nonce`.
    nonce: str = Field(min_length=16, max_length=MAX_TOKEN_LENGTH)
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
    # One cookie is issued; the second header deletes the CSRF cookie an earlier version set, so
    # a browser that signs in again is rid of it rather than carrying a cookie nothing reads for
    # the rest of its 30-day `Max-Age`.
    response.headers.append(
        "Set-Cookie", sessions.expired_legacy_csrf_cookie(secure=secure)
    )


async def _sign_in(
    session,
    response: Response,
    account: Account,
    settings: Settings,
    *,
    method: str,
    user_agent: str | None,
    source_ip: str | None,
) -> account_schemas.SessionEnvelope:
    """Issue a fresh browser session, retiring every earlier *browser* session for this account.

    Revoking the old cookies is what makes a sign-in a clean boundary: whatever this browser
    could reach before, the only live cookie afterwards is the one just handed out.

    `kind=COOKIE` scopes that, and the scoping is load-bearing rather than tidy. CLI bearer
    tokens live on other machines and represent long-running work; an unscoped revoke would mean
    that every time a miner opened the website, every rig's `conjectures` session died — a
    failure nobody would attribute to having visited a web page. A browser sign-in is a
    statement about this browser, not about the account's tooling.
    """
    await account_store.revoke_all_sessions(
        session, account.id, kind=AccountSessionKind.COOKIE
    )
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
    # After the commit, so a rolled-back sign-in is never reported as one. All methods funnel
    # through here, so `method` is what distinguishes them.
    #
    # The account id, never the email address. An id is meaningless outside this database; an
    # address is a person, and this endpoint is deliberately built so that not even its status
    # code discloses who has an account here — shipping the address to a telemetry backend would
    # undo that from the inside.
    get_axiom().info(
        source="api-auth",
        event_type="login_completed",
        account_id=str(account.id),
        method=method,
        email_verified=account.email_verified,
    )
    # The full envelope, not just the account. A sign-in is the one moment a client is
    # guaranteed to need every field in it, and answering with a subset here would mean every
    # sign-in is immediately followed by a `GET /v1/auth/session` that reads the same rows again.
    return await session_envelope(
        session, account, settings=settings, now=_now()
    )


def _client_ip(request: Request, settings: Settings) -> str | None:
    """The address to record on the session row.

    Read through `middleware.client_address` rather than off `request.client`, so that behind a
    load balancer this is the caller rather than the balancer. `source_ip` and `user_agent` are
    the only forensic handles on a long-lived credential and the only thing a session listing can
    show a person deciding whether to revoke one — "last used from 10.0.0.3", the same address for
    every session ever created, is worse than showing nothing.

    Returns None rather than a placeholder when the result is not an address: the column is
    `INET`, and `client_address` yields the string `unknown` when there is no peer at all.
    """
    address = client_address(request.scope, settings.trusted_proxy_hops)
    try:
        return str(ipaddress.ip_address(address))
    except ValueError:
        return None


def _no_store(response: Response) -> None:
    """Forbid caching of an authenticated answer, and say what it varies on.

    Every response in this router is caller-dependent: the account body, the session state, and
    in one case a live credential. `no-store` keeps them out of shared caches and browser disk
    caches alike, and `Vary` names both credential channels so that an intermediary which does
    cache cannot serve one caller's identity to another. `/v1/me` already does this; this router
    was the gap.
    """
    response.headers["Cache-Control"] = "no-store"
    response.headers["Vary"] = "Authorization, Cookie"


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
    summary="The current session: account, identities, holdings and capabilities",
)
async def read_session(
    response: Response,
    principal: OptionalPrincipalDep,
    services: ServicesDep,
    session: SessionDep,
) -> account_schemas.SessionEnvelope:
    """What the website calls on load to decide what to draw.

    401 for an absent or expired session, which is what the contract specifies, rather than
    200 with a null account: the status code is the signal, and a client should not have to
    inspect a body to learn it is anonymous.

    **One request, whole shell.** Beyond the account this returns the identities that reach it,
    the linked coldkeys, the payout destination, the credit balance, the badge counts and the
    five capability flags — because a client that had to assemble those from `/v1/me`,
    `/v1/me/credits` and a submissions page would make four round trips on every page load and
    render a header that disagrees with itself while they land. `account` is unchanged and
    remains the canonical record; the rest is derived from it in the same call.

    `Cache-Control: no-store` on all of it, via `_no_store`. That was already required — the
    body is caller-dependent — and is more so now that it carries a balance and a set of
    permissions: a shared cache serving one account's capabilities to another would be an
    authorisation bug wearing a caching bug's clothes.

    Answers a bearer caller too, redacted — `conjectures auth status` uses it to confirm that a
    stored token is still live without having to interpret an error body. What a CLI session
    sees is narrowed by `account_response`'s rules, and its capabilities reflect the credential
    rather than only the account: an admin on a rig is told, in `manage_roles.missing`, that the
    role is held but not exercisable here.
    """
    _no_store(response)
    if principal is None:
        raise Unauthorized(
            "not signed in", reason_code=sessions.REASON_NOT_AUTHENTICATED
        )
    return await session_envelope(
        session,
        principal.account,
        settings=services.settings,
        now=_now(),
        bearer_scope=principal.coldkey_scope,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Sign out")
async def logout(
    response: Response,
    principal: WriterDep,
    session: SessionDep,
    services: ServicesDep,
) -> None:
    """Revoke this one session row, and clear the cookies if it was a browser session.

    A write, so it takes `WriterDep` and its cross-site check: a cross-site page being able to
    log someone out is a real, if minor, nuisance attack, and the check costs nothing here. A
    bearer caller passes it by construction — see `require_writer`.

    Revoking server-side is the point. Clearing the cookie alone would leave a credential
    that still works if it was captured.

    **One row, whichever kind it is.** `conjectures auth logout` on one rig must not sign the
    miner out of the website, and signing out of the website must not stop the rigs. The
    account-wide version of this is `DELETE /v1/me/sessions/{id}` per session, or the
    sign-out-everywhere on the account page.

    The cookie-clearing headers are skipped for a bearer session. Nothing would break if they
    were sent — a CLI has no cookie jar — but an `Authorization`-authenticated request that
    answers with `Set-Cookie` is exactly the shape that turns a CLI credential into an ambient
    one the first time some client does keep a jar.
    """
    _no_store(response)
    await account_store.revoke_session(session, principal.session.id)
    await session.commit()
    get_axiom().info(
        source="api-auth",
        event_type="logout",
        account_id=str(principal.account.id),
        session_kind=str(principal.session.kind),
    )
    if not principal.is_bearer:
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
    if sent >= settings.email_links_per_hour:
        # No address on the event, for the reason `_sign_in` gives. What is worth recording is
        # that the per-address ceiling is being hit at all: the response cannot say so — it is
        # 202 either way, deliberately — so this is the only place it is visible.
        get_axiom().warn(
            source="api-auth",
            event_type="login_link_sent",
            delivered=False,
            reason="rate_limited",
            recent_requests=sent,
            limit=settings.email_links_per_hour,
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
                    base_url=settings.website_base_url,
                    token=token,
                    path=settings.email_verify_path,
                ),
                expires_in_minutes=settings.email_link_minutes,
            )
        except ServiceUnavailable:
            # No mail transport on this deployment. Surfaced, because a caller who is never
            # going to receive a link should not be told to check their inbox — and unlike
            # the existence of an account, this is not a fact about anyone.
            get_axiom().error(
                source="api-mail",
                event_type="login_link_sent",
                delivered=False,
                reason="no_mail_transport",
            )
            raise
        get_axiom().info(
            source="api-auth",
            event_type="login_link_sent",
            delivered=True,
            expires_in_minutes=settings.email_link_minutes,
        )
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
    _no_store(response)
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
        method="email-link",
        user_agent=user_agent,
        source_ip=_client_ip(request, services.settings),
    )


# --- Email and password ------------------------------------------------------------------
#
# Five endpoints, and the shape of all of them follows from one decision: **no account exists
# until the mailbox answers.** A registration is a `PASSWORD_SIGNUP` challenge carrying the
# address and the already-hashed password, and nothing more. `accounts` is not touched until
# the link is followed.
#
# That is not tidiness. An unverified account row created at registration time is a squatting
# primitive: register someone else's address and they can never sign up, never link Google to
# it, and never be found by their own mailbox — and `email_verified` quietly degrades from
# "this mailbox answered" to "somebody typed this". Holding the pending registration in the
# challenge table costs one nullable column and removes the whole class.
#
# The other four decisions here:
#
# * **Registering an address that already has an account mails a reset link, not a refusal.**
#   The HTTP response is 202 either way, so the endpoint stays useless for enumeration, and the
#   person actually reading the mailbox gets something they can act on. It also happens to be
#   how a Google-only or wallet-only account acquires a password: the owner proves the mailbox
#   and chooses one, which is the same proof a fresh signup gives.
# * **A wrong password and an unknown address cost the same time and return the same body.**
#   `spend_dummy_verification` does a real scrypt derivation for an address with no account,
#   because ~140ms against ~0ms is an enumeration oracle that identical status codes do not
#   close.
# * **Guessing is bounded twice, and the two halves cover different things.** `accounts` carries
#   a durable counter and a pause that survive restarts and are shared across replicas;
#   `services.password_failures` is an in-process window that also applies to addresses with no
#   account, which is what keeps a 429 from being the disclosure the 401 was careful not to be.
# * **The pause stops the password method and nothing else.** Magic link, Google and coldkey
#   sign-in keep working throughout, so tripping it on someone else's address costs them one
#   button for a few minutes rather than access to their account.


def _password_or_reject(password: str, *, email: str | None = None) -> str:
    """Apply policy, or refuse with something the person can act on.

    Policy is checked before anything is looked up, and its refusal is deliberately specific —
    "at least 12 characters" is a fact about what was typed, not about who has an account here,
    so saying it plainly discloses nothing.
    """
    try:
        return passwords.assert_acceptable(password, email=email)
    except passwords.PasswordRejected as exc:
        raise BadRequest(str(exc), reason_code=REASON_PASSWORD_REJECTED) from exc


async def _hash(password: str, settings: Settings) -> str:
    """Derive a hash off the event loop.

    scrypt at the configured cost is ~140ms of CPU with 64 MiB resident. Run inline it would
    stall every other request on this worker for that long, and the endpoints that call it are
    unauthenticated — which would make the KDF's cost a denial-of-service lever rather than a
    defence.
    """
    return await to_thread(
        passwords.hash_password, password, cost_log2=settings.password_cost_log2
    )


async def _mail_budget_spent(session, email: str, settings: Settings, *, now) -> bool:
    """Whether this address has already had its hour's worth of mail from us.

    One budget across all three mailed kinds — sign-in link, signup confirmation, reset — for
    the reason `MAILED_CHALLENGE_KINDS` gives: three separate budgets are three budgets an
    attacker spends in turn against the same mailbox.
    """
    sent = await account_store.recent_challenge_count(
        session,
        kind=MAILED_CHALLENGE_KINDS,
        since=now - dt.timedelta(hours=1),
        email=email,
    )
    if sent >= settings.email_links_per_hour:
        # No address on the event, for the reason `_sign_in` gives. The response is 202 either
        # way, deliberately, so this is the only place the ceiling is visible at all.
        get_axiom().warn(
            source="api-auth",
            event_type="account_mail_sent",
            delivered=False,
            reason="rate_limited",
            recent_requests=sent,
            limit=settings.email_links_per_hour,
        )
        return True
    return False


@router.post(
    "/password/register",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Begin registering an account with an email address and a password",
)
async def register_password(
    payload: PasswordRegisterRequest,
    services: ServicesDep,
    session: SessionDep,
) -> Response:
    """Always 202 once the password itself is acceptable.

    No account is created here. What is created is a single-use `PASSWORD_SIGNUP` challenge
    holding the address and the hashed password; following the link in the mail is what creates
    the account, and an unanswered registration leaves nothing behind but an expired row.

    An address that already has an account gets a password reset link instead, under a subject
    line that explains why. That is the same mail `password/forgot` sends and costs the same
    budget, so it grants an attacker nothing they did not already have — and it is the path by
    which an account that signed up with Google or a wallet adds a password.
    """
    settings = services.settings
    now = _now()
    password = _password_or_reject(payload.password, email=payload.email)

    if await _mail_budget_spent(session, payload.email, settings, now=now):
        return Response(status_code=status.HTTP_202_ACCEPTED)

    existing = await account_store.find_by_email(session, payload.email)
    token = sessions.new_token()
    expires_at = now + dt.timedelta(minutes=settings.email_link_minutes)

    if existing is not None:
        await account_store.create_challenge(
            session,
            kind=LoginChallengeKind.PASSWORD_RESET,
            secret_digest=account_store.digest(token),
            expires_at=expires_at,
            account_id=existing.id,
            email=payload.email,
        )
        await session.commit()
        await services.mail.send_existing_account_notice(
            email=payload.email,
            link=mail.password_reset_link(
                base_url=settings.website_base_url,
                token=token,
                path=settings.password_reset_path,
            ),
            expires_in_minutes=settings.email_link_minutes,
            sign_in_url=mail.sign_in_url(
                base_url=settings.website_base_url, path=settings.sign_in_path
            ),
        )
        get_axiom().info(
            source="api-auth",
            event_type="account_mail_sent",
            delivered=True,
            kind="registration_on_existing_account",
        )
        return Response(status_code=status.HTTP_202_ACCEPTED)

    # Hashed before the row is written, so the plaintext never reaches anything that persists.
    await account_store.create_challenge(
        session,
        kind=LoginChallengeKind.PASSWORD_SIGNUP,
        secret_digest=account_store.digest(token),
        expires_at=expires_at,
        email=payload.email,
        password_hash=await _hash(password, settings),
    )
    await session.commit()
    await services.mail.send_signup_link(
        email=payload.email,
        link=mail.signup_link(
            base_url=settings.website_base_url,
            token=token,
            path=settings.signup_verify_path,
        ),
        expires_in_minutes=settings.email_link_minutes,
    )
    get_axiom().info(
        source="api-auth",
        event_type="account_mail_sent",
        delivered=True,
        kind="signup",
        expires_in_minutes=settings.email_link_minutes,
    )
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post(
    "/password/verify",
    response_model=account_schemas.SessionEnvelope,
    summary="Confirm a registration and create the account",
)
async def verify_password_signup(
    payload: PasswordTokenRequest,
    request: Request,
    response: Response,
    services: ServicesDep,
    session: SessionDep,
    user_agent: Annotated[str | None, Header(alias="User-Agent")] = None,
) -> account_schemas.SessionEnvelope:
    """Consume the confirmation token, create the account, and sign in.

    The address is verified by construction: this token was delivered to it and nowhere else.

    An account appearing for the address between registration and confirmation is refused
    rather than adopted. The password on this challenge was chosen by whoever started the
    registration, and that need not be whoever owns the account now — silently applying it
    would turn a stale link into a credential handover.
    """
    _no_store(response)
    now = _now()
    challenge = await account_store.consume_challenge(
        session,
        kind=LoginChallengeKind.PASSWORD_SIGNUP,
        secret_digest=account_store.digest(payload.token),
        now=now,
    )
    if challenge is None or not challenge.email or not challenge.password_hash:
        # One refusal for expired, already-used, and never-existed, as with the magic link.
        raise Unauthorized(
            "that confirmation link is not valid; register again to get a new one",
            reason_code=login.REASON_CHALLENGE_INVALID,
        )

    if await account_store.find_by_email(session, challenge.email) is not None:
        raise Conflict(
            "an account already exists for that email; sign in or reset the password instead",
            reason_code=REASON_EMAIL_IN_USE,
        )

    account = await account_store.create_account(
        session,
        email=challenge.email,
        email_verified=True,
        password_hash=challenge.password_hash,
        now=now,
    )
    return await _sign_in(
        session,
        response,
        account,
        services.settings,
        method="password-signup",
        user_agent=user_agent,
        source_ip=_client_ip(request, services.settings),
    )


@router.post(
    "/password/login",
    response_model=account_schemas.SessionEnvelope,
    summary="Sign in with an email address and a password",
)
async def password_login(
    payload: PasswordLoginRequest,
    request: Request,
    response: Response,
    services: ServicesDep,
    session: SessionDep,
    user_agent: Annotated[str | None, Header(alias="User-Agent")] = None,
) -> account_schemas.SessionEnvelope:
    """Verify the password and sign in, or refuse identically to every other failure.

    Every refusal that is about the credential — wrong password, no account, an account that
    has never set a password — is the same 401 with the same reason code and the same elapsed
    time. The only distinguishable refusal is the 429, and that one is reachable for an address
    with no account too, which is what stops it from being the oracle the 401 avoids being.
    """
    _no_store(response)
    settings = services.settings
    now = _now()
    clock = time.monotonic()
    key = account_store.normalise_email(payload.email)

    refused = Unauthorized(
        "email address or password is not correct",
        reason_code=REASON_PASSWORD_INVALID,
    )

    attempts = services.password_failures.peek(key, clock)
    if not attempts.allowed:
        raise _too_many_attempts(attempts.reset_seconds)

    account = await account_store.find_by_email(session, payload.email)
    if account is None or account.password_hash is None:
        # A real derivation against a throwaway hash. Returning here in microseconds while a
        # registered address takes ~140ms would say which addresses exist regardless of what
        # the body says.
        await to_thread(passwords.spend_dummy_verification, payload.password)
        services.password_failures.check(key, clock)
        raise refused

    paused = account_store.password_pause_remaining(account, now=now)
    if paused is not None:
        raise _too_many_attempts(math.ceil(paused.total_seconds()))

    if not await to_thread(passwords.verify, payload.password, account.password_hash):
        services.password_failures.check(key, clock)
        throttled_until = await account_store.record_password_failure(
            session,
            account,
            now=now,
            attempts=settings.password_attempts,
            throttle=dt.timedelta(minutes=settings.password_throttle_minutes),
        )
        # Committed, or the counter that exists to survive a restart would not survive this
        # request. The account id, never the address — see `_sign_in`.
        await session.commit()
        get_axiom().warn(
            source="api-auth",
            event_type="password_sign_in_failed",
            account_id=str(account.id),
            throttled=throttled_until is not None,
        )
        raise refused

    if passwords.needs_rehash(
        account.password_hash, cost_log2=settings.password_cost_log2
    ):
        # The stored hash predates a cost increase. This is the only moment the plaintext is
        # available to derive a stronger one, which is why the rehash lives on the sign-in path
        # rather than in a migration — a migration cannot do it at all. `rehash_password`
        # rather than `set_password`, so `password_updated_at` keeps meaning "when the owner
        # last chose a password" rather than "when a hash was last written".
        await account_store.rehash_password(
            session, account, password_hash=await _hash(payload.password, settings)
        )
    else:
        await account_store.clear_password_failures(session, account)

    return await _sign_in(
        session,
        response,
        account,
        settings,
        method="password",
        user_agent=user_agent,
        source_ip=_client_ip(request, settings),
    )


@router.post(
    "/password/forgot",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Mail a single-use password reset link",
)
async def request_password_reset(
    payload: PasswordEmailRequest,
    services: ServicesDep,
    session: SessionDep,
) -> Response:
    """Always 202, whatever happened. Same reasoning as `request-link`.

    Offered to any account with an address, not only ones that already have a password: an
    account created through Google or a coldkey uses this to set its first one, and refusing
    here would disclose which accounts have a password.
    """
    settings = services.settings
    now = _now()
    if await _mail_budget_spent(session, payload.email, settings, now=now):
        return Response(status_code=status.HTTP_202_ACCEPTED)

    account = await account_store.find_by_email(session, payload.email)
    if account is None or not account.email:
        return Response(status_code=status.HTTP_202_ACCEPTED)

    token = sessions.new_token()
    await account_store.create_challenge(
        session,
        kind=LoginChallengeKind.PASSWORD_RESET,
        secret_digest=account_store.digest(token),
        expires_at=now + dt.timedelta(minutes=settings.email_link_minutes),
        account_id=account.id,
        email=account.email,
    )
    await session.commit()
    await services.mail.send_password_reset_link(
        email=account.email,
        link=mail.password_reset_link(
            base_url=settings.website_base_url,
            token=token,
            path=settings.password_reset_path,
        ),
        expires_in_minutes=settings.email_link_minutes,
    )
    get_axiom().info(
        source="api-auth",
        event_type="account_mail_sent",
        delivered=True,
        kind="password_reset",
        expires_in_minutes=settings.email_link_minutes,
    )
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post(
    "/password/reset",
    response_model=account_schemas.SessionEnvelope,
    summary="Set a new password with a reset token, and sign in",
)
async def reset_password(
    payload: PasswordResetRequest,
    request: Request,
    response: Response,
    services: ServicesDep,
    session: SessionDep,
    user_agent: Annotated[str | None, Header(alias="User-Agent")] = None,
) -> account_schemas.SessionEnvelope:
    """Consume the reset token, set the password, and sign in.

    Signing in here rather than sending the person back to a login form is the same judgement
    the magic link makes: the mailbox has just been proved, which is the whole of what a
    password reset can establish. `_sign_in` then revokes every other browser session, so a
    reset ends any session an attacker had — which is the reason most people reach for it.

    Reaching the mailbox also verifies the address, for a Google-first account whose address
    was a claim from a provider rather than something this service ever mailed.
    """
    _no_store(response)
    settings = services.settings
    now = _now()
    # Policy first, and before the token is spent: a rejected password must not consume the
    # link, or the person is sent back to request another for typing a short one.
    password = _password_or_reject(payload.password)

    challenge = await account_store.consume_challenge(
        session,
        kind=LoginChallengeKind.PASSWORD_RESET,
        secret_digest=account_store.digest(payload.token),
        now=now,
    )
    if challenge is None or challenge.account_id is None:
        raise Unauthorized(
            "that reset link is not valid; request a new one",
            reason_code=login.REASON_CHALLENGE_INVALID,
        )

    account = await account_store.get_account(session, challenge.account_id)
    await account_store.set_password(
        session, account, password_hash=await _hash(password, settings), now=now
    )
    if not account.email_verified:
        account.email_verified = True
        await session.flush()

    return await _sign_in(
        session,
        response,
        account,
        settings,
        method="password-reset",
        user_agent=user_agent,
        source_ip=_client_ip(request, settings),
    )


def _too_many_attempts(retry_after_seconds: int) -> TooManyRequests:
    """The one refusal on the sign-in path that is not a 401.

    Reachable for an address with no account as well as one with too many failures against it,
    because `services.password_failures` keys on the address rather than on a row. If it were
    only reachable for real accounts it would answer the question the 401 refuses to.
    """
    return TooManyRequests(
        "too many sign-in attempts for that address; try again later",
        reason_code=REASON_PASSWORD_ATTEMPTS_EXCEEDED,
        extra={"retry_after_seconds": retry_after_seconds},
    )


# --- Google sign-in ----------------------------------------------------------------------


async def _google_account_for_sign_in(
    session, identity: GoogleIdentity, *, now: dt.datetime
) -> Account:
    """Resolve a stable Google subject, creating an account only when no account collides.

    A matching email is deliberately not an implicit merge.  Someone who already has a wallet
    or magic-link account signs into that account first and explicitly links Google below.  This
    keeps the provider callback from silently combining two security principals.
    """

    linked = await account_store.find_by_identity(
        session, provider=GOOGLE_PROVIDER, subject=identity.subject
    )
    if linked is not None:
        account, stored = linked
        await account_store.touch_identity(
            session, stored, email=identity.email, now=now
        )
        return account

    if await account_store.find_by_email(session, identity.email) is not None:
        raise Conflict(
            "an account already uses that email; sign in to it and link Google from Account",
            reason_code=REASON_GOOGLE_ACCOUNT_LINK_REQUIRED,
        )

    try:
        account = await account_store.create_account(
            session,
            email=identity.email,
            email_verified=identity.authoritative_email,
        )
        await account_store.link_identity(
            session,
            account,
            provider=GOOGLE_PROVIDER,
            subject=identity.subject,
            email=identity.email,
        )
        return account
    except RecordConflict:
        # A concurrent callback with the same valid credential may have won either uniqueness
        # race. Both writes above were in one transaction and the store rolled it back, so no
        # orphan account remains. Resolve the winner instead of turning a double-click into an
        # error; an email claimed by a different account still requires explicit linking.
        linked = await account_store.find_by_identity(
            session, provider=GOOGLE_PROVIDER, subject=identity.subject
        )
        if linked is not None:
            account, stored = linked
            await account_store.touch_identity(
                session, stored, email=identity.email, now=now
            )
            return account
        raise Conflict(
            "an account already uses that email; sign in to it and link Google from Account",
            reason_code=REASON_GOOGLE_ACCOUNT_LINK_REQUIRED,
        )


def _callback_field(fields: dict[str, list[str]], name: str, *, maximum: int) -> str:
    values = fields.get(name, [])
    if len(values) != 1 or not values[0] or len(values[0]) > maximum:
        raise BadRequest(
            f"Google callback field {name!r} is missing or malformed",
            reason_code="GOOGLE_CALLBACK_INVALID",
        )
    return values[0]


def _website_route(settings: Settings, path: str, **query: str) -> str:
    target = f"{settings.website_base_url.rstrip('/')}/{path.lstrip('/')}"
    return target if not query else f"{target}?{urlencode(query)}"


@router.post(
    "/google/callback",
    response_class=RedirectResponse,
    status_code=status.HTTP_303_SEE_OTHER,
    summary="Verify Google and open a session",
)
async def google_callback(
    request: Request,
    services: ServicesDep,
    session: SessionDep,
    user_agent: Annotated[str | None, Header(alias="User-Agent")] = None,
) -> Response:
    """Receive Google Identity Services' redirect-mode form POST.

    This is the one cross-site state-changing route. Google supplies a random value in both the
    callback body and a cookie on this origin; both must exist and compare equal before the ID
    token is read. The token then independently proves its signature, audience, issuer and
    expiry through ``google-auth``.
    """

    content_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
    if content_type != "application/x-www-form-urlencoded":
        raise BadRequest(
            "Google callback must be form encoded",
            reason_code="GOOGLE_CALLBACK_INVALID",
        )
    body = await request.body()
    if len(body) > MAX_GOOGLE_CALLBACK_BYTES:
        raise BadRequest(
            "Google callback is too large",
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            reason_code="GOOGLE_CALLBACK_INVALID",
        )
    try:
        fields = parse_qs(
            body.decode("utf-8"),
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=8,
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise BadRequest(
            "Google callback form is malformed",
            reason_code="GOOGLE_CALLBACK_INVALID",
        ) from exc

    form_csrf = _callback_field(fields, GOOGLE_CSRF_COOKIE, maximum=512)
    cookie_csrf = request.cookies.get(GOOGLE_CSRF_COOKIE, "")
    if not cookie_csrf or len(cookie_csrf) > 512 or not secrets.compare_digest(
        form_csrf, cookie_csrf
    ):
        raise Forbidden(
            "Google callback CSRF token is missing or does not match",
            reason_code=REASON_GOOGLE_CSRF_INVALID,
        )

    credential = _callback_field(
        fields, "credential", maximum=MAX_GOOGLE_CREDENTIAL_LENGTH
    )
    identity = await services.google.verify(credential)
    try:
        account = await _google_account_for_sign_in(session, identity, now=_now())
    except Conflict as exc:
        # A redirect-mode login should land back in the product rather than leave a person on a
        # JSON problem document. No sensitive value crosses the URL; it carries only a stable
        # reason code the sign-in page turns into instructions.
        await session.rollback()
        return RedirectResponse(
            _website_route(
                services.settings,
                "/login",
                reason=exc.reason_code,
            ),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    response = RedirectResponse(
        _website_route(services.settings, "/account"),
        status_code=status.HTTP_303_SEE_OTHER,
    )
    await _sign_in(
        session,
        response,
        account,
        services.settings,
        method="google",
        user_agent=user_agent,
        source_ip=_client_ip(request, services.settings),
    )
    return response


@router.post(
    "/google/link",
    response_model=account_schemas.SessionEnvelope,
    summary="Attach Google to the signed-in account",
)
async def link_google(
    payload: GoogleCredentialRequest,
    principal: CookieWriterDep,
    services: ServicesDep,
    session: SessionDep,
) -> account_schemas.SessionEnvelope:
    """Explicitly link Google after authenticating with an existing method.

    This endpoint uses the normal write guard, unlike `/google/callback`, because the credential
    is posted by same-origin page script rather than by Google. It never merges or deletes an
    account.

    **`CookieWriterDep`, not `WriterDep`.** Attaching a provider adds a way *in* to the account,
    which is the same class of change as linking a coldkey or repointing the payout, and it is
    refused to a CLI token for the same reason: a bearer token is a long-lived file on a
    mining machine, so allowing this would turn one stolen file into "link my Google account,
    then sign in as them". This read as cookie-only before CLI sessions existed; it has to say
    so now that they do.
    """

    identity = await services.google.verify(payload.credential)
    already = await account_store.find_by_identity(
        session, provider=GOOGLE_PROVIDER, subject=identity.subject
    )
    if already is not None:
        account, stored = already
        if account.id != principal.account.id:
            raise Conflict(
                "that Google identity is already linked to another account",
                reason_code=REASON_GOOGLE_IDENTITY_ALREADY_LINKED,
            )
        await account_store.touch_identity(
            session, stored, email=identity.email, now=_now()
        )
        await session.commit()
        return await session_envelope(
            session, principal.account, settings=services.settings, now=_now()
        )

    providers = await account_store.identities_for(session, principal.account.id)
    if any(item.provider == GOOGLE_PROVIDER for item in providers):
        raise Conflict(
            "this account already has a different Google identity",
            reason_code=REASON_GOOGLE_PROVIDER_ALREADY_LINKED,
        )

    try:
        await account_store.link_identity(
            session,
            principal.account,
            provider=GOOGLE_PROVIDER,
            subject=identity.subject,
            email=identity.email,
        )
    except RecordConflict as exc:
        # Close the two races between the reads above and the unique constraints. The database
        # is authoritative; callers still receive the same provider-specific contract as the
        # non-racing path rather than a storage-layer reason code.
        if exc.reason_code == "IDENTITY_ALREADY_LINKED":
            raise Conflict(
                "that Google identity is already linked to another account",
                reason_code=REASON_GOOGLE_IDENTITY_ALREADY_LINKED,
            ) from exc
        if exc.reason_code == "PROVIDER_ALREADY_LINKED":
            raise Conflict(
                "this account already has a different Google identity",
                reason_code=REASON_GOOGLE_PROVIDER_ALREADY_LINKED,
            ) from exc
        raise
    # Linking is an explicit account-owner action, so a currently authoritative Google mailbox
    # can fill or verify the local email. It never overwrites a different address.
    current_email = principal.account.email
    if identity.authoritative_email and (
        current_email is None or current_email.casefold() == identity.email.casefold()
    ):
        collision = await account_store.find_by_email(session, identity.email)
        if collision is None or collision.id == principal.account.id:
            principal.account.email = identity.email
            principal.account.email_verified = True
            await session.flush()
    await session.commit()
    get_axiom().info(
        source="api-auth",
        event_type="identity_linked",
        account_id=str(principal.account.id),
        provider=GOOGLE_PROVIDER,
    )
    return await session_envelope(
        session, principal.account, settings=services.settings, now=_now()
    )


# --- Wallet sign-in ----------------------------------------------------------------------


@router.post(
    "/wallet/challenge",
    response_model=account_schemas.WalletChallenge,
    summary="A nonce and the exact message to sign",
)
async def wallet_challenge(
    payload: WalletChallengeRequest,
    response: Response,
    services: ServicesDep,
    session: SessionDep,
) -> account_schemas.WalletChallenge:
    """Mint a single-use nonce for a coldkey.

    The message is domain-separated with the `conjectures-login-v1` prefix and pins the
    address, the nonce and the expiry, so a signature over it cannot be replayed into the
    coldkey-link flow, into another deployment, or for another address. It is stored verbatim
    and verified verbatim.
    """
    _no_store(response)
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
    _no_store(response)
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
        method="wallet-signature",
        user_agent=user_agent,
        source_ip=_client_ip(request, services.settings),
    )


# --- CLI sign-in -------------------------------------------------------------------------
# A linked coldkey exchanges a signature for a bearer token. Linking that coldkey to an account
# happens first, in a browser, and is not reachable from here — that asymmetry is the design:
# this endpoint can only ever hand out a credential for an account that already claimed the
# key. A key alone can never create an account or attach itself to one, so compromising one
# never produces a new identity, only a session on an identity that chose to include it.
#
# Any linked coldkey may open a session; only the one designated as
# `Account.submission_coldkey` can then submit, because `assert_coldkey_in_scope` binds a
# token to the key that minted it. Login is deliberately not gated on the designation — a
# miner should be able to authenticate and then read `GET /v1/me/coldkeys` to discover that
# they still have to designate a key, rather than be refused with nothing to act on.


@router.post(
    "/cli/challenge",
    response_model=account_schemas.WalletChallenge,
    summary="A nonce and the exact message a coldkey must sign",
)
async def cli_challenge(
    payload: CliChallengeRequest,
    response: Response,
    services: ServicesDep,
    session: SessionDep,
) -> account_schemas.WalletChallenge:
    """Mint a single-use nonce for a coldkey.

    Domain-separated with `conjectures-cli-session-v1`, which is not a prefix of and does not
    contain any of the other signed messages this validator asks for. That matters more here
    than anywhere else: this is the one message that mints a durable credential, and a miner's
    coldkey also signs every submission and every status read — so this flow has to be one a
    harvested signature from those paths cannot satisfy, and vice versa.

    **It does not say whether the coldkey is linked to anything.** A coldkey that has ever
    transacted is public on chain, so anyone can ask for a challenge for anyone's key; if the
    answer varied, this would be a free oracle mapping addresses to accounts on this
    deployment. The linkage is checked at verify, once a signature has proved the caller
    controls the key — at which point they are entitled to know.

    Rate-limited per address, like the other two nonce flows, because minting is an action
    taken against a key someone else holds.
    """
    _no_store(response)
    settings = services.settings
    address = _assert_ss58(payload.address)
    now = _now()
    issued = await account_store.recent_challenge_count(
        session,
        kind=LoginChallengeKind.COLDKEY_SESSION,
        since=now - dt.timedelta(hours=1),
        ss58=address,
    )
    if issued >= settings.challenges_per_hour:
        raise TooManyRequests(
            "too many CLI sign-in challenges for that coldkey; try again later",
            reason_code=REASON_TOO_MANY_CHALLENGES,
        )

    nonce = sessions.new_token()
    expires_at = now + dt.timedelta(minutes=settings.challenge_minutes)
    message = login.cli_session_message(
        domain=settings.login_domain,
        address=address,
        nonce=nonce,
        expires_at=expires_at,
    )
    await account_store.create_challenge(
        session,
        kind=LoginChallengeKind.COLDKEY_SESSION,
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
    "/cli/verify",
    response_model=account_schemas.CliSession,
    summary="Verify the coldkey signature and mint a bearer token",
)
async def cli_verify(
    payload: CliVerifyRequest,
    request: Request,
    response: Response,
    services: ServicesDep,
    session: SessionDep,
    user_agent: Annotated[str | None, Header(alias="User-Agent")] = None,
) -> account_schemas.CliSession:
    """Check the signature, find the account that linked this coldkey, and issue a token.

    **The order of the five steps is the security of this endpoint**, and each boundary was
    chosen against a specific failure:

    1. **Find the challenge by its own nonce.** Not "the latest open challenge for this
       address", which is how the browser sign-in flow does it — that is a denial-of-service
       primitive when the address is public, and a coldkey that has transacted is public on
       chain. See `accounts.open_challenge_by_nonce`.
    2. **Verify the signature over the stored message.** Before anything is consumed and before
       anything is disclosed. The message comes off the row, never rebuilt.
    3. **Count a failed attempt** if it did not verify, and refuse. The challenge survives a
       wrong signature — otherwise a bad request forces the user to start over — but not
       unboundedly many, or an open challenge would be free sr25519 work for an anonymous
       caller.
    4. **Resolve the account, and refuse an unlinked coldkey — with the nonce still unspent.**
       This is the common first-run error, and burning the nonce on it would mean a fresh
       challenge, a fresh passphrase prompt and a fresh signature per attempt, for a condition
       the miner has to go and fix in a browser anyway.
    5. **Consume, then issue.** Consuming last means the nonce is spent exactly when a token
       comes into existence, and the conditional UPDATE is what makes two simultaneous
       redemptions of one valid signature produce one token.
    """
    _no_store(response)
    settings = services.settings
    address = _assert_ss58(payload.address)
    signature = _normalise_signature(payload.signature)
    now = _now()

    challenge = await account_store.open_challenge_by_nonce(
        session,
        kind=LoginChallengeKind.COLDKEY_SESSION,
        ss58=address,
        secret_digest=account_store.digest(payload.nonce),
        now=now,
        max_attempts=settings.challenge_attempts,
    )
    if challenge is None or challenge.message is None:
        # One refusal for expired, already-used, out-of-attempts, wrong-address and
        # never-existed. A caller learns nothing about which, and the action is the same for
        # all of them: request a new challenge.
        raise Unauthorized(
            "no open CLI sign-in challenge for that coldkey and nonce; request a new one",
            reason_code=login.REASON_CHALLENGE_INVALID,
        )

    try:
        login.verify_signature(
            address=address, message=challenge.message, signature=signature
        )
    except Unauthorized:
        # Counted and committed before re-raising, so the attempt is recorded even though the
        # request fails. Without the commit the increment would roll back with the response and
        # the ceiling would never be reached.
        await account_store.record_failed_attempt(session, challenge.id)
        await session.commit()
        raise

    # Any linked wallet, not only the designated submission coldkey. See the section comment
    # above: a token for a non-designated key can read but not submit, and the capability
    # surface says so — which is more useful than refusing the login outright.
    account = await account_store.find_by_coldkey(session, address)
    if account is None:
        # 403, not 401: the caller proved they control the key. What is missing is a *link*, and
        # only the website can create one — a key must not be able to claim an account for
        # itself, or a stolen key would be a way in rather than merely a way to work.
        raise Forbidden(
            "that coldkey is not linked to an account; link it at the website first",
            reason_code=login.REASON_COLDKEY_NOT_LINKED,
        )

    consumed = await account_store.consume_challenge(
        session,
        kind=LoginChallengeKind.COLDKEY_SESSION,
        secret_digest=bytes(challenge.secret_sha256),
        now=now,
    )
    if consumed is None:
        # Lost a race with another request presenting the same valid signature.
        raise Unauthorized(
            "that challenge has already been used; request a new one",
            reason_code=login.REASON_CHALLENGE_INVALID,
        )

    await _evict_surplus_cli_sessions(session, account, settings, now=now)

    issued = await sessions.issue_bearer(
        session,
        account,
        coldkey=address,
        now=now,
        lifetime=dt.timedelta(days=settings.cli_session_days),
        user_agent=user_agent,
        source_ip=_client_ip(request, services.settings),
    )
    await session.commit()

    # The token is never a field on an event. Nor is the nonce. What is worth recording is that
    # a CLI session was minted, for which account, and under which coldkey — the address is
    # public on chain once it has transacted, unlike an email address, so it is safe here and it
    # is the one field that makes "a token appeared on a machine I do not recognise" answerable.
    get_axiom().info(
        source="api-auth",
        event_type="login_completed",
        account_id=str(account.id),
        method="cli-coldkey-signature",
        session_kind=str(AccountSessionKind.BEARER),
        coldkey=address,
        privileged=bool(set(account.roles or ()) - {MINER_ROLE}),
    )
    return account_schemas.CliSession(
        access_token=issued.token,
        token_type=sessions.BEARER_TOKEN_TYPE,
        expires_at=issued.row.expires_at,
        coldkey_scope=address,
        account=await account_response(session, account, bearer_scope=address),
    )


async def _evict_surplus_cli_sessions(
    session, account: Account, settings: Settings, *, now: dt.datetime
) -> None:
    """Keep an account's live CLI tokens under the configured ceiling.

    Every `conjectures auth login` mints a token, and nothing about the flow requires the miner
    to ever log out — a rig is reimaged, a laptop is replaced, and the row stays live until it
    expires. Left unbounded, a key that can mint can accumulate durable credentials at
    `challenges_per_hour` forever, each needing its own revocation.

    The oldest live token is evicted rather than the newest refused. Refusing would let a stale
    token on a machine the miner no longer has lock them out of the tooling on the machine they
    are sitting at, which is a worse failure than silently retiring something already unused —
    and the sessions listing shows exactly what is live, so the eviction is visible.
    """
    ceiling = settings.cli_sessions_per_account
    while True:
        live = await account_store.live_session_count(
            session, account.id, kind=AccountSessionKind.BEARER, now=now
        )
        if live < ceiling:
            return
        oldest = await account_store.oldest_live_session(
            session, account.id, kind=AccountSessionKind.BEARER, now=now
        )
        if oldest is None:  # pragma: no cover - live > 0 guarantees one exists
            return
        await account_store.revoke_session(session, oldest.id)
        get_axiom().info(
            source="api-auth",
            event_type="session_revoked",
            account_id=str(account.id),
            session_kind=str(AccountSessionKind.BEARER),
            reason="cli_session_ceiling",
            coldkey=oldest.coldkey_scope,
        )


__all__ = ["router"]
