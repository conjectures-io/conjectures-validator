"""The messages a miner's hotkey signs, and the one place each of them is constructed.

Every signature this validator accepts is over a message minted here. There are two:

    canonical_request_digest(...)   authorise one paid submission
    read_digest(...)                read one submission's status or report

Both are 32 raw bytes of SHA-256, and the signature is over those bytes rather than over
their hex spelling. `submission_api/login.py` holds the other three signed messages — the
UTF-8, human-readable ones a *coldkey* signs to sign in, link a hotkey, or claim a deposit.
They cannot collide with these: a 32-byte digest is not valid UTF-8 for any of those
prefixes, and no prefix here is a prefix of one there.

**Why this module exists at all.** The server verifies a signature over a digest it rebuilds
from the request; the miner's client signs a digest it builds from the same values. If the
two constructions ever drift by a byte, every honest submission fails authentication and the
only way to find out is in production. So there is one definition, imported by both sides,
and it lives here rather than in `conjectures_subnet/db/submissions.py` because a miner's
client must be able to sign without SQLAlchemy, a database, or FastAPI installed —
`verifier.hashing` is stdlib-only and is the whole of what this needs.

`conjectures_subnet.db.submissions` re-exports `canonical_request_digest` so server-side
callers can keep importing it from the store they were already using.
"""

from __future__ import annotations

from verifier.hashing import canonical_json_bytes, sha256_bytes

# Domain separation for the read message. Distinct from the intake digest by construction and
# not only by content: that one is SHA-256 over canonical JSON of six fields, this one is
# SHA-256 over a three-part colon-joined string. A read signature can therefore never be
# replayed as an authorisation to submit, which is the point — reads are cheap and frequent,
# and submissions cost 0.5 TAO.
READ_DOMAIN = "conjectures-read-v1"


def canonical_request_digest(
    *,
    hotkey: str,
    task_id: str,
    task_bundle_sha256: str,
    proof_sha256: str,
    payment_reference: str,
    idempotency_key: str,
) -> str:
    """The identity of a request, and the message the miner signs.

    Reusing an idempotency key with any of these values changed is a conflict rather than a
    replay, so every one of them is part of the digest. It binds the proof digest too, so a
    signature cannot be reused for different proof bytes.
    """
    return sha256_bytes(
        canonical_json_bytes(
            {
                "hotkey": hotkey,
                "idempotency_key": idempotency_key,
                "payment_reference": payment_reference,
                "proof_sha256": proof_sha256,
                "task_bundle_sha256": task_bundle_sha256,
                "task_id": task_id,
            }
        )
    )


def read_digest(*, hotkey: str, submission_id: str) -> str:
    """The message a hotkey signs to read one submission's status or report.

    Pins both the reader and the submission, so a signature harvested for one submission
    cannot be used to read another, and one miner's signature cannot read another's row.
    """
    return sha256_bytes(f"{READ_DOMAIN}:{hotkey}:{submission_id}".encode())


__all__ = ["READ_DOMAIN", "canonical_request_digest", "read_digest"]
