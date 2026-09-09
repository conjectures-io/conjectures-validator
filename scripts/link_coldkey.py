#!/usr/bin/env python3
"""Create an account with a coldkey and designate it for submitting — what the website will do.

`conjectures auth login` refuses a coldkey no account has linked, with `COLDKEY_NOT_LINKED`, and
it refuses it deliberately: a key must never be able to claim an account for itself, or a stolen
key would be a way *in* rather than merely a way to work. Until the frontend exists, this script
is the browser that does the claiming.

    # a real wallet (prompts for the coldkey passphrase, once)
    python3 scripts/link_coldkey.py --api http://localhost:8000 --wallet default

    # a development key against a local validator
    python3 scripts/link_coldkey.py --api http://localhost:8000 --coldkey-uri //Alice

It walks the three calls a website would:

    POST /v1/auth/wallet/challenge      a nonce, and the exact message to sign
    POST /v1/auth/wallet/verify         the signature -> account + session cookies, and the
                                        coldkey is linked as a wallet by this same call
    PUT  /v1/me/coldkeys/submission     designate it as the key this account submits under

Three, not four, and V035 is why. There used to be a separate hotkey to link: the signature that
signed in proved a coldkey, and then a *second* key had to prove itself before the miner could
submit. Miners now sign with the coldkey, so signing in and linking are the same call, and all
that remains is choosing which linked key submits.

The account is created on first sign-in, so there is no separate registration step: proving
control of a coldkey against an address nobody has claimed *is* signing up.

This is a development utility. It holds a live session cookie in memory and prints nothing
secret, but it is not a substitute for the real thing — the `Sec-Fetch-Site` header it sends is
one a browser writes for itself and no page can forge.
"""

from __future__ import annotations

import argparse
import http.cookies
import json
import sys
import urllib.error
import urllib.request
from typing import Any

from bittensor.sp_core import Keypair
from bittensor.wallet import Wallet

SESSION_COOKIE = "conjectures_session"
# The browser sets this itself and no page can forge it. This script is not a browser, so it
# says so here — see the class docstring for why that is honest rather than a bypass.
FETCH_SITE_HEADER = "Sec-Fetch-Site"
SAME_ORIGIN = "same-origin"

LOGIN_PREFIX = "conjectures-login-v1"


class ApiError(RuntimeError):
    """A refusal from the validator, carrying its reason code where there is one."""


class Client:
    """The four calls, with the cookie jar a browser would keep.

    Keeping a cookie jar is what makes this script's credential *ambient* in the same sense a
    browser's is, so the API's write guard applies to it: a write on a cookie session has to
    carry either an allowlisted `Origin` or a same-origin `Sec-Fetch-Site`. This sends the
    latter on every write.

    **That is not a bypass, and it is worth being precise about why.** In a browser both headers
    are on the forbidden-header list, so no page can set them — which is the entire basis of the
    guard. Outside a browser they are ordinary strings anyone can type, and it does not matter:
    the guard exists to stop a *hostile page* from riding on a cookie the browser attached by
    itself. A local script that can set arbitrary headers is already holding the cookie
    deliberately, and a token-based check would have been no different — anything able to send
    the cookie could send the token beside it.
    """

    def __init__(self, api_root: str) -> None:
        self._root = api_root.rstrip("/")
        self._cookies: dict[str, str] = {}

    @property
    def signed_in(self) -> bool:
        return SESSION_COOKIE in self._cookies

    def post(self, path: str, payload: dict[str, Any], *, method: str = "POST") -> Any:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self._root}{path}", data=body, method=method
        )
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "application/json")
        if self._cookies:
            request.add_header(
                "Cookie",
                "; ".join(f"{name}={value}" for name, value in self._cookies.items()),
            )
        # Every call here changes state, and once there is a cookie in the jar the credential
        # is ambient, so the write guard applies. Sent unconditionally: it costs one header,
        # and a request that omits it is refused rather than merely logged oddly.
        request.add_header(FETCH_SITE_HEADER, SAME_ORIGIN)

        try:
            with urllib.request.urlopen(request) as response:
                self._absorb_cookies(response.headers.get_all("Set-Cookie") or [])
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise ApiError(_problem(exc)) from exc
        except urllib.error.URLError as exc:
            raise ApiError(f"could not reach {self._root}: {exc.reason}") from exc

        return json.loads(raw) if raw else None

    def _absorb_cookies(self, headers: list[str]) -> None:
        for header in headers:
            jar = http.cookies.SimpleCookie()
            jar.load(header)
            for name, morsel in jar.items():
                # An expiry of 0 is a clear, and keeping the value would leave a credential
                # the server has already revoked.
                if morsel["max-age"] == "0":
                    self._cookies.pop(name, None)
                else:
                    self._cookies[name] = morsel.value


def _problem(exc: urllib.error.HTTPError) -> str:
    """The validator's problem document as one line, or the status if it sent none."""
    try:
        body = json.loads(exc.read())
    except (OSError, ValueError):
        return f"HTTP {exc.code}"
    detail = body.get("detail") or body.get("title") or f"HTTP {exc.code}"
    reason = body.get("reason_code")
    return f"HTTP {exc.code}: {detail}" + (f" [{reason}]" if reason else "")


def _assert_prefix(message: str, expected: str, *, address: str) -> None:
    """Refuse to sign anything but the message we asked for, for the key we asked about.

    The same check `conjectures-miner` makes before it unlocks a key, and for the same reason:
    a client that signs whatever a server sends is a signing oracle for every other message
    this validator asks a coldkey to sign. That matters more now than it did — a coldkey signs
    all seven of them — and a `conjectures-deposit-claim-v1` signature obtained under the guise
    of a login would claim a transfer.
    """
    lines = message.splitlines()
    if not lines or lines[0] != expected:
        found = lines[0] if lines else "(empty)"
        raise ApiError(
            f"refusing to sign: expected a {expected!r} message, first line was {found!r}"
        )
    named = next(
        (line.partition(": ")[2] for line in lines[1:] if line.startswith("address: ")),
        None,
    )
    if named != address:
        raise ApiError(f"refusing to sign: the challenge names {named!r}, not {address!r}")


def _sign(keypair: Keypair, message: str) -> str:
    return keypair.sign(message.encode("utf-8")).hex()


def _key(args: argparse.Namespace) -> Keypair:
    """The coldkey that claims the account and will submit under it.

    A development URI never touches a wallet file, and the two sources stay mutually exclusive:
    a command line that blends `//Alice` with a real wallet name is a way to claim a throwaway
    account with a production key by accident.
    """
    if args.coldkey_uri:
        return Keypair.create_from_uri(args.coldkey_uri)

    extra = {"path": args.wallet_path} if args.wallet_path else {}
    wallet = Wallet(name=args.wallet, **extra)
    try:
        # Prompts for the passphrase. The only place this script opens the coldkey, and the
        # only reason it has to: an account is claimed by the key that holds the funds.
        return wallet.coldkey
    except Exception as exc:
        raise ApiError(f"could not open coldkey {args.wallet}: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", required=True, help="Validator base URL")
    parser.add_argument("--wallet", default="default", help="Bittensor wallet (coldkey) name")
    parser.add_argument("--wallet-path", default=None, help="Override ~/.bittensor/wallets")
    parser.add_argument("--coldkey-uri", default=None, help="Development coldkey, e.g. //Alice")
    parser.add_argument(
        "--display-name", default=None, help="Set the account's display name while signed in"
    )
    args = parser.parse_args(argv)

    client = Client(args.api)
    try:
        coldkey = _key(args)

        print(f"coldkey  {coldkey.ss58_address}")

        challenge = client.post(
            "/v1/auth/wallet/challenge", {"address": coldkey.ss58_address}
        )
        _assert_prefix(challenge["message"], LOGIN_PREFIX, address=coldkey.ss58_address)
        envelope = client.post(
            "/v1/auth/wallet/verify",
            {
                "address": coldkey.ss58_address,
                "signature": _sign(coldkey, challenge["message"]),
            },
        )
        account = envelope["account"]
        if not client.signed_in:  # pragma: no cover - the server always sets both cookies
            raise ApiError("signed in but no session cookie came back")
        print(f"account  {account['id']}  (roles: {', '.join(account['roles']) or 'none'})")

        if args.display_name:
            account = client.post(
                "/v1/me", {"display_name": args.display_name}, method="PATCH"
            )

        # `wallet/verify` already linked this coldkey — it is how the account was claimed — so
        # there is no separate proving step. All that is left is the designation, and it needs
        # no signature: the key is already proved, and this only chooses among proved keys.
        if account.get("coldkeys", {}).get("submission_coldkey") == coldkey.ss58_address:
            print("already the submission coldkey for this account; nothing to do")
        else:
            account = client.post(
                "/v1/me/coldkeys/submission",
                {"coldkey": coldkey.ss58_address},
                method="PUT",
            )
            print("designated")

        coldkeys = account.get("coldkeys", {})
        linked = ", ".join(entry["coldkey"] for entry in account.get("wallets", ()))
        print(f"wallets    {linked or 'none'}")
        print(f"submission {coldkeys.get('submission_coldkey') or 'none'}")
        print(f"payout     {coldkeys.get('payout_coldkey') or 'none'}")
    except ApiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("\nNow run `conjectures auth login` on the miner.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
