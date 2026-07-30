"""Translation between the verifier's digest strings and the schema's raw bytes.

`verifier/hashing.py` produces `sha256:<64 hex>` and every commitment published to miners
uses that form. The `sha256` domain in `deploy/migrate/sql` stores raw 32 bytes instead, so
a comparison never depends on hex case or on the prefix being present.

Both directions live here so the conversion happens exactly once, at the database edge, and
no caller has to remember which representation it is holding.
"""

from __future__ import annotations

import re


DIGEST_BYTES = 32
PREFIX = "sha256:"
HEX = re.compile(r"^[0-9a-f]{64}$")


class DigestError(ValueError):
    """A digest is not a lowercase sha256 value in either accepted representation."""


def to_bytes(digest: str) -> bytes:
    """`sha256:<hex>` or bare lowercase hex to the 32 raw bytes the schema stores."""
    if not isinstance(digest, str):
        raise DigestError("digest must be a string")
    candidate = digest[len(PREFIX) :] if digest.startswith(PREFIX) else digest
    if HEX.fullmatch(candidate) is None:
        raise DigestError(f"not a lowercase sha256 digest: {digest!r}")
    return bytes.fromhex(candidate)


def to_prefixed(raw: bytes | memoryview | None) -> str | None:
    """The 32 raw bytes back to the `sha256:<hex>` form used in reports and responses."""
    if raw is None:
        return None
    value = bytes(raw)
    if len(value) != DIGEST_BYTES:
        raise DigestError(f"stored digest is {len(value)} bytes, expected {DIGEST_BYTES}")
    return f"{PREFIX}{value.hex()}"


def to_hex(raw: bytes | memoryview | None) -> str | None:
    """Bare hex, for the rejection log, which stores digests as unvalidated text."""
    prefixed = to_prefixed(raw)
    return None if prefixed is None else prefixed[len(PREFIX) :]
