"""Async intake adapter for the competition-owned torchlean schema."""

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from submission_api.errors import Conflict, PaymentRequired, ServiceUnavailable

REQUIRED_COLUMNS = {
    "sources.sha256",
    "sources.content",
    "submissions.registration_id",
    "submissions.source_sha256",
    "submissions.intake_mode",
    "submissions.proof_report",
    "registrations.ss58_hot",
    "registrations.block_date",
    "evaluations.decision",
}


async def compatible(session):
    columns = set(
        (
            await session.execute(
                text("""
        SELECT table_name || '.' || column_name FROM information_schema.columns
        WHERE table_schema=current_schema() AND table_name IN
        ('sources','submissions','registrations','evaluations')
    """)
            )
        ).scalars()
    )
    if not REQUIRED_COLUMNS <= columns:
        raise ServiceUnavailable(
            "TorchLean schema or API grants are unavailable; apply tensor_0002 and platform grants.",
            reason_code="COMPETITION_SCHEMA_UNAVAILABLE",
        )


async def enqueue(session, *, hotkey, kernel, digest, cutoff, dev=False):
    # Also acquired by board activation: a partially received upload cannot cross its boundary.
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('tensor:intake',0))"))
    duplicate = (
        await session.execute(
            text("""
        SELECT id,state FROM submissions WHERE hotkey=:hotkey AND source_sha256=:digest
    """),
            {"hotkey": hotkey, "digest": digest},
        )
    ).first()
    if duplicate:
        raise Conflict(
            "Duplicate submission: this hotkey has already uploaded the same file.",
            reason_code="DUPLICATE_SUBMISSION",
            extra={"submission": duplicate.id, "state": duplicate.state},
        )
    registration = None
    if not dev:
        # Select the latest registration once. Never fall back to an older unused registration.
        registration = (
            await session.execute(
                text("""
            SELECT id,uid,block_date FROM registrations WHERE ss58_hot=:hotkey
            ORDER BY block DESC,id DESC LIMIT 1
        """),
                {"hotkey": hotkey},
            )
        ).first()
        if registration is None:
            raise PaymentRequired(
                "This hotkey is not registered on the subnet.", reason_code="NOT_REGISTERED"
            )
        current = await session.scalar(
            text("""
            SELECT id FROM registrations WHERE uid=:uid ORDER BY block DESC,id DESC LIMIT 1
        """),
            {"uid": registration.uid},
        )
        if current != registration.id:
            raise PaymentRequired(
                "This hotkey is no longer registered on the subnet.", reason_code="NOT_REGISTERED"
            )
        if cutoff is None:
            raise ServiceUnavailable("TorchLean registration cutoff is not configured")
        if registration.block_date <= cutoff:
            raise PaymentRequired(
                "The latest registration must be after the TorchLean cutoff.",
                reason_code="REGISTRATION_BEFORE_CUTOFF",
            )
        if await session.scalar(
            text("SELECT EXISTS(SELECT 1 FROM submissions WHERE registration_id=:id)"),
            {"id": registration.id},
        ):
            raise PaymentRequired(
                "This registration has already been used for a TorchLean submission.",
                reason_code="NO_ENTITLEMENT",
            )
    await session.execute(
        text("""
        INSERT INTO sources(sha256,content) VALUES (:digest,:content) ON CONFLICT DO NOTHING
    """),
        {"digest": digest, "content": kernel},
    )
    stored = await session.scalar(
        text("SELECT content FROM sources WHERE sha256=:digest"), {"digest": digest}
    )
    if stored is None or bytes(stored) != kernel:
        raise ServiceUnavailable("Stored source does not match its digest")
    try:
        sid = await session.scalar(
            text("""
            INSERT INTO submissions(hotkey,registration_id,source_sha256,intake_mode)
            VALUES (:hotkey,:registration,:digest,:mode) RETURNING id
        """),
            {
                "hotkey": hotkey,
                "registration": registration.id if registration else None,
                "digest": digest,
                "mode": "dev" if dev else "subnet",
            },
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == "submissions_registration_id_key":
            raise PaymentRequired(
                "This registration has already been used.", reason_code="NO_ENTITLEMENT"
            ) from exc
        raise
    return sid
