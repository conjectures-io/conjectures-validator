from __future__ import annotations

from pathlib import Path

import pytest

from conjectures_subnet.payout_confirm import validate_payout_transfer
from submission_api.payments import FinalizedTransfer


TREASURY = "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"
DESTINATION = "5C4hrfjw9DjXZTzV3MwzrrAr9P1MJhSrvWGWqi1eSuyUpnhM"


def transfer(**overrides) -> FinalizedTransfer:
    values = {
        "reference": "100-0001",
        "sender": TREASURY,
        "recipient": DESTINATION,
        "amount_rao": 1_000_000_000,
        "block": 100,
    }
    values.update(overrides)
    return FinalizedTransfer(**values)


def test_manual_payout_confirmation_matches_every_frozen_field():
    validate_payout_transfer(
        treasury_account=TREASURY,
        destination_coldkey=DESTINATION,
        amount_rao=1_000_000_000,
        transfer=transfer(),
    )

    for mismatch in (
        {"sender": DESTINATION},
        {"recipient": TREASURY},
        {"amount_rao": 999_999_999},
    ):
        with pytest.raises(RuntimeError, match="does not match the payout instruction"):
            validate_payout_transfer(
                treasury_account=TREASURY,
                destination_coldkey=DESTINATION,
                amount_rao=1_000_000_000,
                transfer=transfer(**mismatch),
            )


def test_payout_commands_contain_no_wallet_or_broadcast_path():
    root = Path(__file__).resolve().parent.parent / "conjectures_subnet"
    source = "\n".join(
        (root / name).read_text()
        for name in ("payout_intent.py", "payout_confirm.py", "chain.py")
    )
    assert "bt.Wallet" not in source
    assert "client.execute" not in source
    assert "wait_for_inclusion" not in source
