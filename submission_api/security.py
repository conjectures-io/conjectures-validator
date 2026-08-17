"""The session credentials, declared so OpenAPI knows they exist.

FastAPI documents only the credentials it was *told* about. Authentication here reads the
`Authorization` header and the session cookie off the raw request, which authenticates
correctly and documents nothing: `/docs` shows no padlock, offers nowhere to paste a CLI
token, and a generated client cannot tell that an endpoint needs one. These declarations
close that gap. They perform the same reads, expressed as `fastapi.security` objects, so the
schemes land in `components.securitySchemes` and every operation whose dependency tree
touches one carries the matching `security` requirement — in Swagger, in ReDoc, and in
anything generated from `/openapi.json`.

**They declare and extract; they never decide.** All three are `auto_error=False`, so the
refusals stay with `require_principal`, `require_writer`, `require_cookie_writer` and
`require_role`, which answer in this API's error envelope with the reason codes a client
branches on (`NOT_AUTHENTICATED`, `CSRF_CHECK_FAILED`, `BROWSER_SESSION_REQUIRED`, ...).
FastAPI's own 401 would be a bare `{"detail": "Not authenticated"}` raised *before* the
dependency that knows what this endpoint actually requires ever ran, and it would break the
one route that is legitimately open to signed-in and anonymous callers alike,
`GET /v1/auth/session`.

Three schemes and not one, because there are three separate things a caller can present and
no endpoint accepts all three interchangeably:

* `CliBearerToken` — the CLI's opaque `conj_cli_…` token in `Authorization: Bearer`, minted by
  `POST /v1/auth/cli/verify`. The one a human wants the Authorize dialog for.
* `BrowserSession` — the HttpOnly session cookie. Declared so the document is honest about
  what authenticates the website; a browser attaches it by itself, and Swagger UI could not
  set an HttpOnly cookie from the dialog even if asked to.
* `BrowserCsrfToken` — the header a cookie-authenticated **write** must also carry. Declared
  for the same reason: it is a required credential, and it was invisible.

Two things the document cannot say, which the descriptions below say instead:

* OpenAPI's `security` list is an OR of alternatives, so a write documents as "bearer, or
  cookie, or CSRF header" where the truth is "bearer, or cookie *and* CSRF header". Expressing
  the mixture would mean one requirement object naming both cookie and header — which would
  then also demand a bearer token, since a single object is an AND.
* A route gated by `require_cookie_writer` still lists `CliBearerToken`, because requirements
  are collected from the whole dependency tree and that gate sits *above* the resolution step
  rather than replacing it. That is the real behaviour — a bearer token is read, then refused
  with `BROWSER_SESSION_REQUIRED` — just not what a padlock suggests.

Nothing here touches the miner's hotkey-signature routes. Those carry their proof in
`X-Conjectures-*` headers already declared as `Header(...)` parameters, or in the request body,
so they are documented without a scheme. Calling a per-request signature a security scheme
would imply a credential a client can be handed and reuse, which is exactly what it is not.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Security
from fastapi.security import (
    APIKeyCookie,
    APIKeyHeader,
    HTTPAuthorizationCredentials,
    HTTPBearer,
)

from submission_api import sessions as session_layer

BEARER_SCHEME_NAME = "CliBearerToken"
COOKIE_SCHEME_NAME = "BrowserSession"
CSRF_SCHEME_NAME = "BrowserCsrfToken"

cli_bearer = HTTPBearer(
    scheme_name=BEARER_SCHEME_NAME,
    # Not a JWT, and deliberately so — see `sessions.py`. Saying `opaque` keeps a reader from
    # trying to decode a claim set out of it.
    bearerFormat="opaque",
    description=(
        f"A CLI session token, minted by `POST /v1/auth/cli/verify` and prefixed "
        f"`{session_layer.BEARER_TOKEN_PREFIX}`. Sent as `Authorization: Bearer <token>`.\n\n"
        "Presenting one *is* the choice of identity: when this header is present it is the "
        "credential, and there is no fall back to the session cookie if it turns out to be "
        "expired or revoked.\n\n"
        "A bearer session is scoped to the one hotkey that minted it, exempt from the CSRF "
        "check, and may exercise only the `MINER` role. Writes that change who the account is "
        "or where its money goes refuse it with `BROWSER_SESSION_REQUIRED`."
    ),
    auto_error=False,
)

browser_session = APIKeyCookie(
    name=session_layer.SESSION_COOKIE,
    scheme_name=COOKIE_SCHEME_NAME,
    description=(
        "The website's session cookie, set by the sign-in routes. `HttpOnly`, so page script "
        "cannot read it and neither can the Authorize dialog — a browser sends it on its own, "
        "and it is documented here so the schema names every credential this API accepts.\n\n"
        f"A **write** authenticated by this cookie must also send the `{session_layer.CSRF_HEADER}` "
        "header (`BrowserCsrfToken`)."
    ),
    auto_error=False,
)

browser_csrf = APIKeyHeader(
    name=session_layer.CSRF_HEADER,
    scheme_name=CSRF_SCHEME_NAME,
    description=(
        f"The CSRF token for the current cookie session, copied out of the "
        f"`{session_layer.CSRF_COOKIE}` cookie. Required on every state-changing request "
        "authenticated by the session cookie; compared against the digest stored on that "
        "session row, so a value the client invents is not itself the proof.\n\n"
        "Not required — and not accepted as a substitute for anything — when the request "
        "authenticates with `CliBearerToken`: a bearer token is not an ambient credential, so "
        "there is no confused deputy for it to prove it is not."
    ),
    auto_error=False,
)

# What a handler names to receive the extracted credential. `Security` rather than `Depends`
# so FastAPI records the requirement on the operation; `Depends` would resolve the value and
# document nothing, which is the state this module exists to fix.
BearerCredentialsDep = Annotated[
    HTTPAuthorizationCredentials | None, Security(cli_bearer)
]
SessionCookieDep = Annotated[str | None, Security(browser_session)]
CsrfTokenDep = Annotated[str | None, Security(browser_csrf)]


def offered_bearer(credentials: HTTPAuthorizationCredentials | None) -> str | None:
    """The token out of an accepted `Authorization: Bearer` header, or None.

    `HTTPBearer` has already discarded a missing header, a bare value with no scheme, and a
    scheme that is not `bearer`. What it does not do is bound the length or trim the value, so
    the result goes through `sessions.accept_bearer` — the one place that says what this API
    is willing to hash — rather than being trusted for having arrived in the right header.
    """
    if credentials is None:
        return None
    return session_layer.accept_bearer(credentials.credentials)


__all__ = [
    "BEARER_SCHEME_NAME",
    "COOKIE_SCHEME_NAME",
    "CSRF_SCHEME_NAME",
    "BearerCredentialsDep",
    "CsrfTokenDep",
    "SessionCookieDep",
    "browser_csrf",
    "browser_session",
    "cli_bearer",
    "offered_bearer",
]
