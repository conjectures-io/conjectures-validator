"""Shared payout-command rendering and safe Discord webhook delivery."""

from __future__ import annotations

import json
import shlex
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from urllib.parse import urlsplit

# Public chain identities, not secrets. The generator and watcher deliberately use the same
# constants so the command printed to an operator is byte-for-byte the command sent to a signer.
DEFAULT_ORIGIN_HOTKEY = "5Gn2SyG6PmBstAjiPD93CTuxADqYaYqf6fKeFuezKsX7Chf9"
DEFAULT_PROXY_FOR = "5HMqFHmvUpzuAjEnse3hzMKS5LsFL428hffCfenF2smuGNhs"
DEFAULT_MULTISIG = "team-mainnet"
# These are signer identities used for delivery ownership and Discord routing.  ``btcli -w``
# takes a local wallet name instead, which is not necessarily the signer's SS58 address.
DEFAULT_WALLETS = (
    "5DkFoRP1gaKrq1LRqWbG1SCHuhHgDELUuRXdGLsv2rU1spsX",
    "5CvtfodyyJWU2pxa25QpC2DTvnNuwQEp5HNS4ntMF8Be8BJL",
)
DEFAULT_DISCORD_MENTIONS = {
    "5DkFoRP1gaKrq1LRqWbG1SCHuhHgDELUuRXdGLsv2rU1spsX": "1103995314299490425",
    "5CvtfodyyJWU2pxa25QpC2DTvnNuwQEp5HNS4ntMF8Be8BJL": "213454129819942912",
}
WEJH_SIGNER = "5CvtfodyyJWU2pxa25QpC2DTvnNuwQEp5HNS4ntMF8Be8BJL"
DEFAULT_CLI_WALLETS = {
    WEJH_SIGNER: "conjectures-mainnet-signer-proxy-member",
}
DEFAULT_NETUID = 66
DEFAULT_NETWORK = "finney"
DISCORD_CONTENT_LIMIT = 2_000

PayoutRow = tuple[int, str, str, str, int]


def _cli_wallet(signer_wallet: str, wallet_names: Mapping[str, str]) -> str:
    """Return the local btcli wallet name for one signer identity."""
    return wallet_names.get(signer_wallet, signer_wallet)


def _discord_instructions(signer_wallet: str, *, multisig: str) -> str:
    """Signer-specific operational instructions that are safe across future payouts."""
    if signer_wallet != WEJH_SIGNER:
        return ""
    return (
        "Before using the command:\n"
        "1. Paste only the command inside the shell code block into Bash. Do not paste "
        "Markdown prose: backticks are command substitution, and prose parentheses can cause "
        "a shell syntax error.\n"
        "2. `-w` expects the local wallet name, not its SS58 address. This command already uses "
        "`conjectures-mainnet-signer-proxy-member`.\n\n"
        "Do not run it until the first signer sends their complete output containing `call_hash` "
        f"and `timepoint`, and you have confirmed that exact call is pending for `{multisig}`. "
        "If no pending record exists, running the command would open a new payout instead of "
        "signing the existing one.\n\n"
    )


def render_command(
    *,
    destination_coldkey: str,
    destination_hotkey: str,
    alpha_amount: int,
    origin_hotkey: str,
    origin_netuid: int,
    destination_netuid: int,
    proxy_for: str,
    multisig: str,
    wallet: str,
    network: str,
) -> str:
    """Render one shell-safe, multiline btcli invocation."""
    call_args = json.dumps(
        {
            "destination_coldkey": destination_coldkey,
            "origin_hotkey": origin_hotkey,
            "destination_hotkey": destination_hotkey,
            "origin_netuid": origin_netuid,
            "destination_netuid": destination_netuid,
            "alpha_amount": alpha_amount,
        },
        separators=(",", ":"),
    )
    return "\n".join(
        (
            "btcli call SubtensorModule.transfer_stake_and_hotkey \\",
            f"  --args {shlex.quote(call_args)} \\",
            f"  --proxy-for {shlex.quote(proxy_for)} \\",
            f"  --multisig {shlex.quote(multisig)} \\",
            f"  -w {shlex.quote(wallet)} \\",
            f"  -n {shlex.quote(network)}",
        )
    )


def render_payouts(
    rows: Sequence[PayoutRow],
    *,
    wallets: Sequence[str],
    origin_hotkey: str,
    origin_netuid: int,
    destination_netuid: int,
    proxy_for: str,
    multisig: str,
    network: str,
    wallet_names: Mapping[str, str] | None = None,
) -> str:
    """Render all payouts grouped into one independently pasteable block per signer."""
    configured_wallet_names = DEFAULT_CLI_WALLETS if wallet_names is None else wallet_names
    signer_blocks: list[str] = []
    for signer_wallet in wallets:
        payout_blocks: list[str] = []
        for event_id, submission_id, coldkey, hotkey, amount in rows:
            command = render_command(
                destination_coldkey=coldkey,
                destination_hotkey=hotkey,
                alpha_amount=amount,
                origin_hotkey=origin_hotkey,
                origin_netuid=origin_netuid,
                destination_netuid=destination_netuid,
                proxy_for=proxy_for,
                multisig=multisig,
                wallet=_cli_wallet(signer_wallet, configured_wallet_names),
                network=network,
            )
            payout_blocks.append(
                f"# reward_event={event_id} submission={submission_id}\n{command}"
            )
        signer_blocks.append(
            f"# signer={signer_wallet}\n" + "\n\n".join(payout_blocks)
        )
    return "\n\n".join(signer_blocks)


def discord_notifications(
    rows: Sequence[PayoutRow],
    *,
    wallets: Sequence[str],
    mentions: dict[str, str],
    origin_hotkey: str,
    origin_netuid: int,
    destination_netuid: int,
    proxy_for: str,
    multisig: str,
    network: str,
    wallet_names: Mapping[str, str] | None = None,
) -> list[dict[str, object]]:
    """Build one bounded Discord message for each payout and signer."""
    configured_wallet_names = DEFAULT_CLI_WALLETS if wallet_names is None else wallet_names
    payloads: list[dict[str, object]] = []
    for signer_wallet in wallets:
        matching_mentions = {
            user_id
            for suffix, user_id in mentions.items()
            if signer_wallet.endswith(suffix)
        }
        if len(matching_mentions) != 1:
            raise ValueError(
                f"wallet {signer_wallet!r} must match exactly one configured Discord mention"
            )
        user_id = matching_mentions.pop()
        if not user_id.isascii() or not user_id.isdigit():
            raise ValueError(
                f"Discord user id for wallet {signer_wallet!r} must contain only digits"
            )

        for event_id, submission_id, coldkey, hotkey, amount in rows:
            command = render_command(
                destination_coldkey=coldkey,
                destination_hotkey=hotkey,
                alpha_amount=amount,
                origin_hotkey=origin_hotkey,
                origin_netuid=origin_netuid,
                destination_netuid=destination_netuid,
                proxy_for=proxy_for,
                multisig=multisig,
                wallet=_cli_wallet(signer_wallet, configured_wallet_names),
                network=network,
            )
            content = (
                f"<@{user_id}> payout ready for reward event `{event_id}` "
                f"(submission `{submission_id}`):\n"
                f"{_discord_instructions(signer_wallet, multisig=multisig)}"
                f"```sh\n{command}\n```"
            )
            if len(content) > DISCORD_CONTENT_LIMIT:
                raise ValueError(
                    f"Discord message for reward event {event_id} exceeds "
                    f"{DISCORD_CONTENT_LIMIT} characters"
                )
            payloads.append(
                {
                    "content": content,
                    "allowed_mentions": {"parse": [], "users": [user_id]},
                }
            )
    return payloads


def validate_discord_webhook(webhook_url: str) -> None:
    parsed = urlsplit(webhook_url)
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not (hostname == "discord.com" or hostname.endswith(".discord.com"))
        or not parsed.path.startswith("/api/webhooks/")
    ):
        raise ValueError("Discord webhook must be an HTTPS discord.com /api/webhooks URL")


def send_discord_notifications(
    webhook_url: str,
    payloads: Sequence[dict[str, object]],
    *,
    timeout_seconds: float = 15.0,
) -> int:
    """POST prepared payout messages, returning the number Discord accepted."""
    validate_discord_webhook(webhook_url)
    sent = 0
    for payload in payloads:
        request = urllib.request.Request(
            webhook_url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "conjectures-payout-notifier/1",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                status = response.getcode()
                response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read(1_000).decode("utf-8", errors="replace").strip()
            suffix = f": {detail}" if detail else ""
            raise RuntimeError(f"Discord webhook returned HTTP {exc.code}{suffix}") from None
        except urllib.error.URLError as exc:
            raise RuntimeError("Discord webhook request failed") from exc
        if not 200 <= status < 300:
            raise RuntimeError(f"Discord webhook returned HTTP {status}")
        sent += 1
    return sent


__all__ = [
    "DEFAULT_CLI_WALLETS",
    "DEFAULT_DISCORD_MENTIONS",
    "DEFAULT_MULTISIG",
    "DEFAULT_NETUID",
    "DEFAULT_NETWORK",
    "DEFAULT_ORIGIN_HOTKEY",
    "DEFAULT_PROXY_FOR",
    "DEFAULT_WALLETS",
    "discord_notifications",
    "render_command",
    "render_payouts",
    "send_discord_notifications",
    "validate_discord_webhook",
]
