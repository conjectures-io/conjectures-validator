"""Serialize admission against staged reservations and retained private package bytes."""
from sqlalchemy import case, func, select, text, union_all

from conjectures_subnet.db.trajectory_models import TrajectoryClaim, TrajectoryPackageChunk, TrajectoryUpload
from submission_api.errors import TooManyRequests

ACCOUNT_BYTES = 20 * 1024 ** 3
GLOBAL_BYTES = 100 * 1024 ** 3
ADMISSION_LOCK = 715302226


async def lock(session):
    if not await session.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": ADMISSION_LOCK}):
        raise TooManyRequests("Chat-log storage is busy; retry shortly.", reason_code="TRAJECTORY_BUSY")


async def check_capacity(session, account_id, byte_length, *, claim_id=None, upload_id=None):
    await lock(session)
    inline = select(TrajectoryClaim.account_id.label("account_id"),
                    func.octet_length(TrajectoryClaim.package).label("bytes")).where(TrajectoryClaim.package.is_not(None))
    chunks = select(TrajectoryClaim.account_id.label("account_id"),
                    func.octet_length(TrajectoryPackageChunk.data).label("bytes")).join(
                        TrajectoryClaim, TrajectoryClaim.id == TrajectoryPackageChunk.claim_id)
    pending = select(TrajectoryUpload.account_id.label("account_id"), TrajectoryUpload.byte_length.label("bytes"))
    if claim_id is not None:
        inline = inline.where(TrajectoryClaim.id != claim_id)
        chunks = chunks.where(TrajectoryClaim.id != claim_id)
    if upload_id is not None:
        pending = pending.where(TrajectoryUpload.id != upload_id)
    usage = union_all(inline, chunks, pending).subquery()
    total, owned = (await session.execute(select(
        func.coalesce(func.sum(usage.c.bytes), 0),
        func.coalesce(func.sum(case((usage.c.account_id == account_id, usage.c.bytes), else_=0)), 0),
    ))).one()
    if owned + byte_length > ACCOUNT_BYTES or total + byte_length > GLOBAL_BYTES:
        raise TooManyRequests("Chat-log storage quota reached; contact support or retry after pending uploads expire.",
                              reason_code="TRAJECTORY_BUSY")
