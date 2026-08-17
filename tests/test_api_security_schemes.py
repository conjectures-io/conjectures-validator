"""Every endpoint that takes a session credential says so in the OpenAPI document.

An app-wide guard, in the spirit of the query-parameter check in `test_api_catalog.py`: not
"this endpoint documents its bearer token" but "no endpoint can stop documenting it". The bug
this exists to prevent is silent and permanent — authentication that works but is invisible, so
`/docs` shows no padlock, offers nowhere to paste a CLI token, and a generated client has no
idea a credential is needed. Nothing fails; the documentation is just wrong, for as long as
nobody notices.

The other direction is guarded too, and it matters as much: the public read surface must ask
for nothing, and the **write guard must not become a scheme**. A write on a cookie session has
to arrive with an allowlisted `Origin` or a same-origin `Sec-Fetch-Site`, and neither is a
credential a caller can be handed — both are browser-set and forbidden to script, which is the
whole reason they are proof. Declaring them would put an input box in `/docs` for two values
nobody can supply. See `submission_api/security.py`.

Built on a bare app carrying the real routers rather than on `create_app`, because schema
generation needs none of what the real app needs — no PostgreSQL, no task pool, no pin lock.
The routers, their dependency trees and the scheme declarations under test are all the real
ones, and `test_the_real_app_serves_the_schemes` covers the wiring that this shortcut skips.
"""

from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("fastapi", reason="submission API tests need the service extra")
pytest.importorskip("sqlalchemy", reason="submission API tests need the db extra")
pytest.importorskip("psycopg", reason="submission API tests need the db extra")

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials

from submission_api import security, sessions
from submission_api.dependencies import (
    get_optional_principal,
    require_cookie_writer,
    require_writer,
)
from submission_api.routers import (
    admin,
    auth,
    catalog,
    health,
    intents,
    me,
    results,
    reviews,
    submissions,
    system,
    tasks,
    tmc_pay,
)

ROUTERS = (
    health,
    tasks,
    submissions,
    catalog,
    results,
    system,
    auth,
    me,
    intents,
    tmc_pay,
    admin,
    reviews,
)

# The header the retired CSRF token rode in. It must not reappear as a security scheme: it is
# unread, and documenting a credential nothing checks is worse than documenting nothing. It is
# still on `CORS_REQUEST_HEADERS` for one deploy cycle, which is a different list — that one is
# about what a preflight permits, not about what a caller must send.
RETIRED_CSRF_HEADER = "X-Conjectures-CSRF"


def build_app() -> FastAPI:
    """The real routers on a bare app. Enough for a schema; not enough to serve a request."""
    application = FastAPI(title="security-scheme probe")
    for module in ROUTERS:
        application.include_router(module.router)
    return application


def _calls(dependant) -> set:
    """Every dependency callable in a route's tree, however deeply nested.

    Walked rather than read off `get_flat_dependant`, because what this asks is whether a
    particular *function* is reachable — `require_role(ADMIN_ROLE)` is a fresh closure over
    `require_writer` and would otherwise be indistinguishable from an unauthenticated route.
    """
    found = {dependant.call}
    for child in dependant.dependencies:
        found |= _calls(child)
    return found


def _schemes(operation) -> set[str]:
    """The scheme names an operation requires, flattened out of OpenAPI's OR-of-ANDs list."""
    return {name for requirement in operation.get("security", ()) for name in requirement}


def _api_routes(router):
    """Every `APIRoute` under a router, recursing through included ones.

    `include_router` no longer flattens into `app.routes`: it appends an `_IncludedRouter`
    holding the router it was given. So a single pass over the top level finds the four
    documentation routes and nothing else, and a walk has to follow `original_router`. The
    paths on the routes it holds are already final — every router in this app carries its own
    prefix and none is included under a second one.
    """
    for route in getattr(router, "routes", ()):
        if isinstance(route, APIRoute):
            yield route
            continue
        included = getattr(route, "original_router", None)
        if included is not None:
            yield from _api_routes(included)


def _documented_routes():
    """Each schema-visible route paired with the security names its operations declare.

    Yields `(method, path, declared, calls)`. `include_in_schema=False` routes are skipped:
    the TMC PAY webhook is authenticated by an HMAC over the raw body and is deliberately
    absent from the document, so there is no operation to carry a requirement.
    """
    application = build_app()
    schema = application.openapi()
    for route in _api_routes(application):
        operations = schema["paths"].get(route.path)
        if operations is None or not route.include_in_schema:
            continue
        for method in sorted(route.methods):
            operation = operations.get(method.lower())
            if operation is None:
                continue
            yield method, route.path, _schemes(operation), _calls(route.dependant)


def test_the_two_credentials_are_declared_with_the_shape_clients_read():
    """The wire form, not just the presence.

    A client is generated from these two objects: the name of the cookie it must send, and the
    fact that the token goes in `Authorization` under the `bearer` scheme. Renaming a scheme or
    moving one from `header` to `cookie` silently regenerates every client wrong, so the shape
    is asserted rather than assumed.
    """
    schemes = build_app().openapi()["components"]["securitySchemes"]

    assert set(schemes) == {security.BEARER_SCHEME_NAME, security.COOKIE_SCHEME_NAME}

    assert schemes[security.BEARER_SCHEME_NAME]["type"] == "http"
    assert schemes[security.BEARER_SCHEME_NAME]["scheme"] == "bearer"

    cookie = schemes[security.COOKIE_SCHEME_NAME]
    assert cookie["type"] == "apiKey"
    assert cookie["in"] == "cookie"
    assert cookie["name"] == sessions.SESSION_COOKIE

    # Every scheme carries prose, because neither can be used from the Authorize dialog and the
    # document cannot express what a cookie-authenticated write additionally needs — see
    # `security.py`.
    for name, declared in schemes.items():
        assert declared.get("description"), f"{name} has no description"


def test_the_write_guard_is_not_declared_as_a_credential():
    """A write must not advertise an input box for something no caller can supply.

    `Origin` and `Sec-Fetch-Site` are on the Fetch spec's forbidden-header list: the browser
    writes them and no client may, which is exactly what makes them proof that a request was
    not caused by another site. A scheme for either would imply a value a caller can be handed
    and reuse. This also pins that the retired CSRF header has not come back as a scheme, in
    the document or in any operation.
    """
    schema = build_app().openapi()
    document = json.dumps(schema)

    for scheme in schema["components"]["securitySchemes"].values():
        assert scheme.get("name", "") not in (
            "Origin",
            "Sec-Fetch-Site",
            RETIRED_CSRF_HEADER,
        )
    assert f'"name": "{RETIRED_CSRF_HEADER}"' not in document

    # The gated writes are found, and each of them declares only the two real credentials.
    gated = 0
    for method, path, declared, calls in _documented_routes():
        if require_writer not in calls and require_cookie_writer not in calls:
            continue
        gated += 1
        assert declared <= {
            security.BEARER_SCHEME_NAME,
            security.COOKIE_SCHEME_NAME,
        }, f"{method} {path} declares {sorted(declared)}"
    # A guard that silently stops finding anything to guard passes forever.
    assert gated > 10, f"only {gated} guarded writes were walked"


def test_every_endpoint_that_resolves_a_principal_advertises_both_credentials():
    """The guard. A new authed endpoint cannot ship without the padlock.

    Phrased against the dependency that does the resolving rather than against a list of paths,
    so it covers routes added after this test was written, including ones reached through a
    role gate's closure.
    """
    offenders = []
    authenticated = 0
    for method, path, declared, calls in _documented_routes():
        if get_optional_principal not in calls:
            continue
        authenticated += 1
        for expected in (security.BEARER_SCHEME_NAME, security.COOKIE_SCHEME_NAME):
            if expected not in declared:
                offenders.append(f"{method} {path}: authenticated but no {expected}")
    assert offenders == [], offenders
    # A guard that silently stops finding anything to guard passes forever. The count only has
    # to be implausible-if-broken, so it is a floor rather than the exact number.
    assert authenticated > 20, f"only {authenticated} authenticated routes were walked"


def test_the_public_read_surface_asks_for_nothing():
    """The other direction, which matters as much.

    The catalog, results and status endpoints are world-readable, and the sign-in routes exist
    precisely to be called without a credential. A padlock on any of them would send a client —
    or a person reading `/docs` — looking for a token that does not exist and is not needed.
    """
    offenders = [
        f"{method} {path}: unauthenticated but declares {sorted(declared)}"
        for method, path, declared, calls in _documented_routes()
        if get_optional_principal not in calls and declared
    ]
    assert offenders == [], offenders


def test_the_real_app_serves_the_schemes():
    """The bare probe above skips `create_app`; this is the part it skips.

    Cheap, and it is the only thing standing between the guards above and an app that includes
    a different set of routers than the probe does.
    """
    from conftest_api import harness  # local: needs PostgreSQL, the others do not

    async def scenario():
        kit = await harness().setup()
        try:
            schema = kit.app.openapi()
            assert set(schema["components"]["securitySchemes"]) == {
                security.BEARER_SCHEME_NAME,
                security.COOKIE_SCHEME_NAME,
            }
            both = {security.BEARER_SCHEME_NAME, security.COOKIE_SCHEME_NAME}
            assert _schemes(schema["paths"]["/v1/me"]["get"]) == both
            # A write declares the same two. What it *additionally* requires — an allowlisted
            # `Origin` or a same-origin `Sec-Fetch-Site` — is not a credential and so is not
            # here; it is in the `BrowserSession` description.
            assert _schemes(schema["paths"]["/v1/me/payout"]["put"]) == both
            assert _schemes(schema["paths"]["/v1/catalog/index"]["get"]) == set()
        finally:
            await kit.teardown()

    asyncio.run(scenario())


# --- extraction ---------------------------------------------------------------------------
# The declarations replaced a raw `request.headers.get("Authorization")`. These pin the part
# that is not documentation: what the scheme hands on is what the old read handed on.


def _offered(header_value: str | None) -> str | None:
    """`offered_bearer` driven the way `HTTPBearer` would drive it for this header."""
    if not header_value:
        return None
    scheme, _, credentials = header_value.partition(" ")
    if not credentials or scheme.lower() != sessions.BEARER_SCHEME:
        return None
    return security.offered_bearer(
        HTTPAuthorizationCredentials(scheme=scheme, credentials=credentials)
    )


def test_the_scheme_accepts_exactly_what_the_header_read_accepted():
    token = sessions.new_bearer_token()
    for header in (
        None,
        "",
        token,  # no scheme
        f"Basic {token}",
        "Bearer",
        "Bearer ",
        f"Bearer {token}",
        f"bearer {token}",
        f"BEARER  {token}",  # doubled space; the token is what matters, not the padding
        f"Bearer {'x' * (sessions.MAX_BEARER_LENGTH + 1)}",
    ):
        assert _offered(header) == sessions.bearer_token(header), header


def test_a_token_over_the_ceiling_is_refused_wherever_it_arrived():
    """The length bound is the acceptance rule, not a property of how the value was parsed.

    `HTTPBearer` will hand over a multi-megabyte credential quite happily. Hashing one is a
    wasted allocation rather than a match, which is why extraction routes through the same
    `accept_bearer` the header read used.
    """
    oversized = "conj_cli_" + "x" * sessions.MAX_BEARER_LENGTH
    assert (
        security.offered_bearer(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials=oversized)
        )
        is None
    )
    assert security.offered_bearer(None) is None
    assert (
        security.offered_bearer(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials="  ")
        )
        is None
    )
