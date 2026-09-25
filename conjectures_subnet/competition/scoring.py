"""What the scorer reads, and what it writes back.

The reads are deliberately narrow: the scorer needs each competing hotkey's position on
the (time, ratio) plane and the history of accepted submissions that improved on the
record. The writes are the audit trail -- the vector that was set and the per-hotkey
reasoning behind it.
"""

from __future__ import annotations

import dataclasses as dc
import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from . import models
from conjectures_subnet.db.engine import session_scope
from .status import SubmissionState


@dc.dataclass(frozen=True, slots=True)
class ScoredSubmission:
    """One accepted submission, reduced to what scoring actually uses.

    `time_s` and `ratio_pct` are the Pareto axes, both "lower is better". `ratio_pct` is
    compressed bytes as a percentage of raw, which is why raw_bytes has to be recorded:
    absolute bytes are not comparable across corpora, and the corpus changes between
    rounds.
    """

    submission_id: int
    hotkey: str
    bytes: int
    raw_bytes: int
    time_s: float
    # The incumbent as this submission's own run measured it. Both are needed: the bytes
    # are the record the improvement component measures progress against (and they move
    # when an operator promotes a new incumbent), and the seconds set the speed floor
    # that is the frontier's time boundary.
    incumbent_bytes: int
    incumbent_seconds: float
    submitted_at: dt.datetime

    @property
    def ratio_pct(self) -> float:
        return 100.0 * self.bytes / self.raw_bytes


def _scorable(stmt):
    # Only accepted submissions carrying every number the frontier needs. A row missing
    # one of them predates the columns or came from a harness that did not print it;
    # scoring it would put a fabricated point on the frontier.
    return stmt.where(
        models.Submission.state == SubmissionState.ACCEPTED.value,
        models.Submission.bytes.is_not(None),
        models.Submission.raw_bytes.is_not(None),
        models.Submission.raw_bytes > 0,
        models.Submission.parse_seconds.is_not(None),
        models.Submission.parse_seconds > 0,
        models.Submission.incumbent_seconds.is_not(None),
        models.Submission.incumbent_bytes.is_not(None),
    )


def _to_scored(row: models.Submission) -> ScoredSubmission:
    # Every column below is nullable on the model and non-null here: `_scorable` is the
    # only way a row reaches this function, and it filters out each one. The asserts are
    # that filter restated where the types are checked -- if the filter is ever loosened,
    # this fails loudly instead of putting a half-measured point on the frontier.
    assert row.bytes is not None, "_scorable filters bytes IS NULL"
    assert row.raw_bytes is not None, "_scorable filters raw_bytes IS NULL"
    assert row.parse_seconds is not None, "_scorable filters parse_seconds IS NULL"
    assert row.incumbent_seconds is not None, "_scorable filters incumbent_seconds IS NULL"
    return ScoredSubmission(
        submission_id=row.id,
        hotkey=row.hotkey,
        bytes=row.bytes,
        raw_bytes=row.raw_bytes,
        time_s=row.parse_seconds,
        incumbent_bytes=row.incumbent_bytes,
        incumbent_seconds=row.incumbent_seconds,
        submitted_at=row.submitted_at,
    )


class ScoringDb:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def best_per_hotkey(self) -> list[ScoredSubmission]:
        """Each hotkey's best accepted submission -- one competitor, one point.

        "Best" is fewest bytes, earliest submission breaking a tie: the same rule the
        leaderboard ranks by, so what a miner sees ranked is what gets scored.
        """
        with session_scope(self._sessions) as session:
            rows = (
                session.execute(
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
                .scalars()
                .all()
            )
            return [_to_scored(r) for r in rows]

    def accepted_history(self) -> list[ScoredSubmission]:
        # Every scorable accepted submission, oldest first: the improvement component
        # walks this in order to find which ones actually moved the record.
        with session_scope(self._sessions) as session:
            rows = (
                session.execute(
                    _scorable(select(models.Submission)).order_by(
                        models.Submission.submitted_at, models.Submission.id
                    )
                )
                .scalars()
                .all()
            )
            return [_to_scored(r) for r in rows]

    def record_weight_set(
        self,
        *,
        netuid: int,
        block: int,
        uids: list[int],
        weights: list[float],
        summary: str | None,
        accepted: bool,
        dry_run: bool = False,
        error: str | None = None,
        snapshots: list[dict[str, object]] | None = None,
    ) -> int:
        """Persist one weight vector and the per-hotkey reasoning behind it.

        Both in one transaction: a vector whose explanation went missing is not an audit
        trail. Returns the weight_sets id.
        """
        with session_scope(self._sessions) as session:
            row = models.WeightSet(
                netuid=netuid,
                block=block,
                uids=list(uids),
                weights=[float(w) for w in weights],
                summary=summary,
                accepted=accepted,
                dry_run=dry_run,
                error=error,
            )
            session.add(row)
            session.flush()
            for snap in snapshots or []:
                session.add(models.ScoreSnapshot(weight_set_id=row.id, **snap))
            return int(row.id)
