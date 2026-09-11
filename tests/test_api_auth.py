"""Authentication and configuration. No database, so these run offline."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="submission API tests need the service extra")
pytest.importorskip("sqlalchemy", reason="submission API tests need the db extra")
pytest.importorskip("bittensor", reason="submission API tests need the subnet extra")

from bittensor.sp_core import Keypair

from conjectures_subnet.db import digests
from submission_api.auth import (
    DevelopmentAuthenticator,
    ColdkeySignatureAuthenticator,
    SignedRequest,
    assert_fresh_nonce,
    assert_valid_coldkey,
    build_authenticator,
    development_signature,
    normalise_signature,
)
from submission_api.errors import Unauthorized
from submission_api.mail import magic_link
from submission_api.payments import (
    ChainPaymentVerifier,
    DevelopmentPaymentVerifier,
    build_payment_verifier,
)
from submission_api.settings import Settings, SettingsError


MINER_COLDKEY = "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"
OTHER_COLDKEY = "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"
RECIPIENT = "5C4hrfjw9DjXZTzV3MwzrrAr9P1MJhSrvWGWqi1eSuyUpnhM"
DIGEST = "sha256:" + "ab" * 32


def signed(*, hotkey: str = MINER_COLDKEY, digest: str = DIGEST, signature: bytes | None = None):
    return SignedRequest(
        signer_coldkey=hotkey,
        request_digest=digest,
        signature=signature
        if signature is not None
        else bytes.fromhex(development_signature()),
    )


def base_env(**overrides: str) -> dict[str, str]:
    environ = {
        "PAYMENT_RECIPIENT_SS58": RECIPIENT,
        "SUBMISSION_AUTHENTICATOR": "development-static-key",
        "DEVELOPMENT_COLDKEYS": MINER_COLDKEY,
    }
    environ.update(overrides)
    return environ


# --- the signed message -------------------------------------------------------------


def test_the_signed_message_is_the_raw_request_digest():
    # 32 raw bytes, matching what the schema stores, not a formatted string.
    assert signed().message == digests.to_bytes(DIGEST)
    assert len(signed().message) == 32


def test_a_different_digest_is_a_different_message():
    assert signed().message != signed(digest="sha256:" + "cd" * 32).message


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
    # submissions.signer_signature has CHECK octet_length(...) = 64.
    assert len(normalise_signature("ab" * 64)) == 64


@pytest.mark.parametrize("value", ["", "not-an-address", "0OIl" * 12, MINER_COLDKEY[:-1] + "!"])
def test_malformed_hotkeys_are_unauthorized(value):
    with pytest.raises(Unauthorized):
        assert_valid_coldkey(value)


def test_valid_hotkey_passes_through():
    assert assert_valid_coldkey(MINER_COLDKEY) == MINER_COLDKEY


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
    authenticator = DevelopmentAuthenticator(coldkeys=(MINER_COLDKEY,))
    authenticator.verify(signed())
    with pytest.raises(Unauthorized, match="development allowlist"):
        authenticator.verify(signed(hotkey=OTHER_COLDKEY))
    with pytest.raises(Unauthorized, match="does not match"):
        authenticator.verify(signed(signature=b"\x01" * 64))


# --- fail-closed configuration ------------------------------------------------------


@pytest.mark.parametrize(
    "override,message",
    [
        ({"SUBMISSION_AUTHENTICATOR": "development-static-key"}, "coldkey-signature"),
        ({"SUBMISSION_PAYMENT_VERIFIER": "development"}, "chain"),
        ({"SUBMISSION_DISPATCHER": "in-process"}, "trust domain"),
    ],
)
def test_production_refuses_development_components(override, message):
    environ = {
        "APP_MODE": "PROD",
        "PAYMENT_RECIPIENT_SS58": RECIPIENT,
        "BOUNTY_WALLET_HOTKEY_SS58": MINER_COLDKEY,
        "DEVELOPMENT_COLDKEYS": MINER_COLDKEY,
    }
    environ.update(override)
    with pytest.raises(SettingsError, match=message):
        Settings.from_env(environ)


def production_env(**overrides: str) -> dict[str, str]:
    environ = {
        "APP_MODE": "PROD",
        "PAYMENT_RECIPIENT_SS58": RECIPIENT,
        "BOUNTY_WALLET_HOTKEY_SS58": MINER_COLDKEY,
        # Required in production, and refused if they are the published development constants;
        # see tests/test_api_public.py for the settings guardrails themselves.
        "PUBLIC_CURSOR_SECRET": "x" * 40,
        "PUBLIC_ACTIVITY_SALT": "y" * 40,
        # Stage 2 additions production also refuses to start without.
        "WEBSITE_BASE_URL": "https://conjectures.io",
        "MAIL_SENDER": "smtp",
        "SMTP_HOST": "smtp.example.com",
        "SMTP_USERNAME": "smtp-user",
        "SMTP_PASSWORD": "smtp-password",
        "SMTP_FROM_ADDRESS": "login@conjectures.io",
    }
    environ.update(overrides)
    return environ


def test_production_defaults_are_all_hardened():
    settings = Settings.from_env(production_env())
    assert isinstance(build_authenticator(settings), ColdkeySignatureAuthenticator)
    assert isinstance(build_payment_verifier(settings), ChainPaymentVerifier)
    assert settings.expose_docs is False
    assert settings.production is True


def test_smtp_configuration_is_complete_and_tls_is_mandatory_in_production():
    for missing in ("SMTP_HOST", "SMTP_FROM_ADDRESS"):
        environ = production_env()
        del environ[missing]
        with pytest.raises(SettingsError, match=missing):
            Settings.from_env(environ)

    with pytest.raises(SettingsError, match="both be set"):
        Settings.from_env(production_env(SMTP_PASSWORD=""))
    with pytest.raises(SettingsError, match="must use TLS"):
        Settings.from_env(production_env(SMTP_SECURITY="none"))

    settings = Settings.from_env(
        production_env(SMTP_PORT="465", SMTP_SECURITY="implicit-tls")
    )
    assert settings.smtp_port == 465
    assert settings.smtp_security == "implicit-tls"
    assert "smtp-password" not in repr(settings)


def test_the_magic_link_points_at_the_configured_website_route():
    """The path is joined onto the website origin, and it is configurable.

    The route belongs to the website repository, so the value that matters is whatever that
    deployment serves. A literal here is how the link came to point at `/auth/verify` while
    the page moved to `/login/verify`: nothing in this repository serves the path, so nothing
    in this repository failed when it drifted.
    """
    settings = Settings.from_env(base_env())
    assert settings.email_verify_path == "/login/verify"
    assert magic_link(
        base_url=settings.website_base_url, token="tok", path=settings.email_verify_path
    ).endswith("/login/verify?token=tok")

    moved = Settings.from_env({**base_env(), "EMAIL_VERIFY_PATH": "/auth/verify"})
    assert (
        magic_link(base_url="https://conjectures.io/", token="tok", path=moved.email_verify_path)
        == "https://conjectures.io/auth/verify?token=tok"
    )

    # The token is the only query parameter, and it is escaped: it reaches the page through a
    # URL, so a raw `+` or `&` in a token would arrive as a different token than was stored.
    assert magic_link(
        base_url="https://conjectures.io", token="a+b&c/d", path="/login/verify"
    ) == "https://conjectures.io/login/verify?token=a%2Bb%26c%2Fd"


def test_the_verify_path_may_not_carry_its_own_origin_or_query():
    """Rooted, and nothing else.

    An absolute URL here would send a live sign-in credential to a host nobody configured,
    which is the same failure `WEBSITE_BASE_URL` is validated to prevent — and a
    protocol-relative `//host` is an absolute URL that merely looks like a path.
    """
    for bad in ("login/verify", "https://elsewhere.test/verify", "//elsewhere.test/verify"):
        with pytest.raises(SettingsError, match="rooted path"):
            Settings.from_env({**base_env(), "EMAIL_VERIFY_PATH": bad})

    for bad in ("/login/verify?next=/", "/login/verify#token"):
        with pytest.raises(SettingsError, match="query string or fragment"):
            Settings.from_env({**base_env(), "EMAIL_VERIFY_PATH": bad})

    # A trailing slash is normalised rather than refused: it changes nothing about where the
    # link lands, and refusing it would fail a deployment over a typo that does not matter.
    trailing = Settings.from_env({**base_env(), "EMAIL_VERIFY_PATH": "/login/verify/"})
    assert trailing.email_verify_path == "/login/verify"


def test_google_client_id_is_optional_and_shape_checked():
    assert Settings.from_env(base_env()).google_client_id == ""
    client_id = (
        "1081377001123-4kkh4sfuemmr66b5a5sl64b8d6jlhmme.apps.googleusercontent.com"
    )
    assert (
        Settings.from_env({**base_env(), "GOOGLE_CLIENT_ID": client_id}).google_client_id
        == client_id
    )
    with pytest.raises(SettingsError, match="GOOGLE_CLIENT_ID"):
        Settings.from_env({**base_env(), "GOOGLE_CLIENT_ID": "not-a-google-web-client"})


def test_production_refuses_a_static_bounty_balance():
    with pytest.raises(SettingsError, match="development-only"):
        Settings.from_env(production_env(BOUNTY_POOL_BALANCE_RAO="4000000000"))


def test_production_requires_the_bounty_stake_hotkey():
    environ = production_env()
    del environ["BOUNTY_WALLET_HOTKEY_SS58"]
    with pytest.raises(SettingsError, match="BOUNTY_WALLET_HOTKEY_SS58"):
        Settings.from_env(environ)


def test_development_defaults_are_convenient():
    settings = Settings.from_env(base_env())
    assert isinstance(build_authenticator(settings), DevelopmentAuthenticator)
    assert isinstance(build_payment_verifier(settings), DevelopmentPaymentVerifier)
    assert settings.payment_amount_rao == 500_000_000
    assert settings.nonce_window_seconds == 120
    assert settings.review_policy_version == "v2"
    assert settings.bounty_pool_balance_rao == 4_000_000_000
    assert settings.bounty_constant_numerator == 1
    assert settings.bounty_constant_denominator == 10
    assert settings.bounty_ramp_seconds == 1296000
    assert settings.bounty_policy_version == "linear-age-v3-locked"
    assert settings.bounty_max_age_weight == 60
    assert settings.bounty_max_share_numerator == 1
    assert settings.bounty_max_share_denominator == 8
    assert settings.bounty_netuid == 66


def test_the_maximum_bounty_share_cannot_exceed_the_treasury():
    with pytest.raises(SettingsError, match="BOUNTY_MAX_SHARE_NUMERATOR"):
        Settings.from_env(
            base_env(
                BOUNTY_MAX_SHARE_NUMERATOR="101",
                BOUNTY_MAX_SHARE_DENOMINATOR="100",
            )
        )


def test_task_repository_root_configures_both_pool_paths(tmp_path: Path):
    settings = Settings.from_env(
        base_env(CONJECTURES_TASKS_ROOT=str(tmp_path / "task-repository"))
    )
    assert settings.task_allowlist_path == tmp_path / "task-repository/allowlist.json"
    assert settings.task_pool_root == tmp_path / "task-repository/pool"


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


@pytest.mark.parametrize(
    "override,message",
    [
        ({"PAYMENT_AMOUNT_RAO": "0"}, "positive"),
        ({"PAYMENT_AMOUNT_RAO": "not-a-number"}, "integer"),
        ({"MAX_BUNDLE_BYTES": "99999999"}, "must not exceed"),
        ({"MANUAL_REWARD_REVIEW_ENABLED": "maybe"}, "boolean"),
        ({"REVIEW_POLICY_VERSION": "Not Valid"}, "REVIEW_POLICY"),
        ({"APP_MODE": "STAGING"}, "APP_MODE"),
        ({"NONCE_WINDOW_SECONDS": "0"}, "positive"),
        # DEVELOPMENT_COLDKEY was retired by V035: the development payment verifier echoes
        # whichever allowlisted key signed, because production requires the payer and the
        # signer to be one key. The list is what is validated now.
        ({"DEVELOPMENT_COLDKEYS": "nope"}, "DEVELOPMENT_COLDKEYS"),
        ({"DEVELOPMENT_COLDKEYS": "nope,also-bad"}, "invalid addresses"),
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
            "DEVELOPMENT_COLDKEYS",
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
        asyncio.run(
            verifier.confirm(reference="0xabc", signer_coldkey=MINER_COLDKEY)
        )
    # Refusing every submission is the only safe default for a component that gates money.
    assert caught.value.status_code == 503
    assert caught.value.reason_code == "PAYMENT_VERIFIER_UNAVAILABLE"


def test_the_development_verifier_honours_its_allowlist():
    import asyncio

    from submission_api.errors import PaymentRequired

    verifier = DevelopmentPaymentVerifier(
        amount_rao=500_000_000, references=("0xallowed",)
    )
    confirmed = asyncio.run(
        verifier.confirm(reference="0xallowed", signer_coldkey=MINER_COLDKEY)
    )
    assert confirmed.amount_rao == 500_000_000
    assert confirmed.block > 0
    # The signer is echoed back as the sender, and there is no `sender=` to configure. That
    # is what keeps development on the production invariant: the extrinsic path requires
    # `payment_sender == signer_coldkey`, so a separately chosen sender would 402 locally
    # for a reason no local change could fix.
    assert confirmed.sender == MINER_COLDKEY
    with pytest.raises(PaymentRequired, match="development allowlist"):
        asyncio.run(
            verifier.confirm(reference="0xother", signer_coldkey=MINER_COLDKEY)
        )


# --- the production signature path, against a real keypair --------------------------


def test_a_real_signature_over_the_request_digest_verifies():
    key = Keypair.create_from_uri("//Alice")
    request = signed(hotkey=key.ss58_address, signature=b"\x00")
    signature = key.sign(request.message)
    assert len(signature) == 64
    ColdkeySignatureAuthenticator().verify(
        signed(hotkey=key.ss58_address, signature=signature)
    )


def test_a_signature_does_not_carry_over_to_another_digest():
    key = Keypair.create_from_uri("//Alice")
    signature = key.sign(signed(hotkey=key.ss58_address, signature=b"\x00").message)
    with pytest.raises(Unauthorized, match="does not match"):
        ColdkeySignatureAuthenticator().verify(
            signed(hotkey=key.ss58_address, digest="sha256:" + "ee" * 32, signature=signature)
        )


def test_another_keys_signature_is_rejected():
    key = Keypair.create_from_uri("//Alice")
    bob = Keypair.create_from_uri("//Bob")
    signature = bob.sign(signed(hotkey=key.ss58_address, signature=b"\x00").message)
    with pytest.raises(Unauthorized, match="does not match"):
        ColdkeySignatureAuthenticator().verify(
            signed(hotkey=key.ss58_address, signature=signature)
        )


def test_a_garbage_signature_is_rejected():
    key = Keypair.create_from_uri("//Alice")
    with pytest.raises(Unauthorized):
        ColdkeySignatureAuthenticator().verify(
            signed(hotkey=key.ss58_address, signature=b"\xff" * 64)
        )


@pytest.mark.parametrize(
    "version", ["dynamic-age-v1", "dynamic-age-v2-locked", "dynamic-age-v2-locked-capped"]
)
def test_old_policy_names_cannot_label_new_quotes(version):
    with pytest.raises(SettingsError, match="old pricing formula"):
        Settings.from_env(base_env(BOUNTY_POLICY_VERSION=version))


def test_starting_bounty_share_cannot_exceed_the_cap():
    with pytest.raises(SettingsError, match="BOUNTY_CONSTANT"):
        Settings.from_env(base_env(BOUNTY_CONSTANT_DENOMINATOR="4"))


@pytest.mark.parametrize("duration", ["0", "-1", "31536001"])
def test_bounty_ramp_duration_must_be_positive_and_bounded(duration):
    with pytest.raises(SettingsError, match="BOUNTY_RAMP_SECONDS"):
        Settings.from_env(base_env(BOUNTY_RAMP_SECONDS=duration))
