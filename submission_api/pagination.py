"""Keyset pagination with opaque, signed cursors.

Two decisions, both about a feed an anonymous caller can page through as far as they like.

**Keyset, not OFFSET.** `ORDER BY created_at DESC, id DESC LIMIT n` with a `(created_at, id) <
(cursor)` predicate reads one index range whatever page you are on. `OFFSET 50000` reads and
discards fifty thousand rows, so a public endpoint with an integer page parameter hands an
anonymous caller a cheap way to make the database do expensive work. It is also correct under
concurrent inserts, which an offset is not: a result certified between two page reads shifts
every subsequent offset by one and silently hides a row.

**Signed, not just encoded.** The cursor is a timestamp and a UUID — nothing secret, so the
signature is not hiding anything. It is there so the handler never parses attacker-chosen values
into a query predicate, and so a tampered cursor is one clean `400` instead of a
`ValueError`/`DataError` from inside SQLAlchemy. `hmac.compare_digest` does the comparison, and
the key is `PUBLIC_CURSOR_SECRET`, which production must set.

The tuple is `(created_at, id)` rather than `created_at` alone because `created_at` is not
unique — two submissions committed in the same transaction share it, and a cursor on the
timestamp alone would either repeat or skip them.
"""

from __future__ import annotations

import base64
import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from submission_api.errors import BadRequest

CURSOR_VERSION = "1"
# 16 bytes of SHA-256 is 128 bits of tag, which is far more than needed to stop tampering with
# a value that is not secret, and keeps the cursor short enough to sit in a URL.
SIGNATURE_BYTES = 16
MAX_CURSOR_LENGTH = 256

REASON_INVALID_CURSOR = "INVALID_CURSOR"


@dataclass(frozen=True)
class Cursor:
    """The position of the last item on the page just served."""

    created_at: datetime
    id: uuid.UUID

    def encode(self, secret: str) -> str:
        # Microseconds since the epoch: an integer, so encoding never depends on how a
        # timezone or a fractional second is formatted.
        micros = int(self.created_at.astimezone(UTC).timestamp() * 1_000_000)
        payload = f"{CURSOR_VERSION}.{micros}.{self.id}"
        return f"{_b64encode(payload.encode())}.{_sign(payload, secret)}"


def encode_cursor(secret: str, *, created_at: datetime, id: uuid.UUID) -> str:
    return Cursor(created_at=created_at, id=id).encode(secret)


def decode_cursor(secret: str, value: str) -> Cursor:
    """Parse a cursor this deployment issued, or raise `BadRequest`.

    Every failure is the same rejection with the same reason code: a client has nothing to
    learn from being told whether their cursor was the wrong shape or the wrong signature.
    """
    if not value or len(value) > MAX_CURSOR_LENGTH:
        raise _invalid()
    payload_b64, _, signature = value.partition(".")
    if not signature:
        raise _invalid()
    try:
        payload = _b64decode(payload_b64).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise _invalid() from exc
    # Signature first: nothing below this line parses a value that was not signed here.
    if not hmac.compare_digest(signature, _sign(payload, secret)):
        raise _invalid()
    version, _, rest = payload.partition(".")
    if version != CURSOR_VERSION:
        raise _invalid()
    micros_text, _, identifier = rest.partition(".")
    try:
        created_at = datetime.fromtimestamp(int(micros_text) / 1_000_000, tz=UTC)
        return Cursor(created_at=created_at, id=uuid.UUID(identifier))
    except (ValueError, OSError, OverflowError) as exc:
        raise _invalid() from exc


def _invalid() -> BadRequest:
    return BadRequest("cursor is not one this API issued", reason_code=REASON_INVALID_CURSOR)


def _sign(payload: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), "sha256").digest()
    return _b64encode(digest[:SIGNATURE_BYTES])


def _b64encode(raw: bytes) -> str:
    """URL-safe and unpadded, so a cursor needs no escaping in a query string."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


__all__ = [
    "MAX_CURSOR_LENGTH",
    "REASON_INVALID_CURSOR",
    "Cursor",
    "decode_cursor",
    "encode_cursor",
]
