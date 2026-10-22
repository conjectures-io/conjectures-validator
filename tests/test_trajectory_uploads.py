"""Private PostgreSQL coverage for the multipart transport and unchanged final intake."""
import asyncio
import datetime as dt
import hashlib
import uuid

import pytest
from sqlalchemy import func, select

from conftest_api import postgres_dsn
from test_api_accounts import client, same_origin, sign_in_by_email
from test_api_trajectories import accepted, kit_config
from test_trajectory_schema import _with, consent_params
from scripts.trajectory_assets import build_package
from conjectures_subnet.db.trajectory_models import TrajectoryClaim, TrajectoryUpload, TrajectoryUploadChunk
from submission_api import trajectory_uploads as staging
from submission_api.trajectory_schema import MAX_PACKAGE_BYTES

pytestmark = pytest.mark.skipif(postgres_dsn() is None, reason="requires verified private fixture")
START = "/v1/me/chat-log-uploads"
ZIP = {**same_origin(), "content-type": "application/zip"}


async def start(http, size, **consent):
    return await http.post(START, params={**consent_params(**consent), "byte_length": size}, headers=same_origin())


def test_parts_are_owned_ordered_retryable_and_stored_unchanged():
    async def scenario():
        kit = await kit_config().setup()
        try:
            async with await client(kit) as owner, await client(kit) as other:
                _, sid, package = await accepted(kit, owner)
                await sign_in_by_email(kit, other, email="other-parts@example.com")
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
                    assert claim.package == raw
                    assert await session.scalar(select(func.count()).select_from(TrajectoryUpload)) == 0
                    assert await session.scalar(select(func.count()).select_from(TrajectoryUploadChunk)) == 0
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
