from __future__ import annotations

import asyncio
from typing import Any, Protocol

import bittensor as bt
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from frontier_subnet.auth import (
    CallerPolicy,
    FixedWindowRateLimiter,
    authenticate_request,
    parse_strict_json,
)
from frontier_subnet.chain import ChainSnapshot
from frontier_subnet.config import MinerSettings
from frontier_subnet.http import BodyLimitMiddleware
from frontier_subnet.protocol import (
    CapabilitiesResponse,
    CommitmentRequest,
    CommitmentResponse,
    HealthResponse,
    RevealRequest,
    RevealResponse,
)
from frontier_subnet.store import (
    CommitmentConflict,
    CommitmentNotFound,
    SubmissionStore,
    SubmissionUnavailable,
)
from verifier.task_generator import MAX_SUBMISSION_BYTES


class ChainView(Protocol):
    async def snapshot(self) -> ChainSnapshot: ...


def create_miner_app(
    *,
    settings: MinerSettings,
    wallet: Any,
    store: SubmissionStore,
    chain_view: ChainView,
    caller_policy: CallerPolicy,
    nonce_store: Any | None = None,
) -> FastAPI:
    """Create a miner that only serves operator-imported Lean submissions."""

    signer = bt.resolve_signer(wallet, role="hotkey")
    miner_hotkey = signer.ss58_address
    replay_store = (
        nonce_store
        if nonce_store is not None
        else bt.http_auth.InMemoryNonceStore(
            retention=max(
                60.0,
                settings.auth_max_age_seconds
                + settings.auth_allowed_skew_seconds
                + 1.0,
            )
        )
    )
    rate_limiter = FixedWindowRateLimiter(settings.requests_per_minute)
    concurrency = asyncio.Semaphore(settings.max_concurrent_requests)

    app = FastAPI(
        title="Frontier Math submission miner",
        version="1",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(BodyLimitMiddleware, maximum=settings.max_request_bytes)

    async def authenticated(request: Request) -> tuple[bt.http_auth.Caller, bytes]:
        body = await request.body()
        try:
            caller = authenticate_request(
                request=request,
                body=body,
                miner_hotkey=miner_hotkey,
                nonce_store=replay_store,
                max_age=settings.auth_max_age_seconds,
                allowed_skew=settings.auth_allowed_skew_seconds,
            )
        except bt.http_auth.AuthError as exc:
            raise HTTPException(
                status_code=401, detail="request authentication failed"
            ) from exc
        if not await caller_policy.allowed(caller.hotkey_ss58):
            raise HTTPException(status_code=403, detail="authenticated caller is not authorized")
        if not rate_limiter.allow(caller.hotkey_ss58, request.url.path):
            raise HTTPException(status_code=429, detail="request rate limit exceeded")
        return caller, body

    def require_json(request: Request) -> None:
        media_type = request.headers.get("content-type", "").partition(";")[0]
        if media_type.strip().lower() != "application/json":
            raise HTTPException(
                status_code=415, detail="content type must be application/json"
            )

    async def snapshot_for(
        *,
        genesis_hash: str,
        netuid: int,
        round_start_block: int,
        committing: bool,
    ) -> ChainSnapshot:
        try:
            snapshot = await chain_view.snapshot()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="chain state is unavailable") from exc
        if snapshot.genesis_hash != genesis_hash or netuid != settings.netuid:
            raise HTTPException(status_code=409, detail="request chain context does not match")
        if settings.round_start(snapshot.block) != round_start_block:
            if snapshot.block >= round_start_block + settings.reveal_blocks:
                raise HTTPException(status_code=410, detail="proof round has expired")
            raise HTTPException(status_code=409, detail="request is not for the active proof round")
        if committing and snapshot.block >= round_start_block + settings.commit_blocks:
            raise HTTPException(status_code=409, detail="proof commitment window is closed")
        return snapshot

    @app.get("/healthz", response_model=HealthResponse)
    async def healthz() -> HealthResponse:
        return HealthResponse()

    @app.get("/v1/capabilities", response_model=CapabilitiesResponse)
    async def capabilities(request: Request) -> CapabilitiesResponse:
        await authenticated(request)
        return CapabilitiesResponse(
            netuid=settings.netuid,
            miner_hotkey=miner_hotkey,
            max_submission_bytes=MAX_SUBMISSION_BYTES,
            round_blocks=settings.round_blocks,
            commit_blocks=settings.commit_blocks,
            reveal_blocks=settings.reveal_blocks,
        )

    @app.post("/v1/commitments", response_model=CommitmentResponse)
    async def commitments(request: Request) -> CommitmentResponse:
        _, body = await authenticated(request)
        async with concurrency:
            require_json(request)
            try:
                value = parse_strict_json(body, CommitmentRequest)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            await snapshot_for(
                genesis_hash=value.genesis_hash,
                netuid=value.netuid,
                round_start_block=value.round_start_block,
                committing=True,
            )
            try:
                commitment = await run_in_threadpool(
                    store.create_commitment,
                    genesis_hash=value.genesis_hash,
                    netuid=value.netuid,
                    round_start_block=value.round_start_block,
                    reveal_after_block=settings.reveal_after_block(value.round_start_block),
                    expires_at_block=settings.expires_at_block(value.round_start_block),
                    task=value.task,
                    wallet=wallet,
                )
            except SubmissionUnavailable as exc:
                raise HTTPException(
                    status_code=404, detail="submission is unavailable"
                ) from exc
            except CommitmentConflict as exc:
                raise HTTPException(
                    status_code=409, detail="proof round configuration changed"
                ) from exc
        return CommitmentResponse(request_id=value.request_id, commitment=commitment)

    @app.post("/v1/reveals", response_model=RevealResponse)
    async def reveals(request: Request) -> RevealResponse:
        _, body = await authenticated(request)
        async with concurrency:
            require_json(request)
            try:
                value = parse_strict_json(body, RevealRequest)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            snapshot = await snapshot_for(
                genesis_hash=value.genesis_hash,
                netuid=value.netuid,
                round_start_block=value.round_start_block,
                committing=False,
            )
            reveal_after = settings.reveal_after_block(value.round_start_block)
            expires_at = settings.expires_at_block(value.round_start_block)
            if snapshot.block < reveal_after:
                raise HTTPException(
                    status_code=425, detail="proof reveal is not available yet"
                )
            if snapshot.block >= expires_at:
                raise HTTPException(status_code=410, detail="proof round has expired")
            try:
                reveal = await run_in_threadpool(
                    store.reveal,
                    genesis_hash=value.genesis_hash,
                    netuid=value.netuid,
                    round_start_block=value.round_start_block,
                    task=value.task,
                    miner_hotkey=miner_hotkey,
                    commitment_sha256=value.commitment_sha256,
                )
            except CommitmentNotFound as exc:
                raise HTTPException(
                    status_code=404, detail="proof commitment was not found"
                ) from exc
        return RevealResponse(request_id=value.request_id, reveal=reveal)

    return app
