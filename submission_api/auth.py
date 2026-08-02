"""Miner authentication by Bittensor hotkey signature.

The signed message is a domain-separated envelope over the canonical request digest and the
millisecond timestamp. The schema stores both the signature and timestamp, so the row itself
carries an auditable authorization while a captured signature cannot be refreshed by changing an
unsigned timestamp.

Because the digest covers the proof digest, the task, the payment reference and the idempotency
key, a captured signature cannot be reused for different proof bytes, a different task, or a
different payment. Replay of the same request is handled by the uniqueness of
`(hotkey, idempotency_key)` and of `payment_reference`, not by a separate nonce table.

No chain query happens here. The hotkey is authenticated; coldkey ownership is established by
the payment verifier, which has to read the chain anyway.
"""

from __future__ import annotations

import hmac
import hashlib
import re
import time
from dataclasses import dataclass
from typing import Protocol

from conjectures_subnet.db import digests
from submission_api.errors import Unauthorized
from submission_api.settings import DEVELOPMENT_AUTH, HOTKEY_SIGNATURE_AUTH, Settings
from verifier.bundle import SS58_ADDRESS


SIGNATURE_HEX = re.compile(r"^[0-9a-f]{128}$")
SIGNATURE_BYTES = 64
DEVELOPMENT_SIGNATURE = "development"
REASON_SIGNATURE_INVALID = "SIGNATURE_INVALID"
SUBMISSION_DOMAIN = "conjectures-submit-v1"
READ_DOMAIN = "conjectures-read-v1"


def authentication_message(*, domain: str, request_digest: str, timestamp_ms: int) -> bytes:
    """The exact 32-byte, domain-separated authentication message."""
    if not domain or "\x00" in domain or timestamp_ms <= 0:
        raise ValueError("invalid authentication envelope")
    envelope = (
        b"conjectures-auth-v1\x00"
        + domain.encode("ascii")
        + b"\x00"
        + str(timestamp_ms).encode("ascii")
        + b"\x00"
        + digests.to_bytes(request_digest)
    )
    return hashlib.sha256(envelope).digest()


@dataclass(frozen=True)
class SignedRequest:
    """Inputs to the timestamped, domain-separated miner signature envelope."""

    hotkey: str
    request_digest: str          # sha256:<hex>
    timestamp_ms: int
    signature: bytes             # 64 raw bytes, as stored on the submission
    domain: str = SUBMISSION_DOMAIN

    @property
    def message(self) -> bytes:
        """The exact bytes the signature is over."""
        return authentication_message(
            domain=self.domain,
            request_digest=self.request_digest,
            timestamp_ms=self.timestamp_ms,
        )


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
        """Raise Unauthorized unless the signature is valid for the full envelope."""


@dataclass(frozen=True)
class HotkeySignatureAuthenticator:
    """Verify an sr25519/ed25519 signature made by the miner's hotkey.

    The keypair implementation is imported lazily so this package stays importable, and its
    offline tests stay runnable, without the bittensor dependency present.
    """

    def verify(self, request: SignedRequest) -> None:
        keypair_type = _load_keypair()
        try:
            keypair = keypair_type(ss58_address=request.hotkey)
        except Exception as exc:  # noqa: BLE001 - any decode failure is an auth failure
            raise Unauthorized(
                "hotkey is not a valid SS58 address", reason_code=REASON_SIGNATURE_INVALID
            ) from exc
        try:
            valid = keypair.verify(request.message, request.signature)
        except Exception as exc:  # noqa: BLE001 - a malformed signature is an auth failure
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


def _load_keypair() -> type:
    try:
        # Bittensor 11 ships its supported Substrate keypair implementation here.
        from bittensor.sp_core import Keypair  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover - exercised only without the subnet extra
        try:
            from bittensor_wallet import Keypair  # type: ignore[import-not-found,no-redef]
        except ImportError:
            try:
                from substrateinterface import Keypair  # type: ignore[import-not-found,no-redef]
            except ImportError as exc:
                raise Unauthorized(
                    "hotkey signature verification is unavailable on this deployment",
                    reason_code=REASON_SIGNATURE_INVALID,
                ) from exc
    return Keypair


def build_authenticator(settings: Settings) -> Authenticator:
    if settings.authenticator == HOTKEY_SIGNATURE_AUTH:
        return HotkeySignatureAuthenticator()
    if settings.authenticator == DEVELOPMENT_AUTH:
        if settings.production:  # pragma: no cover - Settings already refuses this
            raise RuntimeError("the development authenticator is not permitted in production")
        return DevelopmentAuthenticator(hotkeys=settings.development_hotkeys)
    raise RuntimeError(f"unknown authenticator: {settings.authenticator}")


def assert_fresh_nonce(nonce_ms: int, window_seconds: int, now_ms: int | None = None) -> None:
    """Bound how long a signed request stays usable.

    The window is two-sided: a nonce far in the future is as suspect as a stale one, and would
    otherwise let a miner mint long-lived reusable credentials. The timestamp is inside the
    signed envelope, so an observer cannot refresh a captured signature by changing this header.
    """
    current = int(time.time() * 1000) if now_ms is None else now_ms
    if abs(current - nonce_ms) > window_seconds * 1000:
        raise Unauthorized(
            f"timestamp is outside the {window_seconds}-second acceptance window",
            reason_code=REASON_SIGNATURE_INVALID,
        )
