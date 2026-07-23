from __future__ import annotations

import json
import time
from dataclasses import dataclass

import bittensor as bt
from fastapi.testclient import TestClient

from frontier_subnet.auth import HotkeyAllowlistPolicy
from frontier_subnet.chain import ChainSnapshot
from frontier_subnet.commitments import (
    verify_proof_commitment,
    verify_proof_reveal,
)
from frontier_subnet.config import MinerSettings
from frontier_subnet.miner import create_miner_app
from frontier_subnet.protocol import ProofCommitment, ProofReveal, TaskReference
from frontier_subnet.store import SubmissionStore


GENESIS_HASH = "0x" + "42" * 32
TASK = TaskReference(
    task_id="fc-http-test-positive-v1",
    task_bundle_sha256="sha256:" + "ab" * 32,
)
SUBMISSION = b"theorem Bounty.target : True := by\n  trivial\n"


@dataclass
class MutableChain:
    block: int

    async def snapshot(self) -> ChainSnapshot:
        return ChainSnapshot(genesis_hash=GENESIS_HASH, block=self.block)


def _keypair(uri: str):
    return bt.sp_core.Keypair.create_from_uri(uri)


def _request_body(*, commitment_sha256: str | None = None) -> bytes:
    value = {
        "schema_version": 1,
        "protocol_version": 1,
        "request_id": "abcdef12-1234-5678-9234-567812345678",
        "genesis_hash": GENESIS_HASH,
        "netuid": 7,
        "round_start_block": 100,
        "task": TASK.model_dump(mode="json"),
    }
    if commitment_sha256 is not None:
        value["commitment_sha256"] = commitment_sha256
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _signed_headers(
    *,
    caller,
    receiver: str,
    method: str,
    path: str,
    body: bytes = b"",
    content_type: str | None = None,
) -> dict[str, str]:
    headers = bt.http_auth.sign(
        caller,
        method=method,
        path=path,
        body=body,
        receiver_ss58=receiver,
        nonce_ns=time.time_ns(),
    )
    if content_type is not None:
        headers["content-type"] = content_type
    return headers


def _app(tmp_path):
    miner = _keypair("//Alice")
    validator = _keypair("//Bob")
    chain = MutableChain(block=105)
    store = SubmissionStore(tmp_path / "miner.sqlite3")
    store.import_submission(task=TASK, submission=SUBMISSION)
    settings = MinerSettings(
        network="local",
        netuid=7,
        database_path=store.path,
        max_request_bytes=2_048,
        round_blocks=100,
        commit_blocks=10,
        reveal_blocks=100,
    )
    app = create_miner_app(
        settings=settings,
        wallet=miner,
        store=store,
        chain_view=chain,
        caller_policy=HotkeyAllowlistPolicy({validator.ss58_address}),
    )
    return app, miner, validator, chain


def test_signed_commitment_and_timed_reveal_round_trip(tmp_path):
    app, miner, validator, chain = _app(tmp_path)
    receiver = miner.ss58_address

    with TestClient(app) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json() == {"status": "ok", "protocol_version": 1}
        assert "task" not in health.text

        capability_headers = _signed_headers(
            caller=validator,
            receiver=receiver,
            method="GET",
            path="/v1/capabilities",
        )
        capabilities = client.get(
            "/v1/capabilities",
            headers=capability_headers,
        )
        assert capabilities.status_code == 200
        assert capabilities.json()["miner_hotkey"] == receiver

        commitment_body = _request_body()
        commitment_headers = _signed_headers(
            caller=validator,
            receiver=receiver,
            method="POST",
            path="/v1/commitments",
            body=commitment_body,
            content_type="application/json",
        )
        response = client.post(
            "/v1/commitments",
            content=commitment_body,
            headers=commitment_headers,
        )
        assert response.status_code == 200, response.text
        commitment = ProofCommitment.model_validate(response.json()["commitment"])
        assert verify_proof_commitment(commitment)
        assert commitment.reveal_after_block == 110
        assert commitment.expires_at_block == 200

        reveal_body = _request_body(
            commitment_sha256=commitment.commitment_sha256
        )
        early_headers = _signed_headers(
            caller=validator,
            receiver=receiver,
            method="POST",
            path="/v1/reveals",
            body=reveal_body,
            content_type="application/json",
        )
        early = client.post(
            "/v1/reveals",
            content=reveal_body,
            headers=early_headers,
        )
        assert early.status_code == 425

        chain.block = 110
        reveal_headers = _signed_headers(
            caller=validator,
            receiver=receiver,
            method="POST",
            path="/v1/reveals",
            body=reveal_body,
            content_type="application/json; charset=utf-8",
        )
        response = client.post(
            "/v1/reveals",
            content=reveal_body,
            headers=reveal_headers,
        )
        assert response.status_code == 200, response.text
        reveal = ProofReveal.model_validate(response.json()["reveal"])
        assert verify_proof_reveal(reveal)
        assert reveal.submission_bytes() == SUBMISSION


def test_authentication_replay_tampering_and_authorization_fail_closed(tmp_path):
    app, miner, validator, _ = _app(tmp_path)
    receiver = miner.ss58_address
    body = _request_body()
    headers = _signed_headers(
        caller=validator,
        receiver=receiver,
        method="POST",
        path="/v1/commitments",
        body=body,
        content_type="application/json",
    )

    with TestClient(app) as client:
        first = client.post("/v1/commitments", content=body, headers=headers)
        assert first.status_code == 200
        replay = client.post("/v1/commitments", content=body, headers=headers)
        assert replay.status_code == 401
        assert replay.json()["detail"] == "request authentication failed"

        tampered = body.replace(b'"netuid":7', b'"netuid":8')
        tampered_response = client.post(
            "/v1/commitments",
            content=tampered,
            headers=_signed_headers(
                caller=validator,
                receiver=receiver,
                method="POST",
                path="/v1/commitments",
                body=body,
                content_type="application/json",
            ),
        )
        assert tampered_response.status_code == 401

        stranger = _keypair("//Charlie")
        forbidden = client.get(
            "/v1/capabilities",
            headers=_signed_headers(
                caller=stranger,
                receiver=receiver,
                method="GET",
                path="/v1/capabilities",
            ),
        )
        assert forbidden.status_code == 403


def test_miner_rejects_ambiguous_or_oversized_protocol_requests(tmp_path):
    app, miner, validator, _ = _app(tmp_path)
    receiver = miner.ss58_address
    duplicate = _request_body().replace(
        b'"netuid":7',
        b'"netuid":7,"netuid":7',
    )

    with TestClient(app) as client:
        duplicate_response = client.post(
            "/v1/commitments",
            content=duplicate,
            headers=_signed_headers(
                caller=validator,
                receiver=receiver,
                method="POST",
                path="/v1/commitments",
                body=duplicate,
                content_type="application/json",
            ),
        )
        assert duplicate_response.status_code == 422

        wrong_media_body = _request_body()
        wrong_media = client.post(
            "/v1/commitments",
            content=wrong_media_body,
            headers=_signed_headers(
                caller=validator,
                receiver=receiver,
                method="POST",
                path="/v1/commitments",
                body=wrong_media_body,
                content_type="application/json-malicious",
            ),
        )
        assert wrong_media.status_code == 415

        oversized = client.post(
            "/v1/commitments",
            content=b"x" * 2_049,
            headers={"content-type": "application/json"},
        )
        assert oversized.status_code == 413


def test_chain_context_and_round_windows_are_enforced(tmp_path):
    app, miner, validator, chain = _app(tmp_path)
    receiver = miner.ss58_address
    body = _request_body()

    def request():
        return _signed_headers(
            caller=validator,
            receiver=receiver,
            method="POST",
            path="/v1/commitments",
            body=body,
            content_type="application/json",
        )

    with TestClient(app) as client:
        chain.block = 110
        closed = client.post("/v1/commitments", content=body, headers=request())
        assert closed.status_code == 409

        chain.block = 200
        expired = client.post("/v1/commitments", content=body, headers=request())
        assert expired.status_code == 410

        wrong_chain = body.replace(
            GENESIS_HASH.encode(),
            ("0x" + "43" * 32).encode(),
        )
        chain.block = 105
        wrong_chain_response = client.post(
            "/v1/commitments",
            content=wrong_chain,
            headers=_signed_headers(
                caller=validator,
                receiver=receiver,
                method="POST",
                path="/v1/commitments",
                body=wrong_chain,
                content_type="application/json",
            ),
        )
        assert wrong_chain_response.status_code == 409
