"""Strong `ETag` validators for bodies that are stable between requests.

Extracted so that more than one public router can answer `304` the same way. The rule the tag
follows is the one `routers/catalog.py` established and is the whole reason this is shared rather
than reimplemented: **the tag is hashed from the serialised payload, never assembled from the
inputs that built it**. Anything that changes what is published changes the tag, including a field
added to a response model later — so a validator cannot drift from the body it claims to identify.

Correctness does not depend on where the payload came from; usefulness does. A body recomputed from
the database on every request and changing constantly would be tagged honestly and never hit, so
the caller would pay for the hash and get nothing back. Worth attaching only where the body is
stable between requests — an in-memory index, a mirrored snapshot, pool metadata — which is why the
result feeds have no validator and the contribution snapshot does.
"""

from __future__ import annotations

from fastapi import Response
from pydantic import BaseModel

from verifier.hashing import sha256_text

# 128 bits of SHA-256, hex. Far past where an accidental collision between two revisions of one
# endpoint's body is a practical concern, and short enough to keep the header small.
ETAG_HEX_LENGTH = 32


def etag_for(payload: BaseModel) -> str:
    """The strong validator for `payload`, quoted as an HTTP entity-tag."""
    digest = sha256_text(payload.model_dump_json())[len("sha256:") :]
    return '"' + digest[:ETAG_HEX_LENGTH] + '"'


def matches(header: str | None, etag: str) -> bool:
    """Whether an `If-None-Match` header names this entity.

    A list, per RFC 9110, and `*` matches anything. Weak-comparison prefixes are stripped because
    the tag above is strong and a weakened form of it still identifies the same bytes.
    """
    if not header:
        return False
    candidates = [item.strip() for item in header.split(",")]
    return "*" in candidates or any(
        item.removeprefix("W/") == etag for item in candidates
    )


def not_modified(etag: str, cache_control: str) -> Response:
    """The `304` to send instead of a body.

    It must repeat the validator and the caching headers, and must carry no body — a caller that
    revalidates and is told "unchanged" without being told for how long would revalidate again on
    the next read, which is most of what the validator was saving.
    """
    return Response(
        status_code=304, headers={"ETag": etag, "Cache-Control": cache_control}
    )


__all__ = ["ETAG_HEX_LENGTH", "etag_for", "matches", "not_modified"]
