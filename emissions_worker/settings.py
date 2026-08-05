from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class SettingsError(ValueError):
    """The emissions worker cannot start safely with its current environment."""


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise SettingsError(f"{name} is required")
    return value


@dataclass(frozen=True)
class EmissionsSettings:
    network: str
    wallet_name: str
    wallet_hotkey: str
    wallet_path: Path
    retry_seconds: float

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> EmissionsSettings:
        values = os.environ if env is None else env
        network = values.get("EMISSIONS_NETWORK", "finney").strip()
        if not network:
            raise SettingsError("EMISSIONS_NETWORK must not be empty")
        try:
            retry_seconds = float(values.get("EMISSIONS_RETRY_SECONDS", "30"))
        except ValueError as exc:
            raise SettingsError("EMISSIONS_RETRY_SECONDS must be a number") from exc
        if not 1 <= retry_seconds <= 3600:
            raise SettingsError("EMISSIONS_RETRY_SECONDS must be between 1 and 3600")
        return cls(
            network=network,
            wallet_name=_required(values, "EMISSIONS_WALLET_NAME"),
            wallet_hotkey=_required(values, "EMISSIONS_WALLET_HOTKEY"),
            wallet_path=Path(
                values.get("EMISSIONS_WALLET_PATH", "~/.bittensor/wallets")
            ).expanduser(),
            retry_seconds=retry_seconds,
        )


__all__ = ["EmissionsSettings", "SettingsError"]
