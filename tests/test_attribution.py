from __future__ import annotations

import pytest

from conjectures_subnet.attribution import (
    decode_public_credit_header,
    encode_public_credit_header,
    public_credit,
)


def test_public_credit_round_trips_unicode_through_an_ascii_header():
    credit = public_credit(
        "Sofía Kovalevskaya",
        "https://example.org/researchers/sofia",
        "0000-0002-1825-0097",
    )
    assert credit is not None
    encoded = encode_public_credit_header(credit)

    assert encoded.isascii()
    assert decode_public_credit_header(encoded) == credit
    assert credit.to_dict() == {
        "name": "Sofía Kovalevskaya",
        "url": "https://example.org/researchers/sofia",
        "orcid": "0000-0002-1825-0097",
    }


def test_public_credit_is_opt_in_and_secondary_fields_require_a_name():
    assert public_credit(None) is None
    with pytest.raises(ValueError, match="require a public credit name"):
        public_credit(None, "https://example.org/profile")


@pytest.mark.parametrize(
    "name,url,orcid",
    [
        (" outer whitespace ", None, None),
        ("Control\nName", None, None),
        ("Researcher", "http://example.org/insecure", None),
        ("Researcher", "https://user:password@example.org", None),
        ("Researcher", None, "0000-0002-1825-0098"),
    ],
)
def test_malformed_or_unsafe_public_credit_is_refused(name, url, orcid):
    with pytest.raises(ValueError):
        public_credit(name, url, orcid)


def test_noncanonical_header_encoding_is_refused():
    credit = public_credit("Research Team")
    assert credit is not None
    encoded = encode_public_credit_header(credit)
    with pytest.raises(ValueError, match="canonical"):
        decode_public_credit_header(encoded + "=")
