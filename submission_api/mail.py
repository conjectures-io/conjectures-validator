"""Sending the four mails this service sends.

All four carry a single-use credential in a URL — a sign-in link, a signup confirmation, a
password reset — or say that one was *not* sent. Nothing else is mailed from here.

Two transports, one protocol. `SmtpTransport` is provider-agnostic SMTP: authenticated or relay
delivery, STARTTLS on port 587, or implicit TLS on port 465. `BrevoTransport` posts to Brevo's
transactional endpoint over HTTPS instead. Both move their network work off the async event
loop — SMTP because the stdlib client is blocking, Brevo because it is already async — and both
turn every delivery failure into a fail-closed 503 rather than a request that appears to have
mailed a credential it actually discarded.

**Why an HTTP transport at all, when Brevo also speaks SMTP.** The relay would work with no code
at all. What it does not give is a failure the service can act on: SMTP hands back a numeric
code from a relay that has accepted the message for later delivery, so a rejected recipient, a
suspended account and an exhausted quota all look like success at the point of sending. The API
returns a per-message id and a status, and the errors arrive at the moment of the call.

`ConsoleSender` writes the link to the process log for local development, and `Settings`
refuses it in production. It is not a mock: a developer genuinely needs to click the link, and
reading it from the log is how.

Nothing here formats HTML. Each message is one URL and a couple of sentences, so a multipart
template would be more surface for no benefit — and a plain-text credential mail is the one
that renders identically in every client.
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

import httpx

from submission_api.errors import ServiceUnavailable
from submission_api.settings import (
    BREVO_MAIL,
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

SIGNUP_SUBJECT = "Confirm your conjectures.io address"
SIGNUP_BODY = """Confirm this address to finish creating your conjectures.io account:

{link}

It expires in {minutes} minutes and can be used once. Your account is not created until
you follow it, so if you did not ask to register, you can ignore this message — no account
exists for this address and none will.
"""

# The reply to a registration attempt against an address that already has an account. It exists
# so that `POST /auth/password/register` can answer 202 for every address without either
# creating a second account or silently doing nothing: the person who *is* reading this mailbox
# gets something they can act on, and the person who is not learns nothing from the HTTP
# response.
#
# It carries a real reset link rather than only an explanation, because that is what the
# situation actually needs — including for the account that signed up with Google or a wallet
# and is trying to add a password for the first time. It grants an attacker nothing new: it is
# the same single-use token, on the same per-address budget, that `password/forgot` already
# mails to anyone who types an address into it.
EXISTING_ACCOUNT_SUBJECT = "About your conjectures.io account"
EXISTING_ACCOUNT_BODY = """Someone tried to register a conjectures.io account with this address,
but one already exists. You can sign in at:

{sign_in_url}

If you have forgotten the password — or signed up with Google or a wallet and never set one —
use this link to choose a new one:

{link}

It expires in {minutes} minutes and can be used once.

If this was not you, nothing has happened: no account was created and no existing account was
changed. You do not need to do anything, and the link above expires on its own.
"""

RESET_SUBJECT = "Reset your conjectures.io password"
RESET_BODY = """Use this link to choose a new conjectures.io password:

{link}

It expires in {minutes} minutes and can be used once. Setting a new password signs out every
browser currently signed in to the account.

If you did not ask to reset it, you can ignore this message — your password has not changed.
"""


def magic_link(*, base_url: str, token: str, path: str) -> str:
    """The URL in the sign-in email.

    `path` is the website's sign-in route, not one this API serves, so it is passed in from
    `settings.email_verify_path` rather than written here. It was a literal until it drifted:
    the website moved the page and every mailed link 404'd, with nothing in this repository
    to notice because nothing in this repository serves it.

    The token goes in the query string, which means it can end up in browser history and
    in a referrer. That is why it is single-use and short-lived, and why the endpoint
    that consumes it exchanges it for a session cookie immediately: the token in the URL
    is worthless within seconds of being used.
    """
    return _link(base_url, path, token)


def signup_link(*, base_url: str, token: str, path: str) -> str:
    """The URL in the signup confirmation email.

    A different route from `magic_link` because it lands on a different page and consumes a
    different challenge kind. Sharing one would mean the website had to guess which flow a token
    belonged to, and guessing wrong is a confusing failure on a credential that is now spent.

    Configured rather than written here for the reason `magic_link` gives, and the reason is not
    hypothetical for this one: the page it points at does not exist in the website yet, so the
    default names a route someone still has to build. A literal would be a link that 404s until
    two repositories happen to be released together — the exact failure `EMAIL_VERIFY_PATH` was
    added to end.
    """
    return _link(base_url, path, token)


def password_reset_link(*, base_url: str, token: str, path: str) -> str:
    """The URL in the password reset email. Lands on the page that asks for a new password."""
    return _link(base_url, path, token)


def sign_in_url(*, base_url: str, path: str) -> str:
    """Where the "you already have an account" mail says to go.

    Not a credential, and a flow does not break if it is wrong — but it is still a route owned
    by another repository, so it is configured rather than guessed like the other three.
    """
    return f"{base_url.rstrip('/')}{path}"


def _link(base_url: str, path: str, token: str) -> str:
    return f"{base_url.rstrip('/')}{path}?token={quote(token, safe='')}"


class MailDeliveryError(Exception):
    """A transport could not deliver.

    Carries no provider text, because a provider's response can quote the recipient back. The
    exception type is what gets logged; see `TransportSender`.
    """


class MailSender(Protocol):
    async def send_login_link(
        self, *, email: str, link: str, expires_in_minutes: int
    ) -> None:
        """Deliver a sign-in link, or raise ServiceUnavailable."""
        ...

    async def send_signup_link(
        self, *, email: str, link: str, expires_in_minutes: int
    ) -> None:
        """Deliver a signup confirmation link, or raise ServiceUnavailable."""
        ...

    async def send_existing_account_notice(
        self, *, email: str, link: str, expires_in_minutes: int, sign_in_url: str
    ) -> None:
        """Tell an address a registration hit an account it already has, and offer a reset."""
        ...

    async def send_password_reset_link(
        self, *, email: str, link: str, expires_in_minutes: int
    ) -> None:
        """Deliver a password reset link, or raise ServiceUnavailable."""
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


class BrevoTransport:
    """Brevo's transactional email API: `POST {base_url}/v3/smtp/email`.

    One `httpx.AsyncClient` for the process, as the other outbound clients here do, because a
    TLS handshake per sign-in link is both slow and a connection the provider counts.

    The API key goes in an `api-key` header. It is a bearer credential for the whole Brevo
    account — it can send mail as any verified sender and read the contact list — so it is
    never logged, never echoed in an error, and `Settings` keeps it out of `repr`.

    Anything but a 2xx is a `MailDeliveryError`, including a 400 naming an invalid recipient.
    That is deliberate: from this service's point of view "the provider will not deliver this"
    and "the provider is down" have the same remedy, which is to refuse the request rather than
    tell someone to check an inbox that will stay empty.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        sender_address: str,
        sender_name: str,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._sender = {"email": sender_address}
        if sender_name:
            self._sender["name"] = sender_name
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            headers={"accept": "application/json"},
        )

    async def send(self, *, to: str, subject: str, body: str) -> None:
        try:
            response = await self._client.post(
                "/v3/smtp/email",
                headers={"api-key": self._api_key},
                json={
                    "sender": self._sender,
                    "to": [{"email": to}],
                    "subject": subject,
                    "textContent": body,
                },
            )
        except httpx.HTTPError as exc:
            raise MailDeliveryError(type(exc).__name__) from exc
        if response.status_code >= 400:
            # The status, and nothing from the body. Brevo's error payloads quote the
            # recipient address back, and this string reaches a log.
            raise MailDeliveryError(f"HTTP {response.status_code}")

    async def aclose(self) -> None:
        await self._client.aclose()


@dataclass(frozen=True)
class TransportSender:
    """Production sender over any transport. Delivery failures are visible to the caller.

    Every message funnels through `_deliver`, so the rule about what may be logged is stated
    once. It is the rule the whole module exists to keep: not the mailbox, not the provider's
    response, and above all not the link, because the link is a live credential.
    """

    transport: MailTransport

    async def send_login_link(
        self, *, email: str, link: str, expires_in_minutes: int
    ) -> None:
        await self._deliver(
            email=email,
            subject=SUBJECT,
            body=BODY.format(link=link, minutes=expires_in_minutes),
        )

    async def send_signup_link(
        self, *, email: str, link: str, expires_in_minutes: int
    ) -> None:
        await self._deliver(
            email=email,
            subject=SIGNUP_SUBJECT,
            body=SIGNUP_BODY.format(link=link, minutes=expires_in_minutes),
        )

    async def send_existing_account_notice(
        self, *, email: str, link: str, expires_in_minutes: int, sign_in_url: str
    ) -> None:
        await self._deliver(
            email=email,
            subject=EXISTING_ACCOUNT_SUBJECT,
            body=EXISTING_ACCOUNT_BODY.format(
                sign_in_url=sign_in_url, link=link, minutes=expires_in_minutes
            ),
        )

    async def send_password_reset_link(
        self, *, email: str, link: str, expires_in_minutes: int
    ) -> None:
        await self._deliver(
            email=email,
            subject=RESET_SUBJECT,
            body=RESET_BODY.format(link=link, minutes=expires_in_minutes),
        )

    async def _deliver(self, *, email: str, subject: str, body: str) -> None:
        try:
            await self.transport.send(to=email, subject=subject, body=body)
        except (
            MailDeliveryError,
            OSError,
            smtplib.SMTPException,
            ValueError,
        ) as exc:
            # Do not log the mailbox, provider response, or link. Any of those may contain PII
            # or a live credential; the exception type is enough for operational triage.
            logger.error("mail delivery failed (%s)", type(exc).__name__)
            raise ServiceUnavailable(
                "email delivery is not available on this deployment",
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
        self._log("sign-in link", email, link, expires_in_minutes)

    async def send_signup_link(
        self, *, email: str, link: str, expires_in_minutes: int
    ) -> None:
        self._log("signup confirmation link", email, link, expires_in_minutes)

    async def send_existing_account_notice(
        self, *, email: str, link: str, expires_in_minutes: int, sign_in_url: str
    ) -> None:
        logger.warning(
            "development mail: registration hit the existing account %s (sign in at %s)",
            email,
            sign_in_url,
        )
        self._log("password reset link", email, link, expires_in_minutes)

    async def send_password_reset_link(
        self, *, email: str, link: str, expires_in_minutes: int
    ) -> None:
        self._log("password reset link", email, link, expires_in_minutes)

    def _log(self, what: str, email: str, link: str, minutes: int) -> None:
        logger.warning(
            "development mail: %s for %s (expires in %s minutes): %s",
            what,
            email,
            minutes,
            link,
        )


def build_mail_sender(settings: Settings) -> MailSender:
    if settings.mail_sender == SMTP_MAIL:
        return TransportSender(
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
    if settings.mail_sender == BREVO_MAIL:
        return TransportSender(
            transport=BrevoTransport(
                api_key=settings.brevo_api_key,
                base_url=settings.brevo_base_url,
                sender_address=settings.brevo_sender_address,
                sender_name=settings.brevo_sender_name,
                timeout_seconds=settings.brevo_timeout_seconds,
            )
        )
    if settings.mail_sender == CONSOLE_MAIL:
        if settings.production:  # pragma: no cover - Settings already refuses this
            raise RuntimeError("the console mail sender is not permitted in production")
        return ConsoleSender()
    raise RuntimeError(f"unknown mail sender: {settings.mail_sender}")


__all__ = [
    "BODY",
    "EXISTING_ACCOUNT_SUBJECT",
    "REASON_MAIL_UNAVAILABLE",
    "RESET_SUBJECT",
    "SIGNUP_SUBJECT",
    "SUBJECT",
    "BrevoTransport",
    "ConsoleSender",
    "MailDeliveryError",
    "MailSender",
    "MailTransport",
    "SmtpTransport",
    "TransportSender",
    "build_mail_sender",
    "magic_link",
    "password_reset_link",
    "sign_in_url",
    "signup_link",
]
