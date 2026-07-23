from __future__ import annotations

import base64

import bittensor as bt
import pytest
from pydantic import ValidationError

from frontier_subnet.commitments import (
    build_proof_commitment,
    build_proof_reveal,
    commitment_opens,
    compute_commitment_sha256,
    verify_proof_commitment,
    verify_proof_reveal,
)
from frontier_subnet.protocol import (
    CapabilitiesResponse,
    CommitmentRequest,
    ProofReveal,
    TaskReference,
)
from verifier.hashing import sha256_bytes


GENESIS_HASH = "0x" + "12" * 32
TASK = TaskReference(
    task_id="fc-test-positive-v1",
    task_bundle_sha256="sha256:" + "ab" * 32,
)
SUBMISSION = b"theorem Bounty.target : True := by\n  trivial\n"
SUBMISSION_SHA256 = sha256_bytes(SUBMISSION)
SALT = bytes(range(32))


def _miner():
    return bt.sp_core.Keypair.create_from_uri("//Alice")


def _other_miner():
    return bt.sp_core.Keypair.create_from_uri("//Bob")


def _commitment():
    return build_proof_commitment(
        genesis_hash=GENESIS_HASH,
        netuid=42,
        round_start_block=120,
        reveal_after_block=130,
        expires_at_block=140,
        task=TASK,
        submission_sha256=SUBMISSION_SHA256,
        salt=SALT,
        wallet=_miner(),
    )


def _reveal() -> ProofReveal:
    return build_proof_reveal(
        commitment=_commitment(),
        submission=SUBMISSION,
        salt=SALT,
        wallet=_miner(),
    )


def _different_digest(fill: str = "cd") -> str:
    return "sha256:" + fill * 32


def _flip_signature(value: str) -> str:
    replacement = "0" if value[2] != "0" else "1"
    return value[:2] + replacement + value[3:]


def test_commitment_hash_has_a_stable_vector_and_binds_every_context_field():
    miner_hotkey = _miner().ss58_address
    expected = compute_commitment_sha256(
        genesis_hash=GENESIS_HASH,
        netuid=42,
        round_start_block=120,
        reveal_after_block=130,
        expires_at_block=140,
        task=TASK,
        miner_hotkey=miner_hotkey,
        submission_sha256=SUBMISSION_SHA256,
        salt=SALT,
    )
    assert expected == "sha256:52ef3fed13d45a876eb6dcbe41afa5a005b7f18c4a9596e4038e8e9642bae1fd"

    variants = (
        {"genesis_hash": "0x" + "13" * 32},
        {"netuid": 43},
        {"round_start_block": 121},
        {"reveal_after_block": 131},
        {"expires_at_block": 141},
        {
            "task": TaskReference(
                task_id="fc-other-positive-v1",
                task_bundle_sha256=TASK.task_bundle_sha256,
            )
        },
        {
            "task": TaskReference(
                task_id=TASK.task_id,
                task_bundle_sha256=_different_digest(),
            )
        },
        {"miner_hotkey": _other_miner().ss58_address},
        {"submission_sha256": _different_digest("de")},
        {"salt": b"\xff" * 32},
    )
    base = {
        "genesis_hash": GENESIS_HASH,
        "netuid": 42,
        "round_start_block": 120,
        "reveal_after_block": 130,
        "expires_at_block": 140,
        "task": TASK,
        "miner_hotkey": miner_hotkey,
        "submission_sha256": SUBMISSION_SHA256,
        "salt": SALT,
    }
    for update in variants:
        assert compute_commitment_sha256(**{**base, **update}) != expected


def test_commitment_and_reveal_signatures_verify_and_detect_tampering():
    commitment = _commitment()
    reveal = build_proof_reveal(
        commitment=commitment,
        submission=SUBMISSION,
        salt=SALT,
        wallet=_miner(),
    )

    assert verify_proof_commitment(commitment)
    assert commitment_opens(
        commitment,
        submission_sha256=SUBMISSION_SHA256,
        salt=SALT,
    )
    assert verify_proof_reveal(reveal)
    assert reveal.submission_bytes() == SUBMISSION

    changed_task = commitment.task.model_copy(
        update={"task_bundle_sha256": _different_digest()}
    )
    commitment_tampering = (
        {"genesis_hash": "0x" + "13" * 32},
        {"netuid": 43},
        {"round_start_block": 121},
        {"reveal_after_block": 131},
        {"expires_at_block": 141},
        {"task": changed_task},
        {"miner_hotkey": _other_miner().ss58_address},
        {"commitment_sha256": _different_digest()},
        {"signature_scheme": "ed25519"},
        {"miner_signature": _flip_signature(commitment.miner_signature)},
    )
    for update in commitment_tampering:
        assert not verify_proof_commitment(commitment.model_copy(update=update))

    reveal_tampering = (
        {
            "submission_b64": base64.b64encode(SUBMISSION + b"-- changed").decode(
                "ascii"
            )
        },
        {"submission_sha256": _different_digest()},
        {"salt_hex": (b"\xff" * 32).hex()},
        {"signature_scheme": "ed25519"},
        {"miner_signature": _flip_signature(reveal.miner_signature)},
        {
            "commitment": commitment.model_copy(
                update={"round_start_block": commitment.round_start_block + 1}
            )
        },
    )
    for update in reveal_tampering:
        assert not verify_proof_reveal(reveal.model_copy(update=update))


def test_reveal_requires_the_committing_hotkey_and_exact_opening():
    commitment = _commitment()

    with pytest.raises(ValueError, match="does not own"):
        build_proof_reveal(
            commitment=commitment,
            submission=SUBMISSION,
            salt=SALT,
            wallet=_other_miner(),
        )
    with pytest.raises(ValueError, match="do not open"):
        build_proof_reveal(
            commitment=commitment,
            submission=SUBMISSION + b"\n",
            salt=SALT,
            wallet=_miner(),
        )
    with pytest.raises(ValueError, match="do not open"):
        build_proof_reveal(
            commitment=commitment,
            submission=SUBMISSION,
            salt=b"\xff" * 32,
            wallet=_miner(),
        )
    with pytest.raises(ValueError, match="exactly 32 bytes"):
        compute_commitment_sha256(
            genesis_hash=GENESIS_HASH,
            netuid=42,
            round_start_block=120,
            reveal_after_block=130,
            expires_at_block=140,
            task=TASK,
            miner_hotkey=_miner().ss58_address,
            submission_sha256=SUBMISSION_SHA256,
            salt=b"short",
        )


def test_protocol_models_are_strict_and_reject_ambiguous_encodings():
    valid_request = {
        "schema_version": 1,
        "protocol_version": 1,
        "request_id": "abcdef12-1234-5678-9234-567812345678",
        "genesis_hash": GENESIS_HASH,
        "netuid": 42,
        "round_start_block": 120,
        "task": TASK.model_dump(mode="json"),
    }
    assert CommitmentRequest.model_validate(valid_request).task == TASK

    invalid_requests = (
        {**valid_request, "unexpected": "field"},
        {**valid_request, "netuid": True},
        {**valid_request, "round_start_block": "120"},
        {**valid_request, "request_id": "ABCDEF12-1234-5678-9234-567812345678"},
        {**valid_request, "genesis_hash": "0x" + "AB" * 32},
        {
            **valid_request,
            "task": {
                "task_id": TASK.task_id,
                "task_bundle_sha256": "sha256:" + "AB" * 32,
            },
        },
    )
    for value in invalid_requests:
        with pytest.raises(ValidationError):
            CommitmentRequest.model_validate(value)

    valid_reveal = _reveal().model_dump(mode="json")
    for invalid_b64 in ("QQ", "QR==", "QQ==\n", "!!!!"):
        with pytest.raises(ValidationError):
            ProofReveal.model_validate(
                {**valid_reveal, "submission_b64": invalid_b64}
            )

    non_bittensor_prefix = bt.sp_core.ss58_encode(_miner().public_key, 0)
    with pytest.raises(ValidationError):
        CapabilitiesResponse(
            netuid=42,
            miner_hotkey=non_bittensor_prefix,
            max_submission_bytes=1_000_000,
            round_blocks=100,
            commit_blocks=10,
            reveal_blocks=100,
        )
