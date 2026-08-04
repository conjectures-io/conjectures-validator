"""Application factory.

Expensive, immutable state is built once during lifespan. Interactive docs are exposed only
outside production; leaving the schema and Swagger UI reachable on a live miner-facing endpoint
is a mistake worth not repeating.

This process now serves two audiences on one port: the miner-facing intake and status surface,
and the unauthenticated public read surface the website is built on. They share the task catalog,
the pin set and the database, so splitting them into two processes would mean two copies of the
audited pool and two things to keep in step. What separates them is the middleware stack, which
applies only to `/v1`, and the response models, which live in two modules with two sets of rules
(`schemas.py` for the miner, `schemas_public.py` for the world).

Middleware order is load-bearing. `add_middleware` prepends, so the layer added *last* is the
outermost one, and the stack is:

    SecurityHeaders  →  ScopedCORS  →  RateLimit  →  routes

Two layers generate responses of their own — the rate limiter's `429` and the CORS layer's
preflight answer — and a response only passes through the layers *outside* the one that made it.
That fixes the order:

* `SecurityHeaders` outermost, so the hardening reaches every response this process emits,
  including the `429` and the preflight, not just the ones a route returned.
* `ScopedCORS` outside the limiter, so a `429` still carries `Access-Control-Allow-Origin`. A
  browser that cannot read the body reports an opaque CORS failure instead of the rate limit,
  and the site's error handling never sees the real cause.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from conjectures_subnet.bounty import FlatBountyPricer
from conjectures_subnet.db import (
    async_session_factory,
    create_async_db_engine,
    database_url,
)
from conjectures_subnet.db.errors import DatabaseError
from submission_api import __version__, errors
from submission_api.auth import build_authenticator
from submission_api.dependencies import Services
from submission_api.credits import SubmissionTerms, parse_packages
from submission_api.mail import build_mail_sender
from submission_api.middleware import (
    CsrfMiddleware,
    SessionCookieRefreshMiddleware,
    RateLimitMiddleware,
    ScopedCORSMiddleware,
    SecurityHeadersMiddleware,
    cors_options,
)
from submission_api.payments import build_payment_verifier
from submission_api.pins import PinSet, assert_agrees_with_catalog
from submission_api.ratelimit import SlidingWindowLimiter
from submission_api.routers import catalog as catalog_router
from submission_api.routers import (
    auth,
    health,
    intents,
    me,
    results,
    submissions,
    system,
    tasks,
)
from submission_api.settings import Settings
from submission_api.taskpool import TaskCatalog
from submission_api.verification import build_dispatcher
from verifier.errors import VerifierError

DESCRIPTION = """
Paid Lean-proof submission API for conjectures.io, Bittensor Subnet 66.

A miner pays for one verification attempt, then submits a `conjectures-submission/v1` bundle
for one allowlisted task. Payment buys an attempt; it never changes the Lean verdict and does
not guarantee a reward.

The catalog, results and status endpoints are unauthenticated and world-readable. They publish
conjecture statements, the Lean challenge each solver compiles against, and verified results
attributed to conjectures.io — never a miner identity, proof bytes, or verifier output.
""".strip()


def build_services(
    settings: Settings,
    *,
    catalog: TaskCatalog | None = None,
    pins: PinSet | None = None,
    terms: SubmissionTerms | None = None,
) -> Services:
    """Assemble the service graph. Tests inject a catalog, pin set and terms directly."""
    resolved_catalog = catalog or TaskCatalog.load(
        allowlist_path=settings.task_allowlist_path,
        pool_root=settings.task_pool_root,
    )
    resolved_pins = pins or PinSet.load(settings.pins_path)
    if catalog is None and pins is None:
        # Both were read from disk, so they are comparable. A pin lock naming a different source
        # revision than the audited allowlist would publish statements from one revision under
        # the pins of another; that is a misconfiguration, and it stops startup here rather than
        # showing up as a mismatched detail page.
        assert_agrees_with_catalog(resolved_pins, resolved_catalog.repository_commit)
    # The URL comes from conjectures_subnet.db, so the API, the workers and Flyway can never
    # disagree about which database they are talking to.
    engine = create_async_db_engine(settings.database_url or database_url())
    return Services(
        settings=settings,
        engine=engine,
        sessions=async_session_factory(engine),
        catalog=resolved_catalog,
        authenticator=build_authenticator(settings),
        payments=build_payment_verifier(settings),
        dispatcher=build_dispatcher(settings),
        pricing=FlatBountyPricer(
            amount_rao=settings.bounty_amount_rao,
            policy_version=settings.bounty_policy_version,
        ),
        pins=resolved_pins,
        mail=build_mail_sender(settings),
        # Parsed at startup, so a malformed package spec or a bonus larger than its purchase
        # stops the process rather than reaching a pricing page.
        packages=parse_packages(
            settings.credit_packages, credit_price_rao=settings.payment_amount_rao
        ),
        terms=terms
        or SubmissionTerms.load(
            settings.submission_terms_path,
            version=settings.submission_terms_version,
            effective_from=date.fromisoformat(settings.submission_terms_effective_from),
        ),
    )


def create_app(
    settings: Settings | None = None, *, services: Services | None = None
) -> FastAPI:
    resolved_settings = settings or (
        services.settings if services else Settings.from_env()
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if not hasattr(application.state, "services"):
            application.state.services = build_services(resolved_settings)
        try:
            yield
        finally:
            # Only tear down resources this app created; injected ones belong to the caller.
            if services is None:
                built = application.state.services
                await built.engine.dispose()
                # The chain payment verifier holds a websocket open between requests — see
                # SubtensorTransferReader on why it is not opened per submission. Released here.
                reader = getattr(built.payments, "reader", None)
                closer = getattr(reader, "aclose", None)
                if closer is not None:
                    await closer()

    application = FastAPI(
        title="conjectures.io Subnet 66 submission API",
        description=DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if resolved_settings.expose_docs else None,
        redoc_url="/redoc" if resolved_settings.expose_docs else None,
        openapi_url="/openapi.json" if resolved_settings.expose_docs else None,
    )
    if services is not None:
        # Injected services are already constructed, so make them reachable without requiring
        # the caller to drive lifespan (in-process test clients do not).
        application.state.services = services
    application.add_exception_handler(errors.ApiError, errors.api_error_handler)
    application.add_exception_handler(VerifierError, errors.verifier_error_handler)
    application.add_exception_handler(DatabaseError, errors.database_error_handler)
    application.add_exception_handler(
        RequestValidationError, errors.validation_error_handler
    )
    application.add_exception_handler(Exception, errors.unhandled_error_handler)

    # Added innermost first, because `add_middleware` prepends: the last one added ends up
    # outermost. See the module docstring for why this order and not another.
    if resolved_settings.rate_limit_enabled:
        application.add_middleware(
            RateLimitMiddleware,
            limiter=SlidingWindowLimiter(
                limit=resolved_settings.rate_limit_requests,
                window_seconds=resolved_settings.rate_limit_window_seconds,
                max_clients=resolved_settings.rate_limit_max_clients,
            ),
            trusted_proxy_hops=resolved_settings.trusted_proxy_hops,
        )
    # Between the limiter and CORS: a refused write should still carry the CORS grant so the
    # browser reports the 403 rather than an opaque CORS error.
    application.add_middleware(
        CsrfMiddleware,
        allowed_origins=resolved_settings.cors_allowed_origins,
        # The hotkey-signature endpoints carry no cookie, so there is no ambient credential for
        # a cross-site page to abuse, and miner tooling sends neither header.
        exempt_prefixes=("/v1/submissions/preflight",),
    )
    if resolved_settings.cors_enabled:
        # Skipped entirely when no origin is configured. An empty allowlist and no CORS layer are
        # the same outcome for a browser, and not installing it keeps the stack honest about
        # whether cross-origin access is configured at all.
        application.add_middleware(
            ScopedCORSMiddleware, **cors_options(resolved_settings)
        )
    application.add_middleware(
        SessionCookieRefreshMiddleware, settings=resolved_settings
    )
    application.add_middleware(SecurityHeadersMiddleware, settings=resolved_settings)

    application.include_router(health.router)
    application.include_router(tasks.router)
    application.include_router(submissions.router)
    application.include_router(catalog_router.router)
    application.include_router(results.router)
    application.include_router(system.router)
    # Stage 2. The intent router shares the /v1/submissions prefix with the extrinsic path, so
    # it is included after it: the fixed segments (/preflight, /intents) cannot collide with
    # /{submission_id}, which is typed as a UUID.
    application.include_router(auth.router)
    application.include_router(me.router)
    application.include_router(intents.router)
    return application
