"""Wallet sign-in and hotkey linking: the messages, and how they are verified.

Both flows are the same shape — the server mints a nonce, the client signs a message
containing it, the server checks the signature — and the security of both rests on the
message being *domain-separated*. A signature is only meaningful relative to what was
signed, so every distinct thing this validator ever asks a key to sign gets a distinct,
unambiguous prefix.

There are now seven:

    conjectures-login-v1         sign in to an account with a coldkey
    conjectures-coldkey-link-v1  attach another coldkey to an account
    conjectures-hotkey-link-v1   attach a hotkey to an account
    conjectures-cli-session-v1   open a CLI session with an already-linked hotkey
    conjectures-deposit-claim-v1 claim a transfer this coldkey made
    conjectures-read-v1          read a submission's status (submission_api/routers)
    <the request digest>         authorise one paid submission (32 raw bytes)

``conjectures-cli-session-v1`` deserves a note, because it is the one prefix a *hotkey* signs
for a durable credential, and a hotkey is a key miners sign with routinely. Three things keep
that from being a way to harvest a session:

* No other prefix is a prefix of it and it is a prefix of none of them, and it sits alone on
  the message's first line. A `conjectures-read-v1` signature is not a session signature.
* The nonce is minted and stored by the server that will check it, so a signature collected by
  anyone else — a hostile validator, a proxy, a phishing page — is valid against no open
  challenge here.
* The message is human-readable and the CLI shows it before unlocking the key, so "sign this
  opaque blob" is not the interaction being asked for.

The properties that matter:

* **No two prefixes are a prefix of each other**, and each message pins the address it
  is for, the nonce, and its own expiry. So a signature harvested from one flow cannot
  be replayed into another, and a login signature for one account cannot be reused for
  a different one.
* **The nonce is the server's.** The client never chooses any part of what it signs, so
  it cannot be induced to sign something that means something else. The message is
  stored verbatim on the challenge row and verification reads it back rather than
  rebuilding it — rebuilding it differently is exactly the bug this avoids.
* **Single use.** ``consume_challenge`` claims the row in one conditional UPDATE.

The intake path's request digest is deliberately not prefixed the same way: it is 32
raw bytes of SHA-256 over canonical JSON, which cannot collide with any of the UTF-8
messages here.
"""

from __future__ import annotations

import datetime as dt

from bittensor.sp_core import Keypair

from submission_api.errors import Unauthorized

LOGIN_PREFIX = "conjectures-login-v1"
COLDKEY_LINK_PREFIX = "conjectures-coldkey-link-v1"
HOTKEY_LINK_PREFIX = "conjectures-hotkey-link-v1"
CLI_SESSION_PREFIX = "conjectures-cli-session-v1"
DEPOSIT_CLAIM_PREFIX = "conjectures-deposit-claim-v1"

REASON_SIGNATURE_INVALID = "SIGNATURE_INVALID"
REASON_CHALLENGE_INVALID = "CHALLENGE_INVALID"
# The hotkey signed correctly but no account has claimed it. Distinct from the two above
# because it is the one refusal here the caller can actually fix, and the fix is in the
# browser: link the hotkey at the website first.
REASON_HOTKEY_NOT_LINKED = "HOTKEY_NOT_LINKED"


def login_message(*, domain: str, address: str, nonce: str, expires_at: dt.datetime) -> str:
    """The exact text a coldkey signs to sign in.

    `domain` binds the signature to this deployment, so a signature produced for a
    staging or a third-party instance is not valid here. Newline-separated with
    `key: value` lines because a human is asked to approve this in a wallet UI, and an
    opaque blob is something people click through.
    """
    return _message(LOGIN_PREFIX, domain=domain, address=address, nonce=nonce, expires_at=expires_at)


def hotkey_link_message(
    *, domain: str, address: str, nonce: str, expires_at: dt.datetime
) -> str:
    """The exact text a hotkey signs to be attached to an account."""
    return _message(
        HOTKEY_LINK_PREFIX, domain=domain, address=address, nonce=nonce, expires_at=expires_at
    )


def coldkey_link_message(
    *, domain: str, address: str, nonce: str, expires_at: dt.datetime
) -> str:
    """The exact text a coldkey signs to be attached to an account."""
    return _message(
        COLDKEY_LINK_PREFIX,
        domain=domain,
        address=address,
        nonce=nonce,
        expires_at=expires_at,
    )


def cli_session_message(
    *, domain: str, address: str, nonce: str, expires_at: dt.datetime
) -> str:
    """The exact text a hotkey signs to open a CLI session.

    Same shape as the other two nonce flows, and deliberately so — the security of all three
    rests on the same three facts, and a message built a fourth way would have to be reasoned
    about separately. What differs is only the prefix, which is what makes a signature
    collected for a hotkey *link* useless for minting a *session*: linking proves control of a
    key to an account that is already signed in, while this mints a credential, and the two
    must not be interchangeable.
    """
    return _message(
        CLI_SESSION_PREFIX, domain=domain, address=address, nonce=nonce, expires_at=expires_at
    )


def deposit_claim_message(*, domain: str, address: str, extrinsic_reference: str) -> str:
    """The exact text a coldkey signs to claim a transfer it made.

    The extrinsic reference *is* the nonce, and it is a better one than a minted value would
    be: it names the specific transfer being claimed, so the signature cannot be moved to a
    different transfer, and one extrinsic funds at most one deposit anyway. There is
    deliberately no expiry — a transfer that happened stays claimable, and a claim that expired
    would strand real money.
    """
    return "\n".join(
        (
            DEPOSIT_CLAIM_PREFIX,
            f"domain: {domain}",
            f"address: {address}",
            f"extrinsic: {extrinsic_reference}",
        )
    )


def _message(
    prefix: str, *, domain: str, address: str, nonce: str, expires_at: dt.datetime
) -> str:
    return "\n".join(
        (
            prefix,
            f"domain: {domain}",
            f"address: {address}",
            f"nonce: {nonce}",
            f"expires: {expires_at.astimezone(dt.UTC).isoformat().replace('+00:00', 'Z')}",
        )
    )


def verify_signature(*, address: str, message: str, signature: bytes) -> None:
    """Check an sr25519/ed25519 signature over `message`, or raise Unauthorized.

    `Keypair` here holds only the public key decoded from the SS58 address: this
    process never sees a secret key, and the validator holds none of the user's.

    Every failure — a malformed address, a malformed signature, a valid signature over
    different bytes — is the same refusal with the same reason code. Distinguishing them
    would tell an attacker which half of their guess was wrong.
    """
    try:
        keypair = Keypair(ss58_address=address)
    except Exception as exc:  # any decode failure is an auth failure
        raise Unauthorized(
            "address is not a valid SS58 address", reason_code=REASON_SIGNATURE_INVALID
        ) from exc
    try:
        valid = keypair.verify(message.encode("utf-8"), signature)
    except Exception as exc:  # a malformed signature is an auth failure
        raise Unauthorized(
            "signature could not be verified", reason_code=REASON_SIGNATURE_INVALID
        ) from exc
    if not valid:
        raise Unauthorized(
            "signature does not match the challenge",
            reason_code=REASON_SIGNATURE_INVALID,
        )


__all__ = [
    "CLI_SESSION_PREFIX",
    "COLDKEY_LINK_PREFIX",
    "DEPOSIT_CLAIM_PREFIX",
    "HOTKEY_LINK_PREFIX",
    "LOGIN_PREFIX",
    "REASON_CHALLENGE_INVALID",
    "REASON_HOTKEY_NOT_LINKED",
    "REASON_SIGNATURE_INVALID",
    "cli_session_message",
    "coldkey_link_message",
    "deposit_claim_message",
    "hotkey_link_message",
    "login_message",
    "verify_signature",
]
