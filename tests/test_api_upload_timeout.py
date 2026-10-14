"""Browser uploads must not keep the prechecks' database transaction idle."""

from __future__ import annotations

import asyncio
import datetime as dt

import pytest
from sqlalchemy import text

from conftest_api import COLDKEY, distinct_bundle, harness, postgres_dsn
from submission_api.dependencies import get_session
from test_api_accounts import client
from test_api_session_submissions import SESSION, keyless_account, params, zip_headers
from test_api_web_submissions import WEB, authorisation, ready_account
from verifier.hashing import sha256_bytes

pytestmark = pytest.mark.skipif(
    postgres_dsn() is None,
    reason="no verified private test database (see tests/database_guard.py)",
)


@pytest.mark.parametrize("signed", [False, True], ids=["session", "coldkey"])
def test_upload_outlives_database_idle_timeout_and_retry_spends_once(signed):
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                await (ready_account if signed else keyless_account)(kit, http)

                async def short_transaction():
                    async with kit.session() as session:
                        await session.execute(
                            text("SET LOCAL idle_in_transaction_session_timeout = '1s'")
                        )
                        yield session

                kit.app.dependency_overrides[get_session] = short_transaction
                bundle, _ = distinct_bundle("slow-upload", coldkey=COLDKEY if signed else None)
                query = (authorisation if signed else params)(bundle_sha256=sha256_bytes(bundle))
                route = WEB if signed else SESSION

                async def slow_body():
                    yield bundle[:100]
                    await asyncio.sleep(1.3)
                    yield bundle[100:]

                created = await http.post(
                    route,
                    params=query,
                    content=slow_body(),
                    headers={**zip_headers(http), "Content-Length": str(len(bundle))},
                )
                assert created.status_code == 201, created.text
                assert created.json()["credits"]["credits_available"] == 0

                replay = await http.post(
                    route, params=query, content=bundle, headers=zip_headers(http)
                )
                assert replay.status_code == 200, replay.text
                assert replay.json()["submission"]["id"] == created.json()["submission"]["id"]
                assert replay.json()["credits"]["credits_available"] == 0
        finally:
            await kit.teardown()

    asyncio.run(scenario())


@pytest.mark.parametrize("signed", [False, True], ids=["session", "coldkey"])
def test_credit_spent_during_upload_is_rechecked_before_acceptance(signed):
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                await (ready_account if signed else keyless_account)(kit, http)
                route = WEB if signed else SESSION
                query_for = authorisation if signed else params
                signer = COLDKEY if signed else None
                bundle, _ = distinct_bundle("upload-in-progress", coldkey=signer)
                winner, _ = distinct_bundle("concurrent-winner", coldkey=signer)

                async def interrupted_body():
                    yield bundle[:100]
                    accepted = await http.post(
                        route,
                        params=query_for(bundle_sha256=sha256_bytes(winner)),
                        content=winner,
                        headers=zip_headers(http),
                    )
                    assert accepted.status_code == 201, accepted.text
                    yield bundle[100:]

                refused = await http.post(
                    route,
                    params=query_for(bundle_sha256=sha256_bytes(bundle)),
                    content=interrupted_body(),
                    headers={**zip_headers(http), "Content-Length": str(len(bundle))},
                )
                assert refused.status_code == 409, refused.text
                assert refused.json()["reason_code"] == "INSUFFICIENT_CREDITS"
                balance = await http.get("/v1/me/credits")
                assert balance.json()["credits_available"] == 0
        finally:
            await kit.teardown()

    asyncio.run(scenario())


def test_signature_expiring_during_upload_is_refused_without_charge(monkeypatch):
    from submission_api.routers import web_submissions

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                await ready_account(kit, http)
                bundle, _ = distinct_bundle("expired-in-flight", coldkey=COLDKEY)
                query = authorisation(bundle_sha256=sha256_bytes(bundle))
                expired_at = dt.datetime.fromisoformat(query["expires_at"]) + dt.timedelta(seconds=1)

                async def delayed_body():
                    yield bundle[:100]
                    monkeypatch.setattr(web_submissions, "_now", lambda: expired_at)
                    yield bundle[100:]

                refused = await http.post(
                    WEB,
                    params=query,
                    content=delayed_body(),
                    headers={**zip_headers(http), "Content-Length": str(len(bundle))},
                )
                assert refused.status_code == 401, refused.text
                assert refused.json()["reason_code"] == web_submissions.REASON_AUTHORISATION_EXPIRED
                balance = await http.get("/v1/me/credits")
                assert balance.json()["credits_available"] == 1
        finally:
            await kit.teardown()

    asyncio.run(scenario())
