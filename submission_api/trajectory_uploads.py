"""Bounded, account-owned staging for uploads sent in small HTTP requests.

Parts are private database records, so a restart or another API worker can continue a transfer.
At most eight packages are staged, one per account, for twenty-four hours. Starting another upload
replaces that account's abandoned transfer; completed intake deletes the parts atomically.
"""
import datetime as dt
import uuid

import anyio

from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import OperationalError

from conjectures_subnet.db.trajectory_models import TrajectoryUpload, TrajectoryUploadChunk
from submission_api.trajectory_storage import FilePackage, INLINE_BYTES, require_scratch_space
from submission_api import trajectory_quota as quota
from submission_api.errors import BadRequest, Conflict, NotFound, TooManyRequests

CHUNK_BYTES = 8 * 1024 * 1024
MAX_PENDING_UPLOADS = 8
DELETE_CHUNKS = 16


async def locked(session, query):
    try:
        return await session.scalars(query.with_for_update(nowait=True))
    except OperationalError as error:
        if getattr(error.orig, "sqlstate", None) != "55P03":
            raise
        raise TooManyRequests("Chat-log upload is busy; retry shortly.", reason_code="TRAJECTORY_BUSY",
                              extra={"retry_after_seconds": 2}) from None


async def remove(session, *conditions):
    # Lock the parent first so an append cannot race cleanup between batches.
    ids = list(await locked(session, select(TrajectoryUpload.id).where(*conditions)))
    for upload_id in ids:
        while True:
            offsets = select(TrajectoryUploadChunk.byte_offset).where(
                TrajectoryUploadChunk.upload_id == upload_id).order_by(
                    TrajectoryUploadChunk.byte_offset).limit(DELETE_CHUNKS)
            result = await session.execute(delete(TrajectoryUploadChunk).where(
                TrajectoryUploadChunk.upload_id == upload_id,
                TrajectoryUploadChunk.byte_offset.in_(offsets)))
            if not result.rowcount:
                break
        # The cascade is now empty; deleting a multi-GiB TOAST value set in one statement
        # exceeded the production statement timeout and rolled back every retry.
        await session.execute(delete(TrajectoryUpload).where(TrajectoryUpload.id == upload_id))


async def purge_expired(session):
    await remove(session, TrajectoryUpload.expires_at <= dt.datetime.now(dt.UTC))


async def start(session, account_id, byte_length):
    # Serialize admission across workers; every transfer reserves its entire declared size.
    await quota.lock(session)
    await remove(session, or_(TrajectoryUpload.expires_at <= dt.datetime.now(dt.UTC),
                             TrajectoryUpload.account_id == account_id))
    if await session.scalar(select(func.count()).select_from(TrajectoryUpload)) >= MAX_PENDING_UPLOADS:
        raise TooManyRequests("Chat-log uploads are busy; retry shortly.", reason_code="TRAJECTORY_BUSY")
    await quota.check_capacity(session, account_id, byte_length)
    upload = TrajectoryUpload(id=uuid.uuid4(), account_id=account_id, byte_length=byte_length,
                              received_bytes=0, expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(hours=24))
    session.add(upload)
    await session.flush()
    return {"id": str(upload.id), "chunk_bytes": CHUNK_BYTES, "expires_at": upload.expires_at}


async def owned(session, upload_id, account_id, *, lock=False):
    query = select(TrajectoryUpload).where(TrajectoryUpload.id == upload_id,
        TrajectoryUpload.account_id == account_id, TrajectoryUpload.expires_at > dt.datetime.now(dt.UTC))
    upload = (await locked(session, query)).one_or_none() if lock else await session.scalar(query)
    if upload is None:
        raise NotFound("Chat-log upload not found or expired; select the file again.")
    return upload


async def append(session, upload_id, account_id, offset, raw):
    upload = await owned(session, upload_id, account_id, lock=True)
    require_scratch_space(len(raw))
    if offset % CHUNK_BYTES or offset >= upload.byte_length or len(raw) != min(CHUNK_BYTES, upload.byte_length - offset):
        raise BadRequest("Upload part has an invalid offset or length.")
    if offset < upload.received_bytes:
        # Safe retry after the previous response was lost; never append a duplicate.
        previous = await session.get(TrajectoryUploadChunk, (upload_id, offset))
        if previous is None or previous.data != raw:
            raise Conflict("Upload part differs from the part already received.")
    elif offset == upload.received_bytes:
        session.add(TrajectoryUploadChunk(upload_id=upload_id, byte_offset=offset, data=raw))
        upload.received_bytes += len(raw)
        await session.flush()
    else:
        raise Conflict("Upload parts must be sent in order.")
    return {"received_bytes": upload.received_bytes}


async def read(session, upload_id, account_id):
    upload = await owned(session, upload_id, account_id, lock=True)
    if upload.received_bytes != upload.byte_length:
        raise Conflict("Chat-log upload is incomplete.")
    raw = FilePackage(size_hint=upload.byte_length)
    raw.upload_id = upload_id
    try:
        offset = 0
        while offset < upload.byte_length:
            data = await session.scalar(select(TrajectoryUploadChunk.data).where(
                TrajectoryUploadChunk.upload_id == upload_id, TrajectoryUploadChunk.byte_offset == offset))
            if data is None or len(data) != min(CHUNK_BYTES, upload.byte_length - offset):
                raise Conflict("Chat-log upload is incomplete.")
            await anyio.to_thread.run_sync(raw.write, data)
            offset += len(data)
        if len(raw) <= INLINE_BYTES:
            raw.file.seek(0)
            content = await anyio.to_thread.run_sync(raw.file.read)
            raw.close()
            return content
        return raw
    except BaseException:
        raw.close()
        raise


async def discard(session, upload_id, account_id):
    await remove(session, TrajectoryUpload.id == upload_id, TrajectoryUpload.account_id == account_id)
