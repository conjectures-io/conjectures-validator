"""Signed cursors for competition feeds.

The adapter chooses a row's keyset position (`base.Position`); this signs it and hands it back
unchanged. The signing is `submission_api.pagination.encode_parts`, keyed by the same
`PUBLIC_CURSOR_SECRET` as every other feed.

The version tag names both the feed and the competition. A cursor issued for one competition's
board is then refused by another competition's, and by the same competition's submission feed,
rather than decoded into a plausible wrong page -- all of them are signed by this deployment,
so without the tag the signature alone would pass it.
"""

from __future__ import annotations

from typing import Annotated, Literal, TypeVar

from fastapi import Query

from submission_api.competitions.base import Position
from submission_api.pagination import decode_parts, encode_parts
from submission_api.settings import MAX_PAGE_SIZE

T = TypeVar("T")

LimitQuery = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]
CursorQuery = Annotated[str | None, Query(max_length=512)]

Feed = Literal["rank", "feed", "ops"]


def _version(feed: Feed, slug: str) -> str:
    return f"c1-{feed}-{slug}"


def encode(secret: str, feed: Feed, slug: str, position: Position) -> str:
    return encode_parts(secret, version=_version(feed, slug), parts=position)


def decode(secret: str, feed: Feed, slug: str, cursor: str | None) -> Position | None:
    """The position a cursor carries. Its shape is the adapter's to check, not ours."""
    if not cursor:
        return None
    return decode_parts(secret, cursor, version=_version(feed, slug), count=None)


def split_page(rows: list[T], limit: int) -> tuple[list[T], bool]:
    """One page, and whether another follows. Callers read `limit + 1` rows."""
    return rows[:limit], len(rows) > limit


__all__ = ["CursorQuery", "Feed", "LimitQuery", "decode", "encode", "split_page"]
