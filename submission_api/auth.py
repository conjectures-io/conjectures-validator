"""Miner authentication by Bittensor hotkey signature.

The signed message is the canonical **request digest** — the 32 raw bytes of
`conjectures_subnet.db.submissions.canonical_request_digest`. The schema stores that signature
on the submission (`hotkey_signature`, 64 bytes), so the row itself carries the proof that this
miner authorised this exact request, and the binding stays auditable after the fact.

Because the digest covers the proof digest, the task, the payment reference and the idempotency
key, a captured signature cannot be reused for different proof bytes, a different task, or a
different payment. Replay of the same request is handled by the uniqueness of
`(hotkey, idempotency_key)` and of `payment_reference`, not by a separate nonce table.

No chain query happens here. The hotkey is authenticated; coldkey ownership is established by
the payment verifier, which has to read the chain anyway.
"""

from __future__ import annotations

import hmac
import re
import time
from dataclasses import dataclass
from typing import Protocol

from bittensor.sp_core import Keypair

from conjectures_subnet.db import digests
from submission_api.errors import Unauthorized
from submission_api.settings import DEVELOPMENT_AUTH, HOTKEY_SIGNATURE_AUTH, Settings
from verifier.bundle import SS58_ADDRESS

SIGNATURE_HEX = re.compile(r"^[0-9a-f]{128}$")
SIGNATURE_BYTES = 64
DEVELOPMENT_SIGNATURE = "development"
REASON_SIGNATURE_INVALID = "SIGNATURE_INVALID"


@dataclass(frozen=True)
class SignedRequest:
    """What the miner signed: the request digest, and who claims to have signed it."""

    hotkey: str
    request_digest: str  # sha256:<hex>
    signature: bytes  # 64 raw bytes, as stored on the submission

    @property
    def message(self) -> bytes:
        """The exact bytes the signature is over."""
        return digests.to_bytes(self.request_digest)


def normalise_signature(value: str) -> bytes:
    """Accept `0x`-prefixed or bare hex, in either case, and return the 64 raw bytes."""
    candidate = value.strip()
    if candidate[:2].lower() == "0x":
        candidate = candidate[2:]
    candidate = candidate.lower()
    if SIGNATURE_HEX.fullmatch(candidate) is None:
        raise Unauthorized(
            f"signature must be {SIGNATURE_BYTES} bytes of hex",
            reason_code=REASON_SIGNATURE_INVALID,
        )
    return bytes.fromhex(candidate)


def assert_valid_hotkey(value: str) -> str:
    if SS58_ADDRESS.fullmatch(value) is None:
        raise Unauthorized(
            "hotkey is not a valid SS58 address", reason_code=REASON_SIGNATURE_INVALID
        )
    return value


class Authenticator(Protocol):
    def verify(self, request: SignedRequest) -> None:
        """Raise Unauthorized unless the signature is valid for the request digest."""


@dataclass(frozen=True)
class HotkeySignatureAuthenticator:
    """Verify an sr25519/ed25519 signature made by the miner's hotkey.

    `Keypair` here holds only the public key decoded from the SS58 address — this process
    never sees a miner's secret, and the validator holds no miner keys.
    """

    def verify(self, request: SignedRequest) -> None:
        try:
            keypair = Keypair(ss58_address=request.hotkey)
        except Exception as exc:  # any decode failure is an auth failure
            raise Unauthorized(
                "hotkey is not a valid SS58 address",
                reason_code=REASON_SIGNATURE_INVALID,
            ) from exc
        try:
            valid = keypair.verify(request.message, request.signature)
        except Exception as exc:  # a malformed signature is an auth failure
            raise Unauthorized(
                "signature could not be verified", reason_code=REASON_SIGNATURE_INVALID
            ) from exc
        if not valid:
            raise Unauthorized(
                "signature does not match the request digest",
                reason_code=REASON_SIGNATURE_INVALID,
            )


@dataclass(frozen=True)
class DevelopmentAuthenticator:
    """Accept a fixed marker from an allowlisted hotkey. Never permitted in production."""

    hotkeys: tuple[str, ...]

    def verify(self, request: SignedRequest) -> None:
        if request.hotkey not in self.hotkeys:
            raise Unauthorized(
                "hotkey is not in the development allowlist",
                reason_code=REASON_SIGNATURE_INVALID,
            )
        expected = DEVELOPMENT_SIGNATURE.encode("utf-8").ljust(SIGNATURE_BYTES, b"\x00")
        if not hmac.compare_digest(request.signature, expected):
            raise Unauthorized(
                "signature does not match the request digest",
                reason_code=REASON_SIGNATURE_INVALID,
            )


def development_signature() -> str:
    """The hex a development client sends in place of a real signature."""
    return DEVELOPMENT_SIGNATURE.encode("utf-8").ljust(SIGNATURE_BYTES, b"\x00").hex()


def build_authenticator(settings: Settings) -> Authenticator:
    if settings.authenticator == HOTKEY_SIGNATURE_AUTH:
        return HotkeySignatureAuthenticator()
    if settings.authenticator == DEVELOPMENT_AUTH:
        if settings.production:  # pragma: no cover - Settings already refuses this
            raise RuntimeError(
                "the development authenticator is not permitted in production"
            )
        return DevelopmentAuthenticator(hotkeys=settings.development_hotkeys)
    raise RuntimeError(f"unknown authenticator: {settings.authenticator}")


def assert_fresh_nonce(
    nonce_ms: int, window_seconds: int, now_ms: int | None = None
) -> None:
    """Bound how long a signed request stays usable.

    The window is two-sided: a nonce far in the future is as suspect as a stale one, and would
    otherwise let a miner mint long-lived reusable credentials. Freshness is advisory here —
    the durable guarantee is that a payment reference and an idempotency key are each usable
    once — but it keeps a captured request from being useful indefinitely.
    """
    current = int(time.time() * 1000) if now_ms is None else now_ms
    if abs(current - nonce_ms) > window_seconds * 1000:
        raise Unauthorized(
            f"timestamp is outside the {window_seconds}-second acceptance window",
            reason_code=REASON_SIGNATURE_INVALID,
        )
