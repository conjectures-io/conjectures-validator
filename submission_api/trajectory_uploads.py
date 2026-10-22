"""Bounded, account-owned staging for uploads sent in small HTTP requests.

Parts are private database records, so a restart or another API worker can continue a transfer.
At most eight packages are staged, one per account, for thirty minutes. Starting another upload
replaces that account's abandoned transfer; completed intake deletes the parts atomically.
"""
import datetime as dt
import uuid

from sqlalchemy import delete, func, or_, select, text

from conjectures_subnet.db.trajectory_models import TrajectoryUpload, TrajectoryUploadChunk
from submission_api.errors import BadRequest, Conflict, NotFound, TooManyRequests

CHUNK_BYTES = 8 * 1024 * 1024
MAX_PENDING_UPLOADS = 8


async def purge_expired(session):
    await session.execute(delete(TrajectoryUpload).where(TrajectoryUpload.expires_at <= dt.datetime.now(dt.UTC)))


async def start(session, account_id, byte_length):
    # Serialize admission across workers; every transfer reserves its entire declared size.
    await session.execute(text("SELECT pg_advisory_xact_lock(715302226)"))
    await session.execute(delete(TrajectoryUpload).where(or_(
        TrajectoryUpload.expires_at <= dt.datetime.now(dt.UTC), TrajectoryUpload.account_id == account_id)))
    if await session.scalar(select(func.count()).select_from(TrajectoryUpload)) >= MAX_PENDING_UPLOADS:
        raise TooManyRequests("Chat-log uploads are busy; retry shortly.", reason_code="TRAJECTORY_BUSY")
    upload = TrajectoryUpload(id=uuid.uuid4(), account_id=account_id, byte_length=byte_length,
                              received_bytes=0, expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(minutes=30))
    session.add(upload)
    await session.flush()
    return {"id": str(upload.id), "chunk_bytes": CHUNK_BYTES, "expires_at": upload.expires_at}


async def owned(session, upload_id, account_id, *, lock=False):
    query = select(TrajectoryUpload).where(TrajectoryUpload.id == upload_id,
        TrajectoryUpload.account_id == account_id, TrajectoryUpload.expires_at > dt.datetime.now(dt.UTC))
    upload = await session.scalar(query.with_for_update() if lock else query)
    if upload is None:
        raise NotFound("Chat-log upload not found or expired; select the file again.")
    return upload


async def append(session, upload_id, account_id, offset, raw):
    upload = await owned(session, upload_id, account_id, lock=True)
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
    parts = await session.scalars(select(TrajectoryUploadChunk.data).where(
        TrajectoryUploadChunk.upload_id == upload_id).order_by(TrajectoryUploadChunk.byte_offset))
    raw = b"".join(parts)
    if len(raw) != upload.byte_length:
        raise Conflict("Chat-log upload is incomplete.")
    return raw


async def discard(session, upload_id, account_id):
    await session.execute(delete(TrajectoryUpload).where(
        TrajectoryUpload.id == upload_id, TrajectoryUpload.account_id == account_id))
