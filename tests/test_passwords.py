"""The password KDF, its encoding, and the policy a password has to meet.

No database and no API: this module is pure, and testing it here is what lets the API tests
below stay about flows rather than about hashing.
"""

from __future__ import annotations

import pytest

from submission_api.passwords import (
    MIN_LENGTH,
    PasswordRejected,
    assert_acceptable,
    hash_password,
    needs_rehash,
    normalise,
    spend_dummy_verification,
    verify,
)

# Every test that hashes uses the floor rather than the configured cost. n = 2**12 is 4 MiB and
# ~5ms instead of 64 MiB and ~140ms, and nothing here is testing how slow scrypt is.
CHEAP = {"cost_log2": 12}
PASSWORD = "correct horse battery staple"


def test_a_password_verifies_against_its_own_hash():
    assert verify(PASSWORD, hash_password(PASSWORD, **CHEAP))


def test_a_different_password_does_not():
    assert not verify("Correct horse battery staple", hash_password(PASSWORD, **CHEAP))


def test_two_hashes_of_one_password_differ():
    # A fresh salt each time, so identical passwords are not identical rows and a stolen table
    # cannot be sorted to find the accounts that share one.
    first = hash_password(PASSWORD, **CHEAP)
    second = hash_password(PASSWORD, **CHEAP)
    assert first != second
    assert verify(PASSWORD, first) and verify(PASSWORD, second)


def test_the_encoding_carries_the_parameters_it_was_made_with():
    scheme, cost, block, parallel, salt, key = hash_password(PASSWORD, **CHEAP).split("$")
    assert scheme == "scrypt"
    assert (cost, block, parallel) == ("12", "8", "1")
    assert salt and key


@pytest.mark.parametrize(
    "encoded",
    [
        "",
        "not-a-hash",
        "scrypt$12$8$1$onlyfourfields",
        "argon2$12$8$1$c2FsdA==$a2V5",  # a scheme this build does not know
        "scrypt$12$8$1$not!base64$a2V5",
        "scrypt$99$8$1$c2FsdA==$a2V5",  # a cost that would be a denial of service to honour
        "scrypt$12$8$1$$a2V5",  # no salt
    ],
)
def test_a_hash_that_cannot_be_parsed_is_a_failed_verification_not_a_crash(encoded):
    # A corrupt or hostile row must fail the sign-in, not the process. The account keeps every
    # other credential it has.
    assert verify(PASSWORD, encoded) is False


def test_rehash_is_needed_only_when_the_stored_cost_is_weaker():
    stored = hash_password(PASSWORD, cost_log2=14)
    assert needs_rehash(stored, cost_log2=15) is True
    assert needs_rehash(stored, cost_log2=14) is False
    # Lowering the configured cost is not a request to downgrade every existing hash.
    assert needs_rehash(stored, cost_log2=13) is False


def test_an_unparseable_hash_always_wants_rehashing():
    assert needs_rehash("nonsense") is True


def test_the_dummy_verification_always_fails_and_costs_a_real_derivation():
    # It exists to spend time, so the only assertions available are that it answers False and
    # that it is not a no-op that returns instantly.
    import time

    started = time.perf_counter()
    assert spend_dummy_verification(PASSWORD) is False
    assert time.perf_counter() - started > 0.005


# --- normalisation ---------------------------------------------------------------------


def test_equivalent_unicode_forms_are_the_same_password():
    # The same characters typed on a phone and on a desktop can be different code points. A
    # password that works on one device and not the other is indistinguishable from a bug.
    composed = "café-passphrase-1"
    decomposed = "café-passphrase-1"
    assert composed != decomposed
    assert normalise(composed) == normalise(decomposed)
    assert verify(decomposed, hash_password(composed, **CHEAP))


def test_policy_returns_the_normalised_form_it_checked():
    # So a caller cannot check one string and hash another — which would differ exactly when
    # normalisation mattered.
    assert assert_acceptable("café-passphrase-1") == normalise("café-passphrase-1")


# --- policy ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("password", "because"),
    [
        ("short", "at least"),
        ("x" * 129, "at most"),
        ("aaaaaaaaaaaaaa", "different characters"),
        ("121212121212121", "different characters"),
        ("  leading-space-pass  ", "whitespace"),
        ("trailing-newline-pw\n", "whitespace"),
    ],
)
def test_policy_refuses_with_a_reason_a_person_can_act_on(password, because):
    with pytest.raises(PasswordRejected) as raised:
        assert_acceptable(password)
    assert because in str(raised.value)


def test_the_minimum_is_a_boundary_not_an_approximation():
    assert_acceptable("a" * (MIN_LENGTH - 5) + "bcdef")  # exactly MIN_LENGTH, 6 distinct
    with pytest.raises(PasswordRejected):
        assert_acceptable("a" * (MIN_LENGTH - 6) + "bcdef")  # one short


def test_a_password_may_contain_spaces():
    # A passphrase is the shape this policy is trying to encourage, so interior spaces are fine.
    assert assert_acceptable("four words with spaces")


def test_the_email_local_part_may_not_be_the_password():
    with pytest.raises(PasswordRejected) as raised:
        assert_acceptable("mysolver-account", email="solver@example.com")
    assert "local part" in str(raised.value)


def test_a_short_local_part_is_not_used_for_the_similarity_check():
    # Otherwise an address like bo@example.com would reject every password containing "bo".
    assert assert_acceptable("robust-and-long-pw", email="bo@example.com")


def test_the_local_part_check_ignores_case():
    with pytest.raises(PasswordRejected):
        assert_acceptable("SOLVER-and-more", email="solver@example.com")
