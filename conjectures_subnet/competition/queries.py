"""The competition store, as the API drives it: async, one session per request.

The repositories in `submissions.py` and `registrations.py` open their own transactions and
are what the gate worker uses; those are sync because a worker's life is a forty-five-minute
blocking subprocess with nothing to await. The API cannot use them -- it drives an async
engine -- so this module is the other half of that pair.

The difference is only in execution. Every query comes from `_statements`, so the ranking the
leaderboard promises and the entitlement rule the refusals enforce have one definition, not
two that can drift. Functions here take the session rather than owning one, matching
`conjectures_subnet/db`: the request dependency owns the scope, and the handler commits.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from . import _statements as q
from . import clock, models
from .scoring import ScoredSubmission
from .ratelimit import window_start


async def queue_depth(session: AsyncSession) -> int:
    return int((await session.execute(q.queue_depth())).scalar_one())


async def is_registered(session: AsyncSession, hotkey: str) -> bool:
    return (await session.execute(q.is_registered(hotkey))).first() is not None


async def registered_by(session: AsyncSession, *, hotkey: str, coldkey: str) -> bool:
    """Whether `coldkey` is the coldkey that registered `hotkey` on the subnet."""
    return (
        await session.execute(q.registration_for(hotkey, coldkey))
    ).first() is not None


async def available_slots(session: AsyncSession, hotkey: str) -> int:
    return int((await session.execute(q.available_slots(hotkey))).scalar_one())


async def may_queue(session: AsyncSession, hotkey: str) -> tuple[bool, int, int]:
    """(allowed, slots, pending) for this hotkey.

    A miner may not queue more work than they can pay for. The slot is *not* taken here --
    only acceptance spends one -- so a submission the gate rejects costs the miner nothing,
    which is what keeps a failed attempt at a hard problem from being punished.
    """
    slots = await available_slots(session, hotkey)
    pending = int((await session.execute(q.pending_count(hotkey))).scalar_one())
    return pending < slots, slots, pending


async def add_submission(
    session: AsyncSession,
    *,
    hotkey: str,
    digest: str,
    parse_source: bytes,
    proof_source: bytes,
    account_id: uuid.UUID | None = None,
    now: dt.datetime | None = None,
) -> tuple[int, bool]:
    """Queue a submission. Returns (id, fresh); `fresh` is False for a resubmission.

    Does not commit: the caller owns the transaction, because a submission that is written
    and then fails a later check must not survive the request.
    """
    inserted = (
        await session.execute(
            q.insert_submission(
                hotkey=hotkey,
                digest=digest,
                submitted_at=now or clock.now(),
                parse_source=parse_source,
                proof_source=proof_source,
                account_id=account_id,
            )
        )
    ).scalar_one_or_none()
    if inserted is not None:
        return int(inserted), True
    existing = (
        await session.execute(q.submission_id_by_digest(hotkey, digest))
    ).scalar_one()
    return int(existing), False


async def find_submission(
    session: AsyncSession, *, hotkey: str, digest: str
) -> models.Submission | None:
    """This hotkey's existing submission of these exact files, if it has one."""
    return (
        await session.execute(q.submission_by_digest(hotkey, digest))
    ).scalar_one_or_none()


async def get_submission(session: AsyncSession, sub_id: int) -> models.Submission | None:
    return (await session.execute(q.submission_by_id(sub_id))).scalar_one_or_none()


async def submissions_for_account(
    session: AsyncSession,
    account_id: uuid.UUID,
    *,
    after: tuple[dt.datetime, int] | None = None,
    limit: int | None = None,
) -> list[models.Submission]:
    rows = (
        await session.execute(
            q.submissions_for_account(account_id, after=after, limit=limit)
        )
    ).scalars().all()
    return list(rows)


async def submissions_page(
    session: AsyncSession,
    *,
    after: tuple[dt.datetime, int] | None = None,
    limit: int | None = None,
    state: str | None = None,
    hotkey: str | None = None,
) -> list[models.Submission]:
    rows = (
        await session.execute(
            q.submissions_page(after=after, limit=limit, state=state, hotkey=hotkey)
        )
    ).scalars().all()
    return list(rows)


async def stuck_submissions(
    session: AsyncSession,
    *,
    claimed_before: dt.datetime,
    after: tuple[dt.datetime, int] | None = None,
    limit: int | None = None,
) -> list[tuple[models.Submission, bool]]:
    """(submission, has_sources) for what the gate could not finish.

    The flag travels beside the row rather than on it because the two source columns are
    deferred: an operator needs to know whether a submission can be run again, not to
    receive the megabyte that would answer it.
    """
    rows = (
        await session.execute(
            q.stuck_submissions(claimed_before=claimed_before, after=after, limit=limit)
        )
    ).all()
    return [(row[0], bool(row[1])) for row in rows]


async def submission_for_operator(
    session: AsyncSession, sub_id: int
) -> tuple[models.Submission, bool] | None:
    row = (await session.execute(q.submission_for_operator(sub_id))).first()
    return (row[0], bool(row[1])) if row is not None else None


async def requeue(session: AsyncSession, sub_id: int) -> bool:
    """Put a stuck submission back in the queue. False when it was not one to requeue.

    Does not commit: the caller owns the transaction, as everywhere else in this module.
    """
    return (
        await session.execute(q.requeue_submission(sub_id))
    ).scalar_one_or_none() is not None


async def submission_sources(
    session: AsyncSession, sub_id: int
) -> tuple[bytes, bytes] | None:
    """The two uploaded files, or None when the row has neither.

    Both or neither: `ck_submissions_sources_paired` is what makes that true in the
    database, so a half-answer is not a case this has to represent. Rows written before the
    files moved into the database have neither, which is why this is still optional.
    """
    row = (await session.execute(q.submission_sources(sub_id))).first()
    if row is None or row[0] is None or row[1] is None:
        return None
    return bytes(row[0]), bytes(row[1])


async def counts_by_state(session: AsyncSession) -> dict[str, int]:
    rows = (await session.execute(q.state_counts())).all()
    return {state: int(count) for state, count in rows}


async def counts_by_state_for_hotkey(
    session: AsyncSession, hotkey: str
) -> dict[str, int]:
    rows = (await session.execute(q.state_counts(hotkey))).all()
    return {state: int(count) for state, count in rows}


async def best_for_hotkey(
    session: AsyncSession, hotkey: str
) -> models.Submission | None:
    return (
        await session.execute(q.best_for_hotkey(hotkey))
    ).scalar_one_or_none()


async def rank_before(
    session: AsyncSession, point: tuple[int, dt.datetime, int]
) -> int:
    """How many leaderboard entries a page starting after `point` has already skipped."""
    return int((await session.execute(q.leaderboard_position(point))).scalar_one())


async def rank_of(session: AsyncSession, row: models.Submission) -> int:
    """One submission's absolute rank on the board, 1-based."""
    return int(
        (
            await session.execute(
                q.leaderboard_position((row.bytes or 0, row.submitted_at, row.id))
            )
        ).scalar_one()
    )


async def competitor_count(session: AsyncSession) -> int:
    return int((await session.execute(q.competitor_count())).scalar_one())


async def best_bytes(session: AsyncSession) -> int | None:
    return (await session.execute(q.best_bytes())).scalar_one_or_none()


async def last_accepted_at(session: AsyncSession) -> dt.datetime | None:
    return (await session.execute(q.last_accepted_at())).scalar_one_or_none()


async def scorable_best_per_hotkey(session: AsyncSession) -> list[ScoredSubmission]:
    """Each hotkey's best scorable accepted submission -- one competitor, one point.

    Shares `_scorable` and `_to_scored` with the sync repository, so what the API scores
    and what the operator's tooling scores cannot come from two different filters.
    """
    from sqlalchemy import select

    from .scoring import _scorable, _to_scored

    rows = (
        await session.execute(
            _scorable(
                select(models.Submission)
                .distinct(models.Submission.hotkey)
                .order_by(
                    models.Submission.hotkey,
                    models.Submission.bytes,
                    models.Submission.submitted_at,
                    models.Submission.id,
                )
            )
        )
    ).scalars().all()
    return [_to_scored(row) for row in rows]


async def scorable_history(session: AsyncSession) -> list[ScoredSubmission]:
    """Every scorable accepted submission, oldest first.

    The improvement component walks this in order to find which submissions actually moved
    the record, so the ordering is part of the result rather than a display choice.
    """
    from sqlalchemy import select

    from .scoring import _scorable, _to_scored

    rows = (
        await session.execute(
            _scorable(select(models.Submission)).order_by(
                models.Submission.submitted_at, models.Submission.id
            )
        )
    ).scalars().all()
    return [_to_scored(row) for row in rows]


async def leaderboard(
    session: AsyncSession,
    *,
    after: tuple[int, dt.datetime, int] | None = None,
    limit: int | None = None,
) -> list[models.Submission]:
    """The standings, already ranked. `limit=None` is the whole board."""
    rows = (
        await session.execute(q.leaderboard(after=after, limit=limit))
    ).scalars().all()
    return list(rows)


async def latest_incumbent_bytes(session: AsyncSession) -> int | None:
    return (await session.execute(q.latest_incumbent_bytes())).scalar_one_or_none()


async def hit_rate_limit(
    session: AsyncSession, subject: str, *, limit: int, window_seconds: int
) -> tuple[bool, int]:
    """Count one attempt against `subject`. Returns (allowed, hits_in_window).

    In the database rather than in a dict because an in-process counter is a limit per
    process per uptime: two API replicas would admit twice the real limit and a restart
    would clear it. `submission_api.ratelimit.SlidingWindowLimiter` still applies per IP in
    the middleware; this one bounds a *hotkey* across every replica, which is the bound
    that actually costs the validator gate time.
    """
    start = window_start(clock.now(), window_seconds)
    hits = int((await session.execute(q.touch_rate_window(subject, start))).scalar_one())
    return hits <= limit, hits
