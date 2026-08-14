#!/usr/bin/env python3
"""Create an account with a coldkey and link a hotkey to it — what the website will do.

`conjectures auth login` refuses an unlinked hotkey with `HOTKEY_NOT_LINKED`, and it refuses it
deliberately: a hotkey must never be able to claim an account for itself, or a stolen hotkey
would be a way *in* rather than merely a way to work. Linking is a coldkey action, and the
coldkey path is the browser. Until the frontend exists, this script is that browser.

    # a real wallet (prompts for the coldkey passphrase, once)
    python3 scripts/link_hotkey.py --api http://localhost:8000 \
      --wallet default --hotkey default

    # development keys against a local validator
    python3 scripts/link_hotkey.py --api http://localhost:8000 \
      --coldkey-uri //Alice --hotkey-uri //Bob

It walks the same four calls a website would:

    POST /v1/auth/wallet/challenge   a nonce, and the exact message to sign
    POST /v1/auth/wallet/verify      the coldkey signature -> account + session cookies
    POST /v1/me/hotkeys/challenge    a nonce for the hotkey, bound to this account
    POST /v1/me/hotkeys              the hotkey signature -> linked

The account is created on first sign-in, so there is no separate registration step: proving
control of a coldkey against an address nobody has claimed *is* signing up.

This is a development utility. It holds a live session cookie in memory and prints nothing
secret, but it is not a substitute for the real thing — the CSRF token it echoes is a value a
browser would keep same-origin.
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
CSRF_COOKIE = "conjectures_csrf"
CSRF_HEADER = "X-Conjectures-CSRF"

LOGIN_PREFIX = "conjectures-login-v1"
HOTKEY_LINK_PREFIX = "conjectures-hotkey-link-v1"


class ApiError(RuntimeError):
    """A refusal from the validator, carrying its reason code where there is one."""


class Client:
    """The four calls, with the cookie jar a browser would keep.

    The CSRF token rides in a header on every write, copied out of the cookie the sign-in
    response set — which is exactly the double-submit the frontend will do. The other two
    halves of the guard, `Origin` and `Sec-Fetch-Site`, are browser-set headers this script
    does not send at all; the middleware passes a request that omits them, because a
    non-browser client has no ambient credential for a cross-site page to abuse.
    """

    def __init__(self, api_root: str) -> None:
        self._root = api_root.rstrip("/")
        self._cookies: dict[str, str] = {}

    @property
    def csrf_token(self) -> str | None:
        return self._cookies.get(CSRF_COOKIE)

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
        # Only on writes that need it, and only once there is one to send: an unauthenticated
        # call carrying a stale token is a confusing request to have to read in a log.
        if self.csrf_token:
            request.add_header(CSRF_HEADER, self.csrf_token)

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

    The same check `conjectures-miner` makes before it unlocks a hotkey, and for the same
    reason: a client that signs whatever a server sends is a signing oracle for the other five
    messages this validator asks these keys to sign. Here the coldkey is the one that matters —
    a `conjectures-deposit-claim-v1` signature obtained under the guise of a login would claim
    a transfer.
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


def _keys(args: argparse.Namespace) -> tuple[Keypair, Keypair]:
    """The coldkey that owns the account and the hotkey being attached to it.

    A development URI never touches a wallet file, so the two sources are kept apart rather
    than blended: mixing `//Alice` with a real coldkey on one command line is a way to link a
    production hotkey to a throwaway account by accident.
    """
    if args.coldkey_uri or args.hotkey_uri:
        if not (args.coldkey_uri and args.hotkey_uri):
            raise ApiError("--coldkey-uri and --hotkey-uri go together")
        return (
            Keypair.create_from_uri(args.coldkey_uri),
            Keypair.create_from_uri(args.hotkey_uri),
        )

    extra = {"path": args.wallet_path} if args.wallet_path else {}
    wallet = Wallet(name=args.wallet, hotkey=args.hotkey, **extra)
    try:
        # Prompts for the passphrase. The only place this script opens the coldkey, and the
        # only reason it has to: an account is claimed by the key that holds the funds.
        coldkey = wallet.coldkey
    except Exception as exc:
        raise ApiError(f"could not open coldkey {args.wallet}: {exc}") from exc
    try:
        hotkey = wallet.hotkey
    except Exception as exc:
        raise ApiError(f"could not open hotkey {args.wallet}/{args.hotkey}: {exc}") from exc
    return coldkey, hotkey


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", required=True, help="Validator base URL")
    parser.add_argument("--wallet", default="default", help="Bittensor wallet (coldkey) name")
    parser.add_argument("--hotkey", default="default", help="Hotkey name within that wallet")
    parser.add_argument("--wallet-path", default=None, help="Override ~/.bittensor/wallets")
    parser.add_argument("--coldkey-uri", default=None, help="Development coldkey, e.g. //Alice")
    parser.add_argument("--hotkey-uri", default=None, help="Development hotkey, e.g. //Bob")
    parser.add_argument(
        "--display-name", default=None, help="Set the account's display name while signed in"
    )
    args = parser.parse_args(argv)

    client = Client(args.api)
    try:
        coldkey, hotkey = _keys(args)

        print(f"coldkey  {coldkey.ss58_address}")
        print(f"hotkey   {hotkey.ss58_address}")

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

        already = {linked["hotkey"] for linked in account.get("hotkeys", ())}
        if hotkey.ss58_address in already:
            print("hotkey is already linked to this account; nothing to do")
        else:
            link = client.post(
                "/v1/me/hotkeys/challenge", {"hotkey": hotkey.ss58_address}
            )
            _assert_prefix(
                link["message"], HOTKEY_LINK_PREFIX, address=hotkey.ss58_address
            )
            account = client.post(
                "/v1/me/hotkeys",
                {
                    "hotkey": hotkey.ss58_address,
                    "signature": _sign(hotkey, link["message"]),
                },
            )
            print("linked")

        linked = ", ".join(entry["hotkey"] for entry in account.get("hotkeys", ()))
        print(f"hotkeys  {linked or 'none'}")
    except ApiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("\nNow run `conjectures auth login` on the miner.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
