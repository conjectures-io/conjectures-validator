"""The hotkey signature that authorises one competition submission.

Only the *message* lives here. Verification is `login.verify_signature`, which the coldkey
paths already use: it answers every failure -- malformed address, malformed signature, valid
signature over different bytes -- with the same refusal, because distinguishing them tells an
attacker which half of their guess was wrong.

One message for every competition, and it names the competition. A signature made for one is
therefore not replayable against another, which matters the moment one API serves more than
one. The digest in it is the competition adapter's (`CompetitionAdapter.digest`), computed by
the server from the bytes it actually read.

The scalars travel in `X-Conjectures-*` headers, not multipart form fields.
`multipart/form-data` is a CORS-safelisted content type, so a signed submit whose hotkey,
timestamp and signature were form fields would be reachable from any allowlisted browser origin
with no preflight. `CORS_REQUEST_HEADERS` in `settings.py` allowlists no `X-Conjectures-*`
header, which is exactly what keeps `POST /v1/submissions` unreachable from a browser; this
inherits that property rather than re-arguing it. A browser uses the session endpoint.
"""

from __future__ import annotations

from typing import Final

COMPETITION_SUBMIT_PREFIX: Final = "conjectures-competition-submit-v1"


def submit_message(*, competition: str, digest: str, hotkey: str, timestamp: int) -> str:
    """The exact text a hotkey signs to authorise one submission.

    `competition` stops cross-competition replay, `digest` binds these exact files, `hotkey`
    the submitter, and `timestamp` makes a captured request perishable: the server refuses one
    outside its freshness window. The server rebuilds this from what it received -- the digest
    of the bytes it read and the slug it resolved -- never from what the request claimed.

    `key: value` per line, like `login.web_submission_message`: a wallet shows the text it is
    asked to sign, and an opaque digest is something people click through.
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


__all__ = ["COMPETITION_SUBMIT_PREFIX", "submit_message"]
