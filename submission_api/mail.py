"""Sending the magic link.

`SmtpTransport` is provider-agnostic SMTP: authenticated or relay delivery, STARTTLS on port
587, or implicit TLS on port 465. Network work is moved off the async event loop and every
delivery failure becomes a fail-closed 503 rather than a request that appears to have mailed a
credential it actually discarded.

`ConsoleSender` writes the link to the process log for local development, and `Settings`
refuses it in production. It is not a mock: a developer genuinely needs to click the
link, and reading it from the log is how.

Nothing here formats HTML. The message is one URL and one sentence, so a multipart
template would be more surface for no benefit.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from asyncio import to_thread
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Protocol
from urllib.parse import quote

from submission_api.errors import ServiceUnavailable
from submission_api.settings import (
    CONSOLE_MAIL,
    SMTP_IMPLICIT_TLS,
    SMTP_MAIL,
    SMTP_STARTTLS,
    Settings,
)

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


class MailTransport(Protocol):
    async def send(self, *, to: str, subject: str, body: str) -> None: ...


@dataclass(frozen=True)
class SmtpTransport:
    """A minimal SMTP transport with explicit TLS and authentication policy."""

    host: str
    port: int
    username: str
    password: str = field(repr=False)
    from_address: str
    security: str
    timeout_seconds: float

    async def send(self, *, to: str, subject: str, body: str) -> None:
        await to_thread(self._send, to=to, subject=subject, body=body)

    def _send(self, *, to: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = self.from_address
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)

        context = ssl.create_default_context()
        if self.security == SMTP_IMPLICIT_TLS:
            connection = smtplib.SMTP_SSL(
                self.host,
                self.port,
                timeout=self.timeout_seconds,
                context=context,
            )
        else:
            connection = smtplib.SMTP(
                self.host, self.port, timeout=self.timeout_seconds
            )

        with connection as smtp:
            if self.security == SMTP_STARTTLS:
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
            if self.username:
                smtp.login(self.username, self.password)
            smtp.send_message(message)


@dataclass(frozen=True)
class SmtpSender:
    """Production sender. SMTP failures are deliberately visible to the caller."""

    transport: MailTransport

    async def send_login_link(
        self, *, email: str, link: str, expires_in_minutes: int
    ) -> None:
        try:
            await self.transport.send(
                to=email,
                subject=SUBJECT,
                body=BODY.format(link=link, minutes=expires_in_minutes),
            )
        except (OSError, smtplib.SMTPException, ValueError) as exc:
            # Do not log the mailbox, provider response, or link. Any of those may contain PII
            # or a live credential; the exception type is enough for operational triage.
            logger.error("SMTP delivery failed (%s)", type(exc).__name__)
            raise ServiceUnavailable(
                "email sign-in is not available on this deployment",
                reason_code=REASON_MAIL_UNAVAILABLE,
            ) from exc


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
        return SmtpSender(
            transport=SmtpTransport(
                host=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_username,
                password=settings.smtp_password,
                from_address=settings.smtp_from_address,
                security=settings.smtp_security,
                timeout_seconds=settings.smtp_timeout_seconds,
            )
        )
    if settings.mail_sender == CONSOLE_MAIL:
        if settings.production:  # pragma: no cover - Settings already refuses this
            raise RuntimeError("the console mail sender is not permitted in production")
        return ConsoleSender()
    raise RuntimeError(f"unknown mail sender: {settings.mail_sender}")


__all__ = [
    "REASON_MAIL_UNAVAILABLE",
    "ConsoleSender",
    "MailSender",
    "MailTransport",
    "SmtpSender",
    "SmtpTransport",
    "build_mail_sender",
    "magic_link",
]
