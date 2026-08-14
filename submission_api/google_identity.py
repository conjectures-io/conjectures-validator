"""Google Identity Services credentials, verified into a minimal local identity.

Google is an authentication provider here, not an API data source.  The application asks for
only ``openid``, ``email`` and ``profile`` and retains no access token, refresh token, picture or
profile name.  The stable ``sub`` claim is the provider key; email is account metadata and can
change.

The verifier is injected through :class:`submission_api.dependencies.Services`, so request tests
never call Google.  Production uses Google's supported ``google-auth`` verifier with a small
HTTPX-backed certificate cache.  Certificate responses are cached for the lifetime Google puts in
``Cache-Control``; without that, every sign-in would make an avoidable network request.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any, Mapping, Protocol

import anyio
import httpx

from submission_api.errors import ServiceUnavailable, Unauthorized

GOOGLE_PROVIDER = "google"
REASON_GOOGLE_CREDENTIAL_INVALID = "GOOGLE_CREDENTIAL_INVALID"
REASON_GOOGLE_IDENTITY_DISABLED = "GOOGLE_IDENTITY_DISABLED"
REASON_GOOGLE_IDENTITY_UNAVAILABLE = "GOOGLE_IDENTITY_UNAVAILABLE"

MAX_GOOGLE_SUBJECT_LENGTH = 255
MAX_GOOGLE_EMAIL_LENGTH = 254
EMAIL_SHAPE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_CACHE_SECONDS = 86_400


@dataclass(frozen=True)
class GoogleIdentity:
    """Claims the application retains after a Google ID token is verified."""

    subject: str
    email: str
    email_verified: bool
    hosted_domain: str | None = None

    @property
    def authoritative_email(self) -> bool:
        """Whether Google is currently authoritative for the mailbox.

        Google documents two authoritative cases: Gmail addresses, and verified addresses in a
        hosted Google domain.  A non-Gmail consumer Google Account may have been created with an
        address Google verified only at signup, so it is still a valid Google identity but not a
        substitute for a fresh magic-link verification of that mailbox.
        """

        domain = self.email.rpartition("@")[2].casefold()
        return self.email_verified and (domain == "gmail.com" or bool(self.hosted_domain))


class GoogleCredentialVerifier(Protocol):
    async def verify(self, credential: str) -> GoogleIdentity:
        """Verify one Google ID token or raise a transport-level API error."""


class DisabledGoogleCredentialVerifier:
    """Fail closed when a deployment has no Google client ID configured."""

    async def verify(self, credential: str) -> GoogleIdentity:
        del credential
        raise ServiceUnavailable(
            "Google sign-in is not configured on this deployment",
            reason_code=REASON_GOOGLE_IDENTITY_DISABLED,
        )


class GoogleIdTokenVerifier:
    """Verify Google ID tokens for exactly one OAuth web client."""

    def __init__(self, client_id: str) -> None:
        self._client_id = client_id
        self._request = _CachedHttpxRequest()

    async def verify(self, credential: str) -> GoogleIdentity:
        try:
            claims = await anyio.to_thread.run_sync(self._verify, credential)
        except ValueError as exc:
            raise Unauthorized(
                "Google could not verify that sign-in credential",
                reason_code=REASON_GOOGLE_CREDENTIAL_INVALID,
            ) from exc
        except (httpx.HTTPError, OSError) as exc:
            raise ServiceUnavailable(
                "Google sign-in could not be verified right now",
                reason_code=REASON_GOOGLE_IDENTITY_UNAVAILABLE,
            ) from exc
        return identity_from_claims(claims)

    def _verify(self, credential: str) -> Mapping[str, Any]:
        # Lazy so development installations that leave Google sign-in disabled do not need the
        # optional verifier merely to import or run the rest of the service.
        try:
            from google.oauth2 import id_token
        except ModuleNotFoundError as exc:  # pragma: no cover - deployment packaging failure
            raise ServiceUnavailable(
                "Google sign-in support is unavailable on this deployment",
                reason_code=REASON_GOOGLE_IDENTITY_UNAVAILABLE,
            ) from exc
        return id_token.verify_oauth2_token(
            credential,
            self._request,
            self._client_id,
        )


def identity_from_claims(claims: Mapping[str, Any]) -> GoogleIdentity:
    """Reduce verified claims to the bounded identity the account store accepts."""

    subject = claims.get("sub")
    email = claims.get("email")
    verified = claims.get("email_verified")
    hosted_domain = claims.get("hd")
    if not isinstance(subject, str) or not (1 <= len(subject) <= MAX_GOOGLE_SUBJECT_LENGTH):
        raise Unauthorized(
            "Google sign-in did not contain a valid account identifier",
            reason_code=REASON_GOOGLE_CREDENTIAL_INVALID,
        )
    if (
        not isinstance(email, str)
        or not (3 <= len(email) <= MAX_GOOGLE_EMAIL_LENGTH)
        or EMAIL_SHAPE.fullmatch(email) is None
    ):
        raise Unauthorized(
            "Google sign-in did not contain a valid email address",
            reason_code=REASON_GOOGLE_CREDENTIAL_INVALID,
        )
    if verified is not True:
        raise Unauthorized(
            "Google has not verified that email address",
            reason_code=REASON_GOOGLE_CREDENTIAL_INVALID,
        )
    if hosted_domain is not None and (
        not isinstance(hosted_domain, str) or not (1 <= len(hosted_domain) <= 253)
    ):
        raise Unauthorized(
            "Google sign-in contained an invalid hosted domain",
            reason_code=REASON_GOOGLE_CREDENTIAL_INVALID,
        )
    return GoogleIdentity(
        subject=subject,
        email=email.strip().casefold(),
        email_verified=True,
        hosted_domain=hosted_domain.casefold() if hosted_domain else None,
    )


def build_google_credential_verifier(client_id: str) -> GoogleCredentialVerifier:
    if not client_id:
        return DisabledGoogleCredentialVerifier()
    return GoogleIdTokenVerifier(client_id)


@dataclass(frozen=True)
class _CachedResponse:
    status: int
    data: bytes
    headers: Mapping[str, str]


class _CachedHttpxRequest:
    """The callable transport interface expected by ``google-auth``.

    Only successful GET responses are cached.  ID-token verification uses this for Google's
    rotating certificate/JWK document; token contents themselves never enter the cache.
    """

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, _CachedResponse]] = {}
        self._lock = threading.RLock()

    def __call__(
        self,
        url: str,
        method: str = "GET",
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        **_: Any,
    ) -> _CachedResponse:
        cache_key = url if method.upper() == "GET" and body is None else ""
        now = time.monotonic()
        if cache_key:
            with self._lock:
                cached = self._cache.get(cache_key)
                if cached and cached[0] > now:
                    return cached[1]

        response = httpx.request(
            method,
            url,
            content=body,
            headers=headers,
            timeout=timeout or 10.0,
            follow_redirects=False,
        )
        result = _CachedResponse(
            status=response.status_code,
            data=response.content,
            headers=dict(response.headers),
        )
        lifetime = _cache_lifetime(response.headers, now=time.time())
        if cache_key and response.status_code == 200 and lifetime > 0:
            with self._lock:
                self._cache[cache_key] = (now + lifetime, result)
        return result


def _cache_lifetime(headers: Mapping[str, str], *, now: float) -> int:
    cache_control = headers.get("cache-control", "")
    match = re.search(r"(?:^|,)\s*max-age=(\d+)\s*(?:,|$)", cache_control, re.I)
    if match:
        return min(int(match.group(1)), MAX_CACHE_SECONDS)
    expires = headers.get("expires")
    if expires:
        try:
            return max(
                0,
                min(int(parsedate_to_datetime(expires).timestamp() - now), MAX_CACHE_SECONDS),
            )
        except (TypeError, ValueError, OverflowError):
            return 0
    return 0


__all__ = [
    "GOOGLE_PROVIDER",
    "DisabledGoogleCredentialVerifier",
    "GoogleCredentialVerifier",
    "GoogleIdentity",
    "GoogleIdTokenVerifier",
    "build_google_credential_verifier",
    "identity_from_claims",
]
