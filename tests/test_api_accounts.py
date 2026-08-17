"""Accounts, sessions, cross-site writes, credits, and the credit-funded submission path.

Mostly about what must *not* work: a write a hostile page could have caused, a magic link used
twice, a signature replayed from another flow, an account reading another account's rows, a
credit spent twice. Needs a real PostgreSQL server:

    docker compose -f docker-compose.pytest-db.yml up -d

Signatures here are real sr25519 over the exact messages the server minted — the fixture
addresses are the standard development URIs, so a test can sign as the miner it claims to be.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

pytest.importorskip("fastapi", reason="submission API tests need the service extra")
pytest.importorskip("sqlalchemy", reason="submission API tests need the db extra")
pytest.importorskip("httpx", reason="submission API tests need the service extra")
pytest.importorskip("psycopg", reason="submission API tests need the db extra")

from bittensor.sp_core import Keypair
from conftest_api import (
    COLDKEY,
    HOTKEY,
    OTHER_HOTKEY,
    TASK_DIGEST,
    TASK_ID,
    distinct_bundle,
    harness,
    postgres_dsn,
)

from conjectures_subnet.attribution import public_credit
from conjectures_subnet.db import credits as credit_store
from conjectures_subnet.db import digests
from conjectures_subnet.db.models import (
    CreditEntryKind,
    IntentState,
    ManualReviewState,
    ReviewDecision,
    ReviewerKind,
    ReviewOutcome,
    Submission,
    VerificationState,
)
from submission_api.auth import development_signature
from submission_api.credits import btcli_command, parse_packages
from submission_api.routers.intents import intent_request_digest
from submission_api.sessions import LEGACY_CSRF_COOKIE, SESSION_COOKIE

pytestmark = pytest.mark.skipif(
    postgres_dsn() is None,
    reason="no database: run `docker compose -f docker-compose.pytest-db.yml up -d`",
)

ORIGIN = "https://conjectures.io"
EMAIL = "solver@example.com"
OTHER_COLDKEY = Keypair.create_from_uri("//Eve").ss58_address

# The fixture addresses are the standard development keys, so a test can sign as them.
URI = {
    HOTKEY: "//Alice",
    OTHER_HOTKEY: "//Bob",
    COLDKEY: "//Dave",
    OTHER_COLDKEY: "//Eve",
}


def run(coroutine):
    return asyncio.run(coroutine)


def sign(address: str, message: str) -> str:
    """A real signature over the exact message the server asked for."""
    return Keypair.create_from_uri(URI[address]).sign(message.encode("utf-8")).hex()


async def client(kit, **kwargs):
    from httpx import ASGITransport, AsyncClient

    return AsyncClient(
        transport=ASGITransport(app=kit.app, raise_app_exceptions=True),
        base_url="http://validator.test",
        **kwargs,
    )


async def sign_in_by_email(kit, http, email: str = EMAIL) -> dict:
    """Complete the magic-link flow and return the account body.

    The link token is read from the challenge row rather than from the log: the development mail
    sender writes it, but a test parsing log output would be testing the logger.
    """
    requested = await http.post(
        "/v1/auth/email/request-link", json={"email": email}
    )
    assert requested.status_code == 202, requested.text

    # The stored secret is a digest, so the token itself cannot be recovered from the row.
    # Tests mint their own instead: request a link, then create a second challenge whose token
    # they know. Simpler and equally faithful — `consume_challenge` is what is under test.
    token = await _mint_email_token(kit, email)
    verified = await http.post("/v1/auth/email/verify", json={"token": token})
    assert verified.status_code == 200, verified.text
    return verified.json()["account"]


async def _mint_email_token(kit, email: str) -> str:
    """Create an EMAIL challenge with a token the caller knows.

    Necessary because the table stores only a digest — which is the property being relied on
    everywhere else, so the test works with it rather than around it.
    """
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


def same_origin(_http=None) -> dict[str, str]:
    """What a browser sends when the page calling the API is served from this very origin.

    A page cannot set this header — it is on the Fetch spec's forbidden list — so a test that
    sends it is standing in for the browser, not for the attacker. The tests that stand in for
    the attacker send `cross-site`, or an `Origin` that is not on the allowlist.

    Takes and ignores an argument so that the call sites read the same whichever client they
    are acting for; the header does not depend on the session the way the old CSRF token did,
    which is most of the point of the change.
    """
    return {"Sec-Fetch-Site": "same-origin"}


async def grant_credits(kit, account_id, credits_: int, *, price: int = 500_000_000):
    """Put credits on an account without a chain.

    Goes through the real ledger — a DEPOSIT entry against a real deposit row — rather than
    writing a balance, because there is no balance column to write: the balance is the sum of
    the ledger, and a test that bypassed it would not exercise what production reads.
    """
    import datetime as dt

    async with kit.session() as session:
        deposit = await credit_store.create_deposit(
            session,
            account_id=account_id,
            amount_rao=credits_ * price,
            treasury_address=kit.settings.payment_recipient,
            credit_price_rao=price,
            expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(hours=24),
        )
        await credit_store.credit_deposit(
            session,
            deposit,
            extrinsic_reference=f"0xdeposit-{uuid.uuid4().hex[:8]}",
            sender_coldkey=COLDKEY,
            observed_amount_rao=credits_ * price,
            block=42,
        )
        await session.commit()


# --- Sessions ----------------------------------------------------------------------------


def test_the_session_endpoint_is_401_until_signed_in():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                anonymous = await http.get("/v1/auth/session")
                assert anonymous.status_code == 401
                assert anonymous.json()["reason_code"] == "NOT_AUTHENTICATED"

                account = await sign_in_by_email(kit, http)
                assert account["email"] == EMAIL
                assert account["email_verified"] is True
                assert account["roles"] == ["MINER"]

                signed_in = await http.get("/v1/auth/session")
                assert signed_in.status_code == 200
                assert signed_in.json()["account"]["id"] == account["id"]
        finally:
            await kit.teardown()

    run(scenario())


def test_sign_in_sets_one_httponly_cookie_and_expires_the_retired_one():
    """One credential, and script cannot read it.

    There used to be a second, deliberately script-readable cookie holding a CSRF token. It is
    gone — the browser's own `Origin` and `Sec-Fetch-Site` prove the same thing and cannot be
    read *or* written by a page — so the only thing a sign-in says about that name now is
    `Max-Age=0`, clearing one left behind by the previous version.
    """

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                await sign_in_by_email(kit, http)
                # Re-request so the Set-Cookie headers are on a fresh response to inspect.
                token = await _mint_email_token(kit, "second@example.com")
                fresh = await http.post("/v1/auth/email/verify", json={"token": token})
                headers = fresh.headers.get_list("set-cookie")
                session_header = next(h for h in headers if h.startswith(SESSION_COOKIE))
                retired = next(h for h in headers if h.startswith(LEGACY_CSRF_COOKIE))

                assert "HttpOnly" in session_header
                # Lax, not Strict: a magic link arrives as a cross-site top-level navigation,
                # and Strict would withhold the cookie on exactly that request.
                assert "SameSite=Lax" in session_header
                # Not Secure in development, or a browser on plain-HTTP localhost would refuse
                # to send it back.
                assert "Secure" not in session_header

                # Expired, never re-issued with a value, and so absent from the jar afterwards.
                assert retired.startswith(f"{LEGACY_CSRF_COOKIE}=;")
                assert "Max-Age=0" in retired
                assert LEGACY_CSRF_COOKIE not in http.cookies
        finally:
            await kit.teardown()

    run(scenario())


def test_a_magic_link_token_works_once():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                token = await _mint_email_token(kit, EMAIL)
                first = await http.post("/v1/auth/email/verify", json={"token": token})
                assert first.status_code == 200

                second = await http.post("/v1/auth/email/verify", json={"token": token})
                assert second.status_code == 401
                assert second.json()["reason_code"] == "CHALLENGE_INVALID"
        finally:
            await kit.teardown()

    run(scenario())


def test_request_link_never_discloses_whether_an_account_exists():
    """The response must be identical for a known address, an unknown one, and a
    rate-limited one, or this endpoint becomes an account-enumeration oracle."""

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                await sign_in_by_email(kit, http)
                known = await http.post(
                    "/v1/auth/email/request-link", json={"email": EMAIL}
                )
                unknown = await http.post(
                    "/v1/auth/email/request-link", json={"email": "nobody@example.com"}
                )
                assert known.status_code == unknown.status_code == 202
                assert known.content == unknown.content == b""

                # And past the per-address hourly limit, still 202.
                for _ in range(10):
                    limited = await http.post(
                        "/v1/auth/email/request-link", json={"email": EMAIL}
                    )
                    assert limited.status_code == 202
        finally:
            await kit.teardown()

    run(scenario())


def test_wallet_sign_in_verifies_a_real_signature_over_the_served_message():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                challenge = await http.post(
                    "/v1/auth/wallet/challenge", json={"address": COLDKEY}
                )
                assert challenge.status_code == 200, challenge.text
                body = challenge.json()
                # Domain-separated, and it pins the address and the expiry, so the signature
                # cannot be replayed into the hotkey-link flow or for another address.
                assert body["message"].startswith("conjectures-login-v1\n")
                assert f"address: {COLDKEY}" in body["message"]

                verified = await http.post(
                    "/v1/auth/wallet/verify",
                    json={
                        "address": COLDKEY,
                        "signature": sign(COLDKEY, body["message"]),
                    },
                )
                assert verified.status_code == 200, verified.text
                account = verified.json()["account"]
                assert account["wallets"] == [
                    {"coldkey": COLDKEY, "linked_at": account["wallets"][0]["linked_at"]}
                ]
                # A wallet-only account has no email and is not pretending to.
                assert account["email"] is None
        finally:
            await kit.teardown()

    run(scenario())


def test_a_signature_over_different_bytes_is_refused():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                challenge = await http.post(
                    "/v1/auth/wallet/challenge", json={"address": COLDKEY}
                )
                refused = await http.post(
                    "/v1/auth/wallet/verify",
                    json={
                        "address": COLDKEY,
                        # A valid signature, over something else entirely.
                        "signature": sign(COLDKEY, "conjectures-login-v1\nnope"),
                    },
                )
                assert refused.status_code == 401
                assert refused.json()["reason_code"] == "SIGNATURE_INVALID"
                del challenge

                # The nonce was not consumed by the failure, so the real attempt still works.
                good = await http.post(
                    "/v1/auth/wallet/challenge", json={"address": COLDKEY}
                )
                ok = await http.post(
                    "/v1/auth/wallet/verify",
                    json={
                        "address": COLDKEY,
                        "signature": sign(COLDKEY, good.json()["message"]),
                    },
                )
                assert ok.status_code == 200
        finally:
            await kit.teardown()

    run(scenario())


def test_logout_revokes_the_session_server_side():
    """Clearing the cookie alone would leave a credential that still works if captured."""

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                await sign_in_by_email(kit, http)
                stolen = http.cookies[SESSION_COOKIE]

                out = await http.post("/v1/auth/logout", headers=same_origin(http))
                assert out.status_code == 204

                # The same token, presented directly, is now dead.
                async with await client(kit) as fresh:
                    fresh.cookies.set(SESSION_COOKIE, stolen)
                    assert (await fresh.get("/v1/auth/session")).status_code == 401
        finally:
            await kit.teardown()

    run(scenario())


def test_signing_in_again_retires_the_previous_session():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                await sign_in_by_email(kit, http)
                first_token = http.cookies[SESSION_COOKIE]

                token = await _mint_email_token(kit, EMAIL)
                await http.post("/v1/auth/email/verify", json={"token": token})
                assert http.cookies[SESSION_COOKIE] != first_token

                async with await client(kit) as old:
                    old.cookies.set(SESSION_COOKIE, first_token)
                    assert (await old.get("/v1/auth/session")).status_code == 401
        finally:
            await kit.teardown()

    run(scenario())


# --- Cross-site writes ---------------------------------------------------------------------
#
# The guard has two halves and they fail in opposite directions on purpose. `CrossOriginWriteGuard`
# in middleware refuses only what the headers positively say is cross-site, so a non-browser
# client that sends neither header keeps working. `require_writer` refuses anything that is not
# positive proof, because it knows the request authenticated with a cookie — a credential the
# browser attached by itself. The tests below pin both directions, and pinning the *combination*
# is the point: fail-open middleware alone would be a hole, and fail-closed middleware alone
# would refuse the miner CLI.


def test_a_cookie_write_that_proves_nothing_is_refused():
    """The header check fails closed for an ambient credential.

    This is the load-bearing change. The old guard let a request through when both initiator
    headers were absent, because a session-bound token was there to catch it; with the token
    gone, absence has to be a refusal or there is no protection left at all.
    """

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                await sign_in_by_email(kit, http)

                silent = await http.patch("/v1/me", json={"display_name": "Ada"})
                assert silent.status_code == 403
                assert silent.json()["reason_code"] == "CROSS_SITE_WRITE_REFUSED"

                # A value no browser emits is not proof of anything either.
                nonsense = await http.patch(
                    "/v1/me",
                    json={"display_name": "Ada"},
                    headers={"Sec-Fetch-Site": "totally-fine-honest"},
                )
                assert nonsense.status_code == 403

                ok = await http.patch(
                    "/v1/me", json={"display_name": "Ada"}, headers=same_origin(http)
                )
                assert ok.status_code == 200
                assert ok.json()["display_name"] == "Ada"
        finally:
            await kit.teardown()

    run(scenario())


def test_a_cross_site_origin_cannot_change_state():
    async def scenario():
        kit = await harness(CORS_ALLOWED_ORIGINS=ORIGIN).setup()
        try:
            async with await client(kit) as http:
                await sign_in_by_email(kit, http)

                # An Origin off the allowlist is refused even though Sec-Fetch-Site says
                # same-origin: a browser sends both, and the pair cannot disagree honestly.
                refused = await http.patch(
                    "/v1/me",
                    json={"display_name": "Ada"},
                    headers={
                        **same_origin(http),
                        "Origin": "https://evil.example",
                    },
                )
                assert refused.status_code == 403
                assert refused.json()["reason_code"] == "CROSS_SITE_WRITE_REFUSED"

                # Sec-Fetch-Site alone is enough to refuse, with no Origin at all — the case of
                # an intermediary that strips Origin but cannot strip a Sec- header.
                cross = await http.patch(
                    "/v1/me",
                    json={"display_name": "Ada"},
                    headers={"Sec-Fetch-Site": "cross-site"},
                )
                assert cross.status_code == 403

                # A sibling subdomain is not this origin. `same-site` is refused for the same
                # reason the allowlist is exact: one subdomain takeover must not become account
                # access.
                sibling = await http.patch(
                    "/v1/me",
                    json={"display_name": "Ada"},
                    headers={"Sec-Fetch-Site": "same-site"},
                )
                assert sibling.status_code == 403

                # `null` is what a sandboxed iframe, a data: URL and a file:// page send. It can
                # never be configured onto the allowlist, and it is refused by name as well.
                opaque = await http.patch(
                    "/v1/me",
                    json={"display_name": "Ada"},
                    headers={"Origin": "null", "Sec-Fetch-Site": "cross-site"},
                )
                assert opaque.status_code == 403

                allowed = await http.patch(
                    "/v1/me",
                    json={"display_name": "Ada"},
                    headers={
                        **same_origin(http),
                        "Origin": ORIGIN,
                    },
                )
                assert allowed.status_code == 200
        finally:
            await kit.teardown()

    run(scenario())


def test_an_allowlisted_origin_may_write_cross_site():
    """The half of the rule that is a deliberate widening, and why it is not a weakening.

    A website on its own origin calling an API on another — `conjectures.io` to
    `api.conjectures.io`, or `www.` to the apex — produces `Sec-Fetch-Site: same-site` or
    `cross-site`. Demanding `same-origin` *as well as* an allowlisted `Origin` would mean the
    API can only ever be reverse-proxied under the website's own origin. The allowlist is the
    trust boundary; a browser naming an entry on it has said everything there is to say.
    """

    async def scenario():
        kit = await harness(CORS_ALLOWED_ORIGINS=ORIGIN).setup()
        try:
            async with await client(kit) as http:
                await sign_in_by_email(kit, http)
                for site in ("same-site", "cross-site"):
                    allowed = await http.patch(
                        "/v1/me",
                        json={"display_name": "Ada"},
                        headers={"Origin": ORIGIN, "Sec-Fetch-Site": site},
                    )
                    assert allowed.status_code == 200, site
        finally:
            await kit.teardown()

    run(scenario())


def test_the_write_allowlist_can_be_narrower_than_the_read_allowlist():
    """Reading the catalog and spending an account's credits are different grants.

    `WRITE_ALLOWED_ORIGINS` defaults to `CORS_ALLOWED_ORIGINS`, so the split is invisible until
    it is set. Set, it is authoritative: an origin that may read is not thereby an origin that
    may write.
    """

    async def scenario():
        reader = "https://docs.example"
        kit = await harness(
            CORS_ALLOWED_ORIGINS=f"{ORIGIN},{reader}",
            WRITE_ALLOWED_ORIGINS=ORIGIN,
        ).setup()
        try:
            async with await client(kit) as http:
                await sign_in_by_email(kit, http)

                refused = await http.patch(
                    "/v1/me",
                    json={"display_name": "Ada"},
                    headers={"Origin": reader, "Sec-Fetch-Site": "cross-site"},
                )
                assert refused.status_code == 403
                assert refused.json()["reason_code"] == "CROSS_SITE_WRITE_REFUSED"

                allowed = await http.patch(
                    "/v1/me",
                    json={"display_name": "Ada"},
                    headers={"Origin": ORIGIN, "Sec-Fetch-Site": "cross-site"},
                )
                assert allowed.status_code == 200
        finally:
            await kit.teardown()

    run(scenario())


def test_an_unauthenticated_write_is_guarded_too():
    """`request-link` sends mail, so a cross-site page must not be able to trigger it.

    There is no principal here for `require_writer` to inspect, which is exactly why the
    middleware half exists. It refuses the positively-cross-site case and lets the silent one
    through — a client with no cookie has nothing for a hostile page to ride on.
    """

    async def scenario():
        kit = await harness(CORS_ALLOWED_ORIGINS=ORIGIN).setup()
        try:
            async with await client(kit) as http:
                hostile = await http.post(
                    "/v1/auth/email/request-link",
                    json={"email": EMAIL},
                    headers={"Origin": "https://evil.example"},
                )
                assert hostile.status_code == 403
                assert hostile.json()["reason_code"] == "CROSS_SITE_WRITE_REFUSED"

                silent = await http.post(
                    "/v1/auth/email/request-link", json={"email": EMAIL}
                )
                assert silent.status_code == 202
        finally:
            await kit.teardown()

    run(scenario())


def test_reads_need_no_proof_of_initiator():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                await sign_in_by_email(kit, http)
                assert (await http.get("/v1/me")).status_code == 200
                assert (await http.get("/v1/me/credits")).status_code == 200
                assert (await http.get("/v1/me/submissions")).status_code == 200
        finally:
            await kit.teardown()

    run(scenario())


def test_every_account_response_is_no_store():
    """A balance or a submission list is caller-specific; `public` would be a cross-account
    disclosure through any shared cache."""

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                await sign_in_by_email(kit, http)
                for path in ("/v1/me", "/v1/me/credits", "/v1/me/submissions"):
                    response = await http.get(path)
                    assert response.headers["cache-control"] == "no-store", path
        finally:
            await kit.teardown()

    run(scenario())


# --- Linked hotkeys and payout -----------------------------------------------------------


async def link_wallet(kit, http, coldkey: str):
    challenge = await http.post(
        "/v1/me/wallets/challenge", json={"coldkey": coldkey}, headers=same_origin(http)
    )
    assert challenge.status_code == 200, challenge.text
    message = challenge.json()["message"]
    assert message.startswith("conjectures-coldkey-link-v1\n")
    return await http.post(
        "/v1/me/wallets",
        json={"coldkey": coldkey, "signature": sign(coldkey, message)},
        headers=same_origin(http),
    )


def test_multiple_coldkeys_can_be_linked_but_never_rebound_between_accounts():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as first, await client(kit) as second:
                account = await sign_in_by_email(kit, first, email="wallet-one@example.com")
                assert account["wallets"] == []

                linked = await link_wallet(kit, first, COLDKEY)
                assert linked.status_code == 201, linked.text
                linked_again = await link_wallet(kit, first, OTHER_COLDKEY)
                assert linked_again.status_code == 201, linked_again.text
                assert {item["coldkey"] for item in linked_again.json()["wallets"]} == {
                    COLDKEY,
                    OTHER_COLDKEY,
                }

                await sign_in_by_email(kit, second, email="wallet-two@example.com")
                stolen = await link_wallet(kit, second, COLDKEY)
                assert stolen.status_code == 409
                assert stolen.json()["reason_code"] == "WALLET_ALREADY_LINKED"
        finally:
            await kit.teardown()

    run(scenario())


def test_a_coldkey_link_signature_cannot_be_replayed_as_a_sign_in():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                await sign_in_by_email(kit, http)
                challenge = await http.post(
                    "/v1/me/wallets/challenge",
                    json={"coldkey": COLDKEY},
                    headers=same_origin(http),
                )
                link_message = challenge.json()["message"]

            async with await client(kit) as attacker:
                await attacker.post(
                    "/v1/auth/wallet/challenge", json={"address": COLDKEY}
                )
                replayed = await attacker.post(
                    "/v1/auth/wallet/verify",
                    json={
                        "address": COLDKEY,
                        "signature": sign(COLDKEY, link_message),
                    },
                )
                assert replayed.status_code == 401
                assert replayed.json()["reason_code"] == "SIGNATURE_INVALID"
        finally:
            await kit.teardown()

    run(scenario())


async def link(kit, http, hotkey: str):
    challenge = await http.post(
        "/v1/me/hotkeys/challenge", json={"hotkey": hotkey}, headers=same_origin(http)
    )
    assert challenge.status_code == 200, challenge.text
    message = challenge.json()["message"]
    assert message.startswith("conjectures-hotkey-link-v1\n")
    return await http.post(
        "/v1/me/hotkeys",
        json={"hotkey": hotkey, "signature": sign(hotkey, message)},
        headers=same_origin(http),
    )


def test_a_hotkey_is_linked_by_signature_and_belongs_to_one_account():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as first, await client(kit) as second:
                await sign_in_by_email(kit, first, email="one@example.com")
                linked = await link(kit, first, HOTKEY)
                assert linked.status_code == 201, linked.text
                assert [item["hotkey"] for item in linked.json()["hotkeys"]] == [HOTKEY]

                # A second account cannot claim it: attribution must have one answer, and a
                # reward one owner.
                await sign_in_by_email(kit, second, email="two@example.com")
                stolen = await link(kit, second, HOTKEY)
                assert stolen.status_code == 409
                assert stolen.json()["reason_code"] == "HOTKEY_ALREADY_LINKED"
        finally:
            await kit.teardown()

    run(scenario())


def test_a_link_signature_cannot_be_replayed_as_a_sign_in():
    """The two flows use different domain-separated prefixes precisely so that a hotkey
    signature collected for linking is not a credential."""

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                await sign_in_by_email(kit, http)
                challenge = await http.post(
                    "/v1/me/hotkeys/challenge",
                    json={"hotkey": HOTKEY},
                    headers=same_origin(http),
                )
                link_message = challenge.json()["message"]

            async with await client(kit) as attacker:
                # Open a login challenge for the same address, then present the signature that
                # was made over the *link* message.
                await attacker.post("/v1/auth/wallet/challenge", json={"address": HOTKEY})
                replayed = await attacker.post(
                    "/v1/auth/wallet/verify",
                    json={
                        "address": HOTKEY,
                        "signature": sign(HOTKEY, link_message),
                    },
                )
                assert replayed.status_code == 401
                assert replayed.json()["reason_code"] == "SIGNATURE_INVALID"
        finally:
            await kit.teardown()

    run(scenario())


def test_a_payout_destination_must_be_a_hotkey_the_account_linked():
    """Otherwise a signed-in session could nominate any address at all — the shape of a
    change-the-payout-address takeover."""

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                await sign_in_by_email(kit, http)

                premature = await http.put(
                    "/v1/me/payout",
                    json={"coldkey": COLDKEY, "hotkey": HOTKEY},
                    headers=same_origin(http),
                )
                assert premature.status_code == 409
                assert premature.json()["reason_code"] == "PAYOUT_HOTKEY_NOT_LINKED"

                await link(kit, http, HOTKEY)
                ok = await http.put(
                    "/v1/me/payout",
                    json={"coldkey": COLDKEY, "hotkey": HOTKEY},
                    headers=same_origin(http),
                )
                assert ok.status_code == 200
                assert ok.json()["payout"] == {"coldkey": COLDKEY, "hotkey": HOTKEY}
        finally:
            await kit.teardown()

    run(scenario())


def test_roles_are_never_client_input():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                await sign_in_by_email(kit, http)
                refused = await http.patch(
                    "/v1/me",
                    json={"display_name": "Ada", "roles": ["ADMIN"]},
                    headers=same_origin(http),
                )
                # `extra="forbid"` on the payload model: an unknown field is a 400, not a
                # silently ignored escalation attempt.
                assert refused.status_code == 400
                assert (await http.get("/v1/me")).json()["roles"] == ["MINER"]
        finally:
            await kit.teardown()

    run(scenario())


# --- The session envelope ------------------------------------------------------------------
# `GET /v1/auth/session` answers with everything a signed-in shell needs to draw itself. What
# these test is that the derived halves cannot disagree with `account`, and that `capabilities`
# reports the same refusal the endpoint it describes would.


async def grant_role(kit, account_id: str, role: str) -> None:
    """Grant a role out of band, the way an operator bootstraps the first admin."""
    from conjectures_subnet.db import accounts as account_store
    from conjectures_subnet.db.models import MINER_ROLE

    async with kit.session() as session:
        account = await account_store.get_account(session, uuid.UUID(account_id))
        await account_store.set_roles(session, account, [MINER_ROLE, role])
        await session.commit()


def test_the_session_envelope_carries_identities_holdings_and_capabilities():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                account = await sign_in_by_email(kit, http)
                read = await http.get("/v1/auth/session")
                assert read.status_code == 200, read.text
                # A body carrying a balance and a set of permissions must never be cached.
                assert read.headers["cache-control"] == "no-store"
                body = read.json()

                # The verified mailbox is a way back in, and is listed as one.
                assert body["identities"] == [
                    {
                        "provider": "email",
                        "label": EMAIL,
                        "linked_at": body["account"]["created_at"],
                    }
                ]
                assert body["hotkeys"] == []
                assert body["payout"] is None
                assert body["credits"] == {"balance": 0, "held": 0}
                assert body["counts"] == {
                    "submissions_total": 0,
                    "submissions_in_review": 0,
                    "rewards_unclaimed": 0,
                    # Null rather than a number: this caller may not open the queue, and a
                    # populated badge would lead to a 403.
                    "review_queue": None,
                }

                # Nothing linked and nothing bought, so both reasons are reported — in the
                # order the endpoint would hit them.
                assert body["capabilities"]["submit"] == {
                    "allowed": False,
                    "missing": ["HOTKEY_NOT_LINKED", "INSUFFICIENT_CREDITS"],
                }
                assert body["capabilities"]["set_payout"] == {
                    "allowed": False,
                    "missing": ["HOTKEY_NOT_LINKED"],
                }
                # A browser session can always buy: the declared-deposit path needs nothing
                # beyond the cookie.
                assert body["capabilities"]["buy_credits"]["allowed"] is True
                assert body["capabilities"]["review"] == {
                    "allowed": False,
                    "missing": ["ROLE_REQUIRED"],
                }
                assert body["capabilities"]["manage_roles"]["allowed"] is False
                del account
        finally:
            await kit.teardown()

    run(scenario())


def test_capabilities_open_as_the_account_gains_what_they_require():
    """The point of `missing`: a client greys a button out for a named reason and can watch it
    go away, rather than re-deriving the rule from roles, hotkeys and a balance."""

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                account = await sign_in_by_email(kit, http)

                await link(kit, http, HOTKEY)
                after_link = (await http.get("/v1/auth/session")).json()
                # The hotkey reason is gone; the credit one is not.
                assert after_link["capabilities"]["submit"]["missing"] == [
                    "INSUFFICIENT_CREDITS"
                ]
                assert after_link["capabilities"]["set_payout"]["allowed"] is True
                assert [item["hotkey"] for item in after_link["hotkeys"]] == [HOTKEY]
                # No column to name a key yet, and the field says so rather than inventing one.
                assert after_link["hotkeys"][0]["label"] is None

                await grant_credits(kit, uuid.UUID(account["id"]), 3)
                funded = (await http.get("/v1/auth/session")).json()
                assert funded["capabilities"]["submit"] == {
                    "allowed": True,
                    "missing": [],
                }
                assert funded["credits"] == {"balance": 3, "held": 0}

                # And the payout, once set, appears at the top level and inside `account` —
                # derived from one read, so the two cannot drift.
                await http.put(
                    "/v1/me/payout",
                    json={"coldkey": COLDKEY, "hotkey": HOTKEY},
                    headers=same_origin(http),
                )
                paid = (await http.get("/v1/auth/session")).json()
                assert paid["payout"] == {"coldkey": COLDKEY, "hotkey": HOTKEY}
                assert paid["payout"] == paid["account"]["payout"]
        finally:
            await kit.teardown()

    run(scenario())


def test_a_wallet_account_lists_its_coldkey_as_the_identity_that_reaches_it():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                challenge = await http.post(
                    "/v1/auth/wallet/challenge", json={"address": COLDKEY}
                )
                verified = await http.post(
                    "/v1/auth/wallet/verify",
                    json={
                        "address": COLDKEY,
                        "signature": sign(COLDKEY, challenge.json()["message"]),
                    },
                )
                assert verified.status_code == 200, verified.text

                # A sign-in answers with the whole envelope, so a client need not immediately
                # re-read the session it was just handed.
                body = verified.json()
                assert body["identities"] == [
                    {
                        "provider": "coldkey",
                        "label": COLDKEY,
                        "linked_at": body["account"]["wallets"][0]["linked_at"],
                    }
                ]
                # No mailbox, so no email identity is invented for one.
                assert body["account"]["email"] is None
                assert body["capabilities"]["buy_credits"]["allowed"] is True

                # And reading the session back gives the same thing.
                assert (await http.get("/v1/auth/session")).json() == body
        finally:
            await kit.teardown()

    run(scenario())


def test_the_review_queue_depth_is_served_only_to_a_reviewer():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                account = await sign_in_by_email(kit, http)
                assert (await http.get("/v1/auth/session")).json()["counts"][
                    "review_queue"
                ] is None

                await grant_role(kit, account["id"], "REVIEWER")
                body = (await http.get("/v1/auth/session")).json()
                assert body["capabilities"]["review"] == {
                    "allowed": True,
                    "missing": [],
                }
                # A number now, because this caller can act on it. Empty database, so zero.
                assert body["counts"]["review_queue"] == 0
        finally:
            await kit.teardown()

    run(scenario())


# --- Credits -----------------------------------------------------------------------------


def test_credit_arithmetic_floors_clamps_and_reports_the_remainder():
    """Integer rao only, and the credit count is derived from the balance rather than stored."""

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                account = await sign_in_by_email(kit, http)
                account_id = uuid.UUID(account["id"])

                empty = (await http.get("/v1/me/credits")).json()
                assert empty["credits_available"] == 0
                assert empty["balance_rao"] == 0
                assert empty["low_balance"] is True

                await grant_credits(kit, account_id, 3)
                three = (await http.get("/v1/me/credits")).json()
                assert three["credits_available"] == 3
                assert three["balance_rao"] == 1_500_000_000
                assert three["remainder_rao"] == 0
                assert three["low_balance"] is False

                # A part-credit remainder is surfaced, not silently dropped.
                async with kit.session() as session:
                    await credit_store.record_entry(
                        session,
                        account_id=account_id,
                        kind=CreditEntryKind.BONUS,
                        amount_rao=250_000_000,
                        reason="half a credit",
                    )
                    await session.commit()
                partial = (await http.get("/v1/me/credits")).json()
                assert partial["credits_available"] == 3
                assert partial["remainder_rao"] == 250_000_000
        finally:
            await kit.teardown()

    run(scenario())


def test_a_negative_adjustment_cannot_produce_negative_credits():
    """Python's floor division would turn a small negative balance into -1 available credits."""

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                account = await sign_in_by_email(kit, http)
                account_id = uuid.UUID(account["id"])
                await grant_credits(kit, account_id, 1)
                async with kit.session() as session:
                    await credit_store.record_entry(
                        session,
                        account_id=account_id,
                        kind=CreditEntryKind.ADJUSTMENT,
                        amount_rao=-600_000_000,
                        reason="over-credited by an operator",
                    )
                    await session.commit()
                body = (await http.get("/v1/me/credits")).json()
                assert body["balance_rao"] == -100_000_000
                assert body["credits_available"] == 0
                assert body["remainder_rao"] == 0
        finally:
            await kit.teardown()

    run(scenario())


def test_the_ledger_records_the_deposit_and_pages_newest_first():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                account = await sign_in_by_email(kit, http)
                account_id = uuid.UUID(account["id"])
                await grant_credits(kit, account_id, 2)
                await grant_credits(kit, account_id, 5)

                page = (await http.get("/v1/me/credits/ledger?limit=1")).json()
                assert len(page["items"]) == 1
                assert page["items"][0]["kind"] == "DEPOSIT"
                assert page["items"][0]["amount_rao"] == 2_500_000_000
                assert page["next_cursor"] is not None

                second = (
                    await http.get(
                        f"/v1/me/credits/ledger?limit=1&cursor={page['next_cursor']}"
                    )
                ).json()
                assert second["items"][0]["amount_rao"] == 1_000_000_000
                assert second["next_cursor"] is None
        finally:
            await kit.teardown()

    run(scenario())


def test_a_deposit_declares_exactly_one_credit():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                await sign_in_by_email(kit, http)
                created = await http.post(
                    "/v1/me/deposits", json={"credits": 1}, headers=same_origin(http)
                )
                assert created.status_code == 201, created.text
                body = created.json()
                assert body["status"] == "AWAITING_TRANSFER"
                assert body["amount_rao"] == 500_000_000
                assert body["credits_expected"] == 1
                # Nothing is credited by declaring a deposit.
                assert body["credited_rao"] is None
                assert "btcli wallet transfer" in body["btcli_command"]
                assert "--amount 0.5" in body["btcli_command"]

                assert (
                    await http.get(f"/v1/me/deposits/{body['id']}")
                ).json()["id"] == body["id"]
        finally:
            await kit.teardown()

    run(scenario())


def test_a_deposit_refuses_a_multi_credit_purchase():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                await sign_in_by_email(kit, http)
                refused = await http.post(
                    "/v1/me/deposits", json={"credits": 2}, headers=same_origin(http)
                )
                assert refused.status_code == 400
        finally:
            await kit.teardown()

    run(scenario())


def test_a_deposit_belonging_to_another_account_is_absent_not_forbidden():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as mine, await client(kit) as theirs:
                await sign_in_by_email(kit, mine, email="one@example.com")
                created = await mine.post(
                    "/v1/me/deposits", json={"credits": 1}, headers=same_origin(mine)
                )
                deposit_id = created.json()["id"]

                await sign_in_by_email(kit, theirs, email="two@example.com")
                probed = await theirs.get(f"/v1/me/deposits/{deposit_id}")
                assert probed.status_code == 404
                assert probed.json()["reason_code"] == "NOT_FOUND"
        finally:
            await kit.teardown()

    run(scenario())


def test_a_credited_deposit_uses_the_observed_amount_not_the_declared_one():
    """Crediting the declared amount would let someone promise 10 TAO, send 1, and be credited
    for 10."""

    async def scenario():
        import datetime as dt

        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                account = await sign_in_by_email(kit, http)
                async with kit.session() as session:
                    deposit = await credit_store.create_deposit(
                        session,
                        account_id=uuid.UUID(account["id"]),
                        amount_rao=5_000_000_000,  # declared: 10 credits
                        treasury_address=kit.settings.payment_recipient,
                        credit_price_rao=500_000_000,
                        expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
                    )
                    await credit_store.credit_deposit(
                        session,
                        deposit,
                        extrinsic_reference="0xshort-transfer",
                        sender_coldkey=COLDKEY,
                        observed_amount_rao=500_000_000,  # actually sent: 1 credit
                        block=7,
                    )
                    await session.commit()

                assert (await http.get("/v1/me/credits")).json()["credits_available"] == 1
        finally:
            await kit.teardown()

    run(scenario())


# --- The credit-funded submission path ---------------------------------------------------


def test_an_intent_needs_a_credit_and_a_linked_hotkey():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                account = await sign_in_by_email(kit, http)
                body = {
                    "task_id": TASK_ID,
                    "task_bundle_sha256": TASK_DIGEST,
                    "hotkey": HOTKEY,
                }

                unlinked = await http.post(
                    "/v1/submissions/intents", json=body, headers=same_origin(http)
                )
                assert unlinked.status_code == 409
                assert unlinked.json()["reason_code"] == "HOTKEY_NOT_LINKED"

                await link(kit, http, HOTKEY)
                broke = await http.post(
                    "/v1/submissions/intents", json=body, headers=same_origin(http)
                )
                assert broke.status_code == 409
                assert broke.json()["reason_code"] == "INSUFFICIENT_CREDITS"
                assert broke.json()["credits_available"] == 0

                await grant_credits(kit, uuid.UUID(account["id"]), 1)
                opened = await http.post(
                    "/v1/submissions/intents", json=body, headers=same_origin(http)
                )
                assert opened.status_code == 201, opened.text
                assert opened.json()["status"] == IntentState.OPEN.value
                assert opened.json()["credits_held"] == 1
                # The credit is held, not spent: the balance shows nothing available and the
                # ledger has no debit.
                credits_ = (await http.get("/v1/me/credits")).json()
                assert credits_["credits_available"] == 0
                assert credits_["held_rao"] == 500_000_000
                assert credits_["balance_rao"] == 500_000_000
        finally:
            await kit.teardown()

    run(scenario())


def test_a_held_credit_cannot_be_spent_twice_by_opening_two_intents():
    """Without the hold, a miner with one credit could open many intents and race the
    confirmations."""

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                account = await sign_in_by_email(kit, http)
                await link(kit, http, HOTKEY)
                await grant_credits(kit, uuid.UUID(account["id"]), 1)
                body = {
                    "task_id": TASK_ID,
                    "task_bundle_sha256": TASK_DIGEST,
                    "hotkey": HOTKEY,
                }
                first = await http.post(
                    "/v1/submissions/intents", json=body, headers=same_origin(http)
                )
                assert first.status_code == 201
                second = await http.post(
                    "/v1/submissions/intents", json=body, headers=same_origin(http)
                )
                assert second.status_code == 409
                assert second.json()["reason_code"] == "INSUFFICIENT_CREDITS"
        finally:
            await kit.teardown()

    run(scenario())


async def full_intent(
    kit, http, account_id, marker="0001", public_credit_payload=None
):
    """Open an intent, upload a bundle, and return the intent plus the digest to sign."""
    await grant_credits(kit, account_id, 1)
    opened = await http.post(
        "/v1/submissions/intents",
        json={
            "task_id": TASK_ID,
            "task_bundle_sha256": TASK_DIGEST,
            "hotkey": HOTKEY,
            **(
                {}
                if public_credit_payload is None
                else {"public_credit": public_credit_payload}
            ),
        },
        headers=same_origin(http),
    )
    assert opened.status_code == 201, opened.text
    intent_id = opened.json()["id"]

    bundle, _ = distinct_bundle(marker, hotkey=HOTKEY)
    uploaded = await http.put(
        f"/v1/submissions/intents/{intent_id}/bundle",
        content=bundle,
        headers={**same_origin(http), "Content-Type": "application/zip"},
    )
    assert uploaded.status_code == 200, uploaded.text
    return intent_id, uploaded.json()


def test_the_server_computes_the_digest_the_miner_signs():
    """The client must never choose what it is signing."""

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                account = await sign_in_by_email(kit, http)
                await link(kit, http, HOTKEY)
                intent_id, result = await full_intent(
                    kit, http, uuid.UUID(account["id"])
                )

                assert result["request_digest"].startswith("sha256:")
                assert result["proof_sha256"].startswith("sha256:")
                assert result["intent"]["status"] == IntentState.BUNDLE_ATTACHED.value
                # The digest is derived from the admitted bytes, so it changes if the bundle
                # does — invalidating any signature made over the old one.
                _, second = await full_intent(
                    kit, http, uuid.UUID(account["id"]), marker="0002"
                )
                del intent_id
                assert second["request_digest"] != result["request_digest"]
        finally:
            await kit.teardown()

    run(scenario())


def test_confirm_debits_the_credit_and_writes_the_submission_once():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                account = await sign_in_by_email(kit, http)
                await link(kit, http, HOTKEY)
                intent_id, _ = await full_intent(kit, http, uuid.UUID(account["id"]))

                confirmed = await http.post(
                    f"/v1/submissions/intents/{intent_id}/confirm",
                    json={"signature": development_signature()},
                    headers=same_origin(http),
                )
                assert confirmed.status_code == 201, confirmed.text
                body = confirmed.json()

                submission = body["submission"]
                assert submission["verification_status"] == "UNVERIFIED"
                assert submission["hotkey"] == HOTKEY
                # The funding side is a read of durable state, not a guess.
                assert submission["funding"]["source"] == "credit"
                assert submission["funding"]["intent_id"] == intent_id
                assert submission["funding"]["payment_reference"] is None

                # The credit is spent: no hold, no balance.
                assert body["credits"]["credits_available"] == 0
                assert body["credits"]["held_rao"] == 0
                assert body["credits"]["balance_rao"] == 0

                ledger = (await http.get("/v1/me/credits/ledger")).json()["items"]
                spend = next(item for item in ledger if item["kind"] == "SPEND")
                assert spend["amount_rao"] == -500_000_000
                assert spend["submission_id"] == submission["id"]

                # A second confirm charges nothing and names the original submission.
                again = await http.post(
                    f"/v1/submissions/intents/{intent_id}/confirm",
                    json={"signature": development_signature()},
                    headers=same_origin(http),
                )
                assert again.status_code == 409
                assert again.json()["reason_code"] == "INTENT_ALREADY_CONFIRMED"
                assert again.json()["submission_id"] == submission["id"]
        finally:
            await kit.teardown()

    run(scenario())


def test_credit_funded_submission_signs_and_snapshots_public_credit():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                account = await sign_in_by_email(kit, http)
                await link(kit, http, HOTKEY)
                credit = {
                    "name": "Ramanujan Collaboration",
                    "url": "https://example.org/ramanujan",
                    "orcid": "0000-0002-1825-0097",
                }
                intent_id, uploaded = await full_intent(
                    kit,
                    http,
                    uuid.UUID(account["id"]),
                    marker="public-credit",
                    public_credit_payload=credit,
                )
                assert uploaded["intent"]["public_credit"] == credit

                confirmed = await http.post(
                    f"/v1/submissions/intents/{intent_id}/confirm",
                    json={"signature": development_signature()},
                    headers=same_origin(http),
                )
                assert confirmed.status_code == 201, confirmed.text
                assert confirmed.json()["submission"]["public_credit"] == credit

                async with kit.session() as session:
                    submission = await session.get(
                        Submission,
                        uuid.UUID(confirmed.json()["submission"]["id"]),
                    )
                    assert submission.public_credit_name == credit["name"]
                    assert submission.public_credit_url == credit["url"]
                    assert submission.public_credit_orcid == credit["orcid"]
                    signed_credit = public_credit(
                        credit["name"], credit["url"], credit["orcid"]
                    )
                    expected = intent_request_digest(
                        intent_id=uuid.UUID(intent_id),
                        hotkey=HOTKEY,
                        task_id=TASK_ID,
                        task_bundle_sha256=TASK_DIGEST,
                        proof_sha256=uploaded["proof_sha256"],
                        public_credit=signed_credit,
                    )
                    assert digests.to_prefixed(submission.request_digest) == expected
        finally:
            await kit.teardown()

    run(scenario())


def test_confirming_without_a_bundle_is_refused():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                account = await sign_in_by_email(kit, http)
                await link(kit, http, HOTKEY)
                await grant_credits(kit, uuid.UUID(account["id"]), 1)
                opened = await http.post(
                    "/v1/submissions/intents",
                    json={
                        "task_id": TASK_ID,
                        "task_bundle_sha256": TASK_DIGEST,
                        "hotkey": HOTKEY,
                    },
                    headers=same_origin(http),
                )
                refused = await http.post(
                    f"/v1/submissions/intents/{opened.json()['id']}/confirm",
                    json={"signature": development_signature()},
                    headers=same_origin(http),
                )
                assert refused.status_code == 409
                assert refused.json()["reason_code"] == "INTENT_HAS_NO_BUNDLE"
        finally:
            await kit.teardown()

    run(scenario())


def test_an_intent_belonging_to_another_account_is_absent():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as mine, await client(kit) as theirs:
                account = await sign_in_by_email(kit, mine, email="one@example.com")
                await link(kit, mine, HOTKEY)
                intent_id, _ = await full_intent(kit, mine, uuid.UUID(account["id"]))

                await sign_in_by_email(kit, theirs, email="two@example.com")
                for method, path in (
                    ("get", f"/v1/submissions/intents/{intent_id}"),
                    ("post", f"/v1/submissions/intents/{intent_id}/confirm"),
                ):
                    call = getattr(theirs, method)
                    response = await (
                        call(path)
                        if method == "get"
                        else call(
                            path,
                            json={"signature": development_signature()},
                            headers=same_origin(theirs),
                        )
                    )
                    assert response.status_code == 404, path
        finally:
            await kit.teardown()

    run(scenario())


def test_the_panel_shows_only_the_accounts_own_submissions():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as mine, await client(kit) as theirs:
                account = await sign_in_by_email(kit, mine, email="one@example.com")
                await link(kit, mine, HOTKEY)
                intent_id, _ = await full_intent(kit, mine, uuid.UUID(account["id"]))
                confirmed = await mine.post(
                    f"/v1/submissions/intents/{intent_id}/confirm",
                    json={"signature": development_signature()},
                    headers=same_origin(mine),
                )
                submission_id = confirmed.json()["submission"]["id"]

                listed = (await mine.get("/v1/me/submissions")).json()
                assert [item["id"] for item in listed["items"]] == [submission_id]

                # The timeline explains what is happening in the meantime.
                events = (
                    await mine.get(f"/v1/me/submissions/{submission_id}/events")
                ).json()
                assert [item["kind"] for item in events] == [
                    "SUBMISSION_ACCEPTED",
                    "QUEUED_FOR_VERIFICATION",
                ]

                await sign_in_by_email(kit, theirs, email="two@example.com")
                assert (await theirs.get("/v1/me/submissions")).json()["items"] == []
                assert (
                    await theirs.get(f"/v1/me/submissions/{submission_id}")
                ).status_code == 404
                assert (
                    await theirs.get(f"/v1/me/submissions/{submission_id}/events")
                ).status_code == 404
        finally:
            await kit.teardown()

    run(scenario())


def test_submission_detail_returns_public_review_notes_but_never_internal_evidence():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                account = await sign_in_by_email(kit, http)
                await link(kit, http, HOTKEY)
                intent_id, _ = await full_intent(kit, http, uuid.UUID(account["id"]))
                confirmed = await http.post(
                    f"/v1/submissions/intents/{intent_id}/confirm",
                    json={"signature": development_signature()},
                    headers=same_origin(http),
                )
                submission_id = confirmed.json()["submission"]["id"]

                async with kit.session() as session:
                    submission = await session.get(Submission, uuid.UUID(submission_id))
                    submission.verification_status = VerificationState.VERIFIED
                    submission.manual_review_status = ManualReviewState.APPROVED
                    session.add(
                        ReviewDecision(
                            submission_id=submission.id,
                            decision=ReviewOutcome.APPROVED,
                            kind=ReviewerKind.HUMAN,
                            reviewer="private-team-identity",
                            policy_version=submission.review_policy_version,
                            reason_code="FORMALIZATION_DEFECT_AWARD",
                            notes="internal security and reviewer discussion",
                            notes_public=(
                                "Lean verified the published task, but it did not match the "
                                "informal conjecture."
                            ),
                            evidence={"private_agent_trace": "never publish this"},
                        )
                    )
                    await session.commit()

                response = await http.get(f"/v1/me/submissions/{submission_id}")
                assert response.status_code == 200, response.text
                review = response.json()["review"]
                assert review["decision"] == "APPROVED"
                assert review["reason_code"] == "FORMALIZATION_DEFECT_AWARD"
                assert review["policy_version"] == "v2"
                assert review["decided_at"] is not None
                assert review["notes_public"] == (
                    "Lean verified the published task, but it did not match the informal "
                    "conjecture."
                )
                assert "internal security" not in response.text
                assert "private-team-identity" not in response.text
                assert "private_agent_trace" not in response.text
        finally:
            await kit.teardown()

    run(scenario())


def test_intake_endpoints_refuse_while_submissions_are_paused():
    async def scenario():
        kit = await harness(SUBMISSIONS_PAUSED="true").setup()
        try:
            async with await client(kit) as http:
                account = await sign_in_by_email(kit, http)
                await link(kit, http, HOTKEY)
                await grant_credits(kit, uuid.UUID(account["id"]), 1)
                refused = await http.post(
                    "/v1/submissions/intents",
                    json={
                        "task_id": TASK_ID,
                        "task_bundle_sha256": TASK_DIGEST,
                        "hotkey": HOTKEY,
                    },
                    headers=same_origin(http),
                )
                assert refused.status_code == 503
                assert refused.json()["reason_code"] == "SUBMISSIONS_PAUSED"
        finally:
            await kit.teardown()

    run(scenario())


# --- Preflight and the public Stage 2 catalog --------------------------------------------


def test_preflight_is_free_unauthenticated_and_costs_no_credit():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                bundle, digest = distinct_bundle("preflight", hotkey=HOTKEY)
                ok = await http.post(
                    "/v1/submissions/preflight",
                    content=bundle,
                    headers={
                        "Content-Type": "application/zip",
                        "X-Conjectures-Task-Id": TASK_ID,
                        "X-Conjectures-Task-Sha256": TASK_DIGEST,
                        "X-Conjectures-Hotkey": HOTKEY,
                    },
                )
                assert ok.status_code == 200, ok.text
                assert ok.json() == {
                    "ok": True,
                    "reason_code": None,
                    "detail": None,
                    "line": None,
                    "column": None,
                    "proof_sha256": digest,
                    "proof_bytes": ok.json()["proof_bytes"],
                }

                # A refused bundle is `ok: false` with a reason code, not an error status: the
                # question "would this be accepted" was answered successfully.
                bad = await http.post(
                    "/v1/submissions/preflight",
                    content=b"not a zip archive at all",
                    headers={
                        "Content-Type": "application/zip",
                        "X-Conjectures-Task-Id": TASK_ID,
                        "X-Conjectures-Task-Sha256": TASK_DIGEST,
                        "X-Conjectures-Hotkey": HOTKEY,
                    },
                )
                assert bad.status_code == 200, bad.text
                assert bad.json()["ok"] is False
                assert bad.json()["reason_code"]
        finally:
            await kit.teardown()

    run(scenario())


def test_credit_pricing_and_terms_are_public():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                pricing = await http.get("/v1/catalog/credit-pricing")
                assert pricing.status_code == 200, pricing.text
                body = pricing.json()
                assert body["price_rao"] == 500_000_000
                # No pinned USD rate configured, so the field is null rather than invented.
                assert body["price_usd"] is None
                assert body["price_usd_asof"] is None
                assert body["methods"] == ["wallet_extension"]
                assert body["recipient"] == kit.settings.payment_recipient
                assert body["packages"] == [{
                    "credits": 1,
                    "bonus_credits": 0,
                    "total_credits": 1,
                    "price_rao": 500_000_000,
                }]
                # A bonus is extra credits, never a discount: the price is credits x price.
                for package in body["packages"]:
                    assert (
                        package["price_rao"] == package["credits"] * body["price_rao"]
                    )
                    assert (
                        package["total_credits"]
                        == package["credits"] + package["bonus_credits"]
                    )

                terms = await http.get("/v1/catalog/submission-terms")
                assert terms.status_code == 200
                # v4 adds signed opt-in public credit to the expanded v2 review contract. It
                # moves with `docs/SUBMISSION_TERMS.md`: the body is served under this string, so
                # the two must not drift.
                assert terms.json()["version"] == "v4"
                assert "One credit buys" in terms.json()["body_md"]
                assert "Your hotkey is published" in terms.json()["body_md"]
                assert "Public name credit is optional" in terms.json()["body_md"]
                approval_codes = {
                    item["code"] for item in terms.json()["approval_reasons"]
                }
                assert approval_codes == {
                    "FORMALIZATION_DEFECT_AWARD",
                    "REVIEW_APPROVED",
                }
                codes = {
                    item["code"] for item in terms.json()["disqualification_reasons"]
                }
                assert "ADMITTED_DEPENDENCY" in codes
                assert "TRIVIALISED_STATEMENT" in codes
        finally:
            await kit.teardown()

    run(scenario())


def test_an_unset_choice_variable_falls_back_to_its_default():
    """`docker compose` substitutes an unset variable as the empty string.

    So `SUBMISSION_AUTHENTICATOR: ${SUBMISSION_AUTHENTICATOR:-}` reaches Settings as `""`, and
    treating that as an invalid choice makes a deployment that meant "use the default" refuse to
    boot. Found by running docker-compose.api.yml, which does exactly that for six variables.
    """
    from conftest_api import build_settings

    settings = build_settings(
        SUBMISSION_AUTHENTICATOR="",
        SUBMISSION_PAYMENT_VERIFIER="",
        SUBMISSION_DISPATCHER="",
        MAIL_SENDER="",
        APP_MODE="",
    )
    assert settings.app_mode == "DEV"
    assert settings.authenticator == "development-static-key"
    assert settings.payment_verifier == "development"
    assert settings.dispatcher == "queue"
    assert settings.mail_sender == "console"

    # A value that is present and wrong is still refused.
    from submission_api.settings import SettingsError

    with pytest.raises(SettingsError, match="SUBMISSION_DISPATCHER"):
        build_settings(SUBMISSION_DISPATCHER="in-proces")


def test_a_pinned_usd_price_must_carry_the_date_it_was_pinned():
    """A quoted price with no date cannot be judged for staleness."""
    from submission_api.settings import Settings, SettingsError

    from conftest_api import build_settings

    with pytest.raises(SettingsError, match="CREDIT_PRICE_USD_ASOF"):
        build_settings(CREDIT_PRICE_USD="4.50")
    settings = build_settings(
        CREDIT_PRICE_USD="4.50", CREDIT_PRICE_USD_ASOF="2026-08-01"
    )
    assert settings.credit_price_usd == "4.50"
    del Settings


# --- Package and command arithmetic ------------------------------------------------------


def test_a_package_bonus_is_extra_credits_not_a_discount():
    packages = parse_packages("1,10:1,50:8", credit_price_rao=500_000_000)
    assert [(item.credits, item.bonus_credits, item.price_rao) for item in packages] == [
        (1, 0, 500_000_000),
        (10, 1, 5_000_000_000),
        (50, 8, 25_000_000_000),
    ]
    assert packages[1].total_credits == 11


def test_a_bonus_larger_than_its_purchase_is_refused_as_a_typo():
    from submission_api.credits import CreditsConfigError

    with pytest.raises(CreditsConfigError, match="exceeds"):
        parse_packages("10:99", credit_price_rao=500_000_000)


def test_the_btcli_amount_is_rendered_from_integer_rao():
    """`amount_rao / 1e9` is exactly the step that silently loses a rao."""
    assert "--amount 0.5" in btcli_command(
        treasury=COLDKEY, amount_rao=500_000_000, rao_per_tao=1_000_000_000
    )
    assert "--amount 2" in btcli_command(
        treasury=COLDKEY, amount_rao=2_000_000_000, rao_per_tao=1_000_000_000
    )
    assert "--amount 1.000000001" in btcli_command(
        treasury=COLDKEY, amount_rao=1_000_000_001, rao_per_tao=1_000_000_000
    )
