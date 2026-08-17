"""ASGI middleware for the network-facing surface.

Five concerns. The order they are composed in is in `app.py`; what each one owns is here:

* `SecurityHeadersMiddleware` — the response hardening every answer carries.
* `SessionCookieRefreshMiddleware` — keeps the browser's cookie expiry in step with the rolling
  server-side session.
* `ScopedCORSMiddleware` — browser access to `/v1`, and nothing else.
* `CrossOriginWriteGuard` — refuses a write whose initiator headers name a site that may not
  write here. The fail-closed counterpart for authenticated writes is a route dependency,
  because only the resolved route knows whether the credential was an ambient one.
* `RateLimitMiddleware` — the per-client ceiling on `/v1`.

Written as raw ASGI rather than as `BaseHTTPMiddleware` subclasses on purpose.
`BaseHTTPMiddleware` buffers the response through an anyio task group, which breaks the
streaming read in `POST /v1/submissions` and `PUT .../bundle` — those handlers consume
`request.stream()` under a running byte cap specifically so a hostile body dies mid-flight
instead of being buffered and then measured. A middleware that buffers it first would quietly
undo that.
"""

from __future__ import annotations

import ipaddress
import json
import time
from collections.abc import Iterable, MutableMapping
from typing import Any

from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from submission_api import origin_policy
from submission_api.errors import PROBLEM_MEDIA_TYPE
from submission_api.ratelimit import Decision, SlidingWindowLimiter
from submission_api.settings import Settings

PUBLIC_PREFIX = "/v1"
# Liveness and readiness are polled by the orchestrator on a fixed interval and must never be
# refused for rate: a limiter that returns 429 to a health probe takes the replica out of
# service. They are also the only paths that reveal nothing.
RATE_LIMIT_EXEMPT = ("/healthz", "/readyz")
DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")

REASON_RATE_LIMITED = "RATE_LIMITED"
UNKNOWN_CLIENT = "unknown"

# `default-src 'none'` is right for a JSON API: there is nothing to load, and a response that
# somehow rendered as HTML could not fetch or execute anything. `frame-ancestors 'none'` is the
# modern form of the X-Frame-Options below, which is kept for older browsers.
API_CSP = (
    "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; "
    "sandbox"
)
PERMISSIONS_POLICY = "camera=(), geolocation=(), microphone=(), payment=(), usb=()"


def client_address(scope: Scope, trusted_hops: int) -> str:
    """The address to bill this request to.

    `X-Forwarded-For` is client-writable, so it is read only as far as the deployment says its
    own proxies extend. Each proxy appends the peer it received from, so with `n` trusted hops
    the originating client is the `n`-th entry from the right; anything further left was written
    by the client and is ignored. `TRUSTED_PROXY_HOPS=0` — the default — ignores the header
    entirely and uses the peer address, which is correct for a directly exposed process and is
    the only setting that cannot be spoofed.

    Getting this wrong in either direction breaks the limiter: trusting an untrusted header lets
    one client mint unlimited keys, and ignoring a real one collapses every visitor behind a CDN
    onto a single budget.
    """
    peer = scope.get("client")
    fallback = peer[0] if peer else UNKNOWN_CLIENT
    if trusted_hops <= 0:
        return fallback
    forwarded = _header(scope, b"x-forwarded-for")
    if not forwarded:
        return fallback
    chain = [item.strip() for item in forwarded.split(",") if item.strip()]
    # A chain shorter than the configured hop count means the request did not come through the
    # proxies this deployment expects, so nothing in it is trustworthy.
    if len(chain) < trusted_hops:
        return fallback
    return _normalised_address(chain[-trusted_hops]) or fallback


def _normalised_address(value: str) -> str | None:
    """A parsed IP, or None. Parsing also bounds the length of an attacker-chosen limiter key."""
    candidate = value.strip()
    # A proxy may append `host:port` for IPv4, or `[v6]:port`.
    if candidate.startswith("["):
        candidate = candidate.partition("]")[0].removeprefix("[")
    elif candidate.count(":") == 1:
        candidate = candidate.partition(":")[0]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def _header(scope: Scope, name: bytes) -> str:
    for key, value in scope.get("headers", ()):
        if key == name:
            return value.decode("latin-1")
    return ""


def _set_header(headers: list[tuple[bytes, bytes]], name: str, value: str) -> None:
    """Set a header, replacing any the application already produced."""
    lowered = name.lower().encode("latin-1")
    headers[:] = [item for item in headers if item[0].lower() != lowered]
    headers.append((lowered, value.encode("latin-1")))


class SecurityHeadersMiddleware:
    """Response hardening applied to every answer this process gives.

    HSTS is only sent in production. In development the API is reached over plain HTTP on
    localhost, and an HSTS header there pins the developer's browser to HTTPS for every other
    service on localhost too — a genuinely disruptive thing to leave behind.

    The strict CSP is not applied to the interactive docs, which are served only outside
    production and need to load their own script and stylesheet. Everything else on this process
    returns JSON.
    """

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self._app = app
        self._settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        path = scope.get("path", "")
        is_docs = any(path.startswith(prefix) for prefix in DOCS_PATHS)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                _set_header(headers, "X-Content-Type-Options", "nosniff")
                _set_header(headers, "Referrer-Policy", "no-referrer")
                _set_header(headers, "X-Frame-Options", "DENY")
                _set_header(headers, "Cross-Origin-Opener-Policy", "same-origin")
                # `cross-origin`, not `same-origin`: this is a public API that a website on
                # another origin is meant to read. The allowlist in CORS is what restricts it.
                _set_header(headers, "Cross-Origin-Resource-Policy", "cross-origin")
                _set_header(headers, "Permissions-Policy", PERMISSIONS_POLICY)
                if not is_docs:
                    _set_header(headers, "Content-Security-Policy", API_CSP)
                if self._settings.production and self._settings.hsts_max_age > 0:
                    _set_header(
                        headers,
                        "Strict-Transport-Security",
                        f"max-age={self._settings.hsts_max_age}; includeSubDomains; preload",
                    )
                if self._settings.alt_svc:
                    # Advertises an HTTP/3 authority for clients to retry over QUIC. The ASGI
                    # app never sees the transport; the value describes the deployment's edge.
                    _set_header(headers, "Alt-Svc", self._settings.alt_svc)
                message = {**message, "headers": headers}
            await send(message)

        await self._app(scope, receive, send_with_headers)


class RateLimitMiddleware:
    """The per-client ceiling on `/v1`, reported in `RateLimit-*` headers.

    The header names are the draft-07 spelling (`RateLimit-Limit`, `RateLimit-Remaining`,
    `RateLimit-Reset`) rather than the structured-field successor, because that is the contract
    the website is written against. They are emitted on admitted responses too, not only on
    `429`, so a client can back off before being refused.

    A refusal is RFC 9457 `application/problem+json` with `reason_code: RATE_LIMITED`, matching
    every other failure this API produces, and carries `Retry-After`.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        limiter: SlidingWindowLimiter,
        trusted_proxy_hops: int,
        prefix: str = PUBLIC_PREFIX,
        exempt: Iterable[str] = RATE_LIMIT_EXEMPT,
    ) -> None:
        self._app = app
        self._limiter = limiter
        self._trusted_proxy_hops = trusted_proxy_hops
        self._prefix = prefix
        self._exempt = tuple(exempt)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if (
            scope["type"] != "http"
            or not path.startswith(self._prefix)
            or path.startswith(self._exempt)
        ):
            await self._app(scope, receive, send)
            return
        # A preflight is not a data read and carries no credentials; refusing it for rate would
        # surface as an opaque CORS failure rather than as the 429 it is.
        if scope.get("method") == "OPTIONS":
            await self._app(scope, receive, send)
            return

        decision = self._limiter.check(
            client_address(scope, self._trusted_proxy_hops), time.monotonic()
        )
        if not decision.allowed:
            await _problem_response(send, decision)
            return

        async def send_with_budget(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                _apply_budget(headers, decision)
                message = {**message, "headers": headers}
            await send(message)

        await self._app(scope, receive, send_with_budget)


def _apply_budget(headers: list[tuple[bytes, bytes]], decision: Decision) -> None:
    _set_header(headers, "RateLimit-Limit", str(decision.limit))
    _set_header(headers, "RateLimit-Remaining", str(decision.remaining))
    _set_header(headers, "RateLimit-Reset", str(decision.reset_seconds))


async def _problem_response(send: Send, decision: Decision) -> None:
    body = json.dumps(
        {
            "type": "about:blank",
            "title": "Too many requests",
            "status": 429,
            "detail": "request rate exceeded; retry after the interval in Retry-After",
            "reason_code": REASON_RATE_LIMITED,
        }
    ).encode("utf-8")
    headers: list[tuple[bytes, bytes]] = [
        (b"content-type", PROBLEM_MEDIA_TYPE.encode("latin-1")),
        (b"content-length", str(len(body)).encode("latin-1")),
        (b"retry-after", str(decision.reset_seconds).encode("latin-1")),
    ]
    _apply_budget(headers, decision)
    await send({"type": "http.response.start", "status": 429, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class SessionCookieRefreshMiddleware:
    """Keep the browser's cookie expiry in step with the rolling server-side session.

    The session is "30-day rolling", and `accounts.touch_session` extends `expires_at` in the
    database. But the browser expires the cookie by the `Max-Age` it was *set* with, so without
    this a continuously active user would be signed out on day 30 anyway — the server would still
    consider the session live, and the credential would simply stop being sent.

    So: whenever a request arrived with a session cookie and the response does not already set
    one, re-send the same token with a fresh `Max-Age`. It touches no database and reads no
    session state, which is what makes it cheap enough to run on every authenticated request.

    It re-sends the token the client already had, so it cannot create or extend a session that
    the server has not authorised: an expired or revoked session still fails `resolve`, and this
    only refreshes the envelope around a credential the client was already holding.

    **Cookie-only by construction, and it must stay that way.** The gate below is the *request*
    cookie, so a request authenticated by an `Authorization: Bearer` header never reaches the
    `Set-Cookie` path. That is not incidental: putting a bearer token into a `Set-Cookie` header
    would make a CLI credential ambient in any client that keeps a cookie jar, which is exactly
    the property the bearer flow's write-guard exemption assumes it does not have. The obvious future
    "improvement" — refresh the credential for every authenticated session, not just cookie ones
    — is the way that invariant gets broken, so it is written down here and asserted in
    `tests/test_api_accounts.py`.
    """

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self._app = app
        self._settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        from submission_api.sessions import SESSION_COOKIE, session_cookie

        token = _cookie_value(scope, SESSION_COOKIE)
        if not token:
            await self._app(scope, receive, send)
            return

        max_age = self._settings.session_days * 24 * 60 * 60
        secure = self._settings.production

        async def send_with_refresh(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                # A handler that signed in, signed out, or otherwise set the cookie itself owns
                # the answer; do not overwrite a logout's clearing header with a refresh.
                already = any(
                    key.lower() == b"set-cookie"
                    and value.startswith(SESSION_COOKIE.encode("latin-1"))
                    for key, value in headers
                )
                if not already and 200 <= message.get("status", 500) < 400:
                    headers.append(
                        (
                            b"set-cookie",
                            session_cookie(
                                token, max_age=max_age, secure=secure
                            ).encode("latin-1"),
                        )
                    )
                message = {**message, "headers": headers}
            await send(message)

        await self._app(scope, receive, send_with_refresh)


def _cookie_value(scope: Scope, name: str) -> str | None:
    """One cookie from the request, without pulling in a full cookie parser."""
    raw = _header(scope, b"cookie")
    if not raw:
        return None
    for item in raw.split(";"):
        key, _, value = item.strip().partition("=")
        if key == name:
            return value or None
    return None


class ScopedCORSMiddleware:
    """Starlette's `CORSMiddleware`, applied to `/v1` and nowhere else.

    Delegating rather than reimplementing: CORS has enough sharp edges — preflight handling,
    `Vary: Origin`, credentialed-versus-not — that a hand-rolled version is a liability. This
    only decides *whether* the audited implementation sees the request.

    Scoping matters because `/healthz` and `/readyz` are for the orchestrator and the docs are
    for a developer; none of them should grow a cross-origin read path because a website needed
    one on `/v1`.
    """

    def __init__(self, app: ASGIApp, *, prefix: str = PUBLIC_PREFIX, **cors: Any) -> None:
        self._plain = app
        self._cors = CORSMiddleware(app, **cors)
        self._prefix = prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path", "").startswith(self._prefix):
            await self._cors(scope, receive, send)
            return
        await self._plain(scope, receive, send)


def cors_options(settings: Settings) -> MutableMapping[str, Any]:
    """The CORS configuration, derived from settings.

    `allow_credentials=True` since Stage 2, because the session is an HttpOnly cookie and the
    browser has to be permitted to send it. That is a real widening, and it is what makes the
    exact-origin allowlist load-bearing rather than merely tidy: with credentials allowed, an
    allowlisted origin can read authenticated responses, so an XSS on any listed site reaches
    the reader's account. Two consequences are enforced elsewhere and worth naming here —
    `Settings` refuses a wildcard or plaintext origin in production, and `CrossOriginWriteGuard`
    below guards every state-changing request.

    This list is the *read* allowlist. Writes have their own, `WRITE_ALLOWED_ORIGINS`, which
    defaults to this one; see `Settings`. They are separable because reading the catalog and
    spending an account's credits are not the same grant.

    Methods still exclude `POST /v1/submissions`' verb set for the browser: writes are allowed
    only on the account surface. `POST /v1/submissions` is called by miner tooling, never by a
    browser, and it authenticates with a hotkey signature rather than a cookie — so a page on a
    compromised allowlisted origin still cannot spend a miner's payment.
    """
    from submission_api.settings import (
        CORS_EXPOSED_HEADERS,
        CORS_MAX_AGE_SECONDS,
        CORS_METHODS,
        CORS_REQUEST_HEADERS,
    )

    return {
        "allow_origins": list(settings.cors_allowed_origins),
        "allow_methods": list(CORS_METHODS),
        "allow_headers": list(CORS_REQUEST_HEADERS),
        "expose_headers": list(CORS_EXPOSED_HEADERS),
        "allow_credentials": True,
        "max_age": CORS_MAX_AGE_SECONDS,
    }


# --- Cross-site writes -------------------------------------------------------------------------


class CrossOriginWriteGuard:
    """Refuse a state-changing request that a browser said came from somewhere else.

    The coarse half of the write guard. It reads the two initiator headers — `Origin` and
    `Sec-Fetch-Site`, neither of which a page can set — and refuses when they name an initiator
    that is not on the write allowlist. `submission_api/origin_policy.py` holds the decision and
    the reasoning behind it.

    **It refuses only a positive `REFUSED`, never an `UNPROVEN`.** A request carrying neither
    header is not a browser request, and a client that is not a browser has no ambient
    credential for a cross-site page to abuse — it has to have been handed the credential
    deliberately. Miner tooling, the CLI, `curl`, and the payment processor's webhook all land
    here, and refusing them would buy nothing. The fail-*closed* half of the guard is
    `dependencies.require_writer`, which knows that the request authenticated with a cookie and
    so demands an `ALLOWED` outright.

    Running here as well as there is not redundancy for its own sake. This layer covers the
    writes that have no authenticated principal for a dependency to inspect —
    `POST /v1/auth/email/request-link` sends mail, so a cross-site page must not be able to
    trigger it — and it refuses before a route parses a body.

    The hotkey-signature endpoints are exempt by path. They carry no cookie and authenticate a
    signature instead, so there is no ambient credential in play at all.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        allowed_origins: Iterable[str],
        prefix: str = PUBLIC_PREFIX,
        exempt_prefixes: Iterable[str] = (),
    ) -> None:
        self._app = app
        self._allowed = frozenset(allowed_origins)
        self._prefix = prefix
        self._exempt = tuple(exempt_prefixes)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if (
            scope["type"] != "http"
            or scope.get("method", "GET") in origin_policy.SAFE_METHODS
            or not path.startswith(self._prefix)
            or path.startswith(self._exempt)
        ):
            await self._app(scope, receive, send)
            return

        verdict = origin_policy.classify(
            origin=_header(scope, b"origin") or None,
            fetch_site=_header(scope, b"sec-fetch-site") or None,
            allowed_origins=self._allowed,
        )
        if verdict is origin_policy.Initiator.REFUSED:
            await _cross_site_refusal(
                send, "this request was initiated by a site that may not change state here"
            )
            return

        await self._app(scope, receive, send)


async def _cross_site_refusal(send: Send, detail: str) -> None:
    from submission_api.sessions import REASON_CROSS_SITE_REFUSED

    body = json.dumps(
        {
            "type": "about:blank",
            "title": "Not permitted",
            "status": 403,
            "detail": detail,
            "reason_code": REASON_CROSS_SITE_REFUSED,
        }
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 403,
            "headers": [
                (b"content-type", PROBLEM_MEDIA_TYPE.encode("latin-1")),
                (b"content-length", str(len(body)).encode("latin-1")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


__all__ = [
    "API_CSP",
    "PUBLIC_PREFIX",
    "REASON_RATE_LIMITED",
    "CrossOriginWriteGuard",
    "SessionCookieRefreshMiddleware",
    "RateLimitMiddleware",
    "ScopedCORSMiddleware",
    "SecurityHeadersMiddleware",
    "client_address",
    "cors_options",
]
