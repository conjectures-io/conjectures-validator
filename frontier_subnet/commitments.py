from __future__ import annotations

import base64
import hmac
from typing import Any

import bittensor as bt

from frontier_subnet.protocol import (
    ProofCommitment,
    ProofReveal,
    TaskReference,
)
from verifier.hashing import canonical_json_bytes, sha256_bytes, sha256_named_bytes


COMMITMENT_DOMAIN = b"frontier-math-proof-commitment-v1"
COMMITMENT_SIGNATURE_DOMAIN = b"frontiermath/proof-commitment/v1\n"
REVEAL_SIGNATURE_DOMAIN = b"frontiermath/proof-reveal/v1\n"


def compute_commitment_sha256(
    *,
    genesis_hash: str,
    netuid: int,
    round_start_block: int,
    reveal_after_block: int,
    expires_at_block: int,
    task: TaskReference,
    miner_hotkey: str,
    submission_sha256: str,
    salt: bytes,
) -> str:
    if len(salt) != 32:
        raise ValueError("commitment salt must be exactly 32 bytes")
    return sha256_named_bytes(
        {
            "domain": COMMITMENT_DOMAIN,
            "genesis_hash": genesis_hash.encode("ascii"),
            "netuid": str(netuid).encode("ascii"),
            "round_start_block": str(round_start_block).encode("ascii"),
            "reveal_after_block": str(reveal_after_block).encode("ascii"),
            "expires_at_block": str(expires_at_block).encode("ascii"),
            "task_id": task.task_id.encode("ascii"),
            "task_bundle_sha256": task.task_bundle_sha256.encode("ascii"),
            "miner_hotkey": miner_hotkey.encode("ascii"),
            "submission_sha256": submission_sha256.encode("ascii"),
            "salt": salt,
        }
    )


def _signer_details(wallet: Any) -> tuple[Any, str, str]:
    signer = bt.resolve_signer(wallet, role="hotkey")
    scheme = bt.format_crypto_type(signer.crypto_type)
    if scheme not in {"sr25519", "ed25519"}:
        raise ValueError(f"unsupported hotkey signature scheme: {scheme}")
    return signer, signer.ss58_address, scheme


def _sign(signer: Any, payload: bytes) -> str:
    signature = signer.sign(payload)
    if not isinstance(signature, (bytes, bytearray)):
        raise TypeError("proof envelope signing requires a synchronous hotkey signer")
    raw = bytes(signature)
    if len(raw) != 64:
        raise ValueError("hotkey produced a non-64-byte signature")
    return "0x" + raw.hex()


def _signature_payload(domain: bytes, fields: dict[str, Any]) -> bytes:
    return domain + canonical_json_bytes(fields)


def build_proof_commitment(
    *,
    genesis_hash: str,
    netuid: int,
    round_start_block: int,
    reveal_after_block: int,
    expires_at_block: int,
    task: TaskReference,
    submission_sha256: str,
    salt: bytes,
    wallet: Any,
) -> ProofCommitment:
    signer, hotkey, scheme = _signer_details(wallet)
    commitment_sha256 = compute_commitment_sha256(
        genesis_hash=genesis_hash,
        netuid=netuid,
        round_start_block=round_start_block,
        reveal_after_block=reveal_after_block,
        expires_at_block=expires_at_block,
        task=task,
        miner_hotkey=hotkey,
        submission_sha256=submission_sha256,
        salt=salt,
    )
    fields: dict[str, Any] = {
        "schema_version": 1,
        "protocol_version": 1,
        "genesis_hash": genesis_hash,
        "netuid": netuid,
        "round_start_block": round_start_block,
        "reveal_after_block": reveal_after_block,
        "expires_at_block": expires_at_block,
        "task": task.model_dump(mode="json"),
        "miner_hotkey": hotkey,
        "commitment_sha256": commitment_sha256,
        "signature_scheme": scheme,
    }
    fields["miner_signature"] = _sign(
        signer, _signature_payload(COMMITMENT_SIGNATURE_DOMAIN, fields)
    )
    return ProofCommitment.model_validate(fields)


def build_proof_reveal(
    *,
    commitment: ProofCommitment,
    submission: bytes,
    salt: bytes,
    wallet: Any,
) -> ProofReveal:
    signer, hotkey, scheme = _signer_details(wallet)
    if hotkey != commitment.miner_hotkey:
        raise ValueError("the active hotkey does not own this proof commitment")
    submission_sha256 = sha256_bytes(submission)
    if not commitment_opens(commitment, submission_sha256=submission_sha256, salt=salt):
        raise ValueError("submission and salt do not open the proof commitment")
    fields: dict[str, Any] = {
        "schema_version": 1,
        "protocol_version": 1,
        "commitment": commitment.model_dump(mode="json"),
        "submission_sha256": submission_sha256,
        "submission_b64": base64.b64encode(submission).decode("ascii"),
        "salt_hex": salt.hex(),
        "signature_scheme": scheme,
    }
    fields["miner_signature"] = _sign(
        signer, _signature_payload(REVEAL_SIGNATURE_DOMAIN, fields)
    )
    return ProofReveal.model_validate(fields)


def commitment_opens(
    commitment: ProofCommitment,
    *,
    submission_sha256: str,
    salt: bytes,
) -> bool:
    expected = compute_commitment_sha256(
        genesis_hash=commitment.genesis_hash,
        netuid=commitment.netuid,
        round_start_block=commitment.round_start_block,
        reveal_after_block=commitment.reveal_after_block,
        expires_at_block=commitment.expires_at_block,
        task=commitment.task,
        miner_hotkey=commitment.miner_hotkey,
        submission_sha256=submission_sha256,
        salt=salt,
    )
    return hmac.compare_digest(expected, commitment.commitment_sha256)


def _verify_signature(
    *,
    hotkey: str,
    scheme: str,
    signature: str,
    payload: bytes,
) -> bool:
    try:
        return bool(
            bt.sp_core.verify(
                payload,
                bytes.fromhex(signature.removeprefix("0x")),
                hotkey,
                bt.parse_crypto_type(scheme),
            )
        )
    except Exception:
        return False


def verify_proof_commitment(commitment: ProofCommitment) -> bool:
    fields = commitment.model_dump(mode="json", exclude={"miner_signature"})
    return _verify_signature(
        hotkey=commitment.miner_hotkey,
        scheme=commitment.signature_scheme,
        signature=commitment.miner_signature,
        payload=_signature_payload(COMMITMENT_SIGNATURE_DOMAIN, fields),
    )


def verify_proof_reveal(reveal: ProofReveal) -> bool:
    try:
        if not verify_proof_commitment(reveal.commitment):
            return False
        submission = reveal.submission_bytes()
        submission_sha256 = sha256_bytes(submission)
        if not hmac.compare_digest(submission_sha256, reveal.submission_sha256):
            return False
        if not commitment_opens(
            reveal.commitment,
            submission_sha256=submission_sha256,
            salt=bytes.fromhex(reveal.salt_hex),
        ):
            return False
        fields = reveal.model_dump(mode="json", exclude={"miner_signature"})
        return _verify_signature(
            hotkey=reveal.commitment.miner_hotkey,
            scheme=reveal.signature_scheme,
            signature=reveal.miner_signature,
            payload=_signature_payload(REVEAL_SIGNATURE_DOMAIN, fields),
        )
    except Exception:
        return False
