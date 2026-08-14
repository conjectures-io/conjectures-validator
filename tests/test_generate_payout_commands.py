from __future__ import annotations

import importlib.util
import io
import json
import shlex
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


def load_generator():
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "generate_payout_commands", root / "scripts" / "generate_payout_commands.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_render_command_preserves_the_integer_amount_and_call_shape():
    generator = load_generator()
    command = generator.render_command(
        destination_coldkey="coldkey",
        destination_hotkey="destination-hotkey",
        alpha_amount=1_044_286_814_577,
        origin_hotkey="origin-hotkey",
        origin_netuid=66,
        destination_netuid=66,
        proxy_for="proxy",
        multisig="team-mainnet",
        wallet="signer wallet",
        network="finney",
    )

    # A multiline command joined by shell continuations is still one ordinary argv vector.
    argv = shlex.split(command.replace("\\\n", ""))
    assert argv[:3] == [
        "btcli",
        "call",
        "SubtensorModule.transfer_stake_and_hotkey",
    ]
    call_args = json.loads(argv[argv.index("--args") + 1])
    assert call_args == {
        "destination_coldkey": "coldkey",
        "origin_hotkey": "origin-hotkey",
        "destination_hotkey": "destination-hotkey",
        "origin_netuid": 66,
        "destination_netuid": 66,
        "alpha_amount": 1_044_286_814_577,
    }
    assert argv[argv.index("--proxy-for") + 1] == "proxy"
    assert argv[argv.index("--multisig") + 1] == "team-mainnet"
    assert argv[argv.index("-w") + 1] == "signer wallet"
    assert argv[argv.index("-n") + 1] == "finney"


def test_render_payouts_prints_one_identical_call_per_signer():
    generator = load_generator()
    output = generator.render_payouts(
        [(7, "submission-uuid", "coldkey", "hotkey", 123)],
        wallets=("signer-a", "signer-b"),
        origin_hotkey="origin",
        origin_netuid=66,
        destination_netuid=66,
        proxy_for="proxy",
        multisig="multisig",
        network="finney",
    )

    assert output.startswith(
        "# signer=signer-a\n# reward_event=7 submission=submission-uuid\n"
    )
    assert output.count("btcli call SubtensorModule.transfer_stake_and_hotkey") == 2
    assert output.count('"alpha_amount":123') == 2
    assert "  -w signer-a \\\n" in output
    assert "  -w signer-b \\\n" in output


def test_multiple_payouts_are_grouped_by_signer():
    generator = load_generator()
    output = generator.render_payouts(
        [
            (7, "submission-7", "coldkey-7", "hotkey-7", 700),
            (8, "submission-8", "coldkey-8", "hotkey-8", 800),
        ],
        wallets=("signer-a", "signer-b"),
        origin_hotkey="origin",
        origin_netuid=66,
        destination_netuid=66,
        proxy_for="proxy",
        multisig="multisig",
        network="finney",
    )

    signer_a, signer_b = output.split("\n\n# signer=signer-b\n", maxsplit=1)
    assert signer_a.startswith("# signer=signer-a\n")
    assert signer_a.count("-w signer-a") == 2
    assert "-w signer-b" not in signer_a
    assert signer_a.index("reward_event=7") < signer_a.index("reward_event=8")
    assert signer_b.count("-w signer-b") == 2
    assert "-w signer-a" not in signer_b
    assert signer_b.index("reward_event=7") < signer_b.index("reward_event=8")


def test_default_output_matches_the_two_requested_wallets():
    generator = load_generator()
    output = generator.render_payouts(
        [
            (
                1,
                "submission-uuid",
                "5G4LNpyehdqUU6CtP7SYSZLFK5mxzTCihXHiDxmbfHwAAW7L",
                "5FqLp5QmNRiHGyj3xbLVnDHfCx25qxJX5CUhpndF9GFfZZiK",
                1_044_286_814_577,
            )
        ],
        wallets=generator.DEFAULT_WALLETS,
        origin_hotkey=generator.DEFAULT_ORIGIN_HOTKEY,
        origin_netuid=generator.DEFAULT_NETUID,
        destination_netuid=generator.DEFAULT_NETUID,
        proxy_for=generator.DEFAULT_PROXY_FOR,
        multisig=generator.DEFAULT_MULTISIG,
        network=generator.DEFAULT_NETWORK,
    )

    assert "-w 5DkFoRP1gaKrq1LRqWbG1SCHuhHgDELUuRXdGLsv2rU1spsX" in output
    assert "-w conjectures-mainnet-signer-proxy-member" in output
    assert "-w 5CvtfodyyJWU2pxa25QpC2DTvnNuwQEp5HNS4ntMF8Be8BJL" not in output
    assert output.count("-n finney") == 2
    assert '"alpha_amount":1044286814577' in output


def test_discord_messages_mention_the_signer_for_each_wallet():
    generator = load_generator()
    payloads = generator.discord_notifications(
        [(7, "submission-uuid", "coldkey", "hotkey", 123)],
        wallets=generator.DEFAULT_WALLETS,
        mentions=generator.DEFAULT_DISCORD_MENTIONS,
        origin_hotkey="origin",
        origin_netuid=66,
        destination_netuid=66,
        proxy_for="proxy",
        multisig="multisig",
        network="finney",
    )

    assert len(payloads) == 2
    assert payloads[0]["content"].startswith(
        "<@1103995314299490425> payout ready for reward event `7`"
    )
    assert "-w 5DkFoRP1gaKrq1LRqWbG1SCHuhHgDELUuRXdGLsv2rU1spsX" in payloads[0][
        "content"
    ]
    assert payloads[0]["allowed_mentions"] == {
        "parse": [],
        "users": ["1103995314299490425"],
    }
    assert payloads[1]["content"].startswith(
        "<@213454129819942912> payout ready for reward event `7`"
    )
    wejhs_notice = str(payloads[1]["content"])
    assert "-w conjectures-mainnet-signer-proxy-member" in wejhs_notice
    assert "-w 5CvtfodyyJWU2pxa25QpC2DTvnNuwQEp5HNS4ntMF8Be8BJL" not in wejhs_notice
    assert "Paste only the command inside the shell code block into Bash" in wejhs_notice
    assert "backticks are command substitution" in wejhs_notice
    assert "`-w` expects the local wallet name, not its SS58 address" in wejhs_notice
    assert "complete output containing `call_hash` and `timepoint`" in wejhs_notice
    assert "confirmed that exact call is pending for `multisig`" in wejhs_notice
    assert "would open a new payout instead of signing the existing one" in wejhs_notice
    assert len(wejhs_notice) <= 2_000


def test_discord_wallet_path_matches_its_key_suffix():
    generator = load_generator()
    wallet = "/wallets/team/5DkFoRP1gaKrq1LRqWbG1SCHuhHgDELUuRXdGLsv2rU1spsX"
    payloads = generator.discord_notifications(
        [(7, "submission-uuid", "coldkey", "hotkey", 123)],
        wallets=(wallet,),
        mentions=generator.DEFAULT_DISCORD_MENTIONS,
        origin_hotkey="origin",
        origin_netuid=66,
        destination_netuid=66,
        proxy_for="proxy",
        multisig="multisig",
        network="finney",
    )

    assert payloads[0]["content"].startswith("<@1103995314299490425>")
    assert f"-w {wallet}" in payloads[0]["content"]


def test_discord_delivery_posts_json_without_exposing_the_webhook(monkeypatch):
    generator = load_generator()
    discord = importlib.import_module("payout_notifier.discord")
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def getcode(self):
            return 204

        def read(self):
            return b""

    def open_request(request, *, timeout):
        requests.append((request, timeout))
        return Response()

    monkeypatch.setattr(discord.urllib.request, "urlopen", open_request)
    payload = {
        "content": "<@1103995314299490425> command",
        "allowed_mentions": {"parse": [], "users": ["1103995314299490425"]},
    }
    sent = generator.send_discord_notifications(
        "https://discord.com/api/webhooks/123/secret-token", [payload]
    )

    assert sent == 1
    assert len(requests) == 1
    request, timeout = requests[0]
    assert timeout == 15.0
    assert request.get_method() == "POST"
    assert json.loads(request.data) == payload
    assert request.headers["Content-type"] == "application/json"


def test_discord_delivery_rejects_non_discord_webhooks_before_network(monkeypatch):
    generator = load_generator()
    discord = importlib.import_module("payout_notifier.discord")
    called = False

    def open_request(_request, *, timeout):
        nonlocal called
        called = True

    monkeypatch.setattr(discord.urllib.request, "urlopen", open_request)

    try:
        generator.send_discord_notifications(
            "https://example.com/api/webhooks/123/secret-token", []
        )
    except ValueError as exc:
        assert "discord.com" in str(exc)
    else:
        raise AssertionError("non-Discord webhook was accepted")
    assert called is False


def test_selecting_multiple_events_refuses_a_partial_result():
    generator = load_generator()
    generator.pending_payouts = lambda _dsn, _ids, **_kwargs: [
        (7, "submission-uuid", "coldkey", "hotkey", 123)
    ]
    stdout = io.StringIO()
    stderr = io.StringIO()

    with redirect_stdout(stdout), redirect_stderr(stderr):
        result = generator.main(
            ["--dsn", "unused", "--event-id", "7", "--event-id", "8"]
        )

    assert result == 1
    assert stdout.getvalue() == ""
    assert "reward event(s) 8" in stderr.getvalue()


def test_query_is_read_only_and_event_ids_are_deduplicated():
    generator = load_generator()
    query = generator._query([8, 7, 8])

    assert query.startswith("BEGIN READ ONLY;\n")
    assert "AND id IN (8,7)" in query
    assert query.endswith("ROLLBACK;\n")
