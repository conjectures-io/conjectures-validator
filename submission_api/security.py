"""The session credentials, declared so OpenAPI knows they exist.

FastAPI documents only the credentials it was *told* about. Authentication here reads the
`Authorization` header and the session cookie off the raw request, which authenticates
correctly and documents nothing: `/docs` shows no padlock, offers nowhere to paste a CLI
token, and a generated client cannot tell that an endpoint needs one. These declarations
close that gap. They perform the same reads, expressed as `fastapi.security` objects, so the
schemes land in `components.securitySchemes` and every operation whose dependency tree
touches one carries the matching `security` requirement — in Swagger, in ReDoc, and in
anything generated from `/openapi.json`.

**They declare and extract; they never decide.** Both are `auto_error=False`, so the refusals
stay with `require_principal`, `require_writer`, `require_cookie_writer` and `require_role`,
which answer in this API's error envelope with the reason codes a client branches on
(`NOT_AUTHENTICATED`, `CROSS_SITE_WRITE_REFUSED`, `BROWSER_SESSION_REQUIRED`, ...). FastAPI's
own 401 would be a bare `{"detail": "Not authenticated"}` raised *before* the dependency that
knows what this endpoint actually requires ever ran, and it would break the one route that is
legitimately open to signed-in and anonymous callers alike, `GET /v1/auth/session`.

Two schemes and not one, because there are two separate things a caller can present and no
endpoint accepts them interchangeably:

* `CliBearerToken` — the CLI's opaque `conj_cli_…` token in `Authorization: Bearer`, minted by
  `POST /v1/auth/cli/verify`. The one a human wants the Authorize dialog for.
* `BrowserSession` — the HttpOnly session cookie. Declared so the document is honest about
  what authenticates the website; a browser attaches it by itself, and Swagger UI could not
  set an HttpOnly cookie from the dialog even if asked to.

**There is no third scheme for the write guard, and that is not an omission.** A write on a
cookie session must also arrive with an `Origin` on the write allowlist or a same-origin
`Sec-Fetch-Site` — see `submission_api/origin_policy.py`. Neither is a credential. Both are on
the Fetch spec's forbidden-header list, which means the browser writes them and no client,
Swagger UI included, is permitted to. Declaring them as security schemes would put a padlock on
an operation and an input box beside it for two values a caller can neither supply nor obtain,
and would imply something a client can be handed and reuse — exactly what they are not. This is
the same reason the miner's hotkey-signature routes carry no scheme: their proof is a
per-request signature in `X-Conjectures-*` headers already declared as `Header(...)` parameters.

(There was a third scheme, `BrowserCsrfToken`, for the `X-Conjectures-CSRF` header. The header
is retired — see `submission_api/sessions.py` — and the scheme went with it. The header name
still appears in `CORS_REQUEST_HEADERS` for one deploy cycle so an older frontend does not fail
preflight; it is unread, and documenting a credential nothing checks would be worse than
documenting nothing.)

One thing the document still cannot say, which the descriptions below say instead: a route
gated by `require_cookie_writer` still lists `CliBearerToken`, because requirements are
collected from the whole dependency tree and that gate sits *above* the resolution step rather
than replacing it. That is the real behaviour — a bearer token is read, then refused with
`BROWSER_SESSION_REQUIRED` — just not what a padlock suggests.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Security
from fastapi.security import (
    APIKeyCookie,
    HTTPAuthorizationCredentials,
    HTTPBearer,
)

from submission_api import sessions as session_layer

BEARER_SCHEME_NAME = "CliBearerToken"
COOKIE_SCHEME_NAME = "BrowserSession"

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
        "A bearer session is scoped to the one hotkey that minted it, and may exercise only "
        "the `MINER` role. It is **not** an ambient credential — nothing attaches it unless "
        "code chose to — so a write from one does not have to prove where it was initiated. "
        "Writes that change who the account is or where its money goes refuse it with "
        "`BROWSER_SESSION_REQUIRED`."
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
        "A **write** authenticated by this cookie must also arrive with an `Origin` on the "
        "API's write allowlist, or with `Sec-Fetch-Site: same-origin`. Both are browser-set "
        "headers no page may forge, which is what makes them proof that the request was not "
        "caused by another site; neither can be supplied from this dialog, and a browser "
        "calling the API from an allowlisted origin sends them without being asked. A write "
        "that shows neither is refused with `CROSS_SITE_WRITE_REFUSED`."
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
    "BearerCredentialsDep",
    "SessionCookieDep",
    "browser_session",
    "cli_bearer",
    "offered_bearer",
]
