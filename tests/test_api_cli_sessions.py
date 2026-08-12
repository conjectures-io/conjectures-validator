"""CLI bearer sessions and the admin IAM surface.

Two credentials now reach the account API: the browser's HttpOnly cookie and a bearer token a
hotkey mints for the miner CLI. Almost everything here is about the boundary between them —
what a bearer token may do, what it must not, and the ways the two could be confused for one
another. Needs a real PostgreSQL server:

    docker compose -f docker-compose.pytest-db.yml up -d

Signatures are real sr25519 over the exact messages the server minted, using the standard
development URIs, so a test signs as the miner it claims to be.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

pytest.importorskip("fastapi", reason="submission API tests need the service extra")
pytest.importorskip("sqlalchemy", reason="submission API tests need the db extra")
pytest.importorskip("httpx", reason="submission API tests need the service extra")
pytest.importorskip("psycopg", reason="submission API tests need the db extra")

from conftest_api import (  # noqa: E402
    COLDKEY,
    HOTKEY,
    OTHER_HOTKEY,
    TASK_DIGEST,
    TASK_ID,
    harness,
    postgres_dsn,
)
from test_api_accounts import (  # noqa: E402
    EMAIL,
    client,
    csrf,
    run,
    sign,
    sign_in_by_email,
)

from conjectures_subnet.db import accounts as account_store  # noqa: E402
from conjectures_subnet.db.models import (  # noqa: E402
    ADMIN_ROLE,
    MINER_ROLE,
    REVIEWER_ROLE,
    AccountSessionKind,
)
from submission_api.sessions import (  # noqa: E402
    BEARER_TOKEN_PREFIX,
    SESSION_COOKIE,
)
from submission_api.settings import CORS_REQUEST_HEADERS  # noqa: E402

pytestmark = pytest.mark.skipif(
    postgres_dsn() is None,
    reason="no database: run `docker compose -f docker-compose.pytest-db.yml up -d`",
)

OTHER_EMAIL = "second@example.com"


# --- Helpers -------------------------------------------------------------------------------


async def link_hotkey(kit, http, hotkey: str = HOTKEY) -> None:
    """Attach a hotkey to the signed-in account, the way the website does."""
    challenge = await http.post(
        "/v1/me/hotkeys/challenge", json={"hotkey": hotkey}, headers=csrf(http)
    )
    assert challenge.status_code == 200, challenge.text
    message = challenge.json()["message"]
    linked = await http.post(
        "/v1/me/hotkeys",
        json={"hotkey": hotkey, "signature": sign(hotkey, message)},
        headers=csrf(http),
    )
    assert linked.status_code == 201, linked.text


async def cli_login(kit, cli, hotkey: str = HOTKEY) -> dict:
    """The whole CLI flow: challenge, sign the server's message, verify. Returns the body."""
    challenge = await cli.post("/v1/auth/cli/challenge", json={"address": hotkey})
    assert challenge.status_code == 200, challenge.text
    body = challenge.json()
    verified = await cli.post(
        "/v1/auth/cli/verify",
        json={
            "address": hotkey,
            "nonce": body["nonce"],
            "signature": sign(hotkey, body["message"]),
        },
    )
    assert verified.status_code == 200, verified.text
    return verified.json()


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def linked_account_with_cli_token(kit, hotkey: str = HOTKEY) -> tuple[dict, str]:
    """A signed-up account with `hotkey` linked, and a live CLI token for it."""
    async with await client(kit) as browser, await client(kit) as cli:
        account = await sign_in_by_email(kit, browser)
        await link_hotkey(kit, browser, hotkey)
        body = await cli_login(kit, cli, hotkey)
    return account, body["access_token"]


async def grant_role(kit, account_id: str, role: str) -> None:
    """Grant a role out of band, the way an operator bootstraps the first admin."""
    async with kit.session() as session:
        account = await account_store.get_account(session, uuid.UUID(account_id))
        await account_store.set_roles(session, account, [MINER_ROLE, role])
        await session.commit()


# --- The happy path ------------------------------------------------------------------------


def test_a_linked_hotkey_exchanges_a_signature_for_a_bearer_token():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as browser, await client(kit) as cli:
                await sign_in_by_email(kit, browser)
                await link_hotkey(kit, browser)

                challenge = await cli.post(
                    "/v1/auth/cli/challenge", json={"address": HOTKEY}
                )
                assert challenge.status_code == 200, challenge.text
                minted = challenge.json()
                # Domain-separated, and pinning the address it was minted for.
                assert minted["message"].startswith("conjectures-cli-session-v1\n")
                assert f"address: {HOTKEY}" in minted["message"]

                verified = await cli.post(
                    "/v1/auth/cli/verify",
                    json={
                        "address": HOTKEY,
                        "nonce": minted["nonce"],
                        "signature": sign(HOTKEY, minted["message"]),
                    },
                )
                assert verified.status_code == 200, verified.text
                body = verified.json()
                assert body["token_type"] == "bearer"
                assert body["hotkey_scope"] == HOTKEY
                assert body["access_token"].startswith(BEARER_TOKEN_PREFIX)
                # The one response in the API carrying a live credential must not be stored.
                assert verified.headers["cache-control"] == "no-store"
        finally:
            await kit.teardown()

    run(scenario())


def test_the_bearer_token_authenticates_the_account_surface():
    async def scenario():
        kit = await harness().setup()
        try:
            _, token = await linked_account_with_cli_token(kit)
            async with await client(kit) as cli:
                read = await cli.get("/v1/me", headers=bearer(token))
                assert read.status_code == 200, read.text
                listed = await cli.get("/v1/me/submissions", headers=bearer(token))
                assert listed.status_code == 200, listed.text
                assert listed.json()["items"] == []
        finally:
            await kit.teardown()

    run(scenario())


def test_a_bearer_write_needs_no_csrf_token():
    """The exemption exists because a bearer token is not an ambient credential.

    Without it every CLI write would 403 — there is no cookie for a CLI to read a CSRF value
    out of. `logout` is the simplest write to prove it on.
    """

    async def scenario():
        kit = await harness().setup()
        try:
            _, token = await linked_account_with_cli_token(kit)
            async with await client(kit) as cli:
                out = await cli.post("/v1/auth/logout", headers=bearer(token))
                assert out.status_code == 204, out.text
                after = await cli.get("/v1/me", headers=bearer(token))
                assert after.status_code == 401
        finally:
            await kit.teardown()

    run(scenario())


def test_an_unlinked_hotkey_is_refused_without_burning_the_challenge():
    """The common first-run error. It must not cost a nonce, a passphrase and a new signature."""

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as cli:
                challenge = await cli.post(
                    "/v1/auth/cli/challenge", json={"address": HOTKEY}
                )
                minted = challenge.json()
                signature = sign(HOTKEY, minted["message"])
                refused = await cli.post(
                    "/v1/auth/cli/verify",
                    json={
                        "address": HOTKEY,
                        "nonce": minted["nonce"],
                        "signature": signature,
                    },
                )
                assert refused.status_code == 403, refused.text
                assert refused.json()["reason_code"] == "HOTKEY_NOT_LINKED"

                # The nonce survived: linking the hotkey and retrying the *same* signature works.
                async with await client(kit) as browser:
                    await sign_in_by_email(kit, browser)
                    await link_hotkey(kit, browser)
                retried = await cli.post(
                    "/v1/auth/cli/verify",
                    json={
                        "address": HOTKEY,
                        "nonce": minted["nonce"],
                        "signature": signature,
                    },
                )
                assert retried.status_code == 200, retried.text
        finally:
            await kit.teardown()

    run(scenario())


def test_the_challenge_endpoint_does_not_disclose_whether_a_hotkey_is_linked():
    """Hotkeys are public on chain, so a differing answer would map keys to accounts here."""

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as cli:
                unlinked = await cli.post(
                    "/v1/auth/cli/challenge", json={"address": HOTKEY}
                )
                async with await client(kit) as browser:
                    await sign_in_by_email(kit, browser)
                    await link_hotkey(kit, browser)
                linked = await cli.post(
                    "/v1/auth/cli/challenge", json={"address": HOTKEY}
                )
                assert unlinked.status_code == linked.status_code == 200
                assert set(unlinked.json()) == set(linked.json())
        finally:
            await kit.teardown()

    run(scenario())


# --- Denial of service and abuse bounds ------------------------------------------------------


def test_a_second_challenge_does_not_invalidate_the_first():
    """The targeted-lockout regression.

    Hotkeys are public, so anyone can request a challenge for anyone's key. If verification
    resolved "the latest open challenge for this address", one request per minute from a
    stranger would permanently stop a miner logging in. Challenges are addressed by their own
    nonce instead, so both stay redeemable by whoever holds one.
    """

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as browser, await client(kit) as cli:
                await sign_in_by_email(kit, browser)
                await link_hotkey(kit, browser)

                mine = (
                    await cli.post("/v1/auth/cli/challenge", json={"address": HOTKEY})
                ).json()
                # An attacker supersedes it with a challenge of their own for the same hotkey.
                await cli.post("/v1/auth/cli/challenge", json={"address": HOTKEY})

                verified = await cli.post(
                    "/v1/auth/cli/verify",
                    json={
                        "address": HOTKEY,
                        "nonce": mine["nonce"],
                        "signature": sign(HOTKEY, mine["message"]),
                    },
                )
                assert verified.status_code == 200, verified.text
        finally:
            await kit.teardown()

    run(scenario())


def test_a_challenge_is_spent_after_too_many_failed_signatures():
    async def scenario():
        kit = await harness(LOGIN_CHALLENGE_ATTEMPTS="2").setup()
        try:
            async with await client(kit) as browser, await client(kit) as cli:
                await sign_in_by_email(kit, browser)
                await link_hotkey(kit, browser)
                minted = (
                    await cli.post("/v1/auth/cli/challenge", json={"address": HOTKEY})
                ).json()

                # A signature over the wrong bytes: valid sr25519, wrong message.
                wrong = sign(HOTKEY, "conjectures-cli-session-v1\nnot the message")
                for _ in range(2):
                    bad = await cli.post(
                        "/v1/auth/cli/verify",
                        json={
                            "address": HOTKEY,
                            "nonce": minted["nonce"],
                            "signature": wrong,
                        },
                    )
                    assert bad.status_code == 401, bad.text

                # The ceiling is reached, so even the right signature no longer finds the row.
                spent = await cli.post(
                    "/v1/auth/cli/verify",
                    json={
                        "address": HOTKEY,
                        "nonce": minted["nonce"],
                        "signature": sign(HOTKEY, minted["message"]),
                    },
                )
                assert spent.status_code == 401
                assert spent.json()["reason_code"] == "CHALLENGE_INVALID"
        finally:
            await kit.teardown()

    run(scenario())


def test_a_wrong_signature_alone_does_not_spend_the_challenge():
    """Verify-before-consume: one fat-fingered attempt must not force a restart."""

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as browser, await client(kit) as cli:
                await sign_in_by_email(kit, browser)
                await link_hotkey(kit, browser)
                minted = (
                    await cli.post("/v1/auth/cli/challenge", json={"address": HOTKEY})
                ).json()
                bad = await cli.post(
                    "/v1/auth/cli/verify",
                    json={
                        "address": HOTKEY,
                        "nonce": minted["nonce"],
                        "signature": sign(HOTKEY, "something else entirely"),
                    },
                )
                assert bad.status_code == 401
                good = await cli.post(
                    "/v1/auth/cli/verify",
                    json={
                        "address": HOTKEY,
                        "nonce": minted["nonce"],
                        "signature": sign(HOTKEY, minted["message"]),
                    },
                )
                assert good.status_code == 200, good.text
        finally:
            await kit.teardown()

    run(scenario())


def test_a_nonce_minted_for_one_hotkey_cannot_be_redeemed_for_another():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as browser, await client(kit) as cli:
                await sign_in_by_email(kit, browser)
                await link_hotkey(kit, browser, HOTKEY)
                await link_hotkey(kit, browser, OTHER_HOTKEY)
                minted = (
                    await cli.post("/v1/auth/cli/challenge", json={"address": HOTKEY})
                ).json()
                crossed = await cli.post(
                    "/v1/auth/cli/verify",
                    json={
                        "address": OTHER_HOTKEY,
                        "nonce": minted["nonce"],
                        "signature": sign(OTHER_HOTKEY, minted["message"]),
                    },
                )
                assert crossed.status_code == 401
                assert crossed.json()["reason_code"] == "CHALLENGE_INVALID"
        finally:
            await kit.teardown()

    run(scenario())


def test_a_hotkey_link_signature_cannot_be_replayed_as_a_cli_login():
    """Domain separation, on the one pair of flows that both take a hotkey signature."""

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as browser, await client(kit) as cli:
                await sign_in_by_email(kit, browser)
                link_challenge = await browser.post(
                    "/v1/me/hotkeys/challenge",
                    json={"hotkey": HOTKEY},
                    headers=csrf(browser),
                )
                link_message = link_challenge.json()["message"]
                assert link_message.startswith("conjectures-hotkey-link-v1\n")

                cli_challenge = (
                    await cli.post("/v1/auth/cli/challenge", json={"address": HOTKEY})
                ).json()
                replayed = await cli.post(
                    "/v1/auth/cli/verify",
                    json={
                        "address": HOTKEY,
                        "nonce": cli_challenge["nonce"],
                        # A real signature — over the *link* message.
                        "signature": sign(HOTKEY, link_message),
                    },
                )
                assert replayed.status_code == 401
                assert replayed.json()["reason_code"] == "SIGNATURE_INVALID"
        finally:
            await kit.teardown()

    run(scenario())


def test_the_number_of_live_cli_tokens_per_account_is_capped():
    async def scenario():
        kit = await harness(CLI_SESSIONS_PER_ACCOUNT="2").setup()
        try:
            async with await client(kit) as browser, await client(kit) as cli:
                account = await sign_in_by_email(kit, browser)
                await link_hotkey(kit, browser)
                first = (await cli_login(kit, cli))["access_token"]
                second = (await cli_login(kit, cli))["access_token"]
                third = (await cli_login(kit, cli))["access_token"]

                # The oldest was evicted rather than the newest refused.
                assert (
                    await cli.get("/v1/me", headers=bearer(first))
                ).status_code == 401
                assert (
                    await cli.get("/v1/me", headers=bearer(second))
                ).status_code == 200
                assert (
                    await cli.get("/v1/me", headers=bearer(third))
                ).status_code == 200

                async with kit.session() as session:
                    live = await account_store.live_session_count(
                        session,
                        uuid.UUID(account["id"]),
                        kind=AccountSessionKind.BEARER,
                        now=dt.datetime.now(dt.UTC),
                    )
                assert live == 2
        finally:
            await kit.teardown()

    run(scenario())


# --- Token confusion -------------------------------------------------------------------------


def test_a_cookie_token_presented_as_a_bearer_is_not_accepted():
    """One digest namespace, two kinds. The kind is in the lookup predicate.

    Not reachable by an attacker who does not already hold the cookie — it is HttpOnly — but
    the two carry different CSRF obligations, and a credential that changes which rules apply
    by changing where it is presented is the confusion worth forbidding outright.
    """

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as browser:
                await sign_in_by_email(kit, browser)
                cookie_token = browser.cookies[SESSION_COOKIE]
            async with await client(kit) as bare:
                moved = await bare.get("/v1/me", headers=bearer(cookie_token))
                assert moved.status_code == 401
        finally:
            await kit.teardown()

    run(scenario())


def test_a_bearer_token_planted_in_the_session_cookie_is_not_accepted():
    """The dangerous direction: a bearer row has no CSRF digest, so accepting it as a cookie
    would produce an ambient credential exempt from the CSRF check."""

    async def scenario():
        kit = await harness().setup()
        try:
            _, token = await linked_account_with_cli_token(kit)
            async with await client(kit) as bare:
                bare.cookies.set(SESSION_COOKIE, token, domain="validator.test")
                planted = await bare.get("/v1/me")
                assert planted.status_code == 401
        finally:
            await kit.teardown()

    run(scenario())


def test_an_invalid_bearer_header_does_not_fall_back_to_the_cookie():
    """A client that offers a bearer token is asserting which identity it wants to act as."""

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as browser:
                await sign_in_by_email(kit, browser)
                assert (await browser.get("/v1/me")).status_code == 200
                shadowed = await browser.get(
                    "/v1/me", headers=bearer("conj_cli_not-a-real-token")
                )
                assert shadowed.status_code == 401
        finally:
            await kit.teardown()

    run(scenario())


def test_a_bearer_request_is_never_answered_with_a_set_cookie():
    """The refresh middleware is cookie-only by construction; this is what says so."""

    async def scenario():
        kit = await harness().setup()
        try:
            _, token = await linked_account_with_cli_token(kit)
            async with await client(kit) as cli:
                read = await cli.get("/v1/me", headers=bearer(token))
                assert read.status_code == 200
                assert "set-cookie" not in {
                    key.lower() for key in read.headers
                }
        finally:
            await kit.teardown()

    run(scenario())


def test_authorization_is_not_a_permitted_cross_origin_request_header():
    """The allowlist is the only thing keeping the CSRF exemption out of a browser's reach."""
    assert "Authorization" not in CORS_REQUEST_HEADERS
    assert not any(name.lower() == "authorization" for name in CORS_REQUEST_HEADERS)


# --- The takeover chain --------------------------------------------------------------------


def test_a_cli_token_cannot_link_another_hotkey():
    """Step one of turning a stolen hotkey into a stolen account."""

    async def scenario():
        kit = await harness().setup()
        try:
            _, token = await linked_account_with_cli_token(kit)
            async with await client(kit) as cli:
                refused = await cli.post(
                    "/v1/me/hotkeys/challenge",
                    json={"hotkey": OTHER_HOTKEY},
                    headers=bearer(token),
                )
                assert refused.status_code == 403, refused.text
                assert refused.json()["reason_code"] == "BROWSER_SESSION_REQUIRED"
        finally:
            await kit.teardown()

    run(scenario())


def test_a_cli_token_cannot_repoint_the_payout_destination():
    """Step two, and the one that turns a compromise into money."""

    async def scenario():
        kit = await harness().setup()
        try:
            _, token = await linked_account_with_cli_token(kit)
            async with await client(kit) as cli:
                refused = await cli.put(
                    "/v1/me/payout",
                    json={"coldkey": COLDKEY, "hotkey": HOTKEY},
                    headers=bearer(token),
                )
                assert refused.status_code == 403, refused.text
                assert refused.json()["reason_code"] == "BROWSER_SESSION_REQUIRED"
        finally:
            await kit.teardown()

    run(scenario())


def test_a_cli_token_cannot_edit_the_profile_or_touch_deposits():
    async def scenario():
        kit = await harness().setup()
        try:
            _, token = await linked_account_with_cli_token(kit)
            async with await client(kit) as cli:
                edited = await cli.patch(
                    "/v1/me", json={"display_name": "taken"}, headers=bearer(token)
                )
                assert edited.status_code == 403
                deposited = await cli.post(
                    "/v1/me/deposits", json={"credits": 1}, headers=bearer(token)
                )
                assert deposited.status_code == 403
        finally:
            await kit.teardown()

    run(scenario())


def test_a_cli_token_is_scoped_to_the_hotkey_that_minted_it():
    """An account may own several hotkeys; a token speaks for exactly one of them."""

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as browser, await client(kit) as cli:
                await sign_in_by_email(kit, browser)
                await link_hotkey(kit, browser, HOTKEY)
                await link_hotkey(kit, browser, OTHER_HOTKEY)
                token = (await cli_login(kit, cli, HOTKEY))["access_token"]

                # A real task, so the refusal is unambiguously the scope check rather than a
                # rejected digest.
                body = {
                    "task_id": TASK_ID,
                    "task_bundle_sha256": TASK_DIGEST,
                    "hotkey": OTHER_HOTKEY,
                }
                refused = await cli.post(
                    "/v1/submissions/intents", json=body, headers=bearer(token)
                )
                assert refused.status_code == 403, refused.text
                assert refused.json()["reason_code"] == "HOTKEY_OUT_OF_SCOPE"

                # The browser session owns both keys and is not scoped, so it gets past the
                # check — proving the refusal above is about the credential, not the hotkey.
                allowed = await browser.post(
                    "/v1/submissions/intents", json=body, headers=csrf(browser)
                )
                assert allowed.status_code != 403, allowed.text
        finally:
            await kit.teardown()

    run(scenario())


def test_a_cli_session_sees_a_redacted_account():
    """A hotkey lives unencrypted on a mining box. It does not get the email or the payout keys."""

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as browser, await client(kit) as cli:
                await sign_in_by_email(kit, browser)
                await link_hotkey(kit, browser, HOTKEY)
                await link_hotkey(kit, browser, OTHER_HOTKEY)
                await browser.put(
                    "/v1/me/payout",
                    json={"coldkey": COLDKEY, "hotkey": HOTKEY},
                    headers=csrf(browser),
                )
                token = (await cli_login(kit, cli, HOTKEY))["access_token"]

                full = (await browser.get("/v1/me")).json()
                assert full["email"] == EMAIL
                assert full["payout"] is not None
                assert len(full["hotkeys"]) == 2

                seen = (await cli.get("/v1/me", headers=bearer(token))).json()
                assert seen["email"] is None
                assert seen["payout"] is None
                assert seen["wallets"] == []
                assert [item["hotkey"] for item in seen["hotkeys"]] == [HOTKEY]
                # Still the same account, and still honest about what it is.
                assert seen["id"] == full["id"]
                assert seen["email_verified"] is True
        finally:
            await kit.teardown()

    run(scenario())


def test_unlinking_the_scoped_hotkey_kills_the_token_on_the_next_request():
    async def scenario():
        kit = await harness().setup()
        try:
            account, token = await linked_account_with_cli_token(kit)
            async with await client(kit) as cli:
                assert (await cli.get("/v1/me", headers=bearer(token))).status_code == 200

                # Remove the link the way an operator or a future endpoint would.
                async with kit.session() as session:
                    from sqlalchemy import delete

                    from conjectures_subnet.db.models import LinkedHotkey

                    await session.execute(
                        delete(LinkedHotkey).where(LinkedHotkey.hotkey == HOTKEY)
                    )
                    await session.commit()

                assert (await cli.get("/v1/me", headers=bearer(token))).status_code == 401
        finally:
            await kit.teardown()

    run(scenario())


# --- Coexistence of the two credentials ------------------------------------------------------


def test_signing_in_to_the_website_does_not_revoke_live_cli_tokens():
    """The regression the existing session tests cannot catch, because theirs are both cookies."""

    async def scenario():
        kit = await harness().setup()
        try:
            _, token = await linked_account_with_cli_token(kit)
            async with await client(kit) as second_browser, await client(kit) as cli:
                await sign_in_by_email(kit, second_browser)
                still_live = await cli.get("/v1/me", headers=bearer(token))
                assert still_live.status_code == 200, still_live.text
        finally:
            await kit.teardown()

    run(scenario())


def test_a_cli_logout_does_not_sign_the_browser_out():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as browser, await client(kit) as cli:
                await sign_in_by_email(kit, browser)
                await link_hotkey(kit, browser)
                token = (await cli_login(kit, cli))["access_token"]

                out = await cli.post("/v1/auth/logout", headers=bearer(token))
                assert out.status_code == 204
                assert (await browser.get("/v1/me")).status_code == 200
        finally:
            await kit.teardown()

    run(scenario())


def test_signing_in_to_the_website_still_retires_the_previous_browser_session():
    """The cookie-scoped revoke must not have weakened the browser guarantee."""

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as first, await client(kit) as second:
                await sign_in_by_email(kit, first)
                await sign_in_by_email(kit, second)
                assert (await first.get("/v1/me")).status_code == 401
                assert (await second.get("/v1/me")).status_code == 200
        finally:
            await kit.teardown()

    run(scenario())


# --- The session inventory ---------------------------------------------------------------


def test_the_session_listing_shows_both_kinds_and_never_a_digest():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as browser, await client(kit) as cli:
                await sign_in_by_email(kit, browser)
                await link_hotkey(kit, browser)
                await cli_login(kit, cli)

                listed = await browser.get("/v1/me/sessions")
                assert listed.status_code == 200, listed.text
                rows = listed.json()
                kinds = sorted(row["kind"] for row in rows)
                assert kinds == ["BEARER", "COOKIE"]

                current = [row for row in rows if row["current"]]
                assert len(current) == 1
                assert current[0]["kind"] == "COOKIE"

                cli_row = next(row for row in rows if row["kind"] == "BEARER")
                assert cli_row["hotkey_scope"] == HOTKEY

                serialised = listed.text
                for leak in ("token_sha256", "csrf_sha256", "access_token"):
                    assert leak not in serialised
        finally:
            await kit.teardown()

    run(scenario())


def test_a_leaked_cli_token_can_be_revoked_from_the_browser():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as browser, await client(kit) as cli:
                await sign_in_by_email(kit, browser)
                await link_hotkey(kit, browser)
                token = (await cli_login(kit, cli))["access_token"]

                rows = (await browser.get("/v1/me/sessions")).json()
                target = next(row for row in rows if row["kind"] == "BEARER")
                killed = await browser.delete(
                    f"/v1/me/sessions/{target['id']}", headers=csrf(browser)
                )
                assert killed.status_code == 204, killed.text
                assert (await cli.get("/v1/me", headers=bearer(token))).status_code == 401
                assert (await browser.get("/v1/me")).status_code == 200
        finally:
            await kit.teardown()

    run(scenario())


def test_one_account_cannot_revoke_another_accounts_session():
    """A foreign id and a nonexistent one are the same 404 — session ids name live credentials."""

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as first, await client(kit) as second:
                await sign_in_by_email(kit, first, EMAIL)
                await sign_in_by_email(kit, second, OTHER_EMAIL)

                victim = (await first.get("/v1/me/sessions")).json()[0]["id"]
                attacked = await second.delete(
                    f"/v1/me/sessions/{victim}", headers=csrf(second)
                )
                assert attacked.status_code == 404
                missing = await second.delete(
                    f"/v1/me/sessions/{uuid.uuid4()}", headers=csrf(second)
                )
                assert missing.status_code == 404
                assert attacked.json()["reason_code"] == missing.json()["reason_code"]
                assert (await first.get("/v1/me")).status_code == 200
        finally:
            await kit.teardown()

    run(scenario())


def test_revoking_every_other_session_spares_the_caller_and_can_select_a_kind():
    async def scenario():
        kit = await harness().setup()
        try:
            async with (
                await client(kit) as browser,
                await client(kit) as cli,
                await client(kit) as other_browser,
            ):
                await sign_in_by_email(kit, browser)
                await link_hotkey(kit, browser)
                token = (await cli_login(kit, cli))["access_token"]

                cleared = await browser.delete(
                    "/v1/me/sessions?kind=BEARER", headers=csrf(browser)
                )
                assert cleared.status_code == 204, cleared.text
                assert (await cli.get("/v1/me", headers=bearer(token))).status_code == 401
                assert (await browser.get("/v1/me")).status_code == 200
                assert other_browser is not None
        finally:
            await kit.teardown()

    run(scenario())


# --- Roles and the admin surface ---------------------------------------------------------


def test_the_admin_surface_is_closed_to_an_ordinary_account():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as browser:
                account = await sign_in_by_email(kit, browser)
                assert account["roles"] == [MINER_ROLE]
                refused = await browser.get(f"/v1/admin/accounts/{account['id']}")
                assert refused.status_code == 403
                assert refused.json()["reason_code"] == "ROLE_REQUIRED"
        finally:
            await kit.teardown()

    run(scenario())


def test_an_admin_can_grant_and_remove_the_reviewer_role():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as admin, await client(kit) as member:
                boss = await sign_in_by_email(kit, admin, EMAIL)
                await grant_role(kit, boss["id"], ADMIN_ROLE)
                subject = await sign_in_by_email(kit, member, OTHER_EMAIL)

                granted = await admin.put(
                    f"/v1/admin/accounts/{subject['id']}/roles",
                    json={"roles": [REVIEWER_ROLE]},
                    headers=csrf(admin),
                )
                assert granted.status_code == 200, granted.text
                # MINER is retained whatever was asked for.
                assert granted.json()["roles"] == [MINER_ROLE, REVIEWER_ROLE]

                removed = await admin.put(
                    f"/v1/admin/accounts/{subject['id']}/roles",
                    json={"roles": []},
                    headers=csrf(admin),
                )
                assert removed.json()["roles"] == [MINER_ROLE]
        finally:
            await kit.teardown()

    run(scenario())


def test_an_unknown_role_is_refused_rather_than_stored():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as admin:
                boss = await sign_in_by_email(kit, admin)
                await grant_role(kit, boss["id"], ADMIN_ROLE)
                refused = await admin.put(
                    f"/v1/admin/accounts/{boss['id']}/roles",
                    json={"roles": [ADMIN_ROLE, "SUPERUSER"]},
                    headers=csrf(admin),
                )
                assert refused.status_code == 409, refused.text
                assert refused.json()["reason_code"] == "UNKNOWN_ROLE"
        finally:
            await kit.teardown()

    run(scenario())


def test_an_admin_cannot_remove_their_own_admin_role():
    """With no other admin it is unrecoverable without database access."""

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as admin:
                boss = await sign_in_by_email(kit, admin)
                await grant_role(kit, boss["id"], ADMIN_ROLE)
                refused = await admin.put(
                    f"/v1/admin/accounts/{boss['id']}/roles",
                    json={"roles": [MINER_ROLE]},
                    headers=csrf(admin),
                )
                assert refused.status_code == 409, refused.text
                assert refused.json()["reason_code"] == "CANNOT_REMOVE_OWN_ADMIN"
        finally:
            await kit.teardown()

    run(scenario())


def test_an_admin_role_cannot_be_exercised_from_a_cli_token():
    """A hotkey sits unencrypted on a mining box; it must not be a route to admin."""

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as browser, await client(kit) as cli:
                boss = await sign_in_by_email(kit, browser)
                await link_hotkey(kit, browser)
                await grant_role(kit, boss["id"], ADMIN_ROLE)
                token = (await cli_login(kit, cli))["access_token"]

                # The account really does hold ADMIN...
                assert (
                    await browser.get(f"/v1/admin/accounts/{boss['id']}")
                ).status_code == 200
                # ...and the CLI credential still cannot use it.
                refused = await cli.get(
                    f"/v1/admin/accounts/{boss['id']}", headers=bearer(token)
                )
                assert refused.status_code == 403, refused.text
                assert refused.json()["reason_code"] == "ROLE_REQUIRES_BROWSER_SESSION"
        finally:
            await kit.teardown()

    run(scenario())


def test_an_admin_can_cut_every_credential_an_account_holds():
    async def scenario():
        kit = await harness().setup()
        try:
            async with (
                await client(kit) as admin,
                await client(kit) as member,
                await client(kit) as cli,
            ):
                boss = await sign_in_by_email(kit, admin, EMAIL)
                await grant_role(kit, boss["id"], ADMIN_ROLE)

                subject = await sign_in_by_email(kit, member, OTHER_EMAIL)
                await link_hotkey(kit, member, OTHER_HOTKEY)
                token = (await cli_login(kit, cli, OTHER_HOTKEY))["access_token"]

                listed = await admin.get(f"/v1/admin/accounts/{subject['id']}/sessions")
                assert listed.status_code == 200, listed.text
                assert sorted(row["kind"] for row in listed.json()) == [
                    "BEARER",
                    "COOKIE",
                ]
                assert all(row["current"] is False for row in listed.json())

                cut = await admin.delete(
                    f"/v1/admin/accounts/{subject['id']}/sessions", headers=csrf(admin)
                )
                assert cut.status_code == 204, cut.text
                assert (await member.get("/v1/me")).status_code == 401
                assert (await cli.get("/v1/me", headers=bearer(token))).status_code == 401
                # The admin's own session is untouched.
                assert (await admin.get("/v1/me")).status_code == 200
        finally:
            await kit.teardown()

    run(scenario())


def test_roles_are_never_taken_from_client_input_on_signup():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as browser:
                account = await sign_in_by_email(kit, browser)
                assert account["roles"] == [MINER_ROLE]
                # The profile patch has no roles field, and forbids extras.
                refused = await browser.patch(
                    "/v1/me",
                    json={"display_name": "x", "roles": [ADMIN_ROLE]},
                    headers=csrf(browser),
                )
                assert refused.status_code == 400
        finally:
            await kit.teardown()

    run(scenario())
