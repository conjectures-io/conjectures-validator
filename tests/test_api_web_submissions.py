"""`POST /v1/submissions/web`: one call, a coldkey signature, one credit.

The website path exists because a browser wallet holds coldkeys only, so most of what is worth
testing here is what must *not* work: a signature from a key nobody linked, a signature over a
different archive, a signature from a wallet linked to somebody else's account, a CLI token
trying to use the one path a coldkey authorises, and a retry being charged twice.

Signatures are real sr25519 over the exact message the server rebuilds — the fixture addresses
are the standard development URIs — so a test that passes has proved the server reconstructed
the same bytes the client signed. Needs a real PostgreSQL server:

    docker compose -f docker-compose.pytest-db.yml up -d
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid

import pytest

pytest.importorskip("fastapi", reason="submission API tests need the service extra")
pytest.importorskip("sqlalchemy", reason="submission API tests need the db extra")
pytest.importorskip("httpx", reason="submission API tests need the service extra")
pytest.importorskip("psycopg", reason="submission API tests need the db extra")

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
from test_api_accounts import (
    ORIGIN,
    OTHER_COLDKEY,
    client,
    grant_credits,
    link,
    same_origin,
    sign,
    sign_in_by_email,
)

from conjectures_subnet.db.models import IntentState, Submission, SubmissionIntent
from submission_api.login import web_submission_message

pytestmark = pytest.mark.skipif(
    postgres_dsn() is None,
    reason="no database: run `docker compose -f docker-compose.pytest-db.yml up -d`",
)

WEB = "/v1/submissions/web"
DOMAIN = "conjectures.io"


def run(coroutine):
    return asyncio.run(coroutine)


def stamp(offset_minutes: int = 10) -> str:
    """An expiry in the one spelling the endpoint accepts."""
    moment = dt.datetime.now(dt.UTC).replace(microsecond=0) + dt.timedelta(
        minutes=offset_minutes
    )
    return moment.isoformat().replace("+00:00", "Z")


def authorisation(
    *,
    bundle_sha256: str,
    coldkey: str = COLDKEY,
    hotkey: str = HOTKEY,
    task_id: str = TASK_ID,
    task_bundle_sha256: str = TASK_DIGEST,
    idempotency_key: str | None = None,
    expires_at: str | None = None,
    signing_coldkey: str | None = None,
) -> dict[str, str]:
    """The query a browser would send, with a real signature over the message it implies.

    `signing_coldkey` signs as somebody else without changing the claimed `coldkey`, which is
    the substitution the endpoint has to refuse.
    """
    key = idempotency_key or str(uuid.uuid4())
    until = expires_at or stamp()
    message = web_submission_message(
        domain=DOMAIN,
        address=coldkey,
        hotkey=hotkey,
        task_id=task_id,
        task_bundle_sha256=task_bundle_sha256,
        bundle_sha256=bundle_sha256,
        idempotency_key=key,
        expires_at=dt.datetime.strptime(until, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.UTC
        ),
    )
    return {
        "task_id": task_id,
        "task_bundle_sha256": task_bundle_sha256,
        "hotkey": hotkey,
        "coldkey": coldkey,
        "bundle_sha256": bundle_sha256,
        "idempotency_key": key,
        "expires_at": until,
        "signature": sign(signing_coldkey or coldkey, message),
    }


def zip_headers(http) -> dict[str, str]:
    return {**same_origin(http), "Content-Type": "application/zip"}


async def link_coldkey(kit, http, coldkey: str = COLDKEY):
    """Attach a second coldkey to an account that signed in some other way."""
    challenge = await http.post(
        "/v1/me/wallets/challenge", json={"coldkey": coldkey}, headers=same_origin(http)
    )
    assert challenge.status_code == 200, challenge.text
    message = challenge.json()["message"]
    assert message.startswith("conjectures-coldkey-link-v1\n")
    linked = await http.post(
        "/v1/me/wallets",
        json={
            "coldkey": coldkey,
            "nonce": challenge.json()["nonce"],
            "signature": sign(coldkey, message),
        },
        headers=same_origin(http),
    )
    assert linked.status_code == 201, linked.text
    return linked


async def ready_account(kit, http, *, credits_: int = 1, email="web@example.com"):
    """A signed-in account with a linked hotkey, a linked coldkey, and credits."""
    account = await sign_in_by_email(kit, http, email=email)
    await link(kit, http, HOTKEY)
    await link_coldkey(kit, http)
    if credits_:
        await grant_credits(kit, uuid.UUID(account["id"]), credits_)
    return account


# --- The path working --------------------------------------------------------------------


def test_one_call_spends_a_credit_and_writes_a_coldkey_signed_submission():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                account = await ready_account(kit, http)
                bundle, proof_digest = distinct_bundle("web-0001")

                created = await http.post(
                    WEB,
                    params=authorisation(bundle_sha256=_digest(bundle)),
                    content=bundle,
                    headers=zip_headers(http),
                )
                assert created.status_code == 201, created.text
                body = created.json()

                submission = body["submission"]
                assert submission["hotkey"] == HOTKEY
                assert submission["proof_sha256"] == proof_digest
                assert submission["verification_status"] == "UNVERIFIED"
                # Credit-funded, like the three-call flow: the schema admits no third option.
                assert submission["funding"]["source"] == "credit"
                assert submission["funding"]["payment_reference"] is None
                assert submission["funding"]["intent_id"] is not None

                # The credit is spent, not held.
                assert body["credits"]["credits_available"] == 0
                assert body["credits"]["held_rao"] == 0
                assert body["credits"]["balance_rao"] == 0

                ledger = (await http.get("/v1/me/credits/ledger")).json()["items"]
                spend = next(item for item in ledger if item["kind"] == "SPEND")
                assert spend["amount_rao"] == -500_000_000
                assert spend["submission_id"] == submission["id"]

                async with kit.session() as session:
                    row = await session.get(Submission, uuid.UUID(submission["id"]))
                    # The signature is on the row and so is the key that made it — without the
                    # second, those 64 bytes verify against nothing.
                    assert row.signer_coldkey == COLDKEY
                    assert len(row.hotkey_signature) == 64
                    assert row.account_id == uuid.UUID(account["id"])
                    # The intent the schema requires behind a credit-funded row exists, is
                    # closed, and carries the same signer.
                    intent = await session.get(SubmissionIntent, row.intent_id)
                    assert intent.status == IntentState.CONFIRMED
                    assert intent.signer_coldkey == COLDKEY
        finally:
            await kit.teardown()

    run(scenario())


def test_the_request_digest_is_the_message_that_was_actually_signed():
    """The stored digest has to be over the bytes the coldkey signed, or the row's signature is
    unverifiable after the fact."""

    async def scenario():
        from verifier.hashing import sha256_bytes

        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                await ready_account(kit, http)
                bundle, _ = distinct_bundle("web-digest")
                query = authorisation(bundle_sha256=_digest(bundle))

                created = await http.post(
                    WEB, params=query, content=bundle, headers=zip_headers(http)
                )
                assert created.status_code == 201, created.text

                message = web_submission_message(
                    domain=DOMAIN,
                    address=COLDKEY,
                    hotkey=HOTKEY,
                    task_id=TASK_ID,
                    task_bundle_sha256=TASK_DIGEST,
                    bundle_sha256=query["bundle_sha256"],
                    idempotency_key=query["idempotency_key"],
                    expires_at=dt.datetime.strptime(
                        query["expires_at"], "%Y-%m-%dT%H:%M:%SZ"
                    ).replace(tzinfo=dt.UTC),
                )
                assert created.json()["submission"]["request_digest"] == sha256_bytes(
                    message.encode("utf-8")
                )

                # And the exact preimage is kept, so the signature stays checkable.
                events = await http.get(
                    f"/v1/me/submissions/{created.json()['submission']['id']}/events"
                )
                authorised = next(
                    item
                    for item in events.json()
                    if item["kind"] == "AUTHORISED_BY_COLDKEY"
                )
                assert authorised["context"]["signed_message"] == message
                assert authorised["context"]["signer_coldkey"] == COLDKEY
        finally:
            await kit.teardown()

    run(scenario())


def test_the_public_credit_is_snapshotted_and_covered_by_the_signature():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                from conjectures_subnet.attribution import encode_public_credit_header

                await ready_account(kit, http)
                bundle, _ = distinct_bundle("web-credit")
                credit = {
                    "name": "Emmy Noether",
                    "url": "https://example.org/emmy-noether",
                    "orcid": "0000-0002-1825-0097",
                }
                query = authorisation(bundle_sha256=_digest(bundle))
                query["public_credit"] = encode_public_credit_header(
                    _credit_value(credit)
                )

                created = await http.post(
                    WEB, params=query, content=bundle, headers=zip_headers(http)
                )
                assert created.status_code == 201, created.text
                assert created.json()["submission"]["public_credit"] == credit
        finally:
            await kit.teardown()

    run(scenario())


# --- What must not work ------------------------------------------------------------------


def test_a_coldkey_nobody_linked_cannot_authorise_a_submission():
    """A signature proves control of a key, not that the key belongs to this account."""

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                account = await sign_in_by_email(kit, http, email="unlinked@example.com")
                await link(kit, http, HOTKEY)
                await grant_credits(kit, uuid.UUID(account["id"]), 1)
                bundle, _ = distinct_bundle("web-unlinked-coldkey")

                refused = await http.post(
                    WEB,
                    params=authorisation(bundle_sha256=_digest(bundle)),
                    content=bundle,
                    headers=zip_headers(http),
                )
                assert refused.status_code == 409
                assert refused.json()["reason_code"] == "WALLET_NOT_LINKED"

                # And nothing was charged for the refusal.
                assert (await http.get("/v1/me/credits")).json()[
                    "credits_available"
                ] == 1
        finally:
            await kit.teardown()

    run(scenario())


def test_a_hotkey_nobody_linked_cannot_receive_the_attribution():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                account = await sign_in_by_email(kit, http, email="nohotkey@example.com")
                await link_coldkey(kit, http)
                await grant_credits(kit, uuid.UUID(account["id"]), 1)
                bundle, _ = distinct_bundle("web-unlinked-hotkey", hotkey=OTHER_HOTKEY)

                refused = await http.post(
                    WEB,
                    params=authorisation(
                        bundle_sha256=_digest(bundle), hotkey=OTHER_HOTKEY
                    ),
                    content=bundle,
                    headers=zip_headers(http),
                )
                assert refused.status_code == 409
                assert refused.json()["reason_code"] == "HOTKEY_NOT_LINKED"
        finally:
            await kit.teardown()

    run(scenario())


def test_a_signature_from_another_wallet_is_refused():
    """The claimed coldkey is linked; the key that actually signed is not it."""

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                await ready_account(kit, http)
                bundle, _ = distinct_bundle("web-wrong-signer")

                refused = await http.post(
                    WEB,
                    params=authorisation(
                        bundle_sha256=_digest(bundle), signing_coldkey=OTHER_COLDKEY
                    ),
                    content=bundle,
                    headers=zip_headers(http),
                )
                assert refused.status_code == 401
                assert refused.json()["reason_code"] == "SIGNATURE_INVALID"
                assert (await http.get("/v1/me/credits")).json()[
                    "credits_available"
                ] == 1
        finally:
            await kit.teardown()

    run(scenario())


def test_an_archive_other_than_the_signed_one_is_refused():
    """The signature is bound to the bytes, so swapping the upload cannot work — and the
    refusal says which of the two the caller should look at."""

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                await ready_account(kit, http)
                signed, _ = distinct_bundle("web-signed")
                swapped, _ = distinct_bundle("web-swapped")

                refused = await http.post(
                    WEB,
                    params=authorisation(bundle_sha256=_digest(signed)),
                    content=swapped,
                    headers=zip_headers(http),
                )
                assert refused.status_code == 400
                assert refused.json()["reason_code"] == "BUNDLE_DIGEST_MISMATCH"
                assert refused.json()["bundle_sha256"] == _digest(swapped)

                # The other half of the same claim: an honest digest for the swapped archive
                # leaves a signature over a message the server does not rebuild.
                query = authorisation(bundle_sha256=_digest(signed))
                query["bundle_sha256"] = _digest(swapped)
                lied = await http.post(
                    WEB, params=query, content=swapped, headers=zip_headers(http)
                )
                assert lied.status_code == 401
                assert lied.json()["reason_code"] == "SIGNATURE_INVALID"
        finally:
            await kit.teardown()

    run(scenario())


def test_a_wallet_linked_to_another_account_cannot_spend_this_ones_credits():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as mine, await client(kit) as theirs:
                # The wallet belongs to the other account.
                await sign_in_by_email(kit, theirs, email="owner@example.com")
                await link_coldkey(kit, theirs, COLDKEY)

                account = await sign_in_by_email(kit, mine, email="thief@example.com")
                await link(kit, mine, HOTKEY)
                await grant_credits(kit, uuid.UUID(account["id"]), 1)
                bundle, _ = distinct_bundle("web-foreign-wallet")

                refused = await http_post(mine, bundle)
                assert refused.status_code == 409
                assert refused.json()["reason_code"] == "WALLET_NOT_LINKED"
        finally:
            await kit.teardown()

    run(scenario())


def test_an_account_with_no_credit_is_refused_before_it_uploads():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                await ready_account(kit, http, credits_=0)
                bundle, _ = distinct_bundle("web-broke")

                refused = await http.post(
                    WEB,
                    params=authorisation(bundle_sha256=_digest(bundle)),
                    content=bundle,
                    headers=zip_headers(http),
                )
                assert refused.status_code == 409
                assert refused.json()["reason_code"] == "INSUFFICIENT_CREDITS"
                assert refused.json()["credits_available"] == 0
        finally:
            await kit.teardown()

    run(scenario())


def test_a_cli_token_cannot_use_the_coldkey_path():
    """The one intake path a coldkey authorises must not be reachable from a file on a mining
    box. A bearer session has the three-call flow and no coldkey to sign with here."""

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as browser:
                await ready_account(kit, browser)
                token = await _cli_token(browser)

            async with await client(kit) as cli:
                bundle, _ = distinct_bundle("web-cli")
                refused = await cli.post(
                    WEB,
                    params=authorisation(bundle_sha256=_digest(bundle)),
                    content=bundle,
                    headers={
                        "Content-Type": "application/zip",
                        "Authorization": f"Bearer {token}",
                    },
                )
                assert refused.status_code == 403
                assert refused.json()["reason_code"] == "BROWSER_SESSION_REQUIRED"
        finally:
            await kit.teardown()

    run(scenario())


def test_a_cross_site_page_cannot_submit_with_an_ambient_cookie():
    async def scenario():
        kit = await harness(CORS_ALLOWED_ORIGINS=ORIGIN).setup()
        try:
            async with await client(kit) as http:
                await ready_account(kit, http)
                bundle, _ = distinct_bundle("web-cross-site")

                refused = await http.post(
                    WEB,
                    params=authorisation(bundle_sha256=_digest(bundle)),
                    content=bundle,
                    headers={
                        "Content-Type": "application/zip",
                        "Origin": "https://evil.example",
                    },
                )
                assert refused.status_code == 403
                assert refused.json()["reason_code"] == "CROSS_SITE_WRITE_REFUSED"
        finally:
            await kit.teardown()

    run(scenario())


def test_an_expired_or_over_long_authorisation_is_refused():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                await ready_account(kit, http)
                bundle, _ = distinct_bundle("web-expiry")

                stale = await http.post(
                    WEB,
                    params=authorisation(
                        bundle_sha256=_digest(bundle), expires_at=stamp(-1)
                    ),
                    content=bundle,
                    headers=zip_headers(http),
                )
                assert stale.status_code == 401
                assert stale.json()["reason_code"] == "AUTHORISATION_EXPIRED"

                # An expiry beyond INTENT_MINUTES would be a long-lived reusable authorisation
                # for this account's credits.
                forever = await http.post(
                    WEB,
                    params=authorisation(
                        bundle_sha256=_digest(bundle), expires_at=stamp(60 * 24)
                    ),
                    content=bundle,
                    headers=zip_headers(http),
                )
                assert forever.status_code == 400
                assert forever.json()["reason_code"] == "AUTHORISATION_WINDOW_TOO_LONG"
                assert forever.json()["max_minutes"] == 30
        finally:
            await kit.teardown()

    run(scenario())


def test_one_spelling_of_the_expiry_is_accepted():
    """The message is verified by rebuilding it, so a second accepted spelling of the same
    instant would be a second message and a signature that fails for no visible reason."""

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                await ready_account(kit, http)
                bundle, _ = distinct_bundle("web-spelling")
                query = authorisation(bundle_sha256=_digest(bundle))
                query["expires_at"] = query["expires_at"].replace("Z", "+00:00")[:20]

                refused = await http.post(
                    WEB, params=query, content=bundle, headers=zip_headers(http)
                )
                assert refused.status_code == 400
        finally:
            await kit.teardown()

    run(scenario())


def test_a_retry_returns_the_original_submission_and_charges_once():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                await ready_account(kit, http, credits_=2)
                bundle, _ = distinct_bundle("web-retry")
                query = authorisation(bundle_sha256=_digest(bundle))

                first = await http.post(
                    WEB, params=query, content=bundle, headers=zip_headers(http)
                )
                assert first.status_code == 201, first.text

                # The same key again — a client that lost the response, retrying.
                again = await http.post(
                    WEB, params=query, content=bundle, headers=zip_headers(http)
                )
                assert again.status_code == 200, again.text
                assert (
                    again.json()["submission"]["id"]
                    == first.json()["submission"]["id"]
                )

                # One debit, and the second credit is still there.
                assert (await http.get("/v1/me/credits")).json()[
                    "credits_available"
                ] == 1
                ledger = (await http.get("/v1/me/credits/ledger")).json()["items"]
                assert len([item for item in ledger if item["kind"] == "SPEND"]) == 1
        finally:
            await kit.teardown()

    run(scenario())


def test_the_same_proof_cannot_be_submitted_twice_under_a_new_key():
    """`submissions.proof_digest` is globally unique: one proof is payable at most once, and a
    fresh idempotency key does not buy a second attempt at the same bytes."""

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                await ready_account(kit, http, credits_=2)
                bundle, _ = distinct_bundle("web-duplicate")

                first = await http.post(
                    WEB,
                    params=authorisation(bundle_sha256=_digest(bundle)),
                    content=bundle,
                    headers=zip_headers(http),
                )
                assert first.status_code == 201, first.text

                second = await http.post(
                    WEB,
                    params=authorisation(bundle_sha256=_digest(bundle)),
                    content=bundle,
                    headers=zip_headers(http),
                )
                assert second.status_code == 409
                assert second.json()["reason_code"] == "DUPLICATE_PROOF"
                # The refused attempt released its hold rather than keeping it.
                assert (await http.get("/v1/me/credits")).json()["held_rao"] == 0
        finally:
            await kit.teardown()

    run(scenario())


def test_a_bundle_for_another_hotkey_is_refused_by_the_scanner():
    """The manifest names the submitting hotkey, and the admission check binds the archive to
    the key the request claims."""

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                await ready_account(kit, http)
                bundle, _ = distinct_bundle("web-other-manifest", hotkey=OTHER_HOTKEY)

                refused = await http.post(
                    WEB,
                    params=authorisation(bundle_sha256=_digest(bundle)),
                    content=bundle,
                    headers=zip_headers(http),
                )
                assert refused.status_code in (400, 422)
                assert refused.json()["reason_code"] != "SIGNATURE_INVALID"
        finally:
            await kit.teardown()

    run(scenario())


def test_a_paused_validator_refuses_before_reading_the_body():
    async def scenario():
        kit = await harness(SUBMISSIONS_PAUSED="true").setup()
        try:
            async with await client(kit) as http:
                await ready_account(kit, http)
                bundle, _ = distinct_bundle("web-paused")

                refused = await http.post(
                    WEB,
                    params=authorisation(bundle_sha256=_digest(bundle)),
                    content=bundle,
                    headers=zip_headers(http),
                )
                assert refused.status_code == 503
                assert refused.json()["reason_code"] == "SUBMISSIONS_PAUSED"
        finally:
            await kit.teardown()

    run(scenario())


def test_an_unknown_task_is_absent_rather_than_a_signature_failure():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                await ready_account(kit, http)
                bundle, _ = distinct_bundle("web-unknown-task")

                refused = await http.post(
                    WEB,
                    params=authorisation(
                        bundle_sha256=_digest(bundle), task_id="no-such-task"
                    ),
                    content=bundle,
                    headers=zip_headers(http),
                )
                assert refused.status_code == 404
                assert refused.json()["reason_code"] == "TASK_NOT_ALLOWED"
        finally:
            await kit.teardown()

    run(scenario())


def test_signing_in_is_required():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                bundle, _ = distinct_bundle("web-anonymous")
                refused = await http.post(
                    WEB,
                    params=authorisation(bundle_sha256=_digest(bundle)),
                    content=bundle,
                    headers=zip_headers(http),
                )
                assert refused.status_code == 401
                assert refused.json()["reason_code"] == "NOT_AUTHENTICATED"
        finally:
            await kit.teardown()

    run(scenario())


# --- Helpers ------------------------------------------------------------------------------


def _digest(raw: bytes) -> str:
    from verifier.hashing import sha256_bytes

    return sha256_bytes(raw)


def _credit_value(payload: dict):
    from conjectures_subnet.attribution import public_credit

    return public_credit(payload["name"], payload["url"], payload["orcid"])


async def http_post(http, bundle: bytes, **overrides):
    return await http.post(
        WEB,
        params=authorisation(bundle_sha256=_digest(bundle), **overrides),
        content=bundle,
        headers=zip_headers(http),
    )


async def _cli_token(http) -> str:
    """Mint a bearer session for the account's linked hotkey, the way the CLI does."""
    challenge = await http.post("/v1/auth/cli/challenge", json={"address": HOTKEY})
    assert challenge.status_code == 200, challenge.text
    body = challenge.json()
    verified = await http.post(
        "/v1/auth/cli/verify",
        json={
            "address": HOTKEY,
            "nonce": body["nonce"],
            "signature": sign(HOTKEY, body["message"]),
        },
    )
    assert verified.status_code == 200, verified.text
    return verified.json()["access_token"]
