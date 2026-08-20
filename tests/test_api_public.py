"""The public surface's infrastructure: CORS, response hardening, rate limiting, and status.

Split from the endpoint tests because these are properties of the stack rather than of any one
route, and because most of them need no database at all. The rate limiter, the pin loader, the
pin-rotation window and the settings guardrails are all pure and are tested directly.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

pytest.importorskip("fastapi", reason="submission API tests need the service extra")
pytest.importorskip("sqlalchemy", reason="submission API tests need the db extra")
pytest.importorskip("httpx", reason="submission API tests need the service extra")

from conftest_api import PINS_JSON, RECIPIENT, REPOSITORY_COMMIT, build_settings, harness, postgres_dsn

from submission_api.middleware import API_CSP, client_address
from submission_api.pins import PinSet, PinsError, assert_agrees_with_catalog
from submission_api.ratelimit import SlidingWindowLimiter
from submission_api.routers.system import STATUS_DEGRADED, STATUS_OK, STATUS_PAUSED, pin_rotation_window
from submission_api.settings import (
    DEVELOPMENT_ACTIVITY_SALT,
    DEVELOPMENT_CURSOR_SECRET,
    Settings,
    SettingsError,
)

ORIGIN = "https://conjectures.io"
OTHER_ORIGIN = "https://evil.example"

needs_db = pytest.mark.skipif(
    postgres_dsn() is None,
    reason="no database: run `docker compose -f docker-compose.pytest-db.yml up -d`",
)


def run(coroutine):
    return asyncio.run(coroutine)


async def _client(kit):
    from httpx import ASGITransport, AsyncClient

    return AsyncClient(
        transport=ASGITransport(app=kit.app, raise_app_exceptions=True),
        base_url="http://validator.test",
    )


def production_env(**overrides: str) -> dict[str, str]:
    environ = {
        "APP_MODE": "PROD",
        "PAYMENT_RECIPIENT_SS58": RECIPIENT,
        "BOUNTY_WALLET_HOTKEY_SS58": RECIPIENT,
        "PUBLIC_CURSOR_SECRET": "c" * 40,
        "PUBLIC_ACTIVITY_SALT": "s" * 40,
        # Stage 2 additions production also refuses to start without.
        "WEBSITE_BASE_URL": "https://conjectures.io",
        "MAIL_SENDER": "smtp",
        "SMTP_HOST": "smtp.example.com",
        "SMTP_USERNAME": "smtp-user",
        "SMTP_PASSWORD": "smtp-password",
        "SMTP_FROM_ADDRESS": "login@conjectures.io",
    }
    environ.update(overrides)
    return environ


# --- settings guardrails -------------------------------------------------------------------


def test_production_refuses_a_wildcard_cors_origin():
    with pytest.raises(SettingsError, match="CORS_ALLOWED_ORIGINS"):
        Settings.from_env(production_env(CORS_ALLOWED_ORIGINS="*"))


def test_production_refuses_a_plaintext_cors_origin():
    with pytest.raises(SettingsError, match="https"):
        Settings.from_env(production_env(CORS_ALLOWED_ORIGINS="http://conjectures.io"))


def test_a_cors_origin_must_be_an_origin_not_a_url():
    for bad in ("https://conjectures.io/", "https://conjectures.io/app", "conjectures.io"):
        with pytest.raises(SettingsError, match="scheme://host"):
            Settings.from_env(production_env(CORS_ALLOWED_ORIGINS=bad))


def test_no_configured_origin_means_no_browser_access_rather_than_all():
    """An empty allowlist is a valid, fail-closed answer, not an invitation to allow everything."""
    settings = Settings.from_env(production_env())
    assert settings.cors_allowed_origins == ()
    assert settings.cors_enabled is False
    assert settings.write_allowed_origins == ()


def test_the_write_allowlist_inherits_the_read_one_unless_it_is_set():
    """Most deployments have one website and want one list, so the split stays invisible."""
    origins = "https://conjectures.io,https://www.conjectures.io"
    inherited = Settings.from_env(production_env(CORS_ALLOWED_ORIGINS=origins))
    assert inherited.write_allowed_origins == inherited.cors_allowed_origins

    narrowed = Settings.from_env(
        production_env(
            CORS_ALLOWED_ORIGINS=origins,
            WRITE_ALLOWED_ORIGINS="https://conjectures.io",
        )
    )
    assert narrowed.write_allowed_origins == ("https://conjectures.io",)
    assert len(narrowed.cors_allowed_origins) == 2


def test_an_explicitly_empty_write_allowlist_is_not_the_same_as_an_unset_one():
    """Set-but-empty means no browser may write here at all — the right setting for a
    deployment that serves only miner tooling. Inheriting the read list instead would silently
    grant writes to every origin permitted to read."""
    settings = Settings.from_env(
        production_env(
            CORS_ALLOWED_ORIGINS="https://conjectures.io",
            WRITE_ALLOWED_ORIGINS="",
        )
    )
    assert settings.cors_allowed_origins == ("https://conjectures.io",)
    assert settings.write_allowed_origins == ()


def test_production_refuses_a_wildcard_or_plaintext_write_origin():
    """The write allowlist gets the read allowlist's validation, because it is the same risk
    one step further along: an origin that may write can spend an account's credits."""
    with pytest.raises(SettingsError, match="WRITE_ALLOWED_ORIGINS"):
        Settings.from_env(production_env(WRITE_ALLOWED_ORIGINS="*"))
    with pytest.raises(SettingsError, match="https"):
        Settings.from_env(
            production_env(WRITE_ALLOWED_ORIGINS="http://conjectures.io")
        )


def test_production_refuses_the_published_development_secrets():
    for key, value in (
        ("PUBLIC_CURSOR_SECRET", DEVELOPMENT_CURSOR_SECRET),
        ("PUBLIC_ACTIVITY_SALT", DEVELOPMENT_ACTIVITY_SALT),
    ):
        with pytest.raises(SettingsError, match="development constant"):
            Settings.from_env(production_env(**{key: value}))


def test_production_refuses_a_short_secret():
    with pytest.raises(SettingsError, match="at least"):
        Settings.from_env(production_env(PUBLIC_ACTIVITY_SALT="short"))


def test_production_refuses_to_disable_rate_limiting():
    with pytest.raises(SettingsError, match="RATE_LIMIT_ENABLED"):
        Settings.from_env(production_env(RATE_LIMIT_ENABLED="false"))


def test_development_gets_working_defaults_without_configuration():
    settings = build_settings()
    assert settings.cursor_secret == DEVELOPMENT_CURSOR_SECRET
    assert settings.rate_limit_enabled is True
    assert settings.trusted_proxy_hops == 0
    # No Alt-Svc outside production: the developer's edge speaks no HTTP/3.
    assert settings.alt_svc == ""
    assert settings.taostats_api_key == ""
    assert settings.taostats_price_cache_seconds == 60


def test_the_pin_rotation_window_is_configured_as_a_utc_clock_time():
    settings = build_settings(PIN_ROTATION_START_UTC="03:30", PIN_ROTATION_WEEKDAY="4")
    assert settings.pin_rotation_start_minute == 210
    assert settings.pin_rotation_weekday == 4
    with pytest.raises(SettingsError, match="HH:MM"):
        build_settings(PIN_ROTATION_START_UTC="25:00")


# --- response hardening --------------------------------------------------------------------


@needs_db
def test_every_response_carries_the_hardening_headers():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await _client(kit) as client:
                response = await client.get("/v1/catalog/meta")
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.headers["referrer-policy"] == "no-referrer"
            assert response.headers["x-frame-options"] == "DENY"
            assert response.headers["content-security-policy"] == API_CSP
            assert response.headers["cross-origin-opener-policy"] == "same-origin"
            assert response.headers["cross-origin-resource-policy"] == "cross-origin"
            assert "camera=()" in response.headers["permissions-policy"]
        finally:
            await kit.teardown()

    run(scenario())


@needs_db
def test_hsts_and_alt_svc_are_production_only():
    """An HSTS header from a localhost development server pins the browser for every other
    service on localhost too, which is a genuinely disruptive thing to leave behind."""

    async def scenario():
        kit = await harness().setup()
        try:
            async with await _client(kit) as client:
                response = await client.get("/v1/catalog/meta")
            assert "strict-transport-security" not in response.headers
            assert "alt-svc" not in response.headers
        finally:
            await kit.teardown()

    run(scenario())


@needs_db
def test_a_production_configured_app_emits_hsts_and_alt_svc():
    """The other half of the previous test: absent in development, present in production.

    Built with the real hotkey authenticator and chain payment verifier, because production
    refuses to start with anything else — so this also asserts the whole hardened service graph
    assembles.
    """

    async def scenario():
        from conftest_api import PYTEST_DSN, REPOSITORY_COMMIT, pin_set, task_entry, terms

        from conjectures_subnet.bounty import DynamicBountyPricer, StaticBalanceReader
        from conjectures_subnet.db.engine import (
            async_session_factory,
            create_async_db_engine,
        )
        from conjectures_subnet.db.models import Base
        from submission_api.app import create_app
        from submission_api.auth import build_authenticator
        from submission_api.dependencies import Services
        from submission_api.payments import build_payment_verifier
        from submission_api.credits import parse_packages
        from submission_api.mail import build_mail_sender
        from submission_api.taskpool import catalog_from_entries
        from submission_api.verification import QueueDispatcher

        settings = Settings.from_env(
            production_env(
                DATABASE_URL=postgres_dsn() or PYTEST_DSN,
                CORS_ALLOWED_ORIGINS=ORIGIN,
                ALT_SVC='h3=":8443"; ma=3600',
            )
        )
        assert settings.production is True
        engine = create_async_db_engine(settings.database_url)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
        services = Services(
            settings=settings,
            engine=engine,
            sessions=async_session_factory(engine),
            catalog=catalog_from_entries(
                repository_commit=REPOSITORY_COMMIT, entries=(task_entry(),)
            ),
            authenticator=build_authenticator(settings),
            payments=build_payment_verifier(settings),
            dispatcher=QueueDispatcher(),
            pricing=DynamicBountyPricer(
                balance_reader=StaticBalanceReader(4_000_000_000),
                balance_coldkey=settings.bounty_wallet_coldkey,
                balance_hotkey=settings.bounty_wallet_hotkey,
                balance_netuid=settings.bounty_netuid,
                reward_target_ids=tuple(
                    sorted(
                        {
                            entry.reward_target_id
                            for entry in catalog_from_entries(
                                repository_commit=REPOSITORY_COMMIT,
                                entries=(task_entry(),),
                            ).entries.values()
                        }
                    )
                ),
                policy_version=settings.bounty_policy_version,
                constant_numerator=settings.bounty_constant_numerator,
                constant_denominator=settings.bounty_constant_denominator,
                age_period_seconds=settings.bounty_age_period_seconds,
                max_age_weight=settings.bounty_max_age_weight,
                max_bounty_share_numerator=settings.bounty_max_share_numerator,
                max_bounty_share_denominator=settings.bounty_max_share_denominator,
            ),
            pins=pin_set(),
            mail=build_mail_sender(settings),
            packages=parse_packages(
                settings.credit_packages, credit_price_rao=settings.payment_amount_rao
            ),
            terms=terms(),
        )
        app = create_app(services=services)
        try:
            from httpx import ASGITransport, AsyncClient

            async with AsyncClient(
                transport=ASGITransport(app=app, raise_app_exceptions=True),
                base_url="https://validator.test",
            ) as client:
                response = await client.get("/v1/catalog/meta")
                docs = await client.get("/docs")

            hsts = response.headers["strict-transport-security"]
            assert "max-age=31536000" in hsts
            assert "includeSubDomains" in hsts
            assert response.headers["alt-svc"] == 'h3=":8443"; ma=3600'
            # Production hides the schema and the interactive docs.
            assert docs.status_code == 404
        finally:
            await engine.dispose()

    run(scenario())


@needs_db
def test_the_strict_csp_is_not_applied_to_the_interactive_docs():
    """The docs need to load their own script and stylesheet; every other path returns JSON."""

    async def scenario():
        kit = await harness().setup()
        try:
            async with await _client(kit) as client:
                docs = await client.get("/docs")
            assert docs.status_code == 200
            assert "content-security-policy" not in docs.headers
            # Still hardened in the ways that do not break it.
            assert docs.headers["x-content-type-options"] == "nosniff"
        finally:
            await kit.teardown()

    run(scenario())


# --- CORS ----------------------------------------------------------------------------------


@needs_db
def test_an_allowlisted_origin_may_read_v1():
    async def scenario():
        kit = await harness(CORS_ALLOWED_ORIGINS=ORIGIN).setup()
        try:
            async with await _client(kit) as client:
                response = await client.get(
                    "/v1/catalog/meta", headers={"Origin": ORIGIN}
                )
            assert response.headers["access-control-allow-origin"] == ORIGIN
            # Credentials ARE allowed since Stage 2: the session is an HttpOnly cookie and the
            # browser has to be permitted to send it. That is a real widening, and it is what
            # makes the exact-origin allowlist load-bearing rather than merely tidy — with
            # credentials on, an allowlisted origin can read authenticated responses.
            assert response.headers["access-control-allow-credentials"] == "true"
            # So the wildcard must never be reachable in production, and it is not: see
            # test_production_refuses_a_wildcard_cors_origin. A wildcard with credentials is
            # rejected by browsers anyway, but relying on that would be relying on the client.
            assert response.headers["access-control-allow-origin"] != "*"
            assert "vary" in response.headers
        finally:
            await kit.teardown()

    run(scenario())


@needs_db
def test_an_unlisted_origin_gets_no_grant():
    async def scenario():
        kit = await harness(CORS_ALLOWED_ORIGINS=ORIGIN).setup()
        try:
            async with await _client(kit) as client:
                response = await client.get(
                    "/v1/catalog/meta", headers={"Origin": OTHER_ORIGIN}
                )
            # The request is answered — it is public data — but the browser will not hand the
            # body to a page on that origin.
            assert response.status_code == 200
            assert "access-control-allow-origin" not in response.headers
        finally:
            await kit.teardown()

    run(scenario())


@needs_db
def test_a_browser_cannot_form_a_submission_even_from_an_allowed_origin():
    """`POST /v1/submissions` is miner tooling, never a browser.

    Stage 2 had to add POST to the CORS verb allowlist for the account surface, so the verb list
    is no longer what protects intake. The control that replaced it is the request-header
    allowlist: that endpoint requires seven `X-Conjectures-*` headers and none of them is
    permitted, so a browser cannot form a valid submission. It also authenticates a hotkey
    signature rather than a cookie, so there is no ambient credential to ride on either.
    """

    async def scenario():
        kit = await harness(CORS_ALLOWED_ORIGINS=ORIGIN).setup()
        try:
            async with await _client(kit) as client:
                refused = await client.request(
                    "OPTIONS",
                    "/v1/submissions",
                    headers={
                        "Origin": ORIGIN,
                        "Access-Control-Request-Method": "POST",
                        # The headers a real submission must carry.
                        "Access-Control-Request-Headers": (
                            "x-conjectures-hotkey,x-conjectures-signature,"
                            "x-conjectures-task-id,x-conjectures-proof-sha256"
                        ),
                    },
                )
                allowed = await client.request(
                    "OPTIONS",
                    "/v1/catalog/meta",
                    headers={
                        "Origin": ORIGIN,
                        "Access-Control-Request-Method": "GET",
                    },
                )
            # The preflight fails on the headers, which is what stops the browser from sending
            # the request at all — a browser may not send a header the preflight did not permit.
            assert refused.status_code == 400
            allowed_headers = refused.headers.get("access-control-allow-headers", "")
            assert "x-conjectures-hotkey" not in allowed_headers.lower()
            assert "x-conjectures-signature" not in allowed_headers.lower()
            # The one `X-Conjectures-*` header still on the preflight allowlist is the retired
            # CSRF token, kept only so a frontend build predating its removal does not fail
            # preflight mid-rollout. Nothing reads it, and it is useless to the intake path.
            assert "x-conjectures-csrf" in allowed_headers.lower()

            assert allowed.status_code == 200
            assert allowed.headers["access-control-allow-origin"] == ORIGIN
            assert "GET" in allowed.headers["access-control-allow-methods"]
            # A response the CORS layer generated itself is still hardened, because the security
            # headers sit outside it.
            assert allowed.headers["x-content-type-options"] == "nosniff"
        finally:
            await kit.teardown()

    run(scenario())


@needs_db
def test_cors_is_scoped_to_v1_and_does_not_leak_onto_the_probes():
    async def scenario():
        kit = await harness(CORS_ALLOWED_ORIGINS=ORIGIN).setup()
        try:
            async with await _client(kit) as client:
                probe = await client.get("/healthz", headers={"Origin": ORIGIN})
            assert probe.status_code == 200
            assert "access-control-allow-origin" not in probe.headers
        finally:
            await kit.teardown()

    run(scenario())


# --- rate limiting -------------------------------------------------------------------------


def test_the_sliding_window_admits_the_budget_then_refuses():
    limiter = SlidingWindowLimiter(limit=3, window_seconds=60, max_clients=10)
    decisions = [limiter.check("1.2.3.4", now=1000.0) for _ in range(4)]
    assert [item.allowed for item in decisions] == [True, True, True, False]
    assert [item.remaining for item in decisions] == [2, 1, 0, 0]
    assert decisions[-1].reset_seconds == 60


def test_a_refused_request_does_not_extend_the_penalty():
    """Counting refusals would turn a burst into an unbounded lockout for a client that retries."""
    limiter = SlidingWindowLimiter(limit=1, window_seconds=10, max_clients=10)
    assert limiter.check("1.2.3.4", now=0.0).allowed is True
    for offset in (1.0, 2.0, 3.0):
        assert limiter.check("1.2.3.4", now=offset).allowed is False
    # The original hit is still what expires, so the budget returns on schedule.
    assert limiter.check("1.2.3.4", now=10.1).allowed is True


def test_the_window_slides_rather_than_resetting_in_steps():
    """A fixed window admits twice the configured rate across its boundary."""
    limiter = SlidingWindowLimiter(limit=2, window_seconds=10, max_clients=10)
    assert limiter.check("a", now=9.0).allowed is True
    assert limiter.check("a", now=9.5).allowed is True
    # A fixed window would reset at t=10 and admit both of these.
    assert limiter.check("a", now=10.5).allowed is False
    assert limiter.check("a", now=19.1).allowed is True


def test_budgets_are_per_client():
    limiter = SlidingWindowLimiter(limit=1, window_seconds=60, max_clients=10)
    assert limiter.check("1.1.1.1", now=0.0).allowed is True
    assert limiter.check("1.1.1.1", now=0.0).allowed is False
    assert limiter.check("2.2.2.2", now=0.0).allowed is True


def test_the_key_table_is_bounded_so_the_limiter_is_not_the_denial_of_service():
    """Client keys are attacker-chosen, one per source address."""
    limiter = SlidingWindowLimiter(limit=5, window_seconds=1, max_clients=16)
    for index in range(500):
        # Each key's window has expired by the time the next batch arrives, so eviction has
        # cold entries to reclaim.
        limiter.check(f"10.0.{index // 256}.{index % 256}", now=float(index) * 2)
    assert limiter.tracked_clients <= 17


def test_an_active_client_is_never_evicted_to_stay_under_the_cap():
    """Eviction hands back a full budget, so it must only ever reclaim expired entries."""
    limiter = SlidingWindowLimiter(limit=1, window_seconds=1000, max_clients=2)
    for index in range(5):
        limiter.check(f"10.0.0.{index}", now=1.0)
    # All five are inside their window, so none could be dropped without resetting a budget.
    assert limiter.tracked_clients == 5
    assert limiter.check("10.0.0.0", now=2.0).allowed is False


@needs_db
def test_the_budget_is_reported_on_every_v1_response_and_a_429_is_problem_json():
    async def scenario():
        kit = await harness(RATE_LIMIT_REQUESTS="2", RATE_LIMIT_WINDOW_SECONDS="60").setup()
        try:
            async with await _client(kit) as client:
                first = await client.get("/v1/catalog/meta")
                assert first.headers["ratelimit-limit"] == "2"
                assert first.headers["ratelimit-remaining"] == "1"
                assert int(first.headers["ratelimit-reset"]) > 0

                await client.get("/v1/catalog/meta")
                refused = await client.get("/v1/catalog/meta")

            assert refused.status_code == 429
            assert refused.headers["content-type"].startswith("application/problem+json")
            assert refused.json()["reason_code"] == "RATE_LIMITED"
            assert int(refused.headers["retry-after"]) >= 1
            assert refused.headers["ratelimit-remaining"] == "0"
            # Hardening still applies to a response the middleware generated itself.
            assert refused.headers["x-content-type-options"] == "nosniff"
        finally:
            await kit.teardown()

    run(scenario())


@needs_db
def test_the_health_probes_are_never_rate_limited():
    """A 429 to an orchestrator probe takes the replica out of service."""

    async def scenario():
        kit = await harness(RATE_LIMIT_REQUESTS="1").setup()
        try:
            async with await _client(kit) as client:
                for _ in range(5):
                    assert (await client.get("/healthz")).status_code == 200
        finally:
            await kit.teardown()

    run(scenario())


@needs_db
def test_a_rate_limited_response_still_carries_the_cors_grant():
    """A browser that cannot read the 429 reports a CORS error instead of the rate limit."""

    async def scenario():
        kit = await harness(RATE_LIMIT_REQUESTS="1", CORS_ALLOWED_ORIGINS=ORIGIN).setup()
        try:
            async with await _client(kit) as client:
                await client.get("/v1/catalog/meta", headers={"Origin": ORIGIN})
                refused = await client.get("/v1/catalog/meta", headers={"Origin": ORIGIN})
            assert refused.status_code == 429
            assert refused.headers["access-control-allow-origin"] == ORIGIN
        finally:
            await kit.teardown()

    run(scenario())


# --- client address resolution -------------------------------------------------------------


def _scope(peer: str, forwarded: str | None = None):
    headers = [] if forwarded is None else [(b"x-forwarded-for", forwarded.encode())]
    return {"type": "http", "client": (peer, 51000), "headers": headers}


def test_a_forwarding_header_is_ignored_unless_the_deployment_says_otherwise():
    """The default cannot be spoofed: one client cannot mint unlimited limiter keys."""
    scope = _scope("10.0.0.1", "1.2.3.4, 5.6.7.8")
    assert client_address(scope, 0) == "10.0.0.1"


def test_the_client_is_read_as_many_hops_from_the_right_as_are_trusted():
    # One proxy: it appended the peer it received from, which is the client.
    assert client_address(_scope("10.0.0.1", "203.0.113.9"), 1) == "203.0.113.9"
    # Two proxies: the CDN appended the client, the load balancer appended the CDN.
    assert (
        client_address(_scope("10.0.0.1", "203.0.113.9, 198.51.100.4"), 2)
        == "203.0.113.9"
    )
    # Anything further left was written by the client and is ignored.
    assert (
        client_address(_scope("10.0.0.1", "127.0.0.1, 203.0.113.9"), 1) == "203.0.113.9"
    )


def test_a_chain_shorter_than_the_trusted_hops_is_not_trusted_at_all():
    """The request did not come through the proxies this deployment expects."""
    assert client_address(_scope("10.0.0.1", "203.0.113.9"), 2) == "10.0.0.1"


def test_a_forwarded_value_that_is_not_an_address_falls_back_to_the_peer():
    """Parsing also bounds the length of an attacker-chosen limiter key."""
    assert client_address(_scope("10.0.0.1", "not-an-ip"), 1) == "10.0.0.1"
    assert client_address(_scope("10.0.0.1", "x" * 5000), 1) == "10.0.0.1"


def test_a_port_suffix_is_stripped_from_either_address_family():
    assert client_address(_scope("10.0.0.1", "203.0.113.9:4711"), 1) == "203.0.113.9"
    assert client_address(_scope("10.0.0.1", "[2001:db8::1]:4711"), 1) == "2001:db8::1"


# --- pins --------------------------------------------------------------------------------


def test_the_pin_loader_publishes_the_known_components_and_drops_the_rest():
    pins = PinSet.from_bytes(PINS_JSON)
    assert pins.schema_version == 1
    assert [pin.component for pin in pins.pins][:3] == [
        "formal_conjectures",
        "mathlib",
        "lean",
    ]
    assert pins.get("nanoda").enabled is False
    assert pins.get("lean").toolchain == "leanprover/lean4:v4.27.0"
    assert pins.lock_sha256.startswith("sha256:")


def test_the_real_pin_lock_loads_and_agrees_with_itself():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    pins = PinSet.load(root / "pins.lock.json")
    commit = pins.get("formal_conjectures").commit
    assert_agrees_with_catalog(pins, commit)


def test_a_pin_lock_that_disagrees_with_the_allowlist_stops_the_process():
    """Publishing statements from one source revision under another revision's pins is a
    misconfiguration, and it has to be a startup failure rather than a wrong detail page."""
    pins = PinSet.from_bytes(PINS_JSON)
    with pytest.raises(PinsError, match="disagree"):
        assert_agrees_with_catalog(pins, "0" * 40)


def test_an_unreadable_pin_lock_is_a_refusal_not_an_empty_pin_set():
    with pytest.raises(PinsError):
        PinSet.from_bytes(b"not json")
    with pytest.raises(PinsError, match="schema_version"):
        PinSet.from_bytes(b"{}")
    with pytest.raises(PinsError, match="none of the known components"):
        PinSet.from_bytes(b'{"schema_version": 1, "something_else": {"commit": "abc"}}')


# --- system status -----------------------------------------------------------------------


def test_the_next_rotation_window_rolls_forward_once_this_week_has_passed():
    settings = build_settings(
        PIN_ROTATION_WEEKDAY="1",  # Tuesday
        PIN_ROTATION_START_UTC="02:00",
        PIN_ROTATION_DURATION_MINUTES="240",
    )
    # Tuesday 2026-08-04, inside the window.
    inside = pin_rotation_window(
        settings, datetime(2026, 8, 4, 3, 0, tzinfo=UTC), drained=True
    )
    assert inside.in_progress is True
    assert inside.starts_at == datetime(2026, 8, 4, 2, 0, tzinfo=UTC)
    assert inside.ends_at == datetime(2026, 8, 4, 6, 0, tzinfo=UTC)

    # Tuesday, after it closed: the next one is a week on.
    after = pin_rotation_window(
        settings, datetime(2026, 8, 4, 7, 0, tzinfo=UTC), drained=True
    )
    assert after.in_progress is False
    assert after.starts_at == datetime(2026, 8, 11, 2, 0, tzinfo=UTC)

    # Monday: this week's window is still ahead.
    before = pin_rotation_window(
        settings, datetime(2026, 8, 3, 12, 0, tzinfo=UTC), drained=False
    )
    assert before.in_progress is False
    assert before.starts_at == datetime(2026, 8, 4, 2, 0, tzinfo=UTC)
    assert before.drained is False


@needs_db
def test_status_reports_open_submissions_empty_queues_and_a_drained_system():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await _client(kit) as client:
                response = await client.get("/v1/system/status")
            assert response.status_code == 200, response.text
            body = response.json()

            assert body["submissions_open"] is True
            assert body["status"] in (STATUS_OK, STATUS_DEGRADED)
            assert body["repository_commit"] == REPOSITORY_COMMIT
            assert body["queue_depths"] == {
                "awaiting_verification": 0,
                "awaiting_review": 0,
                "awaiting_reward": 0,
            }
            # The precondition the weekly pin rotation requires.
            assert body["pin_rotation"]["drained"] is True
            assert body["banner"] is None
            # Never cached: this is what a client polls to learn the state is still true.
            assert response.headers["cache-control"] == "no-store"
        finally:
            await kit.teardown()

    run(scenario())


@needs_db
def test_a_pause_is_reported_and_actually_enforced_by_intake():
    """A status endpoint that reported a pause the intake path ignored would be worse than none,
    because a solver would trust it and spend a payment."""

    async def scenario():
        from conftest_api import distinct_bundle, new_key, submission_headers

        kit = await harness(SUBMISSIONS_PAUSED="true", STATUS_BANNER="Weekly pin rotation").setup()
        try:
            async with await _client(kit) as client:
                status = await client.get("/v1/system/status")
                assert status.json()["submissions_open"] is False
                assert status.json()["status"] == STATUS_PAUSED
                assert status.json()["banner"] == "Weekly pin rotation"

                bundle, digest = distinct_bundle("paused")
                refused = await client.post(
                    "/v1/submissions",
                    content=bundle,
                    headers=submission_headers(
                        bundle, idempotency_key=new_key(), proof_digest=digest
                    ),
                )
            assert refused.status_code == 503
            assert refused.json()["reason_code"] == "SUBMISSIONS_PAUSED"
        finally:
            await kit.teardown()

    run(scenario())


@needs_db
def test_a_queued_submission_makes_the_system_not_drained():
    async def scenario():
        from conftest_api import distinct_bundle, new_key, submission_headers

        kit = await harness().setup()
        try:
            async with await _client(kit) as client:
                bundle, digest = distinct_bundle("queued")
                created = await client.post(
                    "/v1/submissions",
                    content=bundle,
                    headers=submission_headers(
                        bundle, idempotency_key=new_key(), proof_digest=digest
                    ),
                )
                assert created.status_code == 201, created.text
                body = (await client.get("/v1/system/status")).json()

            assert body["queue_depths"]["awaiting_verification"] == 1
            # "No pin update may begin while any submission is queued" — README.md.
            assert body["pin_rotation"]["drained"] is False
        finally:
            await kit.teardown()

    run(scenario())
