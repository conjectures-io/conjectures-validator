"""Authentication and configuration. No database, so these run offline."""

from __future__ import annotations

import time

import pytest

pytest.importorskip("fastapi", reason="submission API tests need the service extra")
pytest.importorskip("sqlalchemy", reason="submission API tests need the db extra")

from conjectures_subnet.db import digests
from submission_api.auth import (
    DevelopmentAuthenticator,
    HotkeySignatureAuthenticator,
    SignedRequest,
    authentication_message,
    assert_fresh_nonce,
    assert_valid_hotkey,
    build_authenticator,
    development_signature,
    normalise_signature,
)
from submission_api.errors import Unauthorized
from submission_api.payments import (
    ChainPaymentVerifier,
    DevelopmentPaymentVerifier,
    build_payment_verifier,
)
from submission_api.settings import Settings, SettingsError
from submission_api.review_asgi import ReviewSettings, create_review_app


HOTKEY = "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"
OTHER_HOTKEY = "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"
RECIPIENT = "5C4hrfjw9DjXZTzV3MwzrrAr9P1MJhSrvWGWqi1eSuyUpnhM"
DIGEST = "sha256:" + "ab" * 32


def signed(*, hotkey: str = HOTKEY, digest: str = DIGEST, signature: bytes | None = None):
    return SignedRequest(
        hotkey=hotkey,
        request_digest=digest,
        timestamp_ms=1_700_000_000_000,
        signature=signature
        if signature is not None
        else bytes.fromhex(development_signature()),
    )


def base_env(**overrides: str) -> dict[str, str]:
    environ = {
        "PAYMENT_RECIPIENT_SS58": RECIPIENT,
        "SUBMISSION_AUTHENTICATOR": "development-static-key",
        "DEVELOPMENT_HOTKEYS": HOTKEY,
    }
    environ.update(overrides)
    return environ


# --- the signed message -------------------------------------------------------------


def test_the_signed_message_is_the_raw_request_digest():
    assert signed().message == authentication_message(
        domain="conjectures-submit-v1",
        request_digest=DIGEST,
        timestamp_ms=1_700_000_000_000,
    )
    assert len(signed().message) == 32


def test_a_different_digest_is_a_different_message():
    assert signed().message != signed(digest="sha256:" + "cd" * 32).message


def test_timestamp_and_operation_are_bound_into_the_signature_message():
    original = signed()
    assert original.message != SignedRequest(
        hotkey=original.hotkey,
        request_digest=original.request_digest,
        timestamp_ms=original.timestamp_ms + 1,
        signature=original.signature,
    ).message
    assert original.message != SignedRequest(
        hotkey=original.hotkey,
        request_digest=original.request_digest,
        timestamp_ms=original.timestamp_ms,
        signature=original.signature,
        domain="conjectures-read-v1",
    ).message


def test_reference_client_builds_the_identical_authentication_envelope():
    from scripts.submit_proof import authentication_message as client_message

    assert signed().message == client_message(
        domain="conjectures-submit-v1",
        request_digest=DIGEST,
        timestamp="1700000000000",
    )


# --- signature normalisation --------------------------------------------------------


def test_signature_accepts_prefixed_and_bare_hex():
    raw = bytes(range(64))
    hex_form = raw.hex()
    assert normalise_signature(hex_form) == raw
    assert normalise_signature("0x" + hex_form) == raw
    assert normalise_signature("0X" + hex_form.upper()) == raw
    assert normalise_signature(f"  {hex_form}  ") == raw


@pytest.mark.parametrize(
    "value", ["", "0x", "ab", "zz" * 64, "ab" * 63, "ab" * 65, "0x0x" + "ab" * 64]
)
def test_malformed_signatures_are_unauthorized(value):
    with pytest.raises(Unauthorized):
        normalise_signature(value)


def test_signature_length_matches_the_column():
    # submissions.hotkey_signature has CHECK octet_length(...) = 64.
    assert len(normalise_signature("ab" * 64)) == 64


@pytest.mark.parametrize("value", ["", "not-an-address", "0OIl" * 12, HOTKEY[:-1] + "!"])
def test_malformed_hotkeys_are_unauthorized(value):
    with pytest.raises(Unauthorized):
        assert_valid_hotkey(value)


def test_valid_hotkey_passes_through():
    assert assert_valid_hotkey(HOTKEY) == HOTKEY


def test_hotkey_regex_matches_the_ss58_domain():
    # The schema's ss58 domain demands exactly 48 characters; a 47-character address that
    # passed here would be refused at INSERT as a 500 instead of a 4xx.
    from verifier.bundle import SS58_ADDRESS

    assert SS58_ADDRESS.fullmatch("1" * 48)
    assert SS58_ADDRESS.fullmatch("1" * 47) is None


# --- timestamp window ---------------------------------------------------------------


def test_a_fresh_timestamp_is_accepted():
    now = int(time.time() * 1000)
    assert_fresh_nonce(now, 120, now_ms=now)


def test_a_stale_timestamp_is_rejected():
    with pytest.raises(Unauthorized, match="acceptance window"):
        assert_fresh_nonce(1_700_000_000_000 - 121_000, 120, now_ms=1_700_000_000_000)


def test_a_future_timestamp_is_rejected():
    with pytest.raises(Unauthorized, match="acceptance window"):
        assert_fresh_nonce(1_700_000_000_000 + 121_000, 120, now_ms=1_700_000_000_000)


def test_the_window_edges_are_inclusive():
    now = 1_700_000_000_000
    assert_fresh_nonce(now - 120_000, 120, now_ms=now)
    assert_fresh_nonce(now + 120_000, 120, now_ms=now)


# --- development authenticator ------------------------------------------------------


def test_development_authenticator_requires_an_allowlisted_hotkey():
    authenticator = DevelopmentAuthenticator(hotkeys=(HOTKEY,))
    authenticator.verify(signed())
    with pytest.raises(Unauthorized, match="development allowlist"):
        authenticator.verify(signed(hotkey=OTHER_HOTKEY))
    with pytest.raises(Unauthorized, match="does not match"):
        authenticator.verify(signed(signature=b"\x01" * 64))


# --- fail-closed configuration ------------------------------------------------------


@pytest.mark.parametrize(
    "override,message",
    [
        ({"SUBMISSION_AUTHENTICATOR": "development-static-key"}, "hotkey-signature"),
        ({"SUBMISSION_PAYMENT_VERIFIER": "development"}, "chain"),
        ({"SUBMISSION_DISPATCHER": "in-process"}, "trust domain"),
    ],
)
def test_production_refuses_development_components(override, message):
    environ = {
        "APP_MODE": "PROD",
        "PAYMENT_RECIPIENT_SS58": RECIPIENT,
        "DEVELOPMENT_HOTKEYS": HOTKEY,
    }
    environ.update(override)
    with pytest.raises(SettingsError, match=message):
        Settings.from_env(environ)


def test_production_defaults_are_all_hardened():
    settings = Settings.from_env(
        {
            "APP_MODE": "PROD",
            "PAYMENT_RECIPIENT_SS58": RECIPIENT,
            "BOUNTY_AMOUNT_RAO": "1000000000",
        }
    )
    assert isinstance(build_authenticator(settings), HotkeySignatureAuthenticator)
    assert isinstance(build_payment_verifier(settings), ChainPaymentVerifier)
    assert settings.expose_docs is False
    assert settings.production is True


def test_development_defaults_are_convenient():
    settings = Settings.from_env(base_env())
    assert isinstance(build_authenticator(settings), DevelopmentAuthenticator)
    assert isinstance(build_payment_verifier(settings), DevelopmentPaymentVerifier)
    assert settings.payment_amount_rao == 500_000_000
    assert settings.nonce_window_seconds == 120
    assert settings.review_policy_version == "v1"
    assert settings.bounty_amount_rao == 1_000_000_000
    assert settings.bounty_policy_version == "flat-tao-v1"


def test_production_requires_an_explicit_frozen_bounty():
    with pytest.raises(SettingsError, match="BOUNTY_AMOUNT_RAO"):
        Settings.from_env(
            {
                "APP_MODE": "PROD",
                "PAYMENT_RECIPIENT_SS58": RECIPIENT,
            }
        )


def test_the_api_does_not_require_its_own_database_url():
    # The API reuses the validator's shared store; conjectures_subnet.db resolves the URL.
    assert Settings.from_env(base_env()).database_url == ""


def test_the_shared_resolver_supplies_the_url(monkeypatch):
    from conjectures_subnet.db import database_url

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_USER", "someone")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("POSTGRES_HOST", "db")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("POSTGRES_DB", "conjectures")
    assert database_url() == "postgresql+psycopg://someone:secret@db:5432/conjectures"
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://explicit/x")
    assert database_url() == "postgresql+psycopg://explicit/x"


def test_review_service_requires_its_own_strong_token_and_exposes_only_review_routes():
    with pytest.raises(RuntimeError, match="at least 32"):
        ReviewSettings.from_env({"REVIEW_API_TOKEN": "short"})
    settings = ReviewSettings.from_env(
        {
            "DATABASE_URL": "postgresql+psycopg://reviewer:secret@db/conjectures",
            "REVIEW_API_TOKEN": "r" * 32,
            "REVIEWER_IDENTITY": "alice",
            "APP_MODE": "PROD",
        }
    )
    app = create_review_app(settings)
    included = [route.original_router for route in app.routes if hasattr(route, "original_router")]
    assert len(included) == 1
    paths = {route.path for route in included[0].routes}
    assert paths == {"/v1/reviews/{submission_id}"}
    assert app.openapi_url is None


@pytest.mark.parametrize(
    "override,message",
    [
        ({"PAYMENT_AMOUNT_RAO": "0"}, "positive"),
        ({"PAYMENT_AMOUNT_RAO": "not-a-number"}, "integer"),
        ({"PAYMENT_AMOUNT_RAO": str(1 << 63)}, "must not exceed"),
        ({"BOUNTY_AMOUNT_RAO": "0"}, "positive"),
        ({"BOUNTY_AMOUNT_RAO": str(1 << 63)}, "must not exceed"),
        ({"BOUNTY_POLICY_VERSION": "Not Valid"}, "BOUNTY_POLICY"),
        ({"MAX_BUNDLE_BYTES": "99999999"}, "must not exceed"),
        ({"MANUAL_REWARD_REVIEW_ENABLED": "maybe"}, "boolean"),
        ({"REVIEW_POLICY_VERSION": "Not Valid"}, "REVIEW_POLICY"),
        ({"APP_MODE": "STAGING"}, "APP_MODE"),
        ({"NONCE_WINDOW_SECONDS": "0"}, "positive"),
        ({"DEVELOPMENT_COLDKEY": "nope"}, "DEVELOPMENT_COLDKEY"),
        ({"DEVELOPMENT_HOTKEYS": "nope,also-bad"}, "invalid addresses"),
        ({"SUBMISSION_PAYMENT_VERIFIER": "magic"}, "SUBMISSION_PAYMENT_VERIFIER"),
    ],
)
def test_misconfiguration_refuses_to_boot(override, message):
    with pytest.raises(SettingsError, match=message):
        Settings.from_env(base_env(**override))


@pytest.mark.parametrize(
    "environ,message",
    [
        ({}, "PAYMENT_RECIPIENT_SS58"),
        ({"PAYMENT_RECIPIENT_SS58": "nope"}, "valid SS58"),
        (
            {"PAYMENT_RECIPIENT_SS58": RECIPIENT, "SUBMISSION_AUTHENTICATOR": "development-static-key"},
            "DEVELOPMENT_HOTKEYS",
        ),
    ],
)
def test_missing_required_configuration_refuses_to_boot(environ, message):
    with pytest.raises(SettingsError, match=message):
        Settings.from_env(environ)


# --- payment verification -----------------------------------------------------------


def test_the_chain_verifier_fails_closed_without_a_reader():
    import asyncio

    from submission_api.errors import PaymentRequired

    verifier = ChainPaymentVerifier(recipient=RECIPIENT, amount_rao=500_000_000)
    with pytest.raises(PaymentRequired) as caught:
        asyncio.run(verifier.confirm(reference="0xabc", hotkey=HOTKEY))
    # Refusing every submission is the only safe default for a component that gates money.
    assert caught.value.status_code == 503
    assert caught.value.reason_code == "PAYMENT_VERIFIER_UNAVAILABLE"


def test_the_development_verifier_honours_its_allowlist():
    import asyncio

    from submission_api.errors import PaymentRequired

    verifier = DevelopmentPaymentVerifier(
        sender=RECIPIENT, amount_rao=500_000_000, references=("0xallowed",)
    )
    confirmed = asyncio.run(verifier.confirm(reference="0xallowed", hotkey=HOTKEY))
    assert confirmed.amount_rao == 500_000_000
    assert confirmed.block > 0
    with pytest.raises(PaymentRequired, match="development allowlist"):
        asyncio.run(verifier.confirm(reference="0xother", hotkey=HOTKEY))


# --- the production signature path, against a real keypair --------------------------

try:
    import bittensor_wallet as keypair_module
except ImportError:  # pragma: no cover - depends on the installed extras
    try:
        from types import SimpleNamespace

        from bittensor.sp_core import Keypair

        keypair_module = SimpleNamespace(Keypair=Keypair)
    except ImportError:
        keypair_module = None

needs_keypair = pytest.mark.skipif(
    keypair_module is None, reason="hotkey signature verification needs the subnet extra"
)


@needs_keypair
def test_a_real_signature_over_the_request_digest_verifies():
    key = keypair_module.Keypair.create_from_uri("//Alice")
    request = signed(hotkey=key.ss58_address, signature=b"\x00")
    signature = key.sign(request.message)
    assert len(signature) == 64
    HotkeySignatureAuthenticator().verify(
        signed(hotkey=key.ss58_address, signature=signature)
    )


@needs_keypair
def test_a_signature_does_not_carry_over_to_another_digest():
    key = keypair_module.Keypair.create_from_uri("//Alice")
    signature = key.sign(signed(hotkey=key.ss58_address, signature=b"\x00").message)
    with pytest.raises(Unauthorized, match="does not match"):
        HotkeySignatureAuthenticator().verify(
            signed(hotkey=key.ss58_address, digest="sha256:" + "ee" * 32, signature=signature)
        )


@needs_keypair
def test_another_keys_signature_is_rejected():
    key = keypair_module.Keypair.create_from_uri("//Alice")
    bob = keypair_module.Keypair.create_from_uri("//Bob")
    signature = bob.sign(signed(hotkey=key.ss58_address, signature=b"\x00").message)
    with pytest.raises(Unauthorized, match="does not match"):
        HotkeySignatureAuthenticator().verify(
            signed(hotkey=key.ss58_address, signature=signature)
        )


@needs_keypair
def test_a_garbage_signature_is_rejected():
    key = keypair_module.Keypair.create_from_uri("//Alice")
    with pytest.raises(Unauthorized):
        HotkeySignatureAuthenticator().verify(
            signed(hotkey=key.ss58_address, signature=b"\xff" * 64)
        )


def test_hotkey_authenticator_fails_closed_without_a_keypair_backend(monkeypatch):
    """With neither keypair library importable, authentication must 401, not crash."""
    import builtins

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name in {"bittensor.sp_core", "bittensor_wallet", "substrateinterface"}:
            raise ImportError(f"blocked for test: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(Unauthorized, match="unavailable"):
        HotkeySignatureAuthenticator().verify(signed(signature=b"\xab" * 64))
