"""Sending the magic link.

The validator has no mail transport, and adding one is a deployment decision rather than
a code one: an SMTP client, an API client for a provider, and the credentials either
needs, all belong to whoever operates the service. So this is a seam, shaped exactly
like `payments.PaymentVerifier` and for the same reason.

`SmtpSender` is the production shape and **fails closed until a transport is injected**.
It refuses to send rather than pretending to, because the alternative — an interface that
silently discards mail — means a production deployment where nobody can sign in and
nothing in the logs says why.

`ConsoleSender` writes the link to the process log for local development, and `Settings`
refuses it in production. It is not a mock: a developer genuinely needs to click the
link, and reading it from the log is how.

Nothing here formats HTML. The message is one URL and one sentence, so a multipart
template would be more surface for no benefit.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote

from submission_api.errors import ServiceUnavailable
from submission_api.settings import CONSOLE_MAIL, SMTP_MAIL, Settings

logger = logging.getLogger("submission_api.mail")

REASON_MAIL_UNAVAILABLE = "MAIL_TRANSPORT_UNAVAILABLE"

SUBJECT = "Your conjectures.io sign-in link"
BODY = """Use this link to sign in to conjectures.io:

{link}

It expires in {minutes} minutes and can be used once. If you did not ask to sign in,
you can ignore this message — nothing has changed on your account.
"""


def magic_link(*, base_url: str, token: str) -> str:
    """The URL in the email.

    The token goes in the query string, which means it can end up in browser history and
    in a referrer. That is why it is single-use and short-lived, and why the endpoint
    that consumes it exchanges it for a session cookie immediately: the token in the URL
    is worthless within seconds of being used.
    """
    return f"{base_url.rstrip('/')}/auth/verify?token={quote(token, safe='')}"


class MailSender(Protocol):
    async def send_login_link(
        self, *, email: str, link: str, expires_in_minutes: int
    ) -> None:
        """Deliver the link, or raise ServiceUnavailable."""
        ...


@dataclass(frozen=True)
class SmtpTransport(Protocol):
    """What a real transport has to provide. Injected; not implemented here."""

    async def send(self, *, to: str, subject: str, body: str) -> None: ...


@dataclass(frozen=True)
class SmtpSender:
    """Production. Fails closed until a transport is injected.

    Refusing is the only safe default for something that gates access: an interface that
    accepted the call and dropped the mail would look healthy while locking every user
    out.
    """

    transport: SmtpTransport | None = None

    async def send_login_link(
        self, *, email: str, link: str, expires_in_minutes: int
    ) -> None:
        if self.transport is None:
            raise ServiceUnavailable(
                "email sign-in is not available on this deployment",
                reason_code=REASON_MAIL_UNAVAILABLE,
            )
        await self.transport.send(
            to=email,
            subject=SUBJECT,
            body=BODY.format(link=link, minutes=expires_in_minutes),
        )


@dataclass(frozen=True)
class ConsoleSender:
    """Development only. Writes the link to the log so a developer can click it.

    `Settings` refuses this in production. It logs the full link, which is a credential —
    acceptable on a developer's machine, catastrophic in a shipped log aggregator, which
    is exactly what that refusal is for.
    """

    async def send_login_link(
        self, *, email: str, link: str, expires_in_minutes: int
    ) -> None:
        logger.warning(
            "development mail: sign-in link for %s (expires in %s minutes): %s",
            email,
            expires_in_minutes,
            link,
        )


def build_mail_sender(settings: Settings) -> MailSender:
    if settings.mail_sender == SMTP_MAIL:
        return SmtpSender()
    if settings.mail_sender == CONSOLE_MAIL:
        if settings.production:  # pragma: no cover - Settings already refuses this
            raise RuntimeError("the console mail sender is not permitted in production")
        return ConsoleSender()
    raise RuntimeError(f"unknown mail sender: {settings.mail_sender}")


__all__ = [
    "REASON_MAIL_UNAVAILABLE",
    "ConsoleSender",
    "MailSender",
    "SmtpSender",
    "SmtpTransport",
    "build_mail_sender",
    "magic_link",
]
