"""Google sign-in, collision handling, explicit linking, and provider CSRF."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

pytest.importorskip("fastapi", reason="submission API tests need the service extra")
pytest.importorskip("sqlalchemy", reason="submission API tests need the db extra")
pytest.importorskip("httpx", reason="submission API tests need the service extra")
pytest.importorskip("psycopg", reason="submission API tests need the db extra")

from sqlalchemy import func, select

from conftest_api import harness, postgres_dsn
from conjectures_subnet.db.models import Account, AccountIdentity
from submission_api.errors import Unauthorized
from submission_api.google_identity import GoogleIdentity
from submission_api.sessions import CSRF_COOKIE, CSRF_HEADER

pytestmark = pytest.mark.skipif(
    postgres_dsn() is None,
    reason="no database: run `docker compose -f docker-compose.pytest-db.yml up -d`",
)

GOOGLE_CSRF_COOKIE = "g_csrf_token"
GOOGLE_CSRF = "provider-csrf-token"
CREDENTIAL = "credential-" + "a" * 128
OTHER_CREDENTIAL = "credential-" + "b" * 128
EMAIL = "solver@gmail.com"
ORIGIN = "https://conjectures.io"


@dataclass
class FakeGoogle:
    identities: dict[str, GoogleIdentity]

    async def verify(self, credential: str) -> GoogleIdentity:
        identity = self.identities.get(credential)
        if identity is None:
            raise Unauthorized(
                "invalid fake Google credential",
                reason_code="GOOGLE_CREDENTIAL_INVALID",
            )
        return identity


def google(*, subject: str = "subject-1", email: str = EMAIL) -> FakeGoogle:
    return FakeGoogle(
        {
            CREDENTIAL: GoogleIdentity(
                subject=subject,
                email=email,
                email_verified=True,
            ),
            OTHER_CREDENTIAL: GoogleIdentity(
                subject="subject-2",
                email="other@gmail.com",
                email_verified=True,
            ),
        }
    )


def run(coroutine):
    return asyncio.run(coroutine)


async def client(kit):
    from httpx import ASGITransport, AsyncClient

    return AsyncClient(
        transport=ASGITransport(app=kit.app, raise_app_exceptions=True),
        base_url="http://validator.test",
        follow_redirects=False,
    )


async def callback(http, credential: str = CREDENTIAL, *, csrf: str = GOOGLE_CSRF):
    http.cookies.set(GOOGLE_CSRF_COOKIE, csrf)
    return await http.post(
        "/v1/auth/google/callback",
        data={"credential": credential, GOOGLE_CSRF_COOKIE: csrf},
    )


async def mint_email_token(kit, email: str) -> str:
    import datetime as dt

    from conjectures_subnet.db import accounts as account_store
    from conjectures_subnet.db.models import LoginChallengeKind
    from submission_api import sessions

    token = sessions.new_token()
    async with kit.session() as session:
        await account_store.create_challenge(
            session,
            kind=LoginChallengeKind.EMAIL,
            secret_digest=account_store.digest(token),
            expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(minutes=15),
            email=email,
        )
        await session.commit()
    return token


async def sign_in_email(kit, http, email: str):
    token = await mint_email_token(kit, email)
    response = await http.post("/v1/auth/email/verify", json={"token": token})
    assert response.status_code == 200, response.text
    return response.json()["account"]


def csrf(http) -> dict[str, str]:
    return {CSRF_HEADER: http.cookies[CSRF_COOKIE]}


def test_google_callback_creates_one_identity_and_the_normal_session():
    async def scenario():
        kit = await harness(google=google(), CORS_ALLOWED_ORIGINS=ORIGIN).setup()
        try:
            async with await client(kit) as http:
                response = await callback(http)
                assert response.status_code == 303, response.text
                assert response.headers["location"] == "http://localhost:3000/account"

                session = await http.get("/v1/auth/session")
                assert session.status_code == 200, session.text
                account = session.json()["account"]
                assert account["email"] == EMAIL
                assert account["email_verified"] is True
                assert account["identities"][0]["provider"] == "google"
                assert account["identities"][0]["email"] == EMAIL
                assert "subject" not in account["identities"][0]

                # A second callback resolves by stable subject rather than creating a duplicate.
                again = await callback(http)
                assert again.status_code == 303
                async with kit.session() as database:
                    assert await database.scalar(select(func.count()).select_from(Account)) == 1
                    assert (
                        await database.scalar(select(func.count()).select_from(AccountIdentity))
                        == 1
                    )
        finally:
            await kit.teardown()

    run(scenario())

def test_google_callback_requires_the_provider_double_submit_csrf_token():
    async def scenario():
        kit = await harness(google=google()).setup()
        try:
            async with await client(kit) as http:
                http.cookies.set(GOOGLE_CSRF_COOKIE, "cookie-value")
                response = await http.post(
                    "/v1/auth/google/callback",
                    data={
                        "credential": CREDENTIAL,
                        GOOGLE_CSRF_COOKIE: "different-form-value",
                    },
                )
                assert response.status_code == 403
                assert response.json()["reason_code"] == "GOOGLE_CSRF_INVALID"
        finally:
            await kit.teardown()

    run(scenario())


def test_matching_email_never_silently_merges_and_can_be_explicitly_linked():
    async def scenario():
        kit = await harness(google=google(), CORS_ALLOWED_ORIGINS=ORIGIN).setup()
        try:
            async with await client(kit) as http:
                existing = await sign_in_email(kit, http, EMAIL)

                collided = await callback(http)
                assert collided.status_code == 303
                assert collided.headers["location"].endswith(
                    "/login?reason=GOOGLE_ACCOUNT_LINK_REQUIRED"
                )
                async with kit.session() as database:
                    assert await database.scalar(select(func.count()).select_from(Account)) == 1
                    assert (
                        await database.scalar(select(func.count()).select_from(AccountIdentity))
                        == 0
                    )

                linked = await http.post(
                    "/v1/auth/google/link",
                    json={"credential": CREDENTIAL},
                    headers={**csrf(http), "Origin": ORIGIN},
                )
                assert linked.status_code == 200, linked.text
                assert linked.json()["account"]["id"] == existing["id"]
                assert linked.json()["account"]["identities"][0]["provider"] == "google"

                signed_in = await callback(http)
                assert signed_in.status_code == 303
                current = await http.get("/v1/auth/session")
                assert current.json()["account"]["id"] == existing["id"]
        finally:
            await kit.teardown()

    run(scenario())


def test_one_google_identity_cannot_be_linked_to_two_accounts():
    async def scenario():
        kit = await harness(google=google()).setup()
        try:
            async with await client(kit) as first, await client(kit) as second:
                await sign_in_email(kit, first, "first@example.com")
                attached = await first.post(
                    "/v1/auth/google/link",
                    json={"credential": CREDENTIAL},
                    headers=csrf(first),
                )
                assert attached.status_code == 200

                await sign_in_email(kit, second, "second@example.com")
                refused = await second.post(
                    "/v1/auth/google/link",
                    json={"credential": CREDENTIAL},
                    headers=csrf(second),
                )
                assert refused.status_code == 409
                assert refused.json()["reason_code"] == "GOOGLE_IDENTITY_ALREADY_LINKED"
        finally:
            await kit.teardown()

    run(scenario())
