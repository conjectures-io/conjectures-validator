"""Registering, signing in, and resetting a password, end to end against the database.

The properties under test are the ones the router's own comments claim, and they are mostly
*negative*: what a caller cannot learn, what is not created, and what a failed attempt does not
cost. Those are the ones that break silently.
"""

from __future__ import annotations

import asyncio
import datetime as dt

import pytest

pytest.importorskip("fastapi", reason="submission API tests need the service extra")
pytest.importorskip("sqlalchemy", reason="submission API tests need the db extra")
pytest.importorskip("httpx", reason="submission API tests need the service extra")
pytest.importorskip("psycopg", reason="submission API tests need the db extra")

from sqlalchemy import func, select

from conftest_api import harness, postgres_dsn
from conjectures_subnet.db import accounts as account_store
from conjectures_subnet.db.models import Account, LoginChallenge, LoginChallengeKind
from submission_api.passwords import hash_password, verify

pytestmark = pytest.mark.skipif(
    postgres_dsn() is None,
    reason="no database: run `docker compose -f docker-compose.pytest-db.yml up -d`",
)

EMAIL = "solver@example.com"
OTHER_EMAIL = "other@example.com"
PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "a different passphrase entirely"

# The API tests hash for real, so they run at the configured cost unless told otherwise. The
# floor keeps a five-endpoint flow at milliseconds instead of most of a second.
CHEAP = {"PASSWORD_COST_LOG2": "12"}


class MailSpy:
    """Captures what would have been mailed.

    A test cannot read the token any other way: it exists in the message and, as a digest, in a
    column — which is the property that makes the mail the credential in the first place.
    """

    def __init__(self) -> None:
        self.signup: list[dict] = []
        self.reset: list[dict] = []
        self.existing: list[dict] = []
        self.login: list[dict] = []

    async def send_login_link(self, *, email, link, expires_in_minutes):
        self.login.append({"email": email, "link": link})

    async def send_signup_link(self, *, email, link, expires_in_minutes):
        self.signup.append({"email": email, "link": link})

    async def send_password_reset_link(self, *, email, link, expires_in_minutes):
        self.reset.append({"email": email, "link": link})

    async def send_existing_account_notice(
        self, *, email, link, expires_in_minutes, sign_in_url
    ):
        self.existing.append({"email": email, "link": link, "sign_in": sign_in_url})

    @staticmethod
    def token(entry: dict) -> str:
        from urllib.parse import parse_qs, urlparse

        return parse_qs(urlparse(entry["link"]).query)["token"][0]


def run(coroutine):
    return asyncio.run(coroutine)


async def client(kit):
    from httpx import ASGITransport, AsyncClient

    return AsyncClient(
        transport=ASGITransport(app=kit.app, raise_app_exceptions=True),
        base_url="http://validator.test",
    )


def kit_with_mail(**overrides):
    spy = MailSpy()
    return harness(mail=spy, **{**CHEAP, **overrides}), spy


async def register(http, email=EMAIL, password=PASSWORD):
    return await http.post(
        "/v1/auth/password/register", json={"email": email, "password": password}
    )


async def confirm(http, token):
    return await http.post("/v1/auth/password/verify", json={"token": token})


async def login(http, email=EMAIL, password=PASSWORD):
    return await http.post(
        "/v1/auth/password/login", json={"email": email, "password": password}
    )


async def accounts_named(kit, email) -> int:
    async with kit.session() as session:
        return (
            await session.execute(
                select(func.count())
                .select_from(Account)
                .where(func.lower(Account.email) == email.lower())
            )
        ).scalar_one()


async def signed_up(kit, http, spy, email=EMAIL, password=PASSWORD):
    """A confirmed account, which is the precondition for most of what follows."""
    assert (await register(http, email, password)).status_code == 202
    response = await confirm(http, MailSpy.token(spy.signup[-1]))
    assert response.status_code == 200, response.text
    return response


# --- registering -----------------------------------------------------------------------


def test_registering_creates_no_account_until_the_link_is_followed():
    async def scenario():
        kit, spy = kit_with_mail()
        await kit.setup()
        try:
            async with await client(kit) as http:
                assert (await register(http)).status_code == 202
                # The whole premise: a registration nobody confirmed leaves no account behind,
                # so an address cannot be squatted by someone who does not hold it.
                assert await accounts_named(kit, EMAIL) == 0
                async with kit.session() as session:
                    pending = (
                        await session.execute(
                            select(LoginChallenge).where(
                                LoginChallenge.kind == LoginChallengeKind.PASSWORD_SIGNUP
                            )
                        )
                    ).scalar_one()
                assert pending.email == EMAIL
                # Hashed before it was ever written, and it is not the password.
                assert pending.password_hash.startswith("scrypt$")
                assert PASSWORD not in pending.password_hash

                response = await confirm(http, MailSpy.token(spy.signup[0]))
                assert response.status_code == 200
                assert await accounts_named(kit, EMAIL) == 1
        finally:
            await kit.teardown()

    run(scenario())


def test_confirming_verifies_the_address_and_signs_in():
    async def scenario():
        kit, spy = kit_with_mail()
        await kit.setup()
        try:
            async with await client(kit) as http:
                response = await signed_up(kit, http, spy)
                body = response.json()
                assert body["account"]["email"] == EMAIL
                # Reaching the mailbox is the proof. There is nothing further to ask for.
                assert body["account"]["email_verified"] is True
                assert "conjectures_session" in response.cookies
                # And the session is live, not just issued.
                assert (await http.get("/v1/auth/session")).status_code == 200
        finally:
            await kit.teardown()

    run(scenario())


def test_a_confirmation_token_works_exactly_once():
    async def scenario():
        kit, spy = kit_with_mail()
        await kit.setup()
        try:
            async with await client(kit) as http:
                assert (await register(http)).status_code == 202
                token = MailSpy.token(spy.signup[0])
                assert (await confirm(http, token)).status_code == 200
                # A forwarded mail, or a double-clicked link, does not make a second account.
                assert (await confirm(http, token)).status_code == 401
                assert await accounts_named(kit, EMAIL) == 1
        finally:
            await kit.teardown()

    run(scenario())


def test_an_expired_confirmation_is_refused_like_an_invented_one():
    async def scenario():
        kit, spy = kit_with_mail(EMAIL_LINK_MINUTES="1")
        await kit.setup()
        try:
            async with await client(kit) as http:
                assert (await register(http)).status_code == 202
                token = MailSpy.token(spy.signup[0])
                async with kit.session() as session:
                    # Both timestamps, because `challenge_expires_after_creation` rightly
                    # refuses a row that expired before it was made. Age the row rather than
                    # break it.
                    past = dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)
                    await session.execute(
                        LoginChallenge.__table__.update().values(
                            created_at=past, expires_at=past + dt.timedelta(minutes=15)
                        )
                    )
                    await session.commit()
                expired = await confirm(http, token)
                invented = await confirm(http, "z" * 40)
                assert expired.status_code == invented.status_code == 401
                assert (
                    expired.json()["reason_code"] == invented.json()["reason_code"]
                    == "CHALLENGE_INVALID"
                )
        finally:
            await kit.teardown()

    run(scenario())


def test_registering_a_taken_address_mails_a_reset_and_still_answers_202():
    async def scenario():
        kit, spy = kit_with_mail()
        await kit.setup()
        try:
            async with await client(kit) as http:
                await signed_up(kit, http, spy)
                # Identical status to a fresh address, so the endpoint is no enumeration
                # oracle — but the person reading the mailbox gets something actionable.
                assert (await register(http, password=NEW_PASSWORD)).status_code == 202
                assert len(spy.signup) == 1  # no second signup mail
                assert spy.existing[-1]["email"] == EMAIL
                assert "/auth/password/reset?token=" in spy.existing[-1]["link"]
                assert await accounts_named(kit, EMAIL) == 1
                # And crucially, the account still has the password its owner chose.
                assert (await login(http)).status_code == 200
        finally:
            await kit.teardown()

    run(scenario())


def test_a_weak_password_is_refused_before_anything_is_looked_up():
    async def scenario():
        kit, spy = kit_with_mail()
        await kit.setup()
        try:
            async with await client(kit) as http:
                response = await register(http, password="short")
                assert response.status_code == 400
                assert response.json()["reason_code"] == "PASSWORD_REJECTED"
                # Specific, because it is a fact about what was typed rather than about who
                # has an account here.
                assert "at least 12 characters" in response.json()["detail"]
                assert spy.signup == [] and spy.existing == []
        finally:
            await kit.teardown()

    run(scenario())


def test_an_account_appearing_between_registering_and_confirming_is_refused():
    async def scenario():
        kit, spy = kit_with_mail()
        await kit.setup()
        try:
            async with await client(kit) as http:
                assert (await register(http)).status_code == 202
                token = MailSpy.token(spy.signup[0])
                # Someone else took the address in the meantime — through Google, say.
                async with kit.session() as session:
                    await account_store.create_account(
                        session, email=EMAIL, email_verified=True
                    )
                    await session.commit()
                response = await confirm(http, token)
                # Refused, not adopted: the password on that challenge was chosen by whoever
                # started the registration, who need not be whoever owns the account now.
                assert response.status_code == 409
                assert response.json()["reason_code"] == "EMAIL_IN_USE"
                assert await accounts_named(kit, EMAIL) == 1
        finally:
            await kit.teardown()

    run(scenario())


# --- signing in ------------------------------------------------------------------------


def test_the_right_password_signs_in_and_the_wrong_one_does_not():
    async def scenario():
        kit, spy = kit_with_mail()
        await kit.setup()
        try:
            async with await client(kit) as http:
                await signed_up(kit, http, spy)
                assert (await login(http)).status_code == 200
                wrong = await login(http, password="not the passphrase at all")
                assert wrong.status_code == 401
                assert wrong.json()["reason_code"] == "PASSWORD_INVALID"
        finally:
            await kit.teardown()

    run(scenario())


def test_an_unknown_address_is_refused_identically_to_a_wrong_password():
    async def scenario():
        kit, spy = kit_with_mail()
        await kit.setup()
        try:
            async with await client(kit) as http:
                await signed_up(kit, http, spy)
                wrong = await login(http, password="not the passphrase at all")
                unknown = await login(http, email=OTHER_EMAIL)
                assert wrong.status_code == unknown.status_code == 401
                # Byte-identical body. Anything that differed would say which addresses are
                # registered here, which is what the 202 on registration is protecting.
                assert wrong.json() == unknown.json()
        finally:
            await kit.teardown()

    run(scenario())


def test_an_account_with_no_password_is_refused_identically_too():
    async def scenario():
        kit, spy = kit_with_mail()
        await kit.setup()
        try:
            async with await client(kit) as http:
                async with kit.session() as session:
                    await account_store.create_account(
                        session, email=EMAIL, email_verified=True
                    )
                    await session.commit()
                refused = await login(http)
                unknown = await login(http, email=OTHER_EMAIL)
                assert refused.status_code == 401
                assert refused.json() == unknown.json()
        finally:
            await kit.teardown()

    run(scenario())


def test_repeated_wrong_passwords_pause_the_password_method():
    async def scenario():
        kit, spy = kit_with_mail(PASSWORD_ATTEMPTS="3")
        await kit.setup()
        try:
            async with await client(kit) as http:
                await signed_up(kit, http, spy)
                for _ in range(3):
                    assert (await login(http, password="wrong wrong wrong")).status_code == 401
                paused = await login(http, password="wrong wrong wrong")
                assert paused.status_code == 429
                assert paused.json()["reason_code"] == "PASSWORD_ATTEMPTS_EXCEEDED"
                assert paused.json()["retry_after_seconds"] >= 1
                # The correct password is paused too — otherwise the ceiling would only
                # inconvenience the person who knows it.
                assert (await login(http)).status_code == 429
        finally:
            await kit.teardown()

    run(scenario())


def test_the_pause_is_reachable_for_an_address_with_no_account():
    async def scenario():
        kit, _spy = kit_with_mail(PASSWORD_ATTEMPTS="3")
        await kit.setup()
        try:
            async with await client(kit) as http:
                for _ in range(3):
                    assert (await login(http, email=OTHER_EMAIL)).status_code == 401
                paused = await login(http, email=OTHER_EMAIL)
                # If the 429 were only reachable for real accounts it would answer the
                # question the 401 is careful not to.
                assert paused.status_code == 429
                assert await accounts_named(kit, OTHER_EMAIL) == 0
        finally:
            await kit.teardown()

    run(scenario())


def test_the_pause_stops_the_password_method_and_nothing_else():
    async def scenario():
        kit, spy = kit_with_mail(PASSWORD_ATTEMPTS="2")
        await kit.setup()
        try:
            async with await client(kit) as http:
                await signed_up(kit, http, spy)
                for _ in range(3):
                    await login(http, password="wrong wrong wrong")
                assert (await login(http)).status_code == 429
                # A magic link still works. That asymmetry is what makes tripping this on
                # someone else's address cost them a button rather than their account.
                token = await mint_email_token(kit, EMAIL)
                assert (
                    await http.post("/v1/auth/email/verify", json={"token": token})
                ).status_code == 200
        finally:
            await kit.teardown()

    run(scenario())


def test_a_correct_password_forgives_the_counter():
    async def scenario():
        kit, spy = kit_with_mail(PASSWORD_ATTEMPTS="3")
        await kit.setup()
        try:
            async with await client(kit) as http:
                await signed_up(kit, http, spy)
                for _ in range(2):
                    await login(http, password="wrong wrong wrong")
                assert (await login(http)).status_code == 200
                async with kit.session() as session:
                    account = await account_store.find_by_email(session, EMAIL)
                    assert account.failed_password_attempts == 0
                    assert account.password_throttled_until is None
        finally:
            await kit.teardown()

    run(scenario())


def test_signing_in_rehashes_a_password_stored_at_a_weaker_cost():
    async def scenario():
        # Configured at 13; the stored hash is a genuine cost-12 derivation, as it would be on
        # an account that signed up before the cost was raised.
        kit, _spy = kit_with_mail(PASSWORD_COST_LOG2="13")
        await kit.setup()
        try:
            async with await client(kit) as http:
                async with kit.session() as session:
                    await account_store.create_account(
                        session,
                        email=EMAIL,
                        email_verified=True,
                        password_hash=hash_password(PASSWORD, cost_log2=12),
                    )
                    await session.commit()

                assert (await login(http)).status_code == 200
                async with kit.session() as session:
                    account = await account_store.find_by_email(session, EMAIL)
                # Rehashed in place. Signing in is the only moment the plaintext exists to
                # derive a stronger hash from, which is why this cannot be a migration.
                assert account.password_hash.split("$")[1] == "13"
                # And the new hash is the same password, not a mangled one.
                assert verify(PASSWORD, account.password_hash)
                assert (await login(http)).status_code == 200
        finally:
            await kit.teardown()

    run(scenario())


# --- resetting -------------------------------------------------------------------------


async def mint_email_token(kit, email: str) -> str:
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


async def forgot(http, email=EMAIL):
    return await http.post("/v1/auth/password/forgot", json={"email": email})


async def reset(http, token, password=NEW_PASSWORD):
    return await http.post(
        "/v1/auth/password/reset", json={"token": token, "password": password}
    )


def test_a_reset_replaces_the_password_and_signs_in():
    async def scenario():
        kit, spy = kit_with_mail()
        await kit.setup()
        try:
            async with await client(kit) as http:
                await signed_up(kit, http, spy)
                assert (await forgot(http)).status_code == 202
                response = await reset(http, MailSpy.token(spy.reset[-1]))
                assert response.status_code == 200, response.text
                assert "conjectures_session" in response.cookies
                assert (await login(http, password=NEW_PASSWORD)).status_code == 200
                assert (await login(http, password=PASSWORD)).status_code == 401
        finally:
            await kit.teardown()

    run(scenario())


def test_forgot_answers_202_for_an_address_with_no_account():
    async def scenario():
        kit, spy = kit_with_mail()
        await kit.setup()
        try:
            async with await client(kit) as http:
                assert (await forgot(http, OTHER_EMAIL)).status_code == 202
                assert spy.reset == []
        finally:
            await kit.teardown()

    run(scenario())


def test_a_reset_gives_a_password_to_an_account_that_never_had_one():
    async def scenario():
        kit, spy = kit_with_mail()
        await kit.setup()
        try:
            async with await client(kit) as http:
                # A Google-first or link-first account: an address, no password.
                async with kit.session() as session:
                    await account_store.create_account(
                        session, email=EMAIL, email_verified=True
                    )
                    await session.commit()
                assert (await login(http)).status_code == 401
                assert (await forgot(http)).status_code == 202
                assert (await reset(http, MailSpy.token(spy.reset[-1]))).status_code == 200
                assert (await login(http, password=NEW_PASSWORD)).status_code == 200
        finally:
            await kit.teardown()

    run(scenario())


def test_a_weak_new_password_does_not_burn_the_reset_link():
    async def scenario():
        kit, spy = kit_with_mail()
        await kit.setup()
        try:
            async with await client(kit) as http:
                await signed_up(kit, http, spy)
                assert (await forgot(http)).status_code == 202
                token = MailSpy.token(spy.reset[-1])
                assert (await reset(http, token, password="short")).status_code == 400
                # Policy is checked before the token is spent, or a typo would cost a mail.
                assert (await reset(http, token)).status_code == 200
        finally:
            await kit.teardown()

    run(scenario())


def test_a_reset_token_works_exactly_once():
    async def scenario():
        kit, spy = kit_with_mail()
        await kit.setup()
        try:
            async with await client(kit) as http:
                await signed_up(kit, http, spy)
                await forgot(http)
                token = MailSpy.token(spy.reset[-1])
                assert (await reset(http, token)).status_code == 200
                assert (await reset(http, token, password=PASSWORD)).status_code == 401
        finally:
            await kit.teardown()

    run(scenario())


def test_a_reset_revokes_the_browser_sessions_that_were_already_open():
    async def scenario():
        kit, spy = kit_with_mail()
        await kit.setup()
        try:
            async with await client(kit) as attacker, await client(kit) as owner:
                await signed_up(kit, attacker, spy)
                assert (await attacker.get("/v1/auth/session")).status_code == 200

                await forgot(owner)
                assert (await reset(owner, MailSpy.token(spy.reset[-1]))).status_code == 200
                # The reason most people reach for a reset: it ends the session someone else
                # had. The mail says so, and this is the assertion behind that promise.
                assert (await attacker.get("/v1/auth/session")).status_code == 401
        finally:
            await kit.teardown()

    run(scenario())


def test_a_reset_token_cannot_be_redeemed_as_a_sign_in_link():
    async def scenario():
        kit, spy = kit_with_mail()
        await kit.setup()
        try:
            async with await client(kit) as http:
                await signed_up(kit, http, spy)
                await forgot(http)
                token = MailSpy.token(spy.reset[-1])
                # `consume_challenge` matches on kind, so a token minted for one flow is not
                # redeemable in another: a reset link is not also a live session credential.
                assert (
                    await http.post("/v1/auth/email/verify", json={"token": token})
                ).status_code == 401
                assert (await confirm(http, token)).status_code == 401
                assert (await reset(http, token)).status_code == 200
        finally:
            await kit.teardown()

    run(scenario())


# --- the shared mail budget -------------------------------------------------------------


def test_one_hourly_budget_covers_every_mailed_kind_for_an_address():
    async def scenario():
        kit, spy = kit_with_mail(EMAIL_LINKS_PER_HOUR="3")
        await kit.setup()
        try:
            async with await client(kit) as http:
                await signed_up(kit, http, spy)  # spends one, on the signup mail
                assert (await forgot(http)).status_code == 202  # two
                assert (
                    await http.post("/v1/auth/email/request-link", json={"email": EMAIL})
                ).status_code == 202  # three
                mailed = len(spy.signup) + len(spy.reset) + len(spy.login)
                assert mailed == 3

                # Over budget. Still 202 — the response never says so — but nothing is sent.
                assert (await forgot(http)).status_code == 202
                assert len(spy.signup) + len(spy.reset) + len(spy.login) == 3
        finally:
            await kit.teardown()

    run(scenario())


def test_the_budget_is_per_address_not_global():
    async def scenario():
        kit, spy = kit_with_mail(EMAIL_LINKS_PER_HOUR="1")
        await kit.setup()
        try:
            async with await client(kit) as http:
                assert (await register(http)).status_code == 202
                assert (await register(http)).status_code == 202
                assert len(spy.signup) == 1
                # A different mailbox has its own budget: one address being hammered must not
                # stop anyone else from signing up.
                assert (await register(http, email=OTHER_EMAIL)).status_code == 202
                assert len(spy.signup) == 2
        finally:
            await kit.teardown()

    run(scenario())


def test_a_rehash_does_not_claim_the_owner_changed_their_password():
    async def scenario():
        kit, _spy = kit_with_mail(PASSWORD_COST_LOG2="13")
        await kit.setup()
        try:
            async with await client(kit) as http:
                async with kit.session() as session:
                    account = await account_store.create_account(
                        session,
                        email=EMAIL,
                        email_verified=True,
                        password_hash=hash_password(PASSWORD, cost_log2=12),
                    )
                    await session.commit()
                    chosen_at = account.password_updated_at

                assert (await login(http)).status_code == 200
                async with kit.session() as session:
                    account = await account_store.find_by_email(session, EMAIL)
                assert account.password_hash.split("$")[1] == "13"  # rehashed
                # ...but `password_updated_at` answers "when did the owner last choose a
                # password", and nobody chose anything here. A reset does move it; this does not.
                assert account.password_updated_at == chosen_at
        finally:
            await kit.teardown()

    run(scenario())


def test_choosing_a_new_password_does_move_that_timestamp():
    async def scenario():
        kit, spy = kit_with_mail()
        await kit.setup()
        try:
            async with await client(kit) as http:
                await signed_up(kit, http, spy)
                async with kit.session() as session:
                    before = (
                        await account_store.find_by_email(session, EMAIL)
                    ).password_updated_at
                await forgot(http)
                assert (await reset(http, MailSpy.token(spy.reset[-1]))).status_code == 200
                async with kit.session() as session:
                    after = (
                        await account_store.find_by_email(session, EMAIL)
                    ).password_updated_at
                assert after > before
        finally:
            await kit.teardown()

    run(scenario())
