"""The hotkey signature that authorises one competition submission.

Only the *message* lives here. Verification is `login.verify_signature`, which the coldkey
paths already use: it decodes the SS58 address to a public key, checks the signature, and
answers every failure -- malformed address, malformed signature, valid signature over
different bytes -- with the same refusal and the same reason code, because distinguishing
them tells an attacker which half of their guess was wrong. A second implementation here
would be a second chance to get that wrong.

Two things differ from what the competition service signed before it moved onto the platform,
and each closes a hole rather than expressing a preference.

**The message names the competition.** The old message was
`submit:<digest>:<hotkey>:<timestamp>`, which binds the files, the submitter and the moment
but not *which competition they are for*. One API serves the whole surface now, so a
signature made for one competition would be replayable against another the moment a second
exists. The slug is in the message, so it cannot be.

**The scalars travel in `X-Conjectures-*` headers, not multipart form fields.** That is the
load-bearing one. `multipart/form-data` is a CORS-safelisted content type, so a signed submit
whose hotkey, timestamp and signature live in form fields is reachable from any allowlisted
browser origin with no preflight at all. `CORS_REQUEST_HEADERS` in `settings.py` deliberately
allowlists no `X-Conjectures-*` header, and that omission is exactly what keeps
`POST /v1/submissions` unreachable from a browser. Carrying these there inherits the property
rather than re-arguing it. A browser gets its own endpoint and a session cookie, which is the
credential it should be presenting anyway.
"""

from __future__ import annotations

import hashlib
from typing import Final

COMPETITION_SUBMIT_PREFIX: Final = "conjectures-competition-submit-v1"


def digest_of(parse_source: bytes, proof_source: bytes) -> str:
    """sha256(parse.rs ‖ Parse.lean), hex: what a submission *is*.

    Also its identity: the same two files from the same hotkey are the same submission,
    which is what makes a resubmission idempotent rather than a second place in the queue.
    """
    digest = hashlib.sha256()
    digest.update(parse_source)
    digest.update(proof_source)
    return digest.hexdigest()


def submit_message(*, competition: str, digest: str, hotkey: str, timestamp: int) -> str:
    """The exact text a hotkey signs to authorise one submission.

    Every field is pinned, for a different reason each. `competition` stops a signature made
    for one competition being replayed against another. `digest` binds it to these exact two
    files, so a captured signature cannot submit different ones. `hotkey` binds it to the
    submitter. `timestamp` makes a captured request perishable: the server refuses a
    signature whose timestamp falls outside its freshness window, so an upload recorded off
    the wire cannot be replayed into a later round.

    **The server rebuilds this from what it actually received** -- the digest of the bytes it
    read and the slug it resolved from the path -- never from what the request claimed. A
    caller who understates either signs one message and is checked against another.

    Readable, `key: value` per line, like `login.web_submission_message`: a wallet shows the
    text it is asked to sign, and an opaque digest is something people click through.
    """
    return "\n".join(
        (
            COMPETITION_SUBMIT_PREFIX,
            f"competition: {competition}",
            f"digest: {digest}",
            f"hotkey: {hotkey}",
            f"timestamp: {timestamp}",
        )
    )
