#!/usr/bin/env python3
"""Send one real message through this deployment's own mail transport.

    python3 scripts/check_mail_delivery.py --config-only
    python3 scripts/check_mail_delivery.py --to you@example.com [--kind signup]

Builds `Settings` from the environment and calls `build_mail_sender`, so what it exercises is
the configuration the API will actually run with — TLS mode, authentication, timeout, From
address, and the message template itself. That is the point of it existing rather than a note in
the README saying "try swaks": a `swaks` that succeeds proves the relay accepts your password,
not that this service is configured to reach it. When the two disagree, the difference is the
bug.

Mail is the only crypto-free way into an account, so a deployment where it silently does not
work is one where email sign-in, registration and password reset are all dead while every health
check stays green. Nothing in the API's own startup can catch that: the credentials are only
exercised when someone is already waiting for a link.

Reads `.env` from the working directory unless `--env` says otherwise. The real environment wins
over the file, so a single value can be overridden inline without editing anything.

Never prints the SMTP password or the Brevo API key. `--config-only` resolves and reports
without sending, which is the safe thing to run against production.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from submission_api.mail import build_mail_sender  # noqa: E402
from submission_api.settings import Settings, SettingsError  # noqa: E402

# Which mail to send. All four are real templates rather than a synthetic "test" body, because
# the body is part of what is being checked: its length, its encoding, and the link in it are
# what a spam filter scores and what a person has to be able to click.
KINDS = ("login", "signup", "reset", "existing")


def load_env_file(path: pathlib.Path) -> dict[str, str]:
    """A deliberately small `.env` reader: `KEY=value`, tolerating `export` and quotes.

    Not a full parser, and specifically not an evaluating one. This file holds credentials, so a
    reader that can run shell is a reader that can be made to run shell.
    """
    values: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip().removeprefix("export ").strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def report(settings: Settings) -> None:
    print(f"\nMAIL_SENDER = {settings.mail_sender}")
    if settings.mail_sender == "smtp":
        print(f"  host      {settings.smtp_host}:{settings.smtp_port}")
        print(f"  security  {settings.smtp_security}")
        print(f"  login     {settings.smtp_username or '(none)'}")
        print(f"  password  {'set' if settings.smtp_password else 'NOT SET'}")
        print(f"  From      {settings.smtp_from_address}")
        if settings.smtp_from_address == settings.smtp_username:
            # The mistake worth catching by name. A provider's SMTP login is an account
            # identifier, not a mailbox, and mail sent From one is unauthenticated for its
            # domain: it will be accepted by the relay and filed as spam by the recipient.
            print(
                "\n  WARNING: From is the same as the SMTP login. A relay login is an account\n"
                "  identifier, not a mailbox — From must be a sender the provider has verified,\n"
                "  on a domain publishing that provider's SPF and DKIM records."
            )
    elif settings.mail_sender == "brevo":
        print(f"  base url  {settings.brevo_base_url}")
        print(f"  api key   {'set' if settings.brevo_api_key else 'NOT SET'}")
        print(f"  sender    {settings.brevo_sender_address} ({settings.brevo_sender_name})")
    else:
        print("  the console sender writes links to the log and delivers nothing")
    print(f"\n  links point at {settings.website_base_url}")
    print(f"    sign in   {settings.email_verify_path}")
    print(f"    signup    {settings.signup_verify_path}")
    print(f"    reset     {settings.password_reset_path}")


async def deliver(settings: Settings, *, to: str, kind: str) -> None:
    sender = build_mail_sender(settings)
    base, minutes = settings.website_base_url, settings.email_link_minutes
    token = "check-mail-delivery"  # not a real challenge: nothing here writes a row
    if kind == "login":
        from submission_api.mail import magic_link

        await sender.send_login_link(
            email=to,
            link=magic_link(base_url=base, token=token, path=settings.email_verify_path),
            expires_in_minutes=minutes,
        )
    elif kind == "signup":
        from submission_api.mail import signup_link

        await sender.send_signup_link(
            email=to,
            link=signup_link(base_url=base, token=token, path=settings.signup_verify_path),
            expires_in_minutes=minutes,
        )
    elif kind == "reset":
        from submission_api.mail import password_reset_link

        await sender.send_password_reset_link(
            email=to,
            link=password_reset_link(
                base_url=base, token=token, path=settings.password_reset_path
            ),
            expires_in_minutes=minutes,
        )
    else:
        from submission_api.mail import password_reset_link, sign_in_url

        await sender.send_existing_account_notice(
            email=to,
            link=password_reset_link(
                base_url=base, token=token, path=settings.password_reset_path
            ),
            expires_in_minutes=minutes,
            sign_in_url=sign_in_url(base_url=base, path=settings.sign_in_path),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--to", help="mailbox to deliver the test message to")
    parser.add_argument("--env", default=".env", help="env file to load (default: .env)")
    parser.add_argument(
        "--kind", choices=KINDS, default="login", help="which message to send"
    )
    parser.add_argument(
        "--config-only", action="store_true", help="resolve and report, send nothing"
    )
    args = parser.parse_args()

    environ = dict(os.environ)
    path = pathlib.Path(args.env)
    if path.is_file():
        environ = {**load_env_file(path), **environ}
        print(f"loaded {path}")
    else:
        print(f"no {path}; using the environment only")

    try:
        settings = Settings.from_env(environ)
    except SettingsError as exc:
        print(f"\nconfiguration refused: {exc}")
        return 2

    report(settings)
    if args.config_only:
        return 0
    if not args.to:
        print("\nnothing sent: pass --to ADDRESS to deliver a message")
        return 0

    print(f"\nsending the {args.kind} message to {args.to} ...")
    try:
        asyncio.run(deliver(settings, to=args.to, kind=args.kind))
    except Exception as exc:
        # `ServiceUnavailable` carries no provider text by design — a provider's response can
        # quote the recipient back, and this is the same refusal a person would see. So point at
        # where the detail actually lives rather than pretending to have it.
        print(f"FAILED: {type(exc).__name__}: {exc}")
        print(
            "\nThe transport withholds the provider's response on purpose. Check the provider's\n"
            "own delivery log — for Brevo, Transactional > Logs — for the other half."
        )
        return 1

    print("accepted.")
    print(
        "\nAccepted is not delivered: the provider has taken the message and nothing more.\n"
        "Confirm in its delivery log and in the inbox, spam included — a message that is\n"
        "accepted and never arrives is what a missing SPF or DKIM record looks like."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
