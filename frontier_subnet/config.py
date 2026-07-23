from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path


DEFAULT_ROUND_BLOCKS = 360
DEFAULT_COMMIT_BLOCKS = 120
DEFAULT_REVEAL_BLOCKS = 360


@dataclass(frozen=True)
class MinerSettings:
    """Validated runtime settings for the submission-only miner."""

    network: str
    netuid: int
    database_path: Path
    host: str = "127.0.0.1"
    port: int = 8091
    public_ip: str | None = None
    public_port: int | None = None
    max_request_bytes: int = 16 * 1024
    auth_max_age_seconds: float = 10.0
    auth_allowed_skew_seconds: float = 2.0
    metagraph_refresh_seconds: float = 30.0
    min_validator_tao: float = 0.0
    round_blocks: int = DEFAULT_ROUND_BLOCKS
    commit_blocks: int = DEFAULT_COMMIT_BLOCKS
    reveal_blocks: int = DEFAULT_REVEAL_BLOCKS
    requests_per_minute: int = 60
    max_concurrent_requests: int = 16

    def __post_init__(self) -> None:
        if (
            not isinstance(self.network, str)
            or not self.network
            or self.network != self.network.strip()
            or any(ord(char) < 32 for char in self.network)
        ):
            raise ValueError("network must be a non-empty endpoint or network name")
        integer_settings = (
            self.netuid,
            self.port,
            self.max_request_bytes,
            self.round_blocks,
            self.commit_blocks,
            self.reveal_blocks,
            self.requests_per_minute,
            self.max_concurrent_requests,
        )
        if any(type(value) is not int for value in integer_settings):
            raise ValueError("integer settings must use integer values")
        if self.public_port is not None and type(self.public_port) is not int:
            raise ValueError("public port must use an integer value")
        float_settings = (
            self.auth_max_age_seconds,
            self.auth_allowed_skew_seconds,
            self.metagraph_refresh_seconds,
            self.min_validator_tao,
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in float_settings
        ):
            raise ValueError("authentication and validator settings must be finite")
        if not 0 <= self.netuid <= 65535:
            raise ValueError("netuid must be between 0 and 65535")
        if not self.host:
            raise ValueError("host must not be empty")
        if not 1 <= self.port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        if self.public_port is not None and not 1 <= self.public_port <= 65535:
            raise ValueError("public port must be between 1 and 65535")
        if not 0 < self.max_request_bytes <= 1024 * 1024:
            raise ValueError("max request bytes must be between 1 and 1048576")
        if self.auth_max_age_seconds <= 0 or self.auth_allowed_skew_seconds < 0:
            raise ValueError("authentication time windows are invalid")
        if self.metagraph_refresh_seconds <= 0 or self.min_validator_tao < 0:
            raise ValueError("validator policy settings are invalid")
        if self.round_blocks <= 0:
            raise ValueError("round blocks must be positive")
        if not 0 < self.commit_blocks < self.reveal_blocks <= self.round_blocks:
            raise ValueError(
                "round timing must satisfy 0 < commit_blocks < reveal_blocks <= round_blocks"
            )
        if self.requests_per_minute <= 0 or self.max_concurrent_requests <= 0:
            raise ValueError("request limits must be positive")

    @property
    def advertised_port(self) -> int:
        return self.public_port or self.port

    def round_start(self, block: int) -> int:
        if block < 0:
            raise ValueError("block must be non-negative")
        return block - (block % self.round_blocks)

    def reveal_after_block(self, round_start: int) -> int:
        return round_start + self.commit_blocks

    def expires_at_block(self, round_start: int) -> int:
        return round_start + self.reveal_blocks
