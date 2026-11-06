"""Bounded-memory storage for private packages larger than PostgreSQL's field limit."""
import hashlib
import shutil
import tempfile

import anyio
from sqlalchemy import delete, insert, select
from starlette.responses import StreamingResponse

from conjectures_subnet.db.trajectory_models import TrajectoryClaim, TrajectoryPackageChunk
from submission_api.errors import Conflict, NotFound, TooManyRequests

CHUNK_BYTES = 8 * 1024 * 1024
INLINE_BYTES = 256 * 1024 * 1024


def require_scratch_space(size_hint=0):
    if shutil.disk_usage(tempfile.gettempdir()).free < size_hint + 6 * 1024 ** 3:
        raise TooManyRequests("Chat-log scratch storage is busy; retry later.", reason_code="TRAJECTORY_BUSY")


class FilePackage:
    def __init__(self, size_hint=0):
        require_scratch_space(size_hint)
        self.file = tempfile.TemporaryFile()
        self.size = 0

    def __len__(self):
        return self.size

    def write(self, data):
        require_scratch_space(len(data))
        self.file.write(data)
        self.size += len(data)

    def chunks(self):
        self.file.seek(0)
        while data := self.file.read(CHUNK_BYTES):
            yield data

    def digest(self):
        digest = hashlib.sha256()
        for data in self.chunks():
            digest.update(data)
        return digest.digest()

    def close(self):
        self.file.close()


def inline(raw):
    return raw if isinstance(raw, bytes) else None


async def clear(session, claim):
    claim.package = None
    claim.package_chunked = False
    await session.execute(delete(TrajectoryPackageChunk).where(TrajectoryPackageChunk.claim_id == claim.id))


async def save(session, claim, raw):
    from submission_api.trajectory_quota import check_capacity
    await check_capacity(session, claim.account_id, len(raw), claim_id=claim.id,
                         upload_id=getattr(raw, "upload_id", None))
    await session.execute(delete(TrajectoryPackageChunk).where(TrajectoryPackageChunk.claim_id == claim.id))
    claim.package = inline(raw)
    claim.package_chunked = isinstance(raw, FilePackage)
    if not claim.package_chunked:
        return
    await session.flush()
    raw.file.seek(0)
    offset = 0
    while data := await anyio.to_thread.run_sync(raw.file.read, CHUNK_BYTES):
        await session.execute(insert(TrajectoryPackageChunk).values(claim_id=claim.id, byte_offset=offset, data=data))
        offset += len(data)
    if offset != len(raw):
        raise Conflict("Chat-log package is incomplete.")


async def download_response(session, claim, headers):
    # Each query fetches one bounded field, after the durable access/export audit commit.
    if not claim.package_chunked:
        from starlette.responses import Response
        content = await session.scalar(select(TrajectoryClaim.package).where(
            TrajectoryClaim.id == claim.id, TrajectoryClaim.package_digest == claim.package_digest,
            TrajectoryClaim.package_chunked.is_(False)))
        if content is None:
            raise NotFound("Chat-log content changed or is unavailable")
        return Response(content, media_type="application/zip", headers=headers)

    async def chunks():
        offset = 0
        digest = hashlib.sha256()
        while True:
            data = await session.scalar(select(TrajectoryPackageChunk.data).join(TrajectoryClaim, TrajectoryClaim.id == TrajectoryPackageChunk.claim_id).where(
                TrajectoryPackageChunk.claim_id == claim.id, TrajectoryPackageChunk.byte_offset == offset,
                TrajectoryClaim.package_digest == claim.package_digest, TrajectoryClaim.package_chunked.is_(True)))
            if data is None:
                break
            digest.update(data)
            offset += len(data)
            yield data
        if digest.digest() != claim.package_digest:
            raise RuntimeError("Chat-log package digest mismatch")

    return StreamingResponse(chunks(), media_type="application/zip", headers=headers)


class EventIndex:
    """Keep duplicate detection and proof timestamps off the heap for long transcripts."""
    def __init__(self):
        import sqlite3
        # SQLite temporary databases use TMPDIR and are removed even if the process exits.
        self.connection = sqlite3.connect("")
        self.connection.execute("PRAGMA page_size=4096")
        self.connection.execute("PRAGMA journal_mode=OFF")
        self.connection.execute("PRAGMA cache_size=-2048")
        self.connection.execute("PRAGMA max_page_count=65536")
        self.connection.execute("CREATE TABLE events (id TEXT PRIMARY KEY, timestamp TEXT) WITHOUT ROWID")

    def __contains__(self, key):
        return self.connection.execute("SELECT 1 FROM events WHERE id = ?", (key,)).fetchone() is not None

    def __setitem__(self, key, value):
        self.connection.execute("INSERT INTO events VALUES (?, ?)", (key, value.isoformat() if value else None))

    def __getitem__(self, key):
        from datetime import datetime
        row = self.connection.execute("SELECT timestamp FROM events WHERE id = ?", (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        return datetime.fromisoformat(row[0]) if row[0] is not None else None

    def close(self):
        self.connection.close()
