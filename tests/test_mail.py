from __future__ import annotations

import asyncio
import smtplib

import pytest

import httpx

from submission_api.errors import ServiceUnavailable
from submission_api.mail import (
    EXISTING_ACCOUNT_SUBJECT,
    RESET_SUBJECT,
    SIGNUP_SUBJECT,
    SUBJECT,
    BrevoTransport,
    MailDeliveryError,
    SmtpTransport,
    TransportSender,
    build_mail_sender,
    magic_link,
    password_reset_link,
    signup_link,
)
from tests.conftest_api import build_settings


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
    sender = TransportSender(transport=FailingTransport())
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


# --- Brevo's HTTP transport -----------------------------------------------------------


class RecordingBrevo:
    """A stand-in for Brevo's endpoint. Records the one request and answers with `status`."""

    def __init__(self, status: int = 201) -> None:
        self.status = status
        self.requests: list[tuple[str, dict, dict]] = []

    async def post(self, path, *, headers, json):
        self.requests.append((path, headers, json))
        return httpx.Response(self.status, json={"messageId": "<abc@brevo>"})


def brevo(client, **overrides):
    return BrevoTransport(
        api_key=overrides.get("api_key", "xkeysib-secret"),
        base_url="https://api.brevo.com",
        sender_address="login@conjectures.io",
        sender_name=overrides.get("sender_name", "conjectures.io"),
        timeout_seconds=10.0,
        client=client,
    )


def test_brevo_posts_the_transactional_message():
    endpoint = RecordingBrevo()

    asyncio.run(
        brevo(endpoint).send(
            to="solver@example.com", subject=SUBJECT, body="one-time link"
        )
    )

    path, headers, payload = endpoint.requests[0]
    assert path == "/v3/smtp/email"
    assert headers["api-key"] == "xkeysib-secret"
    assert payload["sender"] == {
        "email": "login@conjectures.io",
        "name": "conjectures.io",
    }
    assert payload["to"] == [{"email": "solver@example.com"}]
    assert payload["subject"] == SUBJECT
    # Plain text only. An HTML part would be a second rendering of a credential to keep in step.
    assert payload["textContent"] == "one-time link"
    assert "htmlContent" not in payload


def test_brevo_omits_an_empty_sender_name():
    endpoint = RecordingBrevo()
    asyncio.run(brevo(endpoint, sender_name="").send(to="a@b.co", subject="s", body="b"))
    assert endpoint.requests[0][2]["sender"] == {"email": "login@conjectures.io"}


@pytest.mark.parametrize("status", [400, 401, 429, 500])
def test_brevo_treats_every_refusal_as_undeliverable(status):
    # A 400 naming an invalid recipient and a 500 have the same remedy here: refuse the request
    # rather than tell someone to check an inbox that will stay empty.
    with pytest.raises(MailDeliveryError):
        asyncio.run(
            brevo(RecordingBrevo(status)).send(to="a@b.co", subject="s", body="b")
        )


class UnreachableBrevo:
    async def post(self, path, *, headers, json):
        raise httpx.ConnectError("api.brevo.com refused the connection")


def test_brevo_transport_error_is_a_fail_closed_service_error(caplog):
    sender = TransportSender(transport=brevo(UnreachableBrevo()))
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
    assert "api.brevo.com" not in caplog.text


def test_the_brevo_api_key_never_reaches_a_log_or_a_repr(caplog):
    transport = brevo(UnreachableBrevo())
    with pytest.raises(ServiceUnavailable):
        asyncio.run(
            TransportSender(transport=transport).send_login_link(
                email="a@b.co", link="https://x/y?token=t", expires_in_minutes=15
            )
        )
    assert "xkeysib-secret" not in caplog.text
    assert "xkeysib-secret" not in repr(transport)


# --- the four messages ----------------------------------------------------------------


class CapturingTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    async def send(self, *, to: str, subject: str, body: str) -> None:
        self.sent.append((to, subject, body))


def test_every_message_carries_its_link_and_its_expiry():
    transport = CapturingTransport()
    sender = TransportSender(transport=transport)

    asyncio.run(
        sender.send_signup_link(
            email="a@b.co", link="https://site/auth/signup/verify?token=s", expires_in_minutes=15
        )
    )
    asyncio.run(
        sender.send_password_reset_link(
            email="a@b.co", link="https://site/auth/password/reset?token=r", expires_in_minutes=15
        )
    )
    asyncio.run(
        sender.send_existing_account_notice(
            email="a@b.co",
            link="https://site/auth/password/reset?token=n",
            expires_in_minutes=15,
            sign_in_url="https://site/auth/sign-in",
        )
    )

    subjects = [subject for _to, subject, _body in transport.sent]
    assert subjects == [SIGNUP_SUBJECT, RESET_SUBJECT, EXISTING_ACCOUNT_SUBJECT]
    for expected, (_to, _subject, body) in zip(
        ("token=s", "token=r", "token=n"), transport.sent, strict=True
    ):
        assert expected in body
        assert "15 minutes" in body
    # The notice has to be readable by someone who did not ask for it, so it says plainly that
    # nothing happened as well as offering the link.
    assert "nothing has happened" in transport.sent[2][2]


def test_the_signup_message_says_no_account_exists_yet():
    # The promise the whole flow rests on: an unanswered registration creates nothing.
    transport = CapturingTransport()
    asyncio.run(
        TransportSender(transport=transport).send_signup_link(
            email="a@b.co", link="https://site/x?token=s", expires_in_minutes=15
        )
    )
    assert "not created until" in transport.sent[0][2]


# --- the links --------------------------------------------------------------------------


def test_each_flow_lands_on_its_own_path_with_an_escaped_token():
    base = "https://conjectures.io/"
    token = "a token/with+specials"
    assert magic_link(base_url=base, token=token).startswith(
        "https://conjectures.io/auth/verify?token="
    )
    assert signup_link(base_url=base, token=token).startswith(
        "https://conjectures.io/auth/signup/verify?token="
    )
    assert password_reset_link(base_url=base, token=token).startswith(
        "https://conjectures.io/auth/password/reset?token="
    )
    # A token in a URL is a credential; anything that could end the query string is escaped.
    for link in (
        magic_link(base_url=base, token=token),
        signup_link(base_url=base, token=token),
        password_reset_link(base_url=base, token=token),
    ):
        assert "a%20token%2Fwith%2Bspecials" in link


def test_the_three_paths_are_distinct():
    # A token minted for one flow must not be presentable to another's page. Distinct paths are
    # what let the website route without guessing which kind it holds.
    links = {
        magic_link(base_url="https://x", token="t"),
        signup_link(base_url="https://x", token="t"),
        password_reset_link(base_url="https://x", token="t"),
    }
    assert len(links) == 3


# --- choosing a transport ---------------------------------------------------------------


def test_brevo_settings_build_a_brevo_transport():
    settings = build_settings(
        MAIL_SENDER="brevo",
        BREVO_API_KEY="xkeysib-secret",
        BREVO_SENDER_ADDRESS="login@conjectures.io",
    )
    sender = build_mail_sender(settings)
    assert isinstance(sender, TransportSender)
    assert isinstance(sender.transport, BrevoTransport)


def test_smtp_settings_still_build_an_smtp_transport():
    settings = build_settings(
        MAIL_SENDER="smtp",
        SMTP_HOST="smtp-relay.brevo.com",
        SMTP_FROM_ADDRESS="login@conjectures.io",
    )
    sender = build_mail_sender(settings)
    assert isinstance(sender.transport, SmtpTransport)
    # Brevo's relay is an ordinary SMTP provider, so the STARTTLS default is what it wants.
    assert (sender.transport.host, sender.transport.port) == ("smtp-relay.brevo.com", 587)
    assert sender.transport.security == "starttls"
