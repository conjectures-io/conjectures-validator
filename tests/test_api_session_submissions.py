"""`POST /v1/submissions/session`: a credit, a bundle, and a signed-in browser. No key at all.

The fourth intake path, and the one a mathematician can actually reach: an account opened with
an email address holds no Bittensor key, so every other route ends in a signature it cannot
produce.

The tests that matter most are in the last two sections. The first proves the path claims no
identity it has not authenticated — without that, anyone could publish a solved conjecture under
somebody else's address. The second proves a keyless submission that wins is *held* rather than
misdirected, which is the property that made it safe to accept one at all.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

pytest.importorskip("fastapi", reason="submission API tests need the service extra")
pytest.importorskip("sqlalchemy", reason="submission API tests need the db extra")

from conftest_api import (  # noqa: E402
    MINER_COLDKEY,
    TASK_DIGEST,
    TASK_ID,
    distinct_bundle,
    harness,
    postgres_dsn,
)
from conjectures_subnet.db.models import Submission  # noqa: E402
from test_api_accounts import (  # noqa: E402
    client,
    grant_credits,
    same_origin,
    sign_in_by_email,
)

pytestmark = pytest.mark.skipif(
    postgres_dsn() is None,
    reason="no database: run `docker compose -f docker-compose.pytest-db.yml up -d`",
)

SESSION = "/v1/submissions/session"
EMAIL = "mathematician@example.com"


def run(coroutine):
    return asyncio.run(coroutine)


def _digest(raw: bytes) -> str:
    from verifier.hashing import sha256_bytes

    return sha256_bytes(raw)


def zip_headers(http) -> dict[str, str]:
    return {**same_origin(http), "Content-Type": "application/zip"}


def params(*, bundle_sha256: str, idempotency_key: str | None = None) -> dict[str, str]:
    """Everything this path takes. Note what is absent: no hotkey, coldkey, expiry or signature."""
    return {
        "task_id": TASK_ID,
        "task_bundle_sha256": TASK_DIGEST,
        "bundle_sha256": bundle_sha256,
        "idempotency_key": idempotency_key or str(uuid.uuid4()),
    }


async def keyless_account(kit, http, *, credits_: int = 1, email: str = EMAIL):
    """Signed in by email, holding no hotkey and no coldkey. The person this path exists for."""
    account = await sign_in_by_email(kit, http, email=email)
    if credits_:
        await grant_credits(kit, uuid.UUID(account["id"]), credits_)
    return account


# --- The path working ----------------------------------------------------------------------


def test_an_account_with_no_key_submits_and_the_row_names_none():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                await keyless_account(kit, http)
                bundle, proof_digest = distinct_bundle("session-0001", coldkey=None)

                created = await http.post(
                    SESSION,
                    params=params(bundle_sha256=_digest(bundle)),
                    content=bundle,
                    headers=zip_headers(http),
                )
                assert created.status_code == 201, created.text
                submission = created.json()["submission"]

                # Null, not a placeholder. An address nobody controls would be published as the
                # solver of this result.
                assert submission["signer_coldkey"] is None
                assert submission["proof_sha256"] == proof_digest
                assert submission["verification_status"] == "UNVERIFIED"
                assert submission["funding"]["source"] == "credit"
                assert submission["funding"]["payment_reference"] is None
                # The credit was spent exactly once.
                assert created.json()["credits"]["credits_available"] == 0
        finally:
            await kit.teardown()

    run(scenario())


def test_the_submission_is_visible_to_its_owner_and_to_the_public_without_a_hotkey():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                await keyless_account(kit, http)
                bundle, _ = distinct_bundle("session-visible", coldkey=None)
                created = await http.post(
                    SESSION,
                    params=params(bundle_sha256=_digest(bundle)),
                    content=bundle,
                    headers=zip_headers(http),
                )
                assert created.status_code == 201, created.text
                submission_id = created.json()["submission"]["id"]

                mine = await http.get("/v1/me/submissions")
                assert mine.status_code == 200, mine.text
                assert [item["signer_coldkey"] for item in mine.json()["items"]] == [None]

                detail = await http.get(f"/v1/me/submissions/{submission_id}")
                assert detail.status_code == 200, detail.text
                assert detail.json()["signer_coldkey"] is None

            # And the public feed renders it rather than failing to serialise a null.
            async with await client(kit) as anon:
                feed = await anon.get("/v1/results/submissions")
                assert feed.status_code == 200, feed.text
                # `solver_coldkey`, not `signer_coldkey`: the public feed names a solver
                # and the account panel names the key that signed. Both are null here.
                assert any(item["solver_coldkey"] is None for item in feed.json()["items"])
        finally:
            await kit.teardown()

    run(scenario())


def test_credits_are_still_required():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                await keyless_account(kit, http, credits_=0)
                bundle, _ = distinct_bundle("session-broke", coldkey=None)
                refused = await http.post(
                    SESSION,
                    params=params(bundle_sha256=_digest(bundle)),
                    content=bundle,
                    headers=zip_headers(http),
                )
                assert refused.status_code == 409, refused.text
                assert refused.json()["reason_code"] == "INSUFFICIENT_CREDITS"
        finally:
            await kit.teardown()

    run(scenario())


# --- Claiming no identity ------------------------------------------------------------------


def test_a_bundle_naming_a_miner_is_refused_rather_than_ignored():
    """The security property this path turns on.

    Nothing here authenticated an address, so a manifest naming one is an unverified claim of
    authorship. `ResultRow.hotkey` is published and credits a result to its solver, so admitting
    the claim would let anyone attribute a solved conjecture to somebody else's hotkey. Ignoring
    the field would be just as bad as trusting it — the refusal is what makes "no key" mean it.
    """

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                await keyless_account(kit, http)
                # A perfectly valid bundle, naming a real hotkey nobody here proved.
                bundle, _ = distinct_bundle("session-claim", coldkey=MINER_COLDKEY)
                refused = await http.post(
                    SESSION,
                    params=params(bundle_sha256=_digest(bundle)),
                    content=bundle,
                    headers=zip_headers(http),
                )
                # 422, the status the verifier's manifest refusals already map to.
                assert refused.status_code == 422, refused.text
                assert refused.json()["reason_code"] == "BUNDLE_MANIFEST_INVALID"
                assert "nothing has proved control" in refused.json()["detail"]

                # And nothing was charged for the refusal.
                credits_ = await http.get("/v1/me/credits")
                assert credits_.json()["credits_available"] == 1
        finally:
            await kit.teardown()

    run(scenario())


def test_a_cli_token_cannot_use_this_path():
    """`CookieWriterDep`. A bearer token is minted by a hotkey, and an account with a hotkey
    already has the three-call flow — so nothing is lost, and a credential read off a mining box
    cannot spend credits through the path that requires no key."""
    from test_api_cli_sessions import bearer, linked_account_with_cli_token

    async def scenario():
        kit = await harness().setup()
        try:
            _, token = await linked_account_with_cli_token(kit)
            bundle, _ = distinct_bundle("session-cli", coldkey=None)
            async with await client(kit) as cli:
                refused = await cli.post(
                    SESSION,
                    params=params(bundle_sha256=_digest(bundle)),
                    content=bundle,
                    headers={
                        **bearer(token),
                        "Content-Type": "application/zip",
                        "Sec-Fetch-Site": "same-origin",
                    },
                )
                assert refused.status_code == 403, refused.text
                assert refused.json()["reason_code"] == "BROWSER_SESSION_REQUIRED"
        finally:
            await kit.teardown()

    run(scenario())


# --- Idempotency, which the schema stopped enforcing for free --------------------------------


def test_a_replayed_key_answers_the_original_submission_without_charging_again():
    """`submissions_idempotency_unique` is (hotkey, idempotency_key), and PostgreSQL treats NULLs
    as distinct — so once `hotkey` can be null it stops constraining these rows entirely. V032
    adds a partial index scoped to the account, and this is what proves the lookup agrees with
    it: without both, a retry would silently buy a second attempt."""

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                await keyless_account(kit, http, credits_=2)
                bundle, _ = distinct_bundle("session-replay", coldkey=None)
                query = params(bundle_sha256=_digest(bundle))

                first = await http.post(
                    SESSION, params=query, content=bundle, headers=zip_headers(http)
                )
                assert first.status_code == 201, first.text

                again = await http.post(
                    SESSION, params=query, content=bundle, headers=zip_headers(http)
                )
                assert again.status_code == 200, again.text
                assert (
                    again.json()["submission"]["id"] == first.json()["submission"]["id"]
                )

                # One credit spent across both calls, not two.
                credits_ = await http.get("/v1/me/credits")
                assert credits_.json()["credits_available"] == 1
        finally:
            await kit.teardown()

    run(scenario())


def test_two_accounts_may_choose_the_same_idempotency_key():
    """The index is scoped to the account on purpose. A key is client-generated, so a global
    one would let one account's UUID refuse another's legitimate submission."""

    async def scenario():
        kit = await harness().setup()
        try:
            shared = str(uuid.uuid4())
            for index in range(2):
                async with await client(kit) as http:
                    await keyless_account(kit, http, email=f"user{index}@example.com")
                    bundle, _ = distinct_bundle(f"session-shared-{index}", coldkey=None)
                    created = await http.post(
                        SESSION,
                        params=params(
                            bundle_sha256=_digest(bundle), idempotency_key=shared
                        ),
                        content=bundle,
                        headers=zip_headers(http),
                    )
                    assert created.status_code == 201, created.text
        finally:
            await kit.teardown()

    run(scenario())


# --- What happens if one wins ----------------------------------------------------------------


def test_a_keyless_submission_is_held_by_the_payout_queue_rather_than_misdirected():
    """The property that made accepting a keyless submission safe.

    `payout_notifier` resolves its destination through
    `coalesce(Account.payout_coldkey, Submission.signer_coldkey, Submission.payment_sender)`
    and filters on the result being
    non-null. A keyless winner therefore resolves to nothing and is **skipped** — it is not paid
    to a stand-in, and it does not crash the queue. It simply waits.
    """
    from sqlalchemy import select

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                await keyless_account(kit, http)
                bundle, _ = distinct_bundle("session-award", coldkey=None)
                created = await http.post(
                    SESSION,
                    params=params(bundle_sha256=_digest(bundle)),
                    content=bundle,
                    headers=zip_headers(http),
                )
                assert created.status_code == 201, created.text
                submission_id = uuid.UUID(created.json()["submission"]["id"])

            async with kit.session() as session:
                row = (
                    await session.execute(
                        select(Submission).where(Submission.id == submission_id)
                    )
                ).scalar_one()
                # Nothing to pay to, and nothing pretending otherwise.
                assert row.hotkey is None
                assert row.signer_signature is None
                assert row.signer_coldkey is None
                # But it is a real, owned, credit-funded submission.
                assert row.account_id is not None
                assert row.credit_ledger_id is not None
        finally:
            await kit.teardown()

    run(scenario())


def test_setting_a_payout_coldkey_is_what_makes_a_keyless_submission_payable():
    """And the transition needs no migration and no new state: the coalesce simply resolves.

    This is why "awarded, awaiting a payout target" did not have to be built. The submission
    sits in the queue's blind spot until the account has somewhere to be paid, and then it
    does not.

    One coldkey since V035, where this was a coldkey/hotkey pair. The destination expression
    below mirrors `payout_notifier`'s, and there is no second coalesce over hotkey columns to
    keep in step with it any more.
    """
    from sqlalchemy import func, select

    from conjectures_subnet.db.models import Account

    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                account = await keyless_account(kit, http)
                bundle, _ = distinct_bundle("session-payable", coldkey=None)
                created = await http.post(
                    SESSION,
                    params=params(bundle_sha256=_digest(bundle)),
                    content=bundle,
                    headers=zip_headers(http),
                )
                assert created.status_code == 201, created.text

            account_id = uuid.UUID(account["id"])
            destination = func.coalesce(
                Account.payout_coldkey,
                Submission.signer_coldkey,
                Submission.payment_sender,
            )

            async with kit.session() as session:
                before = (
                    await session.execute(
                        select(destination)
                        .select_from(Submission)
                        .outerjoin(Account, Account.id == Submission.account_id)
                        .where(Submission.account_id == account_id)
                    )
                ).scalar_one()
                assert before is None  # skipped by the payout queue

                # An operator or the owner sets a payout destination later. Written
                # directly here rather than through `PUT /v1/me/coldkeys/payout` so the test
                # is about the queue's blind spot rather than about that endpoint — which
                # needs no linked key and would work too.
                owner = await session.get(Account, account_id)
                owner.payout_coldkey = MINER_COLDKEY
                await session.commit()

            async with kit.session() as session:
                after = (
                    await session.execute(
                        select(destination)
                        .select_from(Submission)
                        .outerjoin(Account, Account.id == Submission.account_id)
                        .where(Submission.account_id == account_id)
                    )
                ).scalar_one()
                assert after == MINER_COLDKEY  # the next poll will pick it up
        finally:
            await kit.teardown()

    run(scenario())
