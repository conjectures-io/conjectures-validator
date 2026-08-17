from __future__ import annotations

import asyncio
import smtplib

import pytest

from submission_api.errors import ServiceUnavailable
from submission_api.mail import SUBJECT, SmtpSender, SmtpTransport


class FakeSmtp:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def __enter__(self):
        self.calls.append("enter")
        return self

    def __exit__(self, *_args):
        self.calls.append("exit")

    def ehlo(self) -> None:
        self.calls.append("ehlo")

    def starttls(self, *, context) -> None:
        self.calls.append(("starttls", context))

    def login(self, username: str, password: str) -> None:
        self.calls.append(("login", username, password))

    def send_message(self, message) -> None:
        self.calls.append(("message", message))


def test_smtp_transport_upgrades_to_tls_authenticates_and_sends(monkeypatch):
    smtp = FakeSmtp()

    async def inline(function, **kwargs):
        # Keep this a unit test of the SMTP conversation. The production path uses
        # asyncio.to_thread so the blocking stdlib client never stalls the ASGI loop.
        return function(**kwargs)

    def connect(host, port, *, timeout):
        assert (host, port, timeout) == ("smtp.example.com", 587, 7.5)
        return smtp

    monkeypatch.setattr(smtplib, "SMTP", connect)
    monkeypatch.setattr("submission_api.mail.to_thread", inline)
    transport = SmtpTransport(
        host="smtp.example.com",
        port=587,
        username="smtp-user",
        password="smtp-password",
        from_address="login@conjectures.io",
        security="starttls",
        timeout_seconds=7.5,
    )

    asyncio.run(
        transport.send(
            to="solver@example.com", subject=SUBJECT, body="one-time link"
        )
    )

    assert smtp.calls[0] == "enter"
    assert smtp.calls[1] == "ehlo"
    assert smtp.calls[2][0] == "starttls"
    assert smtp.calls[3] == "ehlo"
    assert smtp.calls[4] == ("login", "smtp-user", "smtp-password")
    message = smtp.calls[5][1]
    assert message["From"] == "login@conjectures.io"
    assert message["To"] == "solver@example.com"
    assert message["Subject"] == SUBJECT
    assert "one-time link" in message.get_content()
    assert smtp.calls[6] == "exit"


class FailingTransport:
    async def send(self, *, to: str, subject: str, body: str) -> None:
        raise smtplib.SMTPServerDisconnected("provider response is not for logs")


def test_smtp_failure_is_a_fail_closed_service_error(caplog):
    sender = SmtpSender(transport=FailingTransport())
    with pytest.raises(ServiceUnavailable) as raised:
        asyncio.run(
            sender.send_login_link(
                email="solver@example.com",
                link="https://conjectures.io/auth/verify?token=secret",
                expires_in_minutes=15,
            )
        )
    assert raised.value.reason_code == "MAIL_TRANSPORT_UNAVAILABLE"
    assert "solver@example.com" not in caplog.text
    assert "token=secret" not in caplog.text
    assert "provider response" not in caplog.text
