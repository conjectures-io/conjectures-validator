"""Keyset pagination for the competition surface.

The competition's own, separate from `routers/results.py` and the proofs feeds, because
the two surfaces page different tables by different keys. A proofs row is keyed by
`(created_at, uuid)`; a competition submission's id is a BIGINT, and its leaderboard is
ranked by `(bytes, submitted_at, id)` rather than by arrival. Neither shape fits the other.

What is shared, deliberately, is the signing: `encode_parts`/`decode_parts` in
`submission_api/pagination.py`, keyed by the same `PUBLIC_CURSOR_SECRET`. One HMAC
scheme and one secret to rotate is the point of having a signing primitive at all, and a
second implementation here would be a second place for "a tampered cursor is one clean
400" to stop being true.

Two cursor shapes, one version tag each. A cursor issued for the board is refused by the
submission feed rather than decoded into the feed's arguments -- both are signed by this
deployment, so without the tag the signature check alone would pass it, and the three-part
rank cursor would answer with a plausible-looking wrong page.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, TypeVar

from fastapi import Query

from conjectures_subnet.competition import models as competition_models
from submission_api.pagination import decode_parts, encode_parts
from submission_api.settings import MAX_PAGE_SIZE

T = TypeVar("T")

LimitQuery = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]
CursorQuery = Annotated[str | None, Query(max_length=256)]

CURSOR_FEED = "cs1"  # (submitted_at, id), newest first
CURSOR_RANK = "cr1"  # (bytes, submitted_at, id), best first


def _micros(moment: datetime) -> str:
    """A timestamp as an integer, so a cursor never depends on how a fraction is formatted."""
    return str(int(moment.astimezone(UTC).timestamp() * 1_000_000))


def _from_micros(value: str) -> datetime:
    return datetime.fromtimestamp(int(value) / 1_000_000, tz=UTC)


def feed_cursor(secret: str, row: competition_models.Submission) -> str:
    return encode_parts(
        secret, version=CURSOR_FEED, parts=(_micros(row.submitted_at), str(row.id))
    )


def feed_after(secret: str, cursor: str | None) -> tuple[datetime, int] | None:
    if not cursor:
        return None
    moment, sub_id = decode_parts(secret, cursor, version=CURSOR_FEED, count=2)
    return _from_micros(moment), int(sub_id)


def rank_cursor(secret: str, row: competition_models.Submission) -> str:
    return encode_parts(
        secret,
        version=CURSOR_RANK,
        parts=(str(row.bytes), _micros(row.submitted_at), str(row.id)),
    )


def rank_after(secret: str, cursor: str | None) -> tuple[int, datetime, int] | None:
    if not cursor:
        return None
    size, moment, sub_id = decode_parts(secret, cursor, version=CURSOR_RANK, count=3)
    return int(size), _from_micros(moment), int(sub_id)


def split_page(rows: list[T], limit: int) -> tuple[list[T], bool]:
    """One page, and whether another follows.

    The handlers read `limit + 1` rows and discard the extra. That is what makes
    `next_cursor` null exactly when the feed is exhausted, rather than handing back a cursor
    that turns out to address an empty page -- so a client loops until null instead of
    comparing counts.
    """
    return rows[:limit], len(rows) > limit


__all__ = [
    "CURSOR_FEED",
    "CURSOR_RANK",
    "CursorQuery",
    "LimitQuery",
    "feed_after",
    "feed_cursor",
    "rank_after",
    "rank_cursor",
    "split_page",
]
