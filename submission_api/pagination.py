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
import re
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


# --- Cursors over keys that are not (timestamp, UUID) --------------------------------------
#
# The pair above is the proofs schema's key everywhere: every public row there has a
# `created_at` and a UUID id. The competition schema does not — its submissions have a BIGINT
# id, and its leaderboard is ranked by `(bytes, submitted_at, id)` rather than by arrival. Two
# more dataclasses beside `Cursor` would be two more copies of the signing, the base64 and the
# five ways a cursor can be malformed, which is the part worth having exactly once.
#
# So the signing is shared and the *shape* is the caller's. `version` is what keeps the shapes
# apart: a cursor issued for one feed fails the version check on another rather than being
# parsed into the wrong number of parts, so a client cannot page a leaderboard with a cursor
# from a submission feed and get a coherent-looking answer.

# Parts travel unescaped in a `.`-joined payload, so they may not contain the separator. Every
# caller passes an integer or a UUID; the check is here so a future one that passes free text
# fails at the point of encoding rather than at the point of decoding, in someone else's page.
_PART = re.compile(r"^[A-Za-z0-9_-]+$")


def encode_parts(secret: str, *, version: str, parts: tuple[str, ...]) -> str:
    """Sign an ordered tuple of scalars as an opaque cursor."""
    if not _PART.match(version):
        raise ValueError(f"not a cursor version: {version!r}")
    for part in parts:
        if not _PART.match(part):
            raise ValueError(f"not a cursor part: {part!r}")
    payload = ".".join((version, *parts))
    return f"{_b64encode(payload.encode())}.{_sign(payload, secret)}"


def decode_parts(secret: str, value: str, *, version: str, count: int) -> tuple[str, ...]:
    """Parse a cursor this deployment issued for `version`, or raise `BadRequest`.

    Every failure is the same rejection `decode_cursor` uses, for the same reason: a client
    has nothing to learn from being told which of the checks it failed.
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
    fields = payload.split(".")
    # The version is checked against what this feed issues, so a validly signed cursor from
    # another feed is refused rather than reinterpreted.
    if len(fields) != count + 1 or fields[0] != version:
        raise _invalid()
    return tuple(fields[1:])


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
    "decode_parts",
    "encode_cursor",
    "encode_parts",
]
