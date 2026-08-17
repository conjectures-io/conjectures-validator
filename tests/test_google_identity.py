"""Google claim reduction and cache policy are offline and deterministic."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="Google identity errors use the API error model")

from submission_api.errors import Unauthorized
from submission_api.google_identity import _cache_lifetime, identity_from_claims


def claims(**overrides):
    values = {
        "sub": "google-subject-123",
        "email": "solver@gmail.com",
        "email_verified": True,
    }
    values.update(overrides)
    return values


def test_google_subject_is_the_identity_and_gmail_is_authoritative():
    identity = identity_from_claims(claims())
    assert identity.subject == "google-subject-123"
    assert identity.email == "solver@gmail.com"
    assert identity.authoritative_email is True


def test_hosted_google_domain_is_authoritative_but_consumer_external_email_is_not():
    hosted = identity_from_claims(
        claims(email="user@example.org", hd="example.org")
    )
    consumer = identity_from_claims(claims(email="user@example.org"))
    assert hosted.authoritative_email is True
    assert consumer.authoritative_email is False


@pytest.mark.parametrize(
    "override",
    [
        {"sub": ""},
        {"sub": None},
        {"sub": "x" * 256},
        {"email": "not-an-email"},
        {"email": "x" * 255},
        {"email_verified": False},
        {"hd": "x" * 254},
    ],
)
def test_malformed_or_unverified_claims_are_refused(override):
    with pytest.raises(Unauthorized):
        identity_from_claims(claims(**override))


def test_certificate_cache_obeys_max_age_and_caps_unreasonable_values():
    assert _cache_lifetime({"cache-control": "public, max-age=3600"}, now=0) == 3600
    assert _cache_lifetime({"cache-control": "max-age=9999999"}, now=0) == 86_400
    assert _cache_lifetime({}, now=0) == 0
