"""Offline tests for the database edge: digest conversion and the canonical request digest.

No database and no web stack, so these run in the base checkout.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy", reason="the db extra provides SQLAlchemy")

from conjectures_subnet.attribution import public_credit
from conjectures_subnet.db import digests
from conjectures_subnet.db.submissions import canonical_request_digest
from verifier.hashing import sha256_bytes

HEX = "ab" * 32
PREFIXED = f"sha256:{HEX}"


# --- digest conversion --------------------------------------------------------------


def test_prefixed_round_trip():
    raw = digests.to_bytes(PREFIXED)
    assert len(raw) == digests.DIGEST_BYTES
    assert digests.to_prefixed(raw) == PREFIXED
    assert digests.to_hex(raw) == HEX


def test_bare_hex_is_accepted():
    assert digests.to_bytes(HEX) == digests.to_bytes(PREFIXED)


def test_verifier_hashing_output_converts():
    # The two representations must agree for the digests the verifier actually produces.
    digest = sha256_bytes(b"theorem target : True := by trivial\n")
    assert digests.to_prefixed(digests.to_bytes(digest)) == digest


def test_memoryview_is_accepted():
    # psycopg can hand back a memoryview for a BYTEA column.
    raw = digests.to_bytes(PREFIXED)
    assert digests.to_prefixed(memoryview(raw)) == PREFIXED


@pytest.mark.parametrize(
    "value",
    [
        "",
        "sha256:",
        "AB" * 32,                 # uppercase: the schema stores one canonical form
        "sha256:" + "AB" * 32,
        "ab" * 31,
        "ab" * 33,
        "sha256:" + "zz" * 32,
        "0x" + "ab" * 32,
        None,
        42,
    ],
)
def test_malformed_digests_are_refused(value):
    with pytest.raises(digests.DigestError):
        digests.to_bytes(value)


@pytest.mark.parametrize("length", [0, 16, 31, 33, 64])
def test_wrong_length_stored_digest_is_refused(length):
    with pytest.raises(digests.DigestError):
        digests.to_prefixed(b"\x00" * length)


# --- canonical request digest -------------------------------------------------------


def _digest(**overrides):
    values = {
        "hotkey": "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
        "task_id": "fixture",
        "task_bundle_sha256": PREFIXED,
        "proof_sha256": "sha256:" + "cd" * 32,
        "payment_reference": "0xpayment-0001",
        "idempotency_key": "6f1b9c1e-0000-4000-8000-000000000001",
    }
    values.update(overrides)
    return canonical_request_digest(**values)


def test_request_digest_is_stable_and_well_formed():
    assert _digest() == _digest()
    assert digests.to_bytes(_digest())  # convertible to the 32 bytes the column stores


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("hotkey", "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"),
        ("task_id", "other-task"),
        ("task_bundle_sha256", "sha256:" + "11" * 32),
        ("proof_sha256", "sha256:" + "22" * 32),
        ("payment_reference", "0xpayment-0002"),
        ("idempotency_key", "6f1b9c1e-0000-4000-8000-000000000002"),
    ],
)
def test_every_component_changes_the_request_digest(field, replacement):
    # The signature is over this digest, so anything it fails to cover could be swapped
    # under a captured signature.
    assert _digest(**{field: replacement}) != _digest()


def test_request_digest_does_not_depend_on_argument_order():
    forward = canonical_request_digest(
        hotkey="5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
        task_id="fixture",
        task_bundle_sha256=PREFIXED,
        proof_sha256="sha256:" + "cd" * 32,
        payment_reference="0xpayment-0001",
        idempotency_key="6f1b9c1e-0000-4000-8000-000000000001",
    )
    backward = canonical_request_digest(
        idempotency_key="6f1b9c1e-0000-4000-8000-000000000001",
        payment_reference="0xpayment-0001",
        proof_sha256="sha256:" + "cd" * 32,
        task_bundle_sha256=PREFIXED,
        task_id="fixture",
        hotkey="5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
    )
    assert forward == backward


def test_public_credit_is_covered_by_the_request_signature():
    credit = public_credit(
        "Emmy Noether",
        "https://example.org/emmy-noether",
        "0000-0002-1825-0097",
    )
    assert credit is not None

    credited = _digest(public_credit=credit)
    assert credited != _digest()
    assert credited != _digest(public_credit=public_credit("Another Researcher"))
