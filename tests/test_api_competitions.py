"""The competition surface's wiring: a second database, or none at all.

Two databases in one process is a shape worth testing directly, because both of its failure
modes are quiet. A deployment that was never given a competition database must refuse that
surface rather than fall through to the proofs engine and write competition rows into it; and
a deployment that *was* given one, and cannot reach it, must leave rotation rather than serve
half its surface as 503s while reporting ready.

The routes themselves are not here yet. What is here is everything they will stand on.
"""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest

pytest.importorskip("fastapi", reason="submission API tests need the service extra")
pytest.importorskip("sqlalchemy", reason="submission API tests need the db extra")
pytest.importorskip("httpx", reason="submission API tests need the service extra")
pytest.importorskip("psycopg", reason="submission API tests need the db extra")

from conftest import COMPETITION_SKIP_REASON, competition_dsn
from conftest_api import (
    MINER_COLDKEY,
    OTHER_MINER_COLDKEY,
    build_settings,
    harness,
    postgres_dsn,
)
from test_api_accounts import client, same_origin, sign_in_by_email
from test_api_auth import production_env

from conjectures_subnet.db import create_async_db_engine
from submission_api import competition_sig
from submission_api.app import competition_url
from submission_api.dependencies import get_competition_session
from submission_api.errors import ServiceUnavailable
from submission_api.settings import Settings, SettingsError

pytestmark = pytest.mark.skipif(postgres_dsn() is None, reason="no database")


def run(coro):
    return asyncio.run(coro)


async def _client(kit):
    from httpx import ASGITransport, AsyncClient

    return AsyncClient(
        transport=ASGITransport(app=kit.app), base_url="http://validator.test"
    )


# ── configuration ──────────────────────────────────────────────────────────


def test_production_refuses_the_competition_surface_without_an_explicit_url():
    # The fallback assembles a URL from POSTGRES_*, which is a guess. A guess about which
    # database to write to is not one to ship, so production says so at startup. Built from
    # test_api_auth.production_env so this asserts the competition refusal specifically,
    # rather than whichever production requirement happens to be checked first.
    with pytest.raises(SettingsError, match="COMPETITION_DATABASE_URL"):
        Settings.from_env(production_env(COMPETITIONS_ENABLED="1"))


def test_production_accepts_the_competition_surface_with_an_explicit_url():
    settings = Settings.from_env(
        production_env(
            COMPETITIONS_ENABLED="1",
            COMPETITION_DATABASE_URL="postgresql+psycopg://u:p@db:5432/competition",
        )
    )
    assert settings.competitions_enabled
    assert settings.competition_database_url.endswith("/competition")


def test_development_may_leave_the_competition_url_to_the_resolver():
    settings = build_settings(COMPETITIONS_ENABLED="1")
    assert settings.competitions_enabled
    assert settings.competition_database_url == ""


def test_competitions_are_off_unless_asked_for():
    assert build_settings().competitions_enabled is False


def test_the_competition_database_may_not_be_the_proofs_database():
    """The guard that keeps the split true at runtime rather than by convention.

    The resolvers default to different names, so this only fires when someone has pointed
    them at the same place by hand -- which would put Alembic's schema into Flyway's database
    and would otherwise be discovered by a migration, not by a startup check.
    """
    shared = postgres_dsn()
    assert shared is not None
    settings = build_settings(
        COMPETITIONS_ENABLED="1",
        DATABASE_URL=shared,
        COMPETITION_DATABASE_URL=shared,
    )
    with pytest.raises(SettingsError, match="must not be the proofs database"):
        competition_url(settings)


def test_a_distinct_competition_url_is_returned_unchanged():
    settings = build_settings(
        COMPETITIONS_ENABLED="1",
        DATABASE_URL="postgresql+psycopg://u:p@db:5432/conjectures",
        COMPETITION_DATABASE_URL="postgresql+psycopg://u:p@db:5432/conjectures_competition",
    )
    assert competition_url(settings).endswith("/conjectures_competition")


# ── the dependency ─────────────────────────────────────────────────────────


def test_an_unconfigured_competition_session_refuses_rather_than_falling_back():
    """The important half: it raises, rather than handing back the proofs session.

    A fallback here would be silent and would write competition rows into the database
    Flyway owns, so the absence is an outage of this surface and not a reason to improvise.
    """

    async def scenario():
        kit = await harness().setup()
        try:
            assert kit.services.competition_sessions is None
            assert kit.services.competition_engine is None
            agen = get_competition_session(kit.services)
            with pytest.raises(ServiceUnavailable) as raised:
                await agen.__anext__()
            assert raised.value.reason_code == "COMPETITIONS_UNAVAILABLE"
        finally:
            await kit.teardown()

    run(scenario())


# ── readiness ──────────────────────────────────────────────────────────────


def test_readiness_reports_no_competition_database_as_absent_not_broken():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await _client(kit) as client:
                ready = await client.get("/readyz")
            assert ready.status_code == 200
            assert ready.json()["competition_database"] is None
        finally:
            await kit.teardown()

    run(scenario())


@pytest.mark.skipif(competition_dsn() is None, reason=COMPETITION_SKIP_REASON)
def test_readiness_reports_a_reachable_competition_database():
    async def scenario():
        engine = create_async_db_engine(competition_dsn())
        kit = await harness(competition_engine=engine).setup()
        try:
            async with await _client(kit) as client:
                ready = await client.get("/readyz")
            assert ready.status_code == 200
            assert ready.json()["competition_database"] is True
        finally:
            await kit.teardown()
            await engine.dispose()

    run(scenario())


def test_an_unreachable_competition_database_takes_the_replica_out_of_rotation():
    """Configured and unreachable is a 503, not a degraded 200.

    One process serves one port on one origin, so a replica that cannot answer
    `/v1/competitions/*` should leave rotation rather than serve that half as errors while
    telling the orchestrator it is fine.
    """

    async def scenario():
        # A URL that parses and resolves to nothing listening. Refused synchronously, so the
        # test does not wait on a connect timeout.
        engine = create_async_db_engine(
            "postgresql+psycopg://nobody:nothing@127.0.0.1:1/absent"
        )
        kit = await harness(competition_engine=engine).setup()
        try:
            async with await _client(kit) as client:
                ready = await client.get("/readyz")
            assert ready.status_code == 503
            # `extra` merges into the problem document at the top level; `detail` is the
            # human-readable string. See ApiError.problem.
            body = ready.json()
            assert body["competition_database"] is False
            # The proofs database is fine; only the competition half failed.
            assert body["database"] is True
        finally:
            await kit.teardown()
            await engine.dispose()

    run(scenario())


# ── the routes ─────────────────────────────────────────────────────────────

RUST = b"fn parse(input: &[u8]) {}"
LEAN = b"theorem holds : True := trivial"

competition_only = pytest.mark.skipif(
    competition_dsn() is None, reason=COMPETITION_SKIP_REASON
)


def _keypair(uri: str = "//Alice"):
    from bittensor.sp_core import Keypair

    return Keypair.create_from_uri(uri)


async def _fresh(engine, *, hotkey: str | None = None, coldkey: str = "5Cold", slots: int = 1):
    """An empty competition database, optionally with `slots` registrations for `hotkey`."""
    from sqlalchemy import text

    async with engine.begin() as conn:
        # rate_limit_windows too: the counters are per hotkey per wall-clock minute and
        # outlive a test, so leaving them would make one test's submissions count against
        # the next one's limit.
        for table in (
            "entitlement_claims",
            "submissions",
            "registrations",
            "rate_limit_windows",
        ):
            await conn.execute(text(f"DELETE FROM {table}"))
        for uid in range(slots):
            if hotkey is None:
                break
            await conn.execute(
                text(
                    "INSERT INTO registrations (uid, ss58_hot, ss58_cold, block, block_date) "
                    "VALUES (:uid, :hot, :cold, 100, now())"
                ),
                {"uid": uid, "hot": hotkey, "cold": coldkey},
            )


def _signed(keypair, *, digest: str, competition: str = "miniz-oxide", timestamp=None):
    stamp = int(time.time()) if timestamp is None else timestamp
    message = competition_sig.submit_message(
        competition=competition, digest=digest, hotkey=keypair.ss58_address, timestamp=stamp
    )
    return {
        "X-Conjectures-Hotkey": keypair.ss58_address,
        "X-Conjectures-Timestamp": str(stamp),
        "X-Conjectures-Signature": keypair.sign(message.encode()).hex(),
    }


def _files(rust: bytes = RUST, lean: bytes = LEAN):
    return {"parse.rs": ("parse.rs", rust), "Parse.lean": ("Parse.lean", lean)}


@competition_only
def test_the_public_surface_reads_without_a_credential():
    async def scenario():
        engine = create_async_db_engine(competition_dsn())
        await _fresh(engine)
        kit = await harness(competition_engine=engine, COMPETITIONS_ENABLED="1").setup()
        try:
            async with await _client(kit) as client:
                index = await client.get("/v1/competitions")
                detail = await client.get("/v1/competitions/miniz-oxide")
                board = await client.get("/v1/competitions/miniz-oxide/leaderboard")
            assert index.status_code == 200
            assert [c["slug"] for c in index.json()["competitions"]] == ["miniz-oxide"]
            assert detail.json()["speed_floor"] == 8.0
            assert detail.json()["queued"] == 0
            assert board.json() == {
                "competition": "miniz-oxide",
                "incumbent_bytes": None,
                "speed_floor": 8.0,
                "ranking": [],
            }
        finally:
            await kit.teardown()
            await engine.dispose()

    run(scenario())


@competition_only
def test_an_unknown_slug_is_not_found():
    async def scenario():
        engine = create_async_db_engine(competition_dsn())
        kit = await harness(competition_engine=engine, COMPETITIONS_ENABLED="1").setup()
        try:
            async with await _client(kit) as client:
                for path in (
                    "/v1/competitions/nope",
                    "/v1/competitions/nope/leaderboard",
                    "/v1/competitions/nope/submissions/1",
                ):
                    assert (await client.get(path)).status_code == 404, path
        finally:
            await kit.teardown()
            await engine.dispose()

    run(scenario())


@competition_only
def test_a_signed_submission_is_queued_and_readable():
    async def scenario():
        engine = create_async_db_engine(competition_dsn())
        keypair = _keypair()
        await _fresh(engine, hotkey=keypair.ss58_address)
        kit = await harness(competition_engine=engine, COMPETITIONS_ENABLED="1").setup()
        try:
            async with await _client(kit) as client:
                digest = competition_sig.digest_of(RUST, LEAN)
                created = await client.post(
                    "/v1/competitions/miniz-oxide/submissions",
                    files=_files(),
                    headers=_signed(keypair, digest=digest),
                )
                assert created.status_code == 201, created.text
                body = created.json()
                assert body["digest"] == digest
                assert body["state"] == "queued"
                read = await client.get(
                    f"/v1/competitions/miniz-oxide/submissions/{body['submission']}"
                )
                report = await client.get(
                    f"/v1/competitions/miniz-oxide/submissions/{body['submission']}/report"
                )
            assert read.json()["hotkey"] == keypair.ss58_address
            # Nothing has run the gate, so there is no verdict to report yet.
            assert report.json()["report"] is None
            assert report.json()["exit_code"] is None
        finally:
            await kit.teardown()
            await engine.dispose()

    run(scenario())


@competition_only
def test_the_files_are_stored_in_the_row_not_on_a_disk_the_gate_cannot_see():
    """The API and the gate no longer share a filesystem, so the bytes travel in the row."""

    async def scenario():
        from sqlalchemy import text

        engine = create_async_db_engine(competition_dsn())
        keypair = _keypair()
        await _fresh(engine, hotkey=keypair.ss58_address)
        kit = await harness(competition_engine=engine, COMPETITIONS_ENABLED="1").setup()
        try:
            async with await _client(kit) as client:
                await client.post(
                    "/v1/competitions/miniz-oxide/submissions",
                    files=_files(),
                    headers=_signed(keypair, digest=competition_sig.digest_of(RUST, LEAN)),
                )
            async with engine.connect() as conn:
                stored = (
                    await conn.execute(
                        text("SELECT parse_source, proof_source FROM submissions")
                    )
                ).one()
            assert bytes(stored[0]) == RUST
            assert bytes(stored[1]) == LEAN
        finally:
            await kit.teardown()
            await engine.dispose()

    run(scenario())


@competition_only
def test_retrying_the_same_files_returns_the_original_submission():
    """A dropped response must not cost a miner their slot.

    The service this came from checked the entitlement before looking for an existing row,
    so a retry answered 402 while the submission being retried sat queued holding the slot
    the refusal claimed was spent.
    """

    async def scenario():
        engine = create_async_db_engine(competition_dsn())
        keypair = _keypair()
        await _fresh(engine, hotkey=keypair.ss58_address, slots=1)
        kit = await harness(competition_engine=engine, COMPETITIONS_ENABLED="1").setup()
        try:
            async with await _client(kit) as client:
                headers = _signed(keypair, digest=competition_sig.digest_of(RUST, LEAN))
                first = await client.post(
                    "/v1/competitions/miniz-oxide/submissions", files=_files(), headers=headers
                )
                again = await client.post(
                    "/v1/competitions/miniz-oxide/submissions", files=_files(), headers=headers
                )
            assert first.status_code == 201
            assert again.status_code == 201
            assert again.json()["submission"] == first.json()["submission"]
            # And both agree on what is left, which is what a miner acts on.
            assert again.json()["slots_remaining"] == first.json()["slots_remaining"] == 0
        finally:
            await kit.teardown()
            await engine.dispose()

    run(scenario())


@competition_only
def test_a_second_distinct_submission_needs_a_second_registration():
    async def scenario():
        engine = create_async_db_engine(competition_dsn())
        keypair = _keypair()
        await _fresh(engine, hotkey=keypair.ss58_address, slots=1)
        kit = await harness(competition_engine=engine, COMPETITIONS_ENABLED="1").setup()
        try:
            async with await _client(kit) as client:
                await client.post(
                    "/v1/competitions/miniz-oxide/submissions",
                    files=_files(),
                    headers=_signed(keypair, digest=competition_sig.digest_of(RUST, LEAN)),
                )
                other = b"fn parse(input: &[u8]) { /* different */ }"
                refused = await client.post(
                    "/v1/competitions/miniz-oxide/submissions",
                    files=_files(rust=other),
                    headers=_signed(
                        keypair, digest=competition_sig.digest_of(other, LEAN)
                    ),
                )
            assert refused.status_code == 402
            assert refused.json()["reason_code"] == "NO_ENTITLEMENT"
        finally:
            await kit.teardown()
            await engine.dispose()

    run(scenario())


@competition_only
def test_an_unregistered_hotkey_is_refused():
    async def scenario():
        engine = create_async_db_engine(competition_dsn())
        keypair = _keypair("//Bob")
        await _fresh(engine)  # nobody registered
        kit = await harness(competition_engine=engine, COMPETITIONS_ENABLED="1").setup()
        try:
            async with await _client(kit) as client:
                refused = await client.post(
                    "/v1/competitions/miniz-oxide/submissions",
                    files=_files(),
                    headers=_signed(keypair, digest=competition_sig.digest_of(RUST, LEAN)),
                )
            assert refused.status_code == 402
            assert refused.json()["reason_code"] == "NOT_REGISTERED"
        finally:
            await kit.teardown()
            await engine.dispose()

    run(scenario())


@competition_only
def test_a_signature_for_one_competition_does_not_authorise_another():
    """The whole reason the slug is in the signed message."""

    async def scenario():
        engine = create_async_db_engine(competition_dsn())
        keypair = _keypair()
        await _fresh(engine, hotkey=keypair.ss58_address)
        kit = await harness(competition_engine=engine, COMPETITIONS_ENABLED="1").setup()
        try:
            async with await _client(kit) as client:
                refused = await client.post(
                    "/v1/competitions/miniz-oxide/submissions",
                    files=_files(),
                    headers=_signed(
                        keypair,
                        digest=competition_sig.digest_of(RUST, LEAN),
                        competition="some-other-competition",
                    ),
                )
            assert refused.status_code == 401
        finally:
            await kit.teardown()
            await engine.dispose()

    run(scenario())


@competition_only
def test_a_signature_over_different_files_does_not_authorise_these():
    async def scenario():
        engine = create_async_db_engine(competition_dsn())
        keypair = _keypair()
        await _fresh(engine, hotkey=keypair.ss58_address)
        kit = await harness(competition_engine=engine, COMPETITIONS_ENABLED="1").setup()
        try:
            async with await _client(kit) as client:
                # Signed for one pair, sent with another: the server rebuilds the message
                # from the bytes it actually read, so the two do not match.
                refused = await client.post(
                    "/v1/competitions/miniz-oxide/submissions",
                    files=_files(rust=b"something else entirely"),
                    headers=_signed(keypair, digest=competition_sig.digest_of(RUST, LEAN)),
                )
            assert refused.status_code == 401
        finally:
            await kit.teardown()
            await engine.dispose()

    run(scenario())


@competition_only
def test_a_stale_timestamp_is_refused_before_the_signature_is_checked():
    async def scenario():
        engine = create_async_db_engine(competition_dsn())
        keypair = _keypair()
        await _fresh(engine, hotkey=keypair.ss58_address)
        kit = await harness(competition_engine=engine, COMPETITIONS_ENABLED="1").setup()
        try:
            async with await _client(kit) as client:
                refused = await client.post(
                    "/v1/competitions/miniz-oxide/submissions",
                    files=_files(),
                    headers=_signed(
                        keypair,
                        digest=competition_sig.digest_of(RUST, LEAN),
                        timestamp=int(time.time()) - 4000,
                    ),
                )
            assert refused.status_code == 401
            assert refused.json()["reason_code"] == "SIGNATURE_EXPIRED"
        finally:
            await kit.teardown()
            await engine.dispose()

    run(scenario())


@competition_only
def test_an_oversized_or_empty_file_is_refused():
    async def scenario():
        from submission_api.settings import MAX_COMPETITION_FILE_BYTES

        engine = create_async_db_engine(competition_dsn())
        keypair = _keypair()
        await _fresh(engine, hotkey=keypair.ss58_address)
        kit = await harness(competition_engine=engine, COMPETITIONS_ENABLED="1").setup()
        try:
            async with await _client(kit) as client:
                huge = b"x" * (MAX_COMPETITION_FILE_BYTES + 1)
                too_big = await client.post(
                    "/v1/competitions/miniz-oxide/submissions",
                    files=_files(rust=huge),
                    headers=_signed(keypair, digest=competition_sig.digest_of(huge, LEAN)),
                )
                empty = await client.post(
                    "/v1/competitions/miniz-oxide/submissions",
                    files=_files(rust=b""),
                    headers=_signed(keypair, digest=competition_sig.digest_of(b"", LEAN)),
                )
            assert too_big.status_code == 413
            assert empty.status_code == 400
        finally:
            await kit.teardown()
            await engine.dispose()

    run(scenario())


@competition_only
def test_the_hotkey_rate_limit_is_counted_in_the_database():
    """Per hotkey and across replicas, which an in-process counter cannot be."""

    async def scenario():
        engine = create_async_db_engine(competition_dsn())
        keypair = _keypair()
        await _fresh(engine, hotkey=keypair.ss58_address)
        kit = await harness(
            competition_engine=engine,
            COMPETITIONS_ENABLED="1",
            COMPETITION_RATE_PER_MINUTE="2",
        ).setup()
        try:
            async with await _client(kit) as client:
                headers = _signed(keypair, digest=competition_sig.digest_of(RUST, LEAN))
                statuses = [
                    (
                        await client.post(
                            "/v1/competitions/miniz-oxide/submissions",
                            files=_files(),
                            headers=headers,
                        )
                    ).status_code
                    for _ in range(3)
                ]
            assert statuses[:2] == [201, 201]
            assert statuses[2] == 429
        finally:
            await kit.teardown()
            await engine.dispose()

    run(scenario())


# ── the browser path ───────────────────────────────────────────────────────


async def _set_submission_coldkey(kit, account_id, coldkey: str) -> None:
    """Link a coldkey to the account and designate it for submissions.

    Written through the database rather than through the wallet-challenge and
    `PUT /v1/me/coldkeys/submission` endpoints, because this test is about what the
    competition path does with the link rather than about how the link is made.

    Both rows, not just the column: `account_submission_coldkey_is_linked` requires the
    designated coldkey to be one the account has actually proved it controls. That constraint
    is load-bearing here -- it is what stops an account designating a stranger's coldkey and
    inheriting the registrations made with it -- so the test sets up the state the constraint
    describes instead of working around it.
    """
    from sqlalchemy import update

    from conjectures_subnet.db.models import Account, AccountWallet

    async with kit.services.sessions() as session:
        session.add(
            AccountWallet(account_id=account_id, coldkey=coldkey, signature=b"\x00" * 64)
        )
        await session.flush()
        await session.execute(
            update(Account)
            .where(Account.id == account_id)
            .values(submission_coldkey=coldkey)
        )
        await session.commit()


@competition_only
def test_a_signed_in_account_may_submit_for_a_hotkey_its_own_coldkey_registered():
    """No hotkey signature: a browser has no business holding one.

    Entitlement runs the other way round instead -- the account owns a submission coldkey,
    and a registration records which coldkey put which hotkey on the subnet.
    """

    async def scenario():
        engine = create_async_db_engine(competition_dsn())
        keypair = _keypair()
        coldkey = MINER_COLDKEY
        await _fresh(engine, hotkey=keypair.ss58_address, coldkey=coldkey)
        kit = await harness(competition_engine=engine, COMPETITIONS_ENABLED="1").setup()
        try:
            async with await client(kit) as http:
                account = await sign_in_by_email(kit, http)
                await _set_submission_coldkey(kit, uuid.UUID(account["id"]), coldkey)
                created = await http.post(
                    "/v1/competitions/miniz-oxide/submissions/session",
                    files=_files(),
                    headers={
                        **same_origin(http),
                        "X-Conjectures-Hotkey": keypair.ss58_address,
                    },
                )
            assert created.status_code == 201, created.text
            assert created.json()["state"] == "queued"
        finally:
            await kit.teardown()
            await engine.dispose()

    run(scenario())


@competition_only
def test_an_account_may_not_submit_for_a_hotkey_it_did_not_register():
    """The check that stops one account spending another's entitlement."""

    async def scenario():
        engine = create_async_db_engine(competition_dsn())
        keypair = _keypair()
        # Registered by somebody else's coldkey.
        await _fresh(engine, hotkey=keypair.ss58_address, coldkey=OTHER_MINER_COLDKEY)
        kit = await harness(competition_engine=engine, COMPETITIONS_ENABLED="1").setup()
        try:
            async with await client(kit) as http:
                account = await sign_in_by_email(kit, http)
                await _set_submission_coldkey(kit, uuid.UUID(account["id"]), MINER_COLDKEY)
                refused = await http.post(
                    "/v1/competitions/miniz-oxide/submissions/session",
                    files=_files(),
                    headers={
                        **same_origin(http),
                        "X-Conjectures-Hotkey": keypair.ss58_address,
                    },
                )
            assert refused.status_code == 403
            assert refused.json()["reason_code"] == "HOTKEY_NOT_YOURS"
        finally:
            await kit.teardown()
            await engine.dispose()

    run(scenario())


@competition_only
def test_an_account_with_no_linked_coldkey_is_told_to_link_one():
    async def scenario():
        engine = create_async_db_engine(competition_dsn())
        keypair = _keypair()
        await _fresh(engine, hotkey=keypair.ss58_address)
        kit = await harness(competition_engine=engine, COMPETITIONS_ENABLED="1").setup()
        try:
            async with await client(kit) as http:
                await sign_in_by_email(kit, http)
                refused = await http.post(
                    "/v1/competitions/miniz-oxide/submissions/session",
                    files=_files(),
                    headers={
                        **same_origin(http),
                        "X-Conjectures-Hotkey": keypair.ss58_address,
                    },
                )
            assert refused.status_code == 402
            assert refused.json()["reason_code"] == "NO_SUBMISSION_COLDKEY"
        finally:
            await kit.teardown()
            await engine.dispose()

    run(scenario())


@competition_only
def test_the_browser_path_refuses_an_unauthenticated_caller():
    async def scenario():
        engine = create_async_db_engine(competition_dsn())
        keypair = _keypair()
        await _fresh(engine, hotkey=keypair.ss58_address)
        kit = await harness(competition_engine=engine, COMPETITIONS_ENABLED="1").setup()
        try:
            async with await client(kit) as http:
                refused = await http.post(
                    "/v1/competitions/miniz-oxide/submissions/session",
                    files=_files(),
                    headers={
                        **same_origin(http),
                        "X-Conjectures-Hotkey": keypair.ss58_address,
                    },
                )
            assert refused.status_code == 401
        finally:
            await kit.teardown()
            await engine.dispose()

    run(scenario())


@competition_only
def test_an_account_sees_its_own_submissions_and_not_a_signed_one():
    """`account_id` records which account authorised a write, not who a hotkey belongs to."""

    async def scenario():
        engine = create_async_db_engine(competition_dsn())
        keypair = _keypair()
        await _fresh(engine, hotkey=keypair.ss58_address, coldkey=MINER_COLDKEY, slots=2)
        kit = await harness(competition_engine=engine, COMPETITIONS_ENABLED="1").setup()
        try:
            async with await client(kit) as http:
                account = await sign_in_by_email(kit, http)
                await _set_submission_coldkey(kit, uuid.UUID(account["id"]), MINER_COLDKEY)
                through_session = await http.post(
                    "/v1/competitions/miniz-oxide/submissions/session",
                    files=_files(),
                    headers={
                        **same_origin(http),
                        "X-Conjectures-Hotkey": keypair.ss58_address,
                    },
                )
                other = b"a different parser entirely"
                await http.post(
                    "/v1/competitions/miniz-oxide/submissions",
                    files=_files(rust=other),
                    headers=_signed(
                        keypair, digest=competition_sig.digest_of(other, LEAN)
                    ),
                )
                mine = await http.get("/v1/me/competitions/submissions")
            assert through_session.status_code == 201
            assert mine.status_code == 200
            ids = [row["id"] for row in mine.json()["submissions"]]
            assert ids == [through_session.json()["submission"]]
        finally:
            await kit.teardown()
            await engine.dispose()

    run(scenario())


@competition_only
def test_the_account_listing_needs_a_credential():
    async def scenario():
        engine = create_async_db_engine(competition_dsn())
        kit = await harness(competition_engine=engine, COMPETITIONS_ENABLED="1").setup()
        try:
            async with await _client(kit) as http:
                assert (await http.get("/v1/me/competitions/submissions")).status_code == 401
        finally:
            await kit.teardown()
            await engine.dispose()

    run(scenario())
