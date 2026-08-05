from __future__ import annotations

from dataclasses import dataclass

import pytest

from emissions_worker.settings import EmissionsSettings, SettingsError
from emissions_worker.worker import NETUID, TREASURY_UID, TreasuryWeightWorker


@dataclass(frozen=True)
class Epoch:
    block: int


class Result:
    def __init__(self) -> None:
        self.checked = False

    def raise_for_failure(self):
        self.checked = True
        return self


class Client:
    def __init__(self, *, fail_once: bool = False) -> None:
        self.waited: list[int] = []
        self.executed = []
        self.fail_once = fail_once
        self.result = Result()

    def wait_for_epoch(self, netuid: int, *, timeout=None):
        del timeout
        self.waited.append(netuid)
        return Epoch(block=1234)

    def execute(self, intent, wallet, *, retries=2):
        self.executed.append((intent, wallet, retries))
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("temporary chain failure")
        return self.result


def test_policy_is_hardcoded_to_subnet_66_treasury_uid_121():
    assert NETUID == 66
    assert TREASURY_UID == 121


def test_one_epoch_sets_the_single_treasury_weight():
    client = Client()
    worker = TreasuryWeightWorker(client=client, wallet="validator")

    epoch, result = worker.run_epoch()

    assert epoch.block == 1234
    assert client.waited == [66]
    assert len(client.executed) == 1
    intent, wallet, retries = client.executed[0]
    assert intent.netuid == 66
    assert intent.uids == [121]
    assert intent.weights == [1.0]
    assert wallet == "validator"
    assert retries == 2
    assert result.checked


def test_submission_retries_without_waiting_for_another_epoch():
    client = Client(fail_once=True)
    sleeps = []
    worker = TreasuryWeightWorker(
        client=client,
        wallet="validator",
        retry_seconds=7,
        sleep=sleeps.append,
    )

    worker.run_epoch()

    assert client.waited == [66]
    assert len(client.executed) == 2
    assert sleeps == [7]


def test_settings_require_the_signing_wallet():
    with pytest.raises(SettingsError, match="EMISSIONS_WALLET_NAME"):
        EmissionsSettings.from_env({})
    with pytest.raises(SettingsError, match="EMISSIONS_WALLET_HOTKEY"):
        EmissionsSettings.from_env({"EMISSIONS_WALLET_NAME": "validator"})


def test_settings_keep_policy_out_of_the_environment():
    settings = EmissionsSettings.from_env(
        {
            "EMISSIONS_WALLET_NAME": "validator",
            "EMISSIONS_WALLET_HOTKEY": "default",
            "EMISSIONS_WALLET_PATH": "/wallets",
            "EMISSIONS_RETRY_SECONDS": "12",
        }
    )

    assert settings.network == "finney"
    assert str(settings.wallet_path) == "/wallets"
    assert settings.retry_seconds == 12
