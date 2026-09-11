"""The hotkey retirement rules, and the difference between exempting history and freezing it.

V035 closed the hotkey columns to new rows with three NOT VALID CHECKs, on the belief that NOT
VALID means "existing rows are exempt". It does not: it exempts them from validation when the
constraint is created, and from nothing afterwards. A CHECK is evaluated against every new row
version, and an UPDATE produces one whether or not it touches the constrained columns — so the
historical rows the migration set out to keep could no longer accept a reward transition, a
re-review or a verification lease. Two REWARDED verdicts could not be written because of it.

V038 moved the rules to BEFORE INSERT triggers, which is where a rule about intake belongs.
Every test here is a property of that move: the door stays shut, and history stays writable.

Deliberately raw SQL. The point of the retirement is that no Python path writes these columns
any more, so there is no store function to go through — and a test that went through one could
not construct the historical rows this is about.

Skipped unless a server is reachable. Start the fixed test stack:

    docker compose -f docker-compose.pytest-db.yml up -d
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid

import pytest
from conftest import DATABASE_SKIP_REASON, postgres_dsn
from sqlalchemy import text

from conjectures_subnet.db.engine import create_async_db_engine
from conjectures_subnet.db.models import Base

pytestmark = pytest.mark.skipif(postgres_dsn() is None, reason=DATABASE_SKIP_REASON)

HOTKEY = "5FqeAt3Crmw2SyHpP1ugWrttdWhNUPgYBXKxQggt6CCVGFQ9"
COLDKEY = "5DU7LcM5nyya6Le7RWbpiMofqcZSRg3H6d25pWAYD6HoGH1B"


def run(coroutine):
    return asyncio.run(coroutine)


async def _fresh_engine():
    engine = create_async_db_engine(postgres_dsn())
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    return engine


# One extrinsic-path submission, written as the pre-V035 code wrote it: authorised by a hotkey
# and the signature that proved it. `history` decides whether the insert trigger stands down,
# which is the only way such a row can exist at all now — and is what a restored production
# dump amounts to.
async def _insert_submission(connection, *, history: bool, **overrides) -> uuid.UUID:
    content = f"theorem t{uuid.uuid4()} : True := trivial".encode()
    digest = hashlib.sha256(content).digest()
    await connection.execute(
        text(
            "INSERT INTO proofs (digest, content, byte_length) "
            "VALUES (:digest, :content, :byte_length)"
        ),
        {"digest": digest, "content": content, "byte_length": len(content)},
    )
    # Any 32 bytes will do for these two: nothing ties them to the proof, and a test that
    # recomputed them would be asserting the fixture rather than the trigger.
    request_digest = uuid.uuid4().bytes + uuid.uuid4().bytes
    row = {
        "id": uuid.uuid4(),
        "hotkey": HOTKEY,
        "hotkey_signature": bytes(64),
        "signer_coldkey": None,
        "signer_signature": None,
        "idempotency_key": uuid.uuid4(),
        "request_digest": request_digest,
        "task_id": "fc-e923379e-fixture-formalized-v1",
        "task_bundle_sha256": request_digest,
        "problem_id": f"fc-e923379e-{uuid.uuid4()}-problem",
        "proof_digest": digest,
        "payment_reference": f"ref-{uuid.uuid4()}",
        "payment_sender": COLDKEY,
    }
    row.update(overrides)
    row["reward_target_id"] = row["problem_id"]
    statement = text(
        "INSERT INTO submissions ("
        "  id, hotkey, hotkey_signature, signer_coldkey, signer_signature,"
        "  idempotency_key, request_digest, task_id, task_bundle_sha256, problem_id,"
        "  reward_target_id, task_mode, proof_digest, payment_reference, payment_sender,"
        "  payment_amount_rao, payment_block, verification_status, manual_review_status,"
        "  reward_status, manual_review_required, review_policy_version, bounty_amount_rao,"
        "  bounty_policy_version"
        ") VALUES ("
        "  :id, :hotkey, :hotkey_signature, :signer_coldkey, :signer_signature,"
        "  :idempotency_key, :request_digest, :task_id, :task_bundle_sha256, :problem_id,"
        "  :reward_target_id, 'formalized', :proof_digest, :payment_reference, :payment_sender,"
        "  1000, 42, 'VERIFIED', 'APPROVED', 'ELIGIBLE', true, 'v2', 1000, 'v2'"
        ")"
    )
    if history:
        await connection.execute(
            text("ALTER TABLE submissions DISABLE TRIGGER submissions_reject_hotkey")
        )
    try:
        await connection.execute(statement, row)
    finally:
        if history:
            await connection.execute(
                text("ALTER TABLE submissions ENABLE TRIGGER submissions_reject_hotkey")
            )
    return row["id"]


def test_a_new_submission_may_not_name_a_hotkey():
    async def scenario():
        engine = await _fresh_engine()
        try:
            async with engine.begin() as connection:
                with pytest.raises(Exception) as caught:
                    await _insert_submission(connection, history=False)
            assert "submission may not name a hotkey" in str(caught.value)
        finally:
            await engine.dispose()

    run(scenario())


def test_a_new_submission_may_not_carry_a_legacy_signature_without_the_key():
    """The other half of the closed door, and the half a rule about `hotkey` alone would miss.

    The V028 web rows put a coldkey signature in `hotkey_signature`, so a row could name no
    hotkey and still write to the retired column. `signer_signature` is where those bytes go.
    """

    async def scenario():
        engine = await _fresh_engine()
        try:
            async with engine.begin() as connection:
                with pytest.raises(Exception) as caught:
                    await _insert_submission(connection, history=False, hotkey=None)
            assert "submission may not name a hotkey" in str(caught.value)
        finally:
            await engine.dispose()

    run(scenario())


def test_a_historical_hotkey_submission_still_accepts_a_status_change():
    """The regression V035 shipped. A reward transition on a legacy row must be writable.

    This is the test that fails against a NOT VALID CHECK and passes against an insert trigger,
    which is the entire behavioural difference between the two.
    """

    async def scenario():
        engine = await _fresh_engine()
        try:
            async with engine.begin() as connection:
                submission_id = await _insert_submission(connection, history=True)
                await connection.execute(
                    text("UPDATE submissions SET reward_status = 'REWARDED' WHERE id = :id"),
                    {"id": submission_id},
                )
                reward_status = (
                    await connection.execute(
                        text("SELECT reward_status FROM submissions WHERE id = :id"),
                        {"id": submission_id},
                    )
                ).scalar_one()
            assert reward_status == "REWARDED"
        finally:
            await engine.dispose()

    run(scenario())


def test_a_historical_hotkey_submission_keeps_its_hotkey_through_the_update():
    """History is retained, not laundered.

    The rows are updatable again because the rule moved, not because the hotkey was cleared to
    satisfy it. `submissions.hotkey` is still the published solver identity for these rows and
    is still the only record of what past payouts were owed against.
    """

    async def scenario():
        engine = await _fresh_engine()
        try:
            async with engine.begin() as connection:
                submission_id = await _insert_submission(connection, history=True)
                await connection.execute(
                    text("UPDATE submissions SET reward_status = 'REWARDED' WHERE id = :id"),
                    {"id": submission_id},
                )
                hotkey = (
                    await connection.execute(
                        text("SELECT hotkey FROM submissions WHERE id = :id"),
                        {"id": submission_id},
                    )
                ).scalar_one()
            assert hotkey == HOTKEY
        finally:
            await engine.dispose()

    run(scenario())


def test_a_new_intent_may_not_name_a_hotkey():
    async def scenario():
        engine = await _fresh_engine()
        try:
            async with engine.begin() as connection:
                account_id = uuid.uuid4()
                await connection.execute(
                    text(
                        "INSERT INTO accounts (id, email, roles) "
                        "VALUES (:id, :email, ARRAY['MINER'])"
                    ),
                    {"id": account_id, "email": f"{uuid.uuid4()}@example.test"},
                )
                with pytest.raises(Exception) as caught:
                    await connection.execute(
                        text(
                            "INSERT INTO submission_intents ("
                            "  id, account_id, hotkey, task_id, task_bundle_sha256,"
                            "  credit_price_rao, expires_at"
                            ") VALUES ("
                            "  :id, :account_id, :hotkey, 'fc-task',"
                            "  :digest, 1, now() + interval '1 hour'"
                            ")"
                        ),
                        {
                            "id": uuid.uuid4(),
                            "account_id": account_id,
                            "hotkey": HOTKEY,
                            "digest": bytes(32),
                        },
                    )
            assert "submission intent may not name a hotkey" in str(caught.value)
        finally:
            await engine.dispose()

    run(scenario())


def test_a_retired_challenge_kind_is_refused_while_a_consumed_one_stays_writable():
    """Both properties in one test, because they are the same property seen twice.

    The retired kinds stay in the enum so that consumed history remains readable — and a
    challenge is UPDATEd when it is consumed, so a CHECK on `kind` would have frozen any
    HOTKEY_LINK challenge that was still open when V035 landed.
    """

    async def scenario():
        engine = await _fresh_engine()
        try:
            # Its own transaction. The refused insert below aborts the one it runs in, so an
            # account created alongside it would not survive to be pointed at.
            account_id = uuid.uuid4()
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO accounts (id, email, roles) "
                        "VALUES (:id, :email, ARRAY['MINER'])"
                    ),
                    {"id": account_id, "email": f"{uuid.uuid4()}@example.test"},
                )

            async with engine.begin() as connection:
                minted = text(
                    "INSERT INTO login_challenges ("
                    "  id, kind, account_id, ss58, message, secret_sha256, expires_at"
                    ") VALUES ("
                    "  :id, :kind, :account_id, :ss58, 'sign this', :secret,"
                    "  now() + interval '1 hour'"
                    ")"
                )
                arguments = {
                    "id": uuid.uuid4(),
                    "kind": "HOTKEY_LINK",
                    "account_id": account_id,
                    "ss58": HOTKEY,
                    "secret": bytes(32),
                }

                with pytest.raises(Exception) as caught:
                    await connection.execute(minted, arguments)
                assert "login challenge kind is retired" in str(caught.value)

            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "ALTER TABLE login_challenges "
                        "DISABLE TRIGGER login_challenges_reject_retired_kind"
                    )
                )
                await connection.execute(minted, arguments)
                await connection.execute(
                    text(
                        "ALTER TABLE login_challenges "
                        "ENABLE TRIGGER login_challenges_reject_retired_kind"
                    )
                )
                await connection.execute(
                    text("UPDATE login_challenges SET consumed_at = now() WHERE id = :id"),
                    {"id": arguments["id"]},
                )
                consumed = (
                    await connection.execute(
                        text("SELECT consumed_at FROM login_challenges WHERE id = :id"),
                        {"id": arguments["id"]},
                    )
                ).scalar_one()
            assert consumed is not None
        finally:
            await engine.dispose()

    run(scenario())
