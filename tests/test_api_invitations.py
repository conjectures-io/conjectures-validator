"""Invitation links: issuing, reading, redeeming, and the ways redeeming must fail.

The load-bearing tests here are the last two sections. Everything above them checks that the
happy path writes what it claims; those check that it cannot be made to write it twice, which is
the only property that actually protects the ledger.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid

import pytest

pytest.importorskip("fastapi", reason="submission API tests need the service extra")
pytest.importorskip("sqlalchemy", reason="submission API tests need the db extra")

from conftest_api import build_settings, harness, postgres_dsn  # noqa: E402
from conjectures_subnet.db import credits as credit_store  # noqa: E402
from conjectures_subnet.db import invitations as invitation_store  # noqa: E402
from conjectures_subnet.db.models import (  # noqa: E402
    ADMIN_ROLE,
    Account,
    CreditEntryKind,
    CreditLedgerEntry,
    Invitation,
)
from submission_api import sessions  # noqa: E402

pytestmark = pytest.mark.skipif(
    postgres_dsn() is None,
    reason="no database: run `docker compose -f docker-compose.pytest-db.yml up -d`",
)

EMAIL = "mathematician@example.com"
OTHER_EMAIL = "second@example.com"
PRICE = 500_000_000


def run(coroutine):
    return asyncio.run(coroutine)


async def _client(kit, **kwargs):
    from httpx import ASGITransport, AsyncClient

    return AsyncClient(
        transport=ASGITransport(app=kit.app, raise_app_exceptions=True),
        base_url="http://validator.test",
        **kwargs,
    )


def same_origin() -> dict[str, str]:
    """What a browser sends; a page cannot forge it. See tests/test_api_accounts.py."""
    return {"Sec-Fetch-Site": "same-origin"}


async def _sign_in(kit, http, email: str = EMAIL) -> dict:
    """The magic-link flow, with a token the test minted so it knows the plaintext."""
    import datetime as dtime

    from conjectures_subnet.db import accounts as account_store
    from conjectures_subnet.db.models import LoginChallengeKind

    token = sessions.new_token()
    async with kit.session() as session:
        await account_store.create_challenge(
            session,
            kind=LoginChallengeKind.EMAIL,
            secret_digest=account_store.digest(token),
            expires_at=dtime.datetime.now(dtime.UTC) + dtime.timedelta(minutes=15),
            email=email,
        )
        await session.commit()
    verified = await http.post("/v1/auth/email/verify", json={"token": token})
    assert verified.status_code == 200, verified.text
    return verified.json()["account"]


async def _grant_admin(kit, account_id: str) -> None:
    """Roles are never client input, so a test grants one the way an operator would: in the row."""
    async with kit.session() as session:
        account = await session.get(Account, uuid.UUID(account_id))
        assert account is not None
        account.roles = [*account.roles, ADMIN_ROLE]
        await session.commit()


async def _issue(kit, http, **body) -> dict:
    """Create an invitation as an admin and return the response body, code included."""
    account = await _sign_in(kit, http, email="operator@example.com")
    await _grant_admin(kit, account["id"])
    payload = {"credits": 3, "note": "mathaton Krakow", **body}
    created = await http.post(
        "/v1/admin/invitations", json=payload, headers=same_origin()
    )
    assert created.status_code == 201, created.text
    return created.json()


# --- Issuing -------------------------------------------------------------------------------


def test_the_code_is_returned_once_and_never_stored():
    """The response carries the code; the database carries only its digest.

    This is the whole security argument for the feature: an operator session that is taken over
    yields the inventory of invitations, not the ability to redeem them.
    """

    async def scenario():
        kit = await harness().setup()
        async with await _client(kit) as http:
            body = await _issue(kit, http)
            code = body["code"]
            assert body["url"].endswith(f"/invite/{code}")

            async with kit.session() as session:
                row = await session.get(Invitation, uuid.UUID(body["id"]))
                assert row is not None
                assert row.code_sha256 == invitation_store.code_digest(code)
                # The plaintext appears in no column of the row.
                stored = " ".join(
                    str(value) for value in row.__dict__.values() if value is not None
                )
                assert code not in stored

            # And no read-back route hands it out again.
            listed = await http.get("/v1/admin/invitations")
            assert listed.status_code == 200
            assert "code" not in listed.json()[0]

            detail = await http.get(f"/v1/admin/invitations/{body['id']}")
            assert detail.status_code == 200
            assert "code" not in detail.json()

    run(scenario())


def test_issuing_requires_admin_and_a_browser_session():
    async def scenario():
        kit = await harness().setup()
        async with await _client(kit) as http:
            await _sign_in(kit, http)  # a plain account, no ADMIN
            refused = await http.post(
                "/v1/admin/invitations",
                json={"credits": 1, "note": "nope"},
                headers=same_origin(),
            )
            assert refused.status_code == 403, refused.text

    run(scenario())


def test_an_invitation_must_say_why_it_exists():
    """`note` is required. An unlabelled invitation cannot be audited six weeks later."""

    async def scenario():
        kit = await harness().setup()
        async with await _client(kit) as http:
            account = await _sign_in(kit, http, email="operator@example.com")
            await _grant_admin(kit, account["id"])
            for payload in ({"credits": 1}, {"credits": 1, "note": "   "}):
                refused = await http.post(
                    "/v1/admin/invitations", json=payload, headers=same_origin()
                )
                # The app maps request-shape failures to 400 with a stable reason code rather
                # than leaving FastAPI's bare 422 -- see `submission_api/errors.py`.
                assert refused.status_code == 400, refused.text
                assert refused.json()["reason_code"] == "MALFORMED_REQUEST"

    run(scenario())


# --- The public page -----------------------------------------------------------------------


def test_the_public_page_describes_the_offer_without_naming_anyone():
    """Anonymous, because the recipient has no account yet — that is why this route exists."""

    async def scenario():
        kit = await harness().setup()
        async with await _client(kit) as http:
            body = await _issue(kit, http, credits=3, max_redemptions=5)

        # A second client with no session at all.
        async with await _client(kit) as anon:
            offer = await anon.get(f"/v1/invitations/{body['code']}")
            assert offer.status_code == 200, offer.text
            assert offer.json() == {
                "credits": 3,
                "remaining": 5,
                "expires_at": None,
            }
            # The URL holds a credential, so no shared cache may keep the answer.
            assert offer.headers["Cache-Control"] == "no-store"

    run(scenario())


def test_an_unknown_code_is_404_and_a_dead_one_says_why():
    """404 only for "no such code". A known-but-dead link tells the holder which reason applies.

    Collapsing these into one answer would send someone with an expired link to check their
    typing instead of asking for a new one. The code is 256 bits, so the distinction leaks
    nothing enumerable.
    """

    async def scenario():
        kit = await harness().setup()
        async with await _client(kit) as http:
            revoked = await _issue(kit, http, note="to be withdrawn")
            await http.delete(
                f"/v1/admin/invitations/{revoked['id']}", headers=same_origin()
            )
            exhausted = await _issue(kit, http, credits=1, max_redemptions=1)

        async with await _client(kit) as anon:
            missing = await anon.get("/v1/invitations/" + "z" * 43)
            assert missing.status_code == 404
            assert missing.json()["reason_code"] == "INVITATION_NOT_FOUND"

            dead = await anon.get(f"/v1/invitations/{revoked['code']}")
            assert dead.status_code == 410
            assert dead.json()["reason_code"] == "INVITATION_REVOKED"

        # Use the single redemption up, then the link reports exhaustion rather than 404.
        async with await _client(kit) as user:
            await _sign_in(kit, user)
            spent = await user.post(
                f"/v1/invitations/{exhausted['code']}/redeem", headers=same_origin()
            )
            assert spent.status_code == 201, spent.text

        async with await _client(kit) as anon:
            gone = await anon.get(f"/v1/invitations/{exhausted['code']}")
            assert gone.status_code == 410
            assert gone.json()["reason_code"] == "INVITATION_EXHAUSTED"

    run(scenario())


def test_an_expired_invitation_is_refused_by_the_clock_not_by_a_sweep():
    async def scenario():
        kit = await harness().setup()
        async with await _client(kit) as http:
            body = await _issue(kit, http)
            # Backdate it in the row: nothing sweeps expired invitations, and nothing should —
            # the expiry is a fact about the link, evaluated when someone presents it.
            async with kit.session() as session:
                row = await session.get(Invitation, uuid.UUID(body["id"]))
                row.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
                await session.commit()

        async with await _client(kit) as user:
            await _sign_in(kit, user)
            refused = await user.post(
                f"/v1/invitations/{body['code']}/redeem", headers=same_origin()
            )
            assert refused.status_code == 410
            assert refused.json()["reason_code"] == "INVITATION_EXPIRED"

    run(scenario())


# --- Redeeming -----------------------------------------------------------------------------


def test_redeeming_writes_one_grant_that_names_its_redemption():
    """The ledger entry is a GRANT, priced at redemption, pointing at the redemption row.

    Checked in the database rather than through the response, because the constraint that a
    grant is never reachable without its source is a schema property and this is what proves the
    code satisfies it.
    """

    async def scenario():
        kit = await harness().setup()
        async with await _client(kit) as http:
            body = await _issue(kit, http, credits=3)

        async with await _client(kit) as user:
            account = await _sign_in(kit, user)
            redeemed = await user.post(
                f"/v1/invitations/{body['code']}/redeem", headers=same_origin()
            )
            assert redeemed.status_code == 201, redeemed.text
            assert redeemed.json()["credits_granted"] == 3
            assert redeemed.json()["credits_available"] == 3
            assert redeemed.json()["balance_rao"] == 3 * PRICE

            # The balance the rest of the API reports agrees, because it is the same sum.
            credits_ = await user.get("/v1/me/credits")
            assert credits_.json()["credits_available"] == 3

        from sqlalchemy import select

        async with kit.session() as session:
            entry = (
                await session.execute(
                    select(CreditLedgerEntry).where(
                        CreditLedgerEntry.account_id == uuid.UUID(account["id"])
                    )
                )
            ).scalar_one()
            assert entry.kind is CreditEntryKind.GRANT
            assert entry.amount_rao == 3 * PRICE
            assert entry.credit_price_rao == PRICE
            assert entry.invitation_redemption_id is not None
            assert entry.created_by == "invitation"

    run(scenario())


def test_a_granted_credit_is_indistinguishable_from_a_bought_one_downstream():
    """The point of using the ledger rather than a side table: nothing downstream special-cases it.

    A mathematician who redeemed an invitation can submit on exactly the terms a miner who paid
    can, because `credits_available` is one sum over one table.
    """

    async def scenario():
        kit = await harness().setup()
        async with await _client(kit) as http:
            body = await _issue(kit, http, credits=2)

        async with await _client(kit) as user:
            await _sign_in(kit, user)
            await user.post(
                f"/v1/invitations/{body['code']}/redeem", headers=same_origin()
            )
            ledger = await user.get("/v1/me/credits/ledger")
            assert ledger.status_code == 200, ledger.text
            kinds = [item["kind"] for item in ledger.json()["items"]]
            assert kinds == ["GRANT"]

            session_body = await user.get("/v1/auth/session")
            missing = session_body.json()["capabilities"]["submit"]["missing"]
            # The credits blocker is gone. The key blocker is not, and that is a different item
            # of work -- see the session-authorised submission path.
            assert "INSUFFICIENT_CREDITS" not in missing

    run(scenario())


def test_a_cli_token_may_not_redeem():
    """Granting credits is `CookieWriterDep`: money onto an account never comes from a CLI token.

    A bearer token is minted by a hotkey, and a hotkey sits unencrypted on a mining box. It is
    not evidence that the account holder is present for a grant -- the same rule that keeps a
    stolen key file from repointing a payout.
    """
    from test_api_cli_sessions import bearer, linked_account_with_cli_token

    async def scenario():
        kit = await harness().setup()
        try:
            async with await _client(kit) as http:
                body = await _issue(kit, http)

            _, token = await linked_account_with_cli_token(kit)
            async with await _client(kit) as cli:
                refused = await cli.post(
                    f"/v1/invitations/{body['code']}/redeem",
                    headers={**bearer(token), **same_origin()},
                )
                assert refused.status_code == 403, refused.text
                assert refused.json()["reason_code"] == "BROWSER_SESSION_REQUIRED"
        finally:
            await kit.teardown()

    run(scenario())


# --- The properties that protect the ledger ------------------------------------------------


def test_one_account_cannot_redeem_the_same_link_twice():
    async def scenario():
        kit = await harness().setup()
        async with await _client(kit) as http:
            body = await _issue(kit, http, credits=3, max_redemptions=5)

        async with await _client(kit) as user:
            await _sign_in(kit, user)
            first = await user.post(
                f"/v1/invitations/{body['code']}/redeem", headers=same_origin()
            )
            assert first.status_code == 201
            again = await user.post(
                f"/v1/invitations/{body['code']}/redeem", headers=same_origin()
            )
            assert again.status_code == 409
            assert again.json()["reason_code"] == "INVITATION_ALREADY_REDEEMED"

            # And the second attempt granted nothing.
            assert (await user.get("/v1/me/credits")).json()["credits_available"] == 3

    run(scenario())


def test_concurrent_redemptions_cannot_exceed_max_redemptions():
    """The reason `redeem` takes FOR UPDATE before reading the counter.

    Two accounts race for the last redemption of a two-use link that already has one. Without the
    row lock both read `redeemed_count == 1`, both find room, and both are granted -- the check
    that reads before it writes is not a check. Driven at the store, in two real concurrent
    transactions, because a test that serialised them would pass either way.
    """

    async def scenario():
        kit = await harness().setup()
        async with await _client(kit) as http:
            body = await _issue(kit, http, credits=1, max_redemptions=2)

        # Three accounts; the invitation allows two redemptions.
        account_ids = []
        for index in range(3):
            async with await _client(kit) as user:
                account = await _sign_in(kit, user, email=f"racer{index}@example.com")
                account_ids.append(uuid.UUID(account["id"]))

        async def attempt(account_id):
            async with kit.session() as session:
                try:
                    await invitation_store.redeem(
                        session,
                        code=body["code"],
                        account_id=account_id,
                        credit_price_rao=PRICE,
                        now=dt.datetime.now(dt.UTC),
                    )
                    await session.commit()
                    return "granted"
                except Exception as exc:  # noqa: BLE001 - the outcome is what is asserted
                    await session.rollback()
                    return type(exc).__name__

        outcomes = await asyncio.gather(*(attempt(a) for a in account_ids))
        assert outcomes.count("granted") == 2, outcomes

        from sqlalchemy import func, select

        async with kit.session() as session:
            row = await session.get(Invitation, uuid.UUID(body["id"]))
            assert row.redeemed_count == 2
            granted = (
                await session.execute(
                    select(func.count()).where(
                        CreditLedgerEntry.kind == CreditEntryKind.GRANT
                    )
                )
            ).scalar_one()
            # Exactly one ledger entry per successful redemption, and no more.
            assert granted == 2

    run(scenario())


def test_a_failed_redemption_grants_nothing_at_all():
    """One transaction: the redemption row, the ledger entry and the counter, or none of them."""

    async def scenario():
        kit = await harness().setup()
        async with await _client(kit) as http:
            body = await _issue(kit, http, credits=1, max_redemptions=1)

        async with await _client(kit) as first:
            await _sign_in(kit, first, email="first@example.com")
            assert (
                await first.post(
                    f"/v1/invitations/{body['code']}/redeem", headers=same_origin()
                )
            ).status_code == 201

        async with await _client(kit) as second:
            account = await _sign_in(kit, second, email=OTHER_EMAIL)
            refused = await second.post(
                f"/v1/invitations/{body['code']}/redeem", headers=same_origin()
            )
            assert refused.status_code == 410
            assert refused.json()["reason_code"] == "INVITATION_EXHAUSTED"
            assert (await second.get("/v1/me/credits")).json()["credits_available"] == 0

        from sqlalchemy import func, select

        async with kit.session() as session:
            entries = (
                await session.execute(
                    select(func.count()).where(
                        CreditLedgerEntry.account_id == uuid.UUID(account["id"])
                    )
                )
            ).scalar_one()
            assert entries == 0

    run(scenario())


def test_a_domain_restricted_invitation_needs_a_verified_address_at_that_domain():
    """Verified, not merely present: otherwise the restriction is satisfied by typing."""

    async def scenario():
        kit = await harness().setup()
        async with await _client(kit) as http:
            body = await _issue(kit, http, email_domain="uni.example")

        async with await _client(kit) as outsider:
            await _sign_in(kit, outsider, email="someone@elsewhere.test")
            refused = await outsider.post(
                f"/v1/invitations/{body['code']}/redeem", headers=same_origin()
            )
            # 409, not 410: the link is alive and someone else can use it.
            assert refused.status_code == 409
            assert refused.json()["reason_code"] == "INVITATION_EMAIL_DOMAIN_REQUIRED"

        async with await _client(kit) as insider:
            await _sign_in(kit, insider, email="scholar@uni.example")
            allowed = await insider.post(
                f"/v1/invitations/{body['code']}/redeem", headers=same_origin()
            )
            assert allowed.status_code == 201, allowed.text

    run(scenario())


def test_revoking_is_soft_and_keeps_the_first_timestamp():
    """Never a DELETE: ledger entries reach the row through their redemptions."""

    async def scenario():
        kit = await harness().setup()
        async with await _client(kit) as http:
            body = await _issue(kit, http, credits=1, max_redemptions=5)

            async with await _client(kit) as user:
                await _sign_in(kit, user)
                assert (
                    await user.post(
                        f"/v1/invitations/{body['code']}/redeem", headers=same_origin()
                    )
                ).status_code == 201

            first = await http.delete(
                f"/v1/admin/invitations/{body['id']}", headers=same_origin()
            )
            assert first.status_code == 200, first.text
            stamp = first.json()["revoked_at"]
            assert stamp is not None

            again = await http.delete(
                f"/v1/admin/invitations/{body['id']}", headers=same_origin()
            )
            assert again.status_code == 200
            assert again.json()["revoked_at"] == stamp

            # The redemption survives revocation, with its ledger entry still attached.
            detail = await http.get(f"/v1/admin/invitations/{body['id']}")
            assert len(detail.json()["redemptions"]) == 1
            assert detail.json()["redemptions"][0]["credit_ledger_id"] is not None

    run(scenario())


def test_everything_given_away_is_one_query():
    """The argument for GRANT being its own kind rather than folded into BONUS."""

    async def scenario():
        kit = await harness().setup()
        async with await _client(kit) as http:
            body = await _issue(kit, http, credits=2, max_redemptions=3)

        for index in range(2):
            async with await _client(kit) as user:
                await _sign_in(kit, user, email=f"taker{index}@example.com")
                await user.post(
                    f"/v1/invitations/{body['code']}/redeem", headers=same_origin()
                )

        async with kit.session() as session:
            assert await invitation_store.granted_rao(session) == 4 * PRICE

    run(scenario())


def test_the_listing_filters_by_state_in_sql():
    async def scenario():
        kit = await harness().setup()
        async with await _client(kit) as http:
            live = await _issue(kit, http, note="live")
            dead = await _issue(kit, http, note="withdrawn")
            await http.delete(
                f"/v1/admin/invitations/{dead['id']}", headers=same_origin()
            )

            active = await http.get("/v1/admin/invitations?state=active")
            assert [item["id"] for item in active.json()] == [live["id"]]

            revoked = await http.get("/v1/admin/invitations?state=revoked")
            assert [item["id"] for item in revoked.json()] == [dead["id"]]

            everything = await http.get("/v1/admin/invitations")
            assert len(everything.json()) == 2

    run(scenario())


def test_the_price_used_is_the_one_in_force_at_redemption():
    """An invitation stores attempts, not rao, so a reprice cannot change what was promised.

    Driven at the store rather than through two harnesses: `harness().setup()` drops and
    recreates the schema, so a second one would take the invitation with it.
    """

    async def scenario():
        kit = await harness().setup()
        try:
            async with await _client(kit) as http:
                body = await _issue(kit, http, credits=2, max_redemptions=2)

            async with await _client(kit) as user:
                account = await _sign_in(kit, user)

            dearer = 900_000_000
            assert dearer != PRICE
            async with kit.session() as session:
                result = await invitation_store.redeem(
                    session,
                    code=body["code"],
                    account_id=uuid.UUID(account["id"]),
                    credit_price_rao=dearer,
                    now=dt.datetime.now(dt.UTC),
                )
                await session.commit()

            # Still the two attempts the link promised; the rao follow the price in force.
            assert result.credits == 2
            assert result.amount_rao == 2 * dearer

            async with kit.session() as session:
                balance = await credit_store.credit_balance(
                    session,
                    uuid.UUID(account["id"]),
                    credit_price_rao=dearer,
                    now=dt.datetime.now(dt.UTC),
                )
                assert balance.credits_available == 2
        finally:
            await kit.teardown()

    run(scenario())


def test_settings_and_store_agree_on_the_credit_ceiling():
    """A schema ceiling nobody can reach from the API is a ceiling nobody tests."""
    assert invitation_store.MAX_GRANT_CREDITS <= 100
    assert build_settings() is not None
