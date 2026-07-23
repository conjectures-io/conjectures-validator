from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, TypeVar

import bittensor as bt
from pydantic import ValidationError
from starlette.requests import Request

from frontier_subnet.protocol import ProtocolModel


class CallerPolicy(Protocol):
    async def allowed(self, hotkey_ss58: str) -> bool: ...


class AuthenticatedCallerPolicy:
    """Development-only policy that accepts every correctly signed caller."""

    async def allowed(self, hotkey_ss58: str) -> bool:
        return bool(hotkey_ss58)


class HotkeyAllowlistPolicy:
    def __init__(self, hotkeys: set[str] | frozenset[str]):
        if any(
            not isinstance(hotkey, str)
            or not bt.wallets.is_bittensor_address(hotkey)
            for hotkey in hotkeys
        ):
            raise ValueError("validator allowlist contains an invalid Bittensor hotkey")
        self.hotkeys = frozenset(hotkeys)

    async def allowed(self, hotkey_ss58: str) -> bool:
        return hotkey_ss58 in self.hotkeys


class MetagraphValidatorPolicy:
    """Fail-closed validator-permit policy with a short metagraph cache."""

    def __init__(
        self,
        *,
        network: str,
        netuid: int,
        refresh_seconds: float = 30.0,
        min_validator_tao: float = 0.0,
        loader: Callable[[], Awaitable[Any]] | None = None,
    ):
        self.network = network
        self.netuid = netuid
        self.refresh_seconds = refresh_seconds
        self.min_validator_tao = min_validator_tao
        self._loader = loader or self._load
        self._validators: frozenset[str] = frozenset()
        self._refreshed_at = float("-inf")
        self._lock = asyncio.Lock()

    async def _load(self) -> Any:
        async with bt.Subtensor(self.network) as client:
            return await client.subnets.metagraph(self.netuid, commitments=False)

    async def _refresh(self) -> None:
        now = time.monotonic()
        if now - self._refreshed_at < self.refresh_seconds:
            return
        async with self._lock:
            now = time.monotonic()
            if now - self._refreshed_at < self.refresh_seconds:
                return
            try:
                metagraph = await self._loader()
                if metagraph is None:
                    raise RuntimeError("subnet metagraph is unavailable")
                threshold = bt.tao(self.min_validator_tao)
                validators = frozenset(
                    neuron.hotkey
                    for neuron in metagraph.neurons
                    if neuron.validator_permit and neuron.tao_stake >= threshold
                )
            except Exception:
                self._validators = frozenset()
                self._refreshed_at = now
                return
            self._validators = validators
            self._refreshed_at = now

    async def allowed(self, hotkey_ss58: str) -> bool:
        await self._refresh()
        return hotkey_ss58 in self._validators


def raw_request_target(request: Request) -> str:
    raw_path = request.scope.get("raw_path", request.url.path.encode("ascii"))
    target = bytes(raw_path).decode("ascii", errors="strict")
    query = bytes(request.scope.get("query_string", b""))
    if query:
        target += "?" + query.decode("ascii", errors="strict")
    return target


def authenticate_request(
    *,
    request: Request,
    body: bytes,
    miner_hotkey: str,
    nonce_store: Any,
    max_age: float,
    allowed_skew: float,
) -> bt.http_auth.Caller:
    return bt.http_auth.verify(
        request.headers,
        body,
        method=request.method,
        path=raw_request_target(request),
        self_hotkey_ss58=miner_hotkey,
        max_age=max_age,
        allowed_skew=allowed_skew,
        require_receiver=True,
        nonce_store=nonce_store,
    )


T = TypeVar("T", bound=ProtocolModel)


def parse_strict_json(body: bytes, model: type[T]) -> T:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(
            body.decode("utf-8", errors="strict"),
            object_pairs_hook=unique,
            parse_constant=lambda item: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant: {item}")
            ),
        )
        if not isinstance(value, dict):
            raise ValueError("request JSON must be an object")
        return model.model_validate(value)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise ValueError("request body does not match the protocol schema") from exc


class FixedWindowRateLimiter:
    """Small single-process limiter keyed by authenticated hotkey and route."""

    def __init__(self, requests: int, window_seconds: float = 60.0):
        self.requests = requests
        self.window_seconds = window_seconds
        self._events: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, hotkey: str, route: str, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        key = (hotkey, route)
        with self._lock:
            events = self._events[key]
            cutoff = current - self.window_seconds
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self.requests:
                return False
            events.append(current)
            return True
