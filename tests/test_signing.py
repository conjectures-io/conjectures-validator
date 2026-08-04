"""The messages a miner's hotkey signs, checked by the code that verifies them.

`conjectures_subnet/signing.py` is a published contract, not an internal helper: the miner CLI
lives in its own repository (`conjectures-miner`) and reimplements both messages byte for byte,
because a client must be able to sign without SQLAlchemy or FastAPI installed. So there are two
implementations of one construction, and the failure mode is asymmetric — a drift of one byte
makes every honest submission fail authentication, and the transfer funding it is already
irreversibly on chain.

Three things are asserted here, in increasing order of what they would catch:

1. the store re-exports the shared digest rather than holding a second copy of it;
2. the digests equal the vectors the miner CLI pins, so the cross-repository contract holds;
3. a signature this construction produces is accepted by
   `submission_api.auth.HotkeySignatureAuthenticator`, and one made by any other key is not.
"""

from __future__ import annotations

import pytest
from bittensor.sp_core import Keypair

from conjectures_subnet import signing
from conjectures_subnet.db import digests
from conjectures_subnet.db import submissions as store
from submission_api.auth import HotkeySignatureAuthenticator, SignedRequest
from submission_api.errors import Unauthorized

ALICE = Keypair.create_from_uri("//Alice")
BOB = Keypair.create_from_uri("//Bob")

TASK_ID = "fc-379fc029-erdos1094-erdos-1094-1ec3e802ca-formalized-v1"
TASK_SHA256 = "sha256:" + "74" * 32
PROOF_SHA256 = "sha256:" + "5f" * 32
PAYMENT_REF = "5321109-2-14"
IDEMPOTENCY_KEY = "84a6bb73-d3f8-4926-aa76-4f56514bd800"
SUBMISSION_ID = "0e2b3c4d-5f60-4718-9a2b-3c4d5e6f7081"

# Copied verbatim from `conjectures-miner/tests/vectors/digests.json`, which that repository
# pins as its own contract with this one. Duplicated here on purpose: the two repositories are
# released independently, and a value only one side asserts is a value that can move.
MINER_VECTOR_INPUT = {
    "hotkey": "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
    "task_id": "fc-379fc029-erdos89-erdos-89-c956ed476a-formalized-v1",
    "task_bundle_sha256": "sha256:" + "aa" * 32,
    "proof_sha256": "sha256:" + "bb" * 32,
    "payment_reference": "0x1234:5",
    "idempotency_key": "6f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0",
}
MINER_VECTOR_REQUEST_DIGEST = (
    "sha256:cac9da813f6a2a5034959995150f917d3b4a9875a63f0a15acd63bd797040c2b"
)
MINER_VECTOR_READ_SUBMISSION_ID = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
MINER_VECTOR_READ_DIGEST = (
    "sha256:158fe6f2b8f4bf3abfb9711030e77266a677ed90b9dc5e0b66b36c5de7be382e"
)


def _request_digest(hotkey: str) -> str:
    return signing.canonical_request_digest(
        hotkey=hotkey,
        task_id=TASK_ID,
        task_bundle_sha256=TASK_SHA256,
        proof_sha256=PROOF_SHA256,
        payment_reference=PAYMENT_REF,
        idempotency_key=IDEMPOTENCY_KEY,
    )


def _sign(keypair, digest: str) -> bytes:
    """Sign the 32 raw bytes the digest names — not its hex spelling. See the test below."""
    return keypair.sign(digests.to_bytes(digest))


# --- one definition ------------------------------------------------------------------------


def test_the_store_re_exports_the_shared_digest():
    """Not "produces the same value" — the same function object.

    A copy that happened to agree today would pass a value-equality test and still be a copy.
    """
    assert store.canonical_request_digest is signing.canonical_request_digest


# --- the contract the miner CLI pins ---------------------------------------------------------


def test_the_request_digest_matches_the_miner_cli_vector():
    """`conjectures-miner/tests/vectors/digests.json`, asserted from this side too.

    If this fails, every submission that CLI sends will be refused with SIGNATURE_INVALID —
    after the miner has paid. Regenerate the vector deliberately, never to make a test pass.
    """
    assert (
        signing.canonical_request_digest(**MINER_VECTOR_INPUT)
        == MINER_VECTOR_REQUEST_DIGEST
    )


def test_the_read_digest_matches_the_miner_cli_vector():
    assert (
        signing.read_digest(
            hotkey=MINER_VECTOR_INPUT["hotkey"],
            submission_id=MINER_VECTOR_READ_SUBMISSION_ID,
        )
        == MINER_VECTOR_READ_DIGEST
    )


def test_every_field_changes_the_request_digest():
    """Six fields go in, and moving any of them must invalidate the signature.

    This is what stops a captured signature being replayed onto different proof bytes, a
    different task, or a different payment.
    """
    original = signing.canonical_request_digest(**MINER_VECTOR_INPUT)
    for field in MINER_VECTOR_INPUT:
        moved = dict(MINER_VECTOR_INPUT, **{field: MINER_VECTOR_INPUT[field] + "-x"})
        assert signing.canonical_request_digest(**moved) != original, field


def test_a_read_digest_can_never_equal_a_request_digest():
    """Different constructions, so a read signature cannot authorise a paid submission."""
    read = signing.read_digest(hotkey=ALICE.ss58_address, submission_id=SUBMISSION_ID)
    assert read != _request_digest(ALICE.ss58_address)
    assert read.startswith("sha256:")


def test_the_read_digest_pins_both_the_reader_and_the_submission():
    original = signing.read_digest(hotkey=ALICE.ss58_address, submission_id=SUBMISSION_ID)
    for changed in (
        signing.read_digest(hotkey=BOB.ss58_address, submission_id=SUBMISSION_ID),
        signing.read_digest(hotkey=ALICE.ss58_address, submission_id="other"),
    ):
        assert changed != original


# --- the validator accepts what this construction signs ---------------------------------------


def test_the_authenticator_accepts_a_signature_over_the_request_digest():
    digest = _request_digest(ALICE.ss58_address)

    HotkeySignatureAuthenticator().verify(
        SignedRequest(
            hotkey=ALICE.ss58_address,
            request_digest=digest,
            signature=_sign(ALICE, digest),
        )
    )


def test_the_authenticator_accepts_a_signature_over_the_read_digest():
    digest = signing.read_digest(hotkey=ALICE.ss58_address, submission_id=SUBMISSION_ID)

    HotkeySignatureAuthenticator().verify(
        SignedRequest(
            hotkey=ALICE.ss58_address,
            request_digest=digest,
            signature=_sign(ALICE, digest),
        )
    )


def test_the_signature_is_over_the_raw_digest_bytes_not_its_hex():
    """`SignedRequest.message` decodes the digest, so signing the string would be wrong.

    Asserted directly rather than left implicit: this is the one encoding mistake that
    produces a well-formed signature over the wrong thing.
    """
    digest = _request_digest(ALICE.ss58_address)
    signature = _sign(ALICE, digest)
    public = Keypair(ss58_address=ALICE.ss58_address)

    assert public.verify(digests.to_bytes(digest), signature)
    assert not public.verify(digest.encode("utf-8"), signature)


# --- only the holder of a hotkey can sign for it ------------------------------------------------


def test_another_wallet_cannot_sign_for_this_hotkey():
    """The security property, stated as the validator sees it.

    Bob holds a real key and signs the identical digest. The claim is Alice's hotkey, and the
    authenticator builds its `Keypair` from that address alone — so the signature does not
    verify and the submission is refused before any payment is looked up.
    """
    digest = _request_digest(ALICE.ss58_address)

    with pytest.raises(Unauthorized) as refusal:
        HotkeySignatureAuthenticator().verify(
            SignedRequest(
                hotkey=ALICE.ss58_address,
                request_digest=digest,
                signature=_sign(BOB, digest),
            )
        )
    assert refusal.value.reason_code == "SIGNATURE_INVALID"


def test_a_signature_over_different_proof_bytes_is_refused():
    """The proof digest is inside the message, so bytes cannot be swapped under a signature."""
    signature = _sign(ALICE, _request_digest(ALICE.ss58_address))
    moved = signing.canonical_request_digest(
        hotkey=ALICE.ss58_address,
        task_id=TASK_ID,
        task_bundle_sha256=TASK_SHA256,
        proof_sha256="sha256:" + "ab" * 32,
        payment_reference=PAYMENT_REF,
        idempotency_key=IDEMPOTENCY_KEY,
    )

    with pytest.raises(Unauthorized):
        HotkeySignatureAuthenticator().verify(
            SignedRequest(
                hotkey=ALICE.ss58_address, request_digest=moved, signature=signature
            )
        )
