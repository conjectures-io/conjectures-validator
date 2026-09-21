"""The submission queue and the leaderboard."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any, cast

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from . import clock, models
from conjectures_subnet.db.engine import session_scope
from .registrations import NoSlot, RegistrationsDb, _available_slots
from .status import PENDING, SubmissionState

logger = logging.getLogger(__name__)

# The columns the gate fills in; anything else a caller passes to finish() is refused
# rather than silently dropped.
SCORE_FIELDS = frozenset(
    {
        "exit_code",
        "report",
        "raw_bytes",
        "incumbent_bytes",
        "bytes",
        "incumbent_seconds",
        "parse_seconds",
        "time_ratio",
    }
)


class SubmissionsDb:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions
        self._registrations = RegistrationsDb(sessions)

    # --- writing --------------------------------------------------------------
    def add(self, hotkey: str, digest: str) -> tuple[int, bool]:
        """Queue a submission. The same files from the same hotkey return the same id.

        Returns (id, fresh). `fresh` is False for a resubmission, which is what tells the
        API not to rewrite the stored files -- an idempotent submit, unchanged since the
        competition's first service.
        """
        with session_scope(self._sessions) as session:
            stmt = (
                insert(models.Submission)
                .values(
                    hotkey=hotkey,
                    digest=digest,
                    submitted_at=clock.now(),
                    state=SubmissionState.QUEUED.value,
                )
                .on_conflict_do_nothing(index_elements=["hotkey", "digest"])
                .returning(models.Submission.id)
            )
            fresh_id = session.execute(stmt).scalar_one_or_none()
            if fresh_id is not None:
                return int(fresh_id), True
            existing = session.execute(
                select(models.Submission.id).where(
                    models.Submission.hotkey == hotkey, models.Submission.digest == digest
                )
            ).scalar_one()
            return int(existing), False

    def claim_next(self, worker_id: str) -> models.Submission | None:
        """Take the oldest queued submission for this worker, in arrival order.

        SKIP LOCKED is what lets several gate machines drain one queue: each claims a
        different row instead of blocking on the same one. The claim and the state flip
        are one transaction, so a submission is never handed out twice.
        """
        with session_scope(self._sessions) as session:
            row = session.execute(
                select(models.Submission)
                .where(models.Submission.state == SubmissionState.QUEUED.value)
                .order_by(models.Submission.submitted_at, models.Submission.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            ).scalar_one_or_none()
            if row is None:
                return None
            row.state = SubmissionState.VERIFYING.value
            row.worker_id = worker_id
            row.claimed_at = clock.now()
            session.flush()
            session.expunge(row)
            return row

    def finish(self, sub_id: int, state: SubmissionState, **fields: object) -> str:
        """Record the gate's outcome, and -- only on acceptance -- spend a registration.

        The claim runs in this transaction, so a submission is charged exactly when it is
        marked accepted or not at all. A hotkey that somehow reaches acceptance with no
        slot left is rejected instead of being paid for free: the entitlement is the
        scarce thing, and failing closed is the only safe direction.
        """
        unknown = set(fields) - SCORE_FIELDS
        if unknown:
            raise ValueError(f"not submission columns: {sorted(unknown)}")
        with session_scope(self._sessions) as session:
            row = session.get(models.Submission, sub_id, with_for_update=True)
            if row is None:
                raise LookupError(f"no submission {sub_id}")
            for key, value in fields.items():
                setattr(row, key, value)
            row.finished_at = clock.now()
            if state is SubmissionState.ACCEPTED:
                try:
                    registration = self._registrations.claim_slot(session, row.hotkey, sub_id)
                except NoSlot:
                    row.state = SubmissionState.REJECTED.value
                    row.report = (row.report or "") + (
                        "\nREJECTED: accepted by the gate, but this hotkey has no unclaimed "
                        "registration left. One registration buys one accepted submission; "
                        "register again to submit again.\n"
                    )
                    logger.warning("submission %d accepted with no slot left; rejected", sub_id)
                    return SubmissionState.REJECTED.value
                logger.info("submission %d spent registration %d", sub_id, registration)
            row.state = state.value
            return state.value

    def requeue(self, sub_id: int) -> None:
        # Put a submission back at the head of the queue without charging it: the gate
        # hit a validator-side error, which is not the miner's fault.
        with session_scope(self._sessions) as session:
            session.execute(
                update(models.Submission)
                .where(models.Submission.id == sub_id)
                .values(state=SubmissionState.QUEUED.value, worker_id=None, claimed_at=None)
            )

    def requeue_stale(self, older_than_seconds: float) -> int:
        """Requeue anything left mid-gate by a worker that died. Returns how many.

        A crashed worker leaves its row in `verifying` with nobody running it; nothing
        else would ever pick it up. `older_than_seconds` should exceed the gate's own
        timeout so a slow but live verification is never stolen from under it.
        """
        cutoff = clock.now() - dt.timedelta(seconds=older_than_seconds)
        with session_scope(self._sessions) as session:
            result = session.execute(
                update(models.Submission)
                .where(
                    models.Submission.state == SubmissionState.VERIFYING.value,
                    models.Submission.claimed_at < cutoff,
                )
                .values(state=SubmissionState.QUEUED.value, worker_id=None, claimed_at=None)
            )
            return int(cast("CursorResult[Any]", result).rowcount or 0)

    # --- reading --------------------------------------------------------------
    def get(self, sub_id: int) -> models.Submission | None:
        with session_scope(self._sessions) as session:
            row = session.get(models.Submission, sub_id)
            if row is not None:
                session.expunge(row)
            return row

    def pending_from(self, hotkey: str) -> int:
        # How many of this hotkey's submissions are queued or being verified.
        with session_scope(self._sessions) as session:
            return int(
                session.execute(
                    select(func.count())
                    .select_from(models.Submission)
                    .where(
                        models.Submission.hotkey == hotkey,
                        models.Submission.state.in_([s.value for s in PENDING]),
                    )
                ).scalar_one()
            )

    def may_queue(self, hotkey: str) -> tuple[bool, int, int]:
        """(allowed, slots, pending) for this hotkey, read in one transaction.

        A miner may not queue more work than they can pay for: verifying a submission
        costs the validator the better part of an hour, so the gate is not a free
        service. The slot is not taken here -- only acceptance spends one.
        """
        with session_scope(self._sessions) as session:
            slots = _available_slots(session, hotkey)
            pending = int(
                session.execute(
                    select(func.count())
                    .select_from(models.Submission)
                    .where(
                        models.Submission.hotkey == hotkey,
                        models.Submission.state.in_([s.value for s in PENDING]),
                    )
                ).scalar_one()
            )
            return pending < slots, slots, pending

    def queue_depth(self) -> int:
        with session_scope(self._sessions) as session:
            return int(
                session.execute(
                    select(func.count())
                    .select_from(models.Submission)
                    .where(models.Submission.state.in_([s.value for s in PENDING]))
                ).scalar_one()
            )

    def leaderboard(self) -> list[models.Submission]:
        """Every hotkey's best accepted submission, fewest bytes first.

        DISTINCT ON picks each hotkey's best row; the final ordering is applied after,
        because Postgres requires DISTINCT ON's leading ORDER BY term to be the
        distinct key. Ties go to the earlier submission, then the lower id -- the order
        the competition has always ranked by, and the one the rules promise.
        """
        with session_scope(self._sessions) as session:
            rows = (
                session.execute(
                    select(models.Submission)
                    .distinct(models.Submission.hotkey)
                    .where(
                        models.Submission.state == SubmissionState.ACCEPTED.value,
                        models.Submission.bytes.is_not(None),
                    )
                    .order_by(
                        models.Submission.hotkey,
                        models.Submission.bytes,
                        models.Submission.submitted_at,
                        models.Submission.id,
                    )
                )
                .scalars()
                .all()
            )
            ordered = sorted(rows, key=lambda r: (r.bytes, r.submitted_at, r.id))
            for row in ordered:
                session.expunge(row)
            return ordered

    def latest_incumbent_bytes(self) -> int | None:
        # The incumbent's total as the most recent report measured it. It moves when the
        # operator promotes a new incumbent, so the newest measurement is the right one.
        with session_scope(self._sessions) as session:
            return session.execute(
                select(models.Submission.incumbent_bytes)
                .where(models.Submission.incumbent_bytes.is_not(None))
                .order_by(models.Submission.id.desc())
                .limit(1)
            ).scalar_one_or_none()
