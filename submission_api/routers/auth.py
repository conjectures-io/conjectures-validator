"""Sign in, sign out, and read the current session.

Four ways in. Three are for a browser and end in an HttpOnly cookie: a Google identity, a magic
link to an email address, and a signature from a coldkey. The fourth is for the miner CLI and ends
in a bearer token: a signature from a hotkey that has already been linked to an account in the
browser. See `submission_api/sessions.py` for the two credentials and why only one of them has to
prove where a write was initiated, `submission_api/origin_policy.py` for how it proves it, and
`submission_api/login.py` for the signed messages.

Seven things here are security decisions rather than conveniences:

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
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import secrets
from typing import Annotated
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, Header, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from conjectures_subnet.axiom import get_axiom
from conjectures_subnet.db import accounts as account_store
from conjectures_subnet.db.errors import RecordConflict
from conjectures_subnet.db.models import (
    MINER_ROLE,
    Account,
    AccountSessionKind,
    LoginChallengeKind,
)
from submission_api import login, mail, schemas_account as account_schemas, sessions
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


class GoogleCredentialRequest(Payload):
    credential: str = Field(min_length=100, max_length=MAX_GOOGLE_CREDENTIAL_LENGTH)


class CliChallengeRequest(Payload):
    address: str = Field(
        min_length=48, max_length=48, description="The hotkey that will sign"
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
    the linked hotkeys, the payout destination, the credit balance, the badge counts and the
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
        bearer_scope=principal.hotkey_scope,
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
                    base_url=settings.website_base_url, token=token
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
    which is the same class of change as linking a hotkey or repointing the payout, and it is
    refused to a CLI token for the same reason: a bearer token is minted by a hotkey that
    Bittensor stores unencrypted on disk, so allowing this would turn one stolen file into "link
    my Google account, then sign in as them". This read as cookie-only before CLI sessions
    existed; it has to say so now that they do.
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
    hotkey-link flow, into another deployment, or for another address. It is stored verbatim
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
# A hotkey exchanges a signature for a bearer token. Linking the hotkey to an account happens
# first, in a browser, and is not reachable from here — that asymmetry is the design: this
# endpoint can only ever hand out a credential for an account that a *coldkey or a mailbox*
# already claimed the hotkey for. A hotkey alone can never create an account or attach itself
# to one, so compromising a hotkey never produces a new identity, only a session on an identity
# that already chose to include it.


@router.post(
    "/cli/challenge",
    response_model=account_schemas.WalletChallenge,
    summary="A nonce and the exact message a hotkey must sign",
)
async def cli_challenge(
    payload: CliChallengeRequest,
    response: Response,
    services: ServicesDep,
    session: SessionDep,
) -> account_schemas.WalletChallenge:
    """Mint a single-use nonce for a hotkey.

    Domain-separated with `conjectures-cli-session-v1`, which is not a prefix of and does not
    contain any of the other four signed messages this validator asks for. That matters more
    here than anywhere else: a hotkey signs routinely — every submission and every status read
    is a signature — so this flow has to be one a harvested signature from those paths cannot
    satisfy, and vice versa.

    **It does not say whether the hotkey is linked to anything.** A hotkey is public on chain,
    so anyone can ask for a challenge for anyone's key; if the answer varied, this would be a
    free oracle mapping hotkeys to accounts on this deployment. The linkage is checked at
    verify, once a signature has proved the caller controls the key — at which point they are
    entitled to know.

    Rate-limited per address, like the other two nonce flows, because minting is an action
    taken against a key someone else holds.
    """
    _no_store(response)
    settings = services.settings
    address = _assert_ss58(payload.address)
    now = _now()
    issued = await account_store.recent_challenge_count(
        session,
        kind=LoginChallengeKind.HOTKEY_SESSION,
        since=now - dt.timedelta(hours=1),
        ss58=address,
    )
    if issued >= settings.challenges_per_hour:
        raise TooManyRequests(
            "too many CLI sign-in challenges for that hotkey; try again later",
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
        kind=LoginChallengeKind.HOTKEY_SESSION,
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
    summary="Verify the hotkey signature and mint a bearer token",
)
async def cli_verify(
    payload: CliVerifyRequest,
    request: Request,
    response: Response,
    services: ServicesDep,
    session: SessionDep,
    user_agent: Annotated[str | None, Header(alias="User-Agent")] = None,
) -> account_schemas.CliSession:
    """Check the signature, find the account that linked this hotkey, and issue a token.

    **The order of the five steps is the security of this endpoint**, and each boundary was
    chosen against a specific failure:

    1. **Find the challenge by its own nonce.** Not "the latest open challenge for this
       address", which is how the coldkey flow does it — that is a denial-of-service primitive
       when the address is public, and hotkeys are published on chain. See
       `accounts.open_challenge_by_nonce`.
    2. **Verify the signature over the stored message.** Before anything is consumed and before
       anything is disclosed. The message comes off the row, never rebuilt.
    3. **Count a failed attempt** if it did not verify, and refuse. The challenge survives a
       wrong signature — otherwise a bad request forces the user to start over — but not
       unboundedly many, or an open challenge would be free sr25519 work for an anonymous
       caller.
    4. **Resolve the account, and refuse an unlinked hotkey — with the nonce still unspent.**
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
        kind=LoginChallengeKind.HOTKEY_SESSION,
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
            "no open CLI sign-in challenge for that hotkey and nonce; request a new one",
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

    account = await account_store.find_by_hotkey(session, address)
    if account is None:
        # 403, not 401: the caller proved they control the key. What is missing is a *link*, and
        # only the website can create one — a hotkey must not be able to claim an account for
        # itself, or a stolen hotkey would be a way in rather than merely a way to work.
        raise Forbidden(
            "that hotkey is not linked to an account; link it with a coldkey first",
            reason_code=login.REASON_HOTKEY_NOT_LINKED,
        )

    consumed = await account_store.consume_challenge(
        session,
        kind=LoginChallengeKind.HOTKEY_SESSION,
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
        hotkey=address,
        now=now,
        lifetime=dt.timedelta(days=settings.cli_session_days),
        user_agent=user_agent,
        source_ip=_client_ip(request, services.settings),
    )
    await session.commit()

    # The token is never a field on an event. Nor is the nonce. What is worth recording is that
    # a CLI session was minted, for which account, and under which hotkey — the hotkey is
    # already published alongside verified results, unlike an email address, so it is safe here
    # and it is the one field that makes "a token appeared on a machine I do not recognise"
    # answerable.
    get_axiom().info(
        source="api-auth",
        event_type="login_completed",
        account_id=str(account.id),
        method="cli-hotkey-signature",
        session_kind=str(AccountSessionKind.BEARER),
        hotkey=address,
        privileged=bool(set(account.roles or ()) - {MINER_ROLE}),
    )
    return account_schemas.CliSession(
        access_token=issued.token,
        token_type=sessions.BEARER_TOKEN_TYPE,
        expires_at=issued.row.expires_at,
        hotkey_scope=address,
        account=await account_response(session, account, bearer_scope=address),
    )


async def _evict_surplus_cli_sessions(
    session, account: Account, settings: Settings, *, now: dt.datetime
) -> None:
    """Keep an account's live CLI tokens under the configured ceiling.

    Every `conjectures auth login` mints a token, and nothing about the flow requires the miner
    to ever log out — a rig is reimaged, a laptop is replaced, and the row stays live until it
    expires. Left unbounded, a hotkey that can mint can accumulate durable credentials at
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
            hotkey=oldest.hotkey_scope,
        )


__all__ = ["router"]
