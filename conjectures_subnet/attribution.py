"""Validated, signed public credit attached to one submission.

Account display names are mutable profile data.  They are deliberately not used for authorship:
changing a profile must not silently rewrite the credit on an old mathematical result.  A
``PublicCredit`` is instead normalized before the request digest is signed and copied onto the
submission as an immutable snapshot.

The direct-payment API transports the object in one base64url-encoded JSON header.  Encoding the
UTF-8 JSON rather than putting a name directly in an HTTP header supports mathematicians' names in
any script without relying on non-portable header encodings.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit


MAX_CREDIT_HEADER_CHARS = 4096
MAX_CREDIT_NAME_CHARS = 128
MAX_CREDIT_URL_CHARS = 2048
ORCID = re.compile(r"^[0-9]{4}-[0-9]{4}-[0-9]{4}-[0-9]{3}[0-9X]$")


@dataclass(frozen=True)
class PublicCredit:
    """The public name and optional scholarly identity a miner chose to publish."""

    name: str
    url: str | None = None
    orcid: str | None = None

    def to_dict(self) -> dict[str, str]:
        value = {"name": self.name}
        if self.url is not None:
            value["url"] = self.url
        if self.orcid is not None:
            value["orcid"] = self.orcid
        return value


def public_credit(
    name: str | None,
    url: str | None = None,
    orcid: str | None = None,
) -> PublicCredit | None:
    """Validate one all-null or fully attributable public-credit snapshot.

    Values are rejected rather than silently trimmed or Unicode-normalized.  The exact strings a
    miner sees and signs are therefore the exact strings stored and later published.
    """

    if name is None:
        if url is not None or orcid is not None:
            raise ValueError("public credit URL and ORCID require a public credit name")
        return None
    if not isinstance(name, str):
        raise ValueError("public credit name must be a string")
    if name != name.strip() or not 1 <= len(name) <= MAX_CREDIT_NAME_CHARS:
        raise ValueError(
            "public credit name must be "
            f"1-{MAX_CREDIT_NAME_CHARS} characters with no outer whitespace"
        )
    if unicodedata.normalize("NFC", name) != name:
        raise ValueError("public credit name must use NFC Unicode normalization")
    if any(unicodedata.category(character).startswith("C") for character in name):
        raise ValueError("public credit name must not contain control or formatting characters")

    checked_url = _profile_url(url)
    checked_orcid = _orcid(orcid)
    return PublicCredit(name=name, url=checked_url, orcid=checked_orcid)


def public_credit_from_values(value: object) -> PublicCredit | None:
    """Read credit columns from a mapped submission or intent."""

    return public_credit(
        getattr(value, "public_credit_name", None),
        getattr(value, "public_credit_url", None),
        getattr(value, "public_credit_orcid", None),
    )


def public_credit_from_dict(value: object) -> PublicCredit | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("public credit must be an object")
    unknown = set(value) - {"name", "url", "orcid"}
    if unknown:
        raise ValueError("unknown public credit fields: " + ", ".join(sorted(unknown)))
    if "name" not in value:
        raise ValueError("public credit name is required")
    return public_credit(value["name"], value.get("url"), value.get("orcid"))


def encode_public_credit_header(value: PublicCredit) -> str:
    """Canonical base64url UTF-8 JSON for ``X-Conjectures-Public-Credit``."""

    raw = json.dumps(
        value.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode_public_credit_header(value: str | None) -> PublicCredit | None:
    if value is None:
        return None
    if not value or len(value) > MAX_CREDIT_HEADER_CHARS:
        raise ValueError(
            f"X-Conjectures-Public-Credit must be 1-{MAX_CREDIT_HEADER_CHARS} base64url characters"
        )
    try:
        raw = base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
        decoded = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            "X-Conjectures-Public-Credit must be base64url-encoded UTF-8 JSON"
        ) from exc
    credit = public_credit_from_dict(decoded)
    if credit is None:  # pragma: no cover - a JSON object can never produce None
        raise ValueError("public credit must be an object")
    if encode_public_credit_header(credit) != value:
        raise ValueError("X-Conjectures-Public-Credit must use the canonical encoding")
    return credit


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate public credit field: {key}")
        result[key] = value
    return result


def _profile_url(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("public credit URL must be a string")
    if value != value.strip() or not 1 <= len(value) <= MAX_CREDIT_URL_CHARS:
        raise ValueError(
            "public credit URL must be "
            f"1-{MAX_CREDIT_URL_CHARS} characters with no outer whitespace"
        )
    if any(
        character.isspace() or unicodedata.category(character).startswith("C")
        for character in value
    ):
        raise ValueError("public credit URL must not contain whitespace or control characters")
    try:
        parsed = urlsplit(value)
        parsed_port = parsed.port
    except ValueError as exc:
        raise ValueError("public credit URL is malformed") from exc
    del parsed_port
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("public credit URL must be an absolute https URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("public credit URL must not contain credentials")
    return value


def _orcid(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or ORCID.fullmatch(value) is None:
        raise ValueError("public credit ORCID must have the form 0000-0000-0000-000X")

    total = 0
    for character in value.replace("-", "")[:-1]:
        total = (total + int(character)) * 2
    remainder = (12 - total % 11) % 11
    check = "X" if remainder == 10 else str(remainder)
    if value[-1] != check:
        raise ValueError("public credit ORCID checksum is invalid")
    return value


__all__ = [
    "MAX_CREDIT_HEADER_CHARS",
    "MAX_CREDIT_NAME_CHARS",
    "MAX_CREDIT_URL_CHARS",
    "PublicCredit",
    "decode_public_credit_header",
    "encode_public_credit_header",
    "public_credit",
    "public_credit_from_dict",
    "public_credit_from_values",
]
