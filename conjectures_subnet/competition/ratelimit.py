"""A fixed-window rate limiter that lives in Postgres, not in a process's memory."""

from __future__ import annotations

import datetime as dt
from typing import Any, cast

from sqlalchemy import CursorResult, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from . import clock, models
from conjectures_subnet.db.engine import session_scope


def window_start(t: dt.datetime, seconds: int) -> dt.datetime:
    # Floor `t` to the start of its window, so every process agrees on which window a
    # request falls in without coordinating.
    epoch = int(t.timestamp()) // seconds * seconds
    return dt.datetime.fromtimestamp(epoch, tz=dt.timezone.utc)


class RateLimiter:
    """Counts writes per subject per window, atomically.

    In the database rather than in a dict because an in-process counter is a limit per
    process per uptime: two API workers double the real limit, and a restart clears it.
    The INSERT ... ON CONFLICT DO UPDATE ... RETURNING is one statement, so concurrent
    requests cannot both read the same count and both decide they are under it.
    """

    def __init__(self, sessions: sessionmaker[Session], *, limit: int, window_seconds: int) -> None:
        self._sessions = sessions
        self._limit = limit
        self._window = window_seconds

    @property
    def limit(self) -> int:
        return self._limit

    def hit(self, subject: str) -> tuple[bool, int]:
        """Count one attempt against `subject`. Returns (allowed, hits_in_window).

        The attempt is counted whether or not it is allowed: a caller hammering the
        endpoint stays refused for the rest of the window instead of being let through
        the moment they stop being counted.
        """
        start = window_start(clock.now(), self._window)
        with session_scope(self._sessions) as session:
            stmt = (
                insert(models.RateLimitWindow)
                .values(subject=subject, window_start=start, hits=1)
                .on_conflict_do_update(
                    index_elements=["subject", "window_start"],
                    set_={"hits": models.RateLimitWindow.hits + 1},
                )
                .returning(models.RateLimitWindow.hits)
            )
            hits = int(session.execute(stmt).scalar_one())
            return hits <= self._limit, hits

    def peek(self, subject: str) -> int:
        # Hits so far in the current window, without counting one.
        start = window_start(clock.now(), self._window)
        with session_scope(self._sessions) as session:
            return int(
                session.execute(
                    select(models.RateLimitWindow.hits).where(
                        models.RateLimitWindow.subject == subject,
                        models.RateLimitWindow.window_start == start,
                    )
                ).scalar_one_or_none()
                or 0
            )

    def prune(self, keep_windows: int = 4) -> int:
        # Drop counters for windows that can no longer be current. Cheap, and it keeps
        # the table from growing without bound over a long-running round.
        cutoff = window_start(clock.now(), self._window) - dt.timedelta(
            seconds=self._window * keep_windows
        )
        with session_scope(self._sessions) as session:
            result = session.execute(
                delete(models.RateLimitWindow).where(models.RateLimitWindow.window_start < cutoff)
            )
            return int(cast("CursorResult[Any]", result).rowcount or 0)
