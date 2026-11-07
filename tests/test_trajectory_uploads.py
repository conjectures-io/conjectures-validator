"""Private PostgreSQL coverage for the multipart transport and unchanged final intake."""
import asyncio
import datetime as dt
import hashlib
import uuid

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import OperationalError

from conftest_api import postgres_dsn
from test_api_accounts import client, same_origin, sign_in_by_email
from test_api_trajectories import accepted, kit_config, _grant_reviewer
from test_trajectory_schema import _with, consent_params
from scripts.trajectory_assets import build_package
from conjectures_subnet.db.trajectory_models import TrajectoryClaim, TrajectoryUpload, TrajectoryUploadChunk, TrajectoryPackageChunk
from submission_api import trajectory_uploads as staging
from submission_api.trajectory_schema import MAX_PACKAGE_BYTES

pytestmark = pytest.mark.skipif(postgres_dsn() is None, reason="requires verified private fixture")
START = "/v1/me/chat-log-uploads"
ZIP = {**same_origin(), "content-type": "application/zip"}


async def start(http, size, **consent):
    return await http.post(START, params={**consent_params(**consent), "byte_length": size}, headers=same_origin())


@pytest.mark.parametrize("file_backed", [False, True])
def test_parts_are_owned_ordered_retryable_and_stored_unchanged(monkeypatch, file_backed):
    if file_backed:
        monkeypatch.setattr(staging, "INLINE_BYTES", 1)
    async def scenario():
        kit = await kit_config().setup()
        try:
            async with await client(kit) as owner, await client(kit) as other:
                _, sid, package = await accepted(kit, owner)
                other_account = await sign_in_by_email(kit, other, email="other-parts@example.com")
                # Stored ZIP entry makes this larger than one transport part.
                raw = _with(build_package(package), "chat-log-package/artifacts/padding.bin", b"x" * staging.CHUNK_BYTES)
                begun = await start(owner, len(raw))
                assert begun.status_code == 200, begun.text
                uid = begun.json()["id"]
                path = f"{START}/{uid}"
                final = f"/v1/me/submissions/{sid}/trajectory"
                finish = {"params": {**consent_params(), "upload_id": uid}, "headers": ZIP, "content": b""}
                first = raw[:staging.CHUNK_BYTES]
                part = {"params": {"offset": 0}, "content": first, "headers": ZIP}
                assert (await other.put(path, **part)).status_code == 404
                assert (await other.delete(path, headers=same_origin())).status_code == 204
                assert (await owner.put(path, **{**part, "params": {"offset": staging.CHUNK_BYTES}})).status_code == 400
                assert (await owner.put(path, params={"offset": staging.CHUNK_BYTES}, content=raw[staging.CHUNK_BYTES:], headers=ZIP)).status_code == 409
                assert (await owner.put(path, **{**part, "content": first + b"x"})).status_code == 413
                assert (await owner.post(final, **finish)).status_code == 409
                assert (await owner.put(path, **{**part, "content": first[:-1]})).status_code == 400
                sent = await owner.put(path, **part)
                assert sent.status_code == 200, sent.text
                assert sent.json()["received_bytes"] == staging.CHUNK_BYTES
                assert sent.headers["cache-control"] == "no-store"
                assert (await owner.put(path, **part)).json() == sent.json()
                changed = await owner.put(path, **{**part, "content": b"z" + first[1:]})
                assert changed.status_code == 409
                last = await owner.put(path, params={"offset": staging.CHUNK_BYTES}, content=raw[staging.CHUNK_BYTES:], headers=ZIP)
                assert last.json()["received_bytes"] == len(raw)
                assert (await owner.post(final, **{**finish, "params": {**finish["params"], "accepted": "false"}})).status_code == 400
                done = await owner.post(final, **finish)
                assert done.status_code == 200, done.text
                assert done.json()["package_sha256"] == "sha256:" + hashlib.sha256(raw).hexdigest()
                async with kit.session() as session:
                    claim = await session.get(TrajectoryClaim, uuid.UUID(done.json()["id"]))
                    assert claim.package_chunked is file_backed
                    if file_backed:
                        stored = await session.scalars(select(TrajectoryPackageChunk.data).where(TrajectoryPackageChunk.claim_id == claim.id).order_by(TrajectoryPackageChunk.byte_offset))
                        assert b"".join(stored) == raw
                    else:
                        assert claim.package == raw
                    assert await session.scalar(select(func.count()).select_from(TrajectoryUpload)) == 0
                    assert await session.scalar(select(func.count()).select_from(TrajectoryUploadChunk)) == 0
                await _grant_reviewer(kit, other_account["id"])
                downloaded = await other.get(f"/v1/admin/trajectories/{done.json()['id']}/package")
                assert downloaded.status_code == 200, downloaded.text[:100]
                assert downloaded.content == raw
                withdrawn = await owner.post(final + "/withdraw", json={"expected_state_version": done.json()["state_version"]}, headers=same_origin())
                assert withdrawn.status_code == 200, withdrawn.text
                async with kit.session() as session:
                    assert await session.scalar(select(func.count()).select_from(TrajectoryPackageChunk)) == 0
                assert (await owner.delete(path, headers=same_origin())).status_code == 204
        finally:
            await kit.teardown()
    asyncio.run(scenario())


def test_staging_limits_expiry_replacement_and_cleanup(monkeypatch):
    monkeypatch.setattr(staging, "MAX_PENDING_UPLOADS", 1)
    async def scenario():
        kit = await kit_config().setup()
        try:
            async with await client(kit) as owner, await client(kit) as other:
                assert (await start(owner, 10)).status_code == 401
                await sign_in_by_email(kit, owner)
                await sign_in_by_email(kit, other, email="other-limit@example.com")
                for size in (0, MAX_PACKAGE_BYTES + 1):
                    assert (await start(owner, size)).status_code == 400
                assert (await start(owner, 10, accepted="false")).status_code == 400
                begun = await start(owner, MAX_PACKAGE_BYTES)
                assert begun.status_code == 200
                uid = uuid.UUID(begun.json()["id"])
                assert (await start(other, 10)).status_code == 429
                replacement = await start(owner, 10)
                assert replacement.status_code == 200
                assert replacement.json()["id"] != str(uid)
                uid = uuid.UUID(replacement.json()["id"])
                path = f"{START}/{uid}"
                assert (await owner.put(path, params={"offset": 0}, content=b"1234567890", headers=ZIP)).status_code == 200
                async with kit.session() as session:
                    row = await session.get(TrajectoryUpload, uid)
                    row.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
                    await session.commit()
                assert (await owner.put(path, params={"offset": 0}, content=b"1234567890", headers=ZIP)).status_code == 404
                assert (await start(other, 10)).status_code == 200
                async with kit.session() as session:
                    assert await session.get(TrajectoryUpload, uid) is None
                    assert await session.scalar(select(func.count()).select_from(TrajectoryUploadChunk)) == 0
        finally:
            await kit.teardown()
    asyncio.run(scenario())


def test_staged_deposit_still_checks_the_package_and_creates_no_claim_until_finalization():
    from test_api_session_submissions import keyless_account
    from test_api_trajectories import package_for, with_new_bounties
    from conftest_api import distinct_bundle
    async def scenario():
        kit = await kit_config().setup()
        with_new_bounties(kit)
        try:
            async with await client(kit) as owner:
                await keyless_account(kit, owner)
                _, proof_hash = distinct_bundle('multipart-deposit', coldkey=None)
                raw = build_package(package_for(proof_hash, None))
                begun = await start(owner, len(raw))
                uid = begun.json()['id']
                path = f'{START}/{uid}'
                assert (await owner.put(path, params={'offset': 0}, content=raw, headers=ZIP)).status_code == 200
                async with kit.session() as session:
                    assert await session.scalar(select(func.count()).select_from(TrajectoryClaim)) == 0
                response = await owner.post('/v1/me/chat-log-deposits', content=b'', headers=ZIP,
                    params={**consent_params(), 'upload_id': uid})
                assert response.status_code == 200, response.text
                assert response.json()['status'] == 'DEPOSITED'
                assert response.json()['package_sha256'] == 'sha256:' + hashlib.sha256(raw).hexdigest()
                async with kit.session() as session:
                    assert await session.get(TrajectoryUpload, uuid.UUID(uid)) is None
                begun = await start(owner, 3)
                uid = begun.json()['id']
                path = f'{START}/{uid}'
                assert (await owner.put(path, params={'offset': 0}, content=b'bad', headers=ZIP)).status_code == 200
                response = await owner.post('/v1/me/chat-log-deposits', content=b'', headers=ZIP,
                    params={**consent_params(), 'upload_id': uid})
                assert response.status_code == 400 and response.json()['reason_code'] == 'TRAJECTORY_INVALID'
                assert (await owner.delete(path, headers=same_origin())).status_code == 204
                async with kit.session() as session:
                    assert await session.scalar(select(func.count()).select_from(TrajectoryUploadChunk)) == 0
                    assert await session.scalar(select(func.count()).select_from(TrajectoryClaim)) == 1
        finally:
            await kit.teardown()
    asyncio.run(scenario())


def test_storage_quotas_include_completed_packages_and_pending_reservations(monkeypatch):
    from submission_api import trajectory_quota as quota, trajectory_storage as storage
    from submission_api.errors import TooManyRequests
    from test_api_trajectories import as_upload
    async def scenario():
        kit = await kit_config().setup()
        try:
            async with await client(kit) as owner, await client(kit) as other, await client(kit) as third:
                _, sid, document = await accepted(kit, owner)
                raw = build_package(document)
                done = await owner.post(f"/v1/me/submissions/{sid}/trajectory", **as_upload(document))
                assert done.status_code == 200, done.text
                await sign_in_by_email(kit, other, email="quota-other@example.com")
                await sign_in_by_email(kit, third, email="quota-third@example.com")
                monkeypatch.setattr(quota, "ACCOUNT_BYTES", len(raw) + 16)
                monkeypatch.setattr(quota, "GLOBAL_BYTES", len(raw) + 32)
                reserved = await start(owner, 16)
                assert reserved.status_code == 200
                assert (await start(owner, 17)).status_code == 429
                assert (await start(other, 16)).status_code == 200
                assert (await start(third, 1)).status_code == 429
                async with kit.session() as session:
                    assert await session.get(TrajectoryUpload, uuid.UUID(reserved.json()["id"])) is not None
                # Final persistence has its own quota gate, including direct uploads.
                monkeypatch.setattr(quota, "ACCOUNT_BYTES", len(raw) - 1)
                async with kit.session() as session:
                    claim = await session.get(TrajectoryClaim, uuid.UUID(done.json()["id"]))
                    with pytest.raises(TooManyRequests):
                        await storage.save(session, claim, raw)
                    await session.rollback()
        finally:
            await kit.teardown()
    asyncio.run(scenario())


def test_concurrent_storage_admission_cannot_overbook_global_quota(monkeypatch):
    from submission_api import trajectory_quota as quota
    from submission_api.errors import TooManyRequests
    monkeypatch.setattr(quota, "ACCOUNT_BYTES", 8)
    monkeypatch.setattr(quota, "GLOBAL_BYTES", 8)
    async def scenario():
        kit = await kit_config().setup()
        try:
            async with await client(kit) as first, await client(kit) as second:
                a = await sign_in_by_email(kit, first, email="quota-first@example.com")
                b = await sign_in_by_email(kit, second, email="quota-second@example.com")
            async with kit.session() as one, kit.session() as two:
                await staging.start(one, uuid.UUID(a["id"]), 8)
                with pytest.raises(TooManyRequests):
                    await staging.start(two, uuid.UUID(b["id"]), 1)
                await two.rollback()
                await one.commit()
                with pytest.raises(TooManyRequests):
                    await staging.start(two, uuid.UUID(b["id"]), 1)
                await two.rollback()
        finally:
            await kit.teardown()
    asyncio.run(scenario())


def test_direct_upload_quota_rejects_before_any_package_insert(monkeypatch):
    from sqlalchemy import event
    from submission_api import trajectory_quota as quota
    from test_api_trajectories import as_upload
    async def scenario():
        kit = await kit_config().setup()
        statements = []
        def capture(connection, cursor, statement, parameters, context, executemany):
            statements.append(statement)
        try:
            async with await client(kit) as owner:
                _, sid, document = await accepted(kit, owner)
                monkeypatch.setattr(quota, "ACCOUNT_BYTES", 1)
                async with kit.session() as session:
                    engine = session.bind.sync_engine
                event.listen(engine, "before_cursor_execute", capture)
                try:
                    result = await owner.post(f"/v1/me/submissions/{sid}/trajectory", **as_upload(document))
                    assert result.status_code == 429, result.text
                    assert not any("INSERT INTO trajectory_claims" in sql or "INSERT INTO trajectory_package_chunks" in sql
                                   for sql in statements)
                finally:
                    event.remove(engine, "before_cursor_execute", capture)
        finally:
            await kit.teardown()
    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["discard", "start", "purge"])
def test_cleanup_survives_a_cascade_larger_than_the_statement_budget(monkeypatch, operation):
    monkeypatch.setattr(staging, "DELETE_CHUNKS", 2)

    async def scenario():
        kit = await kit_config().setup()
        try:
            async with await client(kit) as owner:
                account = await sign_in_by_email(kit, owner)
                begun = await start(owner, 6 * staging.CHUNK_BYTES)
                uid, aid = uuid.UUID(begun.json()["id"]), uuid.UUID(account["id"])
            async with kit.session() as session:
                session.add_all(TrajectoryUploadChunk(upload_id=uid, byte_offset=i * staging.CHUNK_BYTES,
                                                     data=b"x") for i in range(6))
                if operation == "purge":
                    row = await session.get(TrajectoryUpload, uid)
                    row.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
                # Simulate slow TOAST deletion without putting gigabytes in every CI run.
                await session.execute(text("""CREATE OR REPLACE FUNCTION slow_upload_delete() RETURNS trigger
                    LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(0.08); RETURN OLD; END $$"""))
                await session.execute(text("""CREATE TRIGGER slow_upload_delete BEFORE DELETE ON trajectory_upload_chunks
                    FOR EACH ROW EXECUTE FUNCTION slow_upload_delete()"""))
                await session.commit()
                await session.execute(text("SET LOCAL statement_timeout = '400ms'"))
                with pytest.raises(OperationalError) as caught:
                    await session.execute(delete(TrajectoryUpload).where(TrajectoryUpload.id == uid))
                assert caught.value.orig.sqlstate == "57014"
                await session.rollback()
                await session.execute(text("SET LOCAL statement_timeout = '400ms'"))
                if operation == "discard":
                    await staging.discard(session, uid, aid)
                elif operation == "start":
                    await staging.start(session, aid, 10)
                else:
                    await staging.purge_expired(session)
                await session.commit()
                assert await session.get(TrajectoryUpload, uid) is None
                assert await session.scalar(select(func.count()).select_from(TrajectoryUploadChunk)) == 0
        finally:
            await kit.teardown()
    asyncio.run(scenario())


def test_busy_upload_returns_retryable_errors_and_preserves_parts():
    async def scenario():
        kit = await kit_config().setup()
        try:
            async with await client(kit) as owner:
                await sign_in_by_email(kit, owner)
                uid = (await start(owner, 3)).json()["id"]
                path = f"{START}/{uid}"
                part = {"params": {"offset": 0}, "content": b"zip", "headers": ZIP}
                async with kit.session() as holder:
                    await holder.scalar(select(TrajectoryUpload).where(
                        TrajectoryUpload.id == uuid.UUID(uid)).with_for_update())
                    for response in [await owner.put(path, **part),
                                     await owner.delete(path, headers=same_origin()),
                                     await start(owner, 4)]:
                        assert response.status_code == 429, response.text
                        assert response.json()["reason_code"] == "TRAJECTORY_BUSY"
                        assert response.json()["retry_after_seconds"] == 2
                    await holder.rollback()
                assert (await owner.put(path, **part)).status_code == 200
                assert (await owner.put(path, **part)).json()["received_bytes"] == 3
        finally:
            await kit.teardown()
    asyncio.run(scenario())


def test_keepalive_upload_commits_and_reports_validation_errors(monkeypatch):
    monkeypatch.setattr(staging, "INLINE_BYTES", 1)

    async def scenario():
        kit = await kit_config().setup()
        try:
            async with await client(kit) as owner:
                _, sid, document = await accepted(kit, owner)
                raw = build_package(document)
                params = {**consent_params(), "keepalive": "true"}
                begun = await owner.post(START, params={**params, "byte_length": len(raw)}, headers=same_origin())
                uid = begun.json()["result"]["id"]
                assert begun.headers["x-accel-buffering"] == "no"
                assert begun.headers["cache-control"] == "no-store, no-transform"
                assert (await owner.put(f"{START}/{uid}", params={"offset": 0}, content=raw, headers=ZIP)).status_code == 200
                final = f"/v1/me/submissions/{sid}/trajectory"
                done = await owner.post(final, params={**params, "upload_id": uid}, content=b"", headers=ZIP)
                assert done.status_code == 200, done.text
                assert done.json()["result"]["package_sha256"] == "sha256:" + hashlib.sha256(raw).hexdigest()
                async with kit.session() as session:
                    assert await session.get(TrajectoryUpload, uuid.UUID(uid)) is None
                    assert await session.scalar(select(func.count()).select_from(TrajectoryClaim)) == 1
                uid = (await start(owner, 3)).json()["id"]
                assert (await owner.put(f"{START}/{uid}", params={"offset": 0}, content=b"bad", headers=ZIP)).status_code == 200
                failed = await owner.post("/v1/me/chat-log-deposits", params={**params, "upload_id": uid}, content=b"", headers=ZIP)
                assert failed.status_code == 200
                assert failed.json()["error"]["status"] == 400
                assert failed.json()["error"]["reason_code"] == "TRAJECTORY_INVALID"
                async with kit.session() as session:
                    assert await session.scalar(select(func.count()).select_from(TrajectoryClaim)) == 1
        finally:
            await kit.teardown()
    asyncio.run(scenario())
