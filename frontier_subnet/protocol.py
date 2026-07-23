from __future__ import annotations

import base64
import re
from typing import Literal
from uuid import UUID

import bittensor as bt
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from frontier_subnet import PROTOCOL_VERSION
from verifier.hashing import canonical_json_bytes, is_sha256
from verifier.task_generator import MAX_SUBMISSION_BYTES


SCHEMA_VERSION = 1
TASK_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,254}$")
CHAIN_HASH = re.compile(r"^0x[0-9a-f]{64}$")
LOWER_HEX_32 = re.compile(r"^[0-9a-f]{64}$")
SIGNATURE = re.compile(r"^0x[0-9a-f]{128}$")
MAX_SUBMISSION_B64_BYTES = ((MAX_SUBMISSION_BYTES + 2) // 3) * 4


class ProtocolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


def _validate_request_id(value: str) -> str:
    try:
        parsed = UUID(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("request_id must be a UUID") from exc
    if str(parsed) != value:
        raise ValueError("request_id must use canonical lowercase UUID form")
    return value


def _validate_ss58(value: str) -> str:
    if not bt.wallets.is_bittensor_address(value):
        raise ValueError("hotkey must be a canonical Bittensor SS58 address")
    return value


class TaskReference(ProtocolModel):
    task_id: str
    task_bundle_sha256: str

    @field_validator("task_id")
    @classmethod
    def valid_task_id(cls, value: str) -> str:
        if TASK_ID.fullmatch(value) is None:
            raise ValueError("task_id has invalid characters or length")
        return value

    @field_validator("task_bundle_sha256")
    @classmethod
    def valid_task_hash(cls, value: str) -> str:
        if not is_sha256(value):
            raise ValueError("task_bundle_sha256 must be a lowercase SHA-256 commitment")
        return value


class CommitmentRequest(ProtocolModel):
    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    protocol_version: Literal[PROTOCOL_VERSION] = PROTOCOL_VERSION
    request_id: str
    genesis_hash: str
    netuid: int = Field(ge=0, le=65535)
    round_start_block: int = Field(ge=0)
    task: TaskReference

    _request_id = field_validator("request_id")(_validate_request_id)

    @field_validator("genesis_hash")
    @classmethod
    def valid_genesis_hash(cls, value: str) -> str:
        if CHAIN_HASH.fullmatch(value) is None:
            raise ValueError("genesis_hash must be 0x followed by 64 lowercase hex digits")
        return value


class RevealRequest(CommitmentRequest):
    commitment_sha256: str

    @field_validator("commitment_sha256")
    @classmethod
    def valid_commitment_hash(cls, value: str) -> str:
        if not is_sha256(value):
            raise ValueError("commitment_sha256 must be a lowercase SHA-256 commitment")
        return value


class ProofCommitment(ProtocolModel):
    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    protocol_version: Literal[PROTOCOL_VERSION] = PROTOCOL_VERSION
    genesis_hash: str
    netuid: int = Field(ge=0, le=65535)
    round_start_block: int = Field(ge=0)
    reveal_after_block: int = Field(ge=0)
    expires_at_block: int = Field(ge=0)
    task: TaskReference
    miner_hotkey: str
    commitment_sha256: str
    signature_scheme: Literal["sr25519", "ed25519"]
    miner_signature: str

    _genesis_hash = field_validator("genesis_hash")(
        CommitmentRequest.valid_genesis_hash.__func__
    )
    _miner_hotkey = field_validator("miner_hotkey")(_validate_ss58)
    _commitment_hash = field_validator("commitment_sha256")(
        RevealRequest.valid_commitment_hash.__func__
    )

    @field_validator("miner_signature")
    @classmethod
    def valid_signature(cls, value: str) -> str:
        if SIGNATURE.fullmatch(value) is None:
            raise ValueError("miner_signature must be a 64-byte lowercase hex signature")
        return value

    @model_validator(mode="after")
    def valid_round_window(self) -> "ProofCommitment":
        if not (
            self.round_start_block
            < self.reveal_after_block
            < self.expires_at_block
        ):
            raise ValueError(
                "proof timing must satisfy round_start < reveal_after < expires_at"
            )
        return self


class ProofReveal(ProtocolModel):
    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    protocol_version: Literal[PROTOCOL_VERSION] = PROTOCOL_VERSION
    commitment: ProofCommitment
    submission_sha256: str
    submission_b64: str = Field(max_length=MAX_SUBMISSION_B64_BYTES)
    salt_hex: str
    signature_scheme: Literal["sr25519", "ed25519"]
    miner_signature: str

    @field_validator("submission_sha256")
    @classmethod
    def valid_submission_hash(cls, value: str) -> str:
        if not is_sha256(value):
            raise ValueError("submission_sha256 must be a lowercase SHA-256 commitment")
        return value

    @field_validator("submission_b64")
    @classmethod
    def valid_submission_b64(cls, value: str) -> str:
        try:
            decoded = base64.b64decode(value, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("submission_b64 must be canonical base64") from exc
        if base64.b64encode(decoded).decode("ascii") != value:
            raise ValueError("submission_b64 must use canonical padded base64")
        if len(decoded) > MAX_SUBMISSION_BYTES:
            raise ValueError("decoded submission exceeds verifier policy")
        return value

    @field_validator("salt_hex")
    @classmethod
    def valid_salt(cls, value: str) -> str:
        if LOWER_HEX_32.fullmatch(value) is None:
            raise ValueError("salt_hex must contain exactly 32 lowercase hex bytes")
        return value

    _miner_signature = field_validator("miner_signature")(
        ProofCommitment.valid_signature.__func__
    )

    @model_validator(mode="after")
    def consistent_signature_scheme(self) -> "ProofReveal":
        if self.signature_scheme != self.commitment.signature_scheme:
            raise ValueError("reveal and commitment signature schemes must match")
        return self

    def submission_bytes(self) -> bytes:
        return base64.b64decode(self.submission_b64, validate=True)


class CommitmentResponse(ProtocolModel):
    request_id: str
    commitment: ProofCommitment

    _request_id = field_validator("request_id")(_validate_request_id)


class RevealResponse(ProtocolModel):
    request_id: str
    reveal: ProofReveal

    _request_id = field_validator("request_id")(_validate_request_id)


class CapabilitiesResponse(ProtocolModel):
    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    protocol_version: Literal[PROTOCOL_VERSION] = PROTOCOL_VERSION
    netuid: int = Field(ge=0, le=65535)
    miner_hotkey: str
    commitment_scheme: Literal["frontier-math-proof-commitment-v1"] = (
        "frontier-math-proof-commitment-v1"
    )
    max_submission_bytes: int = Field(gt=0, le=MAX_SUBMISSION_BYTES)
    round_blocks: int = Field(gt=0)
    commit_blocks: int = Field(gt=0)
    reveal_blocks: int = Field(gt=0)

    _miner_hotkey = field_validator("miner_hotkey")(_validate_ss58)

    @model_validator(mode="after")
    def valid_round_timing(self) -> "CapabilitiesResponse":
        if not 0 < self.commit_blocks < self.reveal_blocks <= self.round_blocks:
            raise ValueError(
                "round timing must satisfy 0 < commit < reveal <= round"
            )
        return self


class HealthResponse(ProtocolModel):
    status: Literal["ok"] = "ok"
    protocol_version: Literal[PROTOCOL_VERSION] = PROTOCOL_VERSION


def canonical_model_bytes(value: ProtocolModel, *, exclude: set[str] | None = None) -> bytes:
    return canonical_json_bytes(value.model_dump(mode="json", exclude=exclude or set()))
