"""Hashing a password, and the policy one has to meet.

**scrypt from the standard library.** `hashlib.scrypt` is OpenSSL's implementation, so it is
memory-hard, and it costs no entry in `requirements-service.lock` — which is a deliberately
curated set, and a password KDF is not worth a C-extension dependency when the interpreter
already ships one. Argon2id would be the other defensible choice; scrypt at these parameters
is in the same class and is already here.

**The stored string carries its own parameters.** `scrypt$16$8$1$<salt>$<key>` — the cost, the
block size, the parallelism, then the salt and the derived key, all base64. That is what makes
raising the cost later a non-event: `needs_rehash` compares the parameters a stored hash was
made with against the ones configured now, and the sign-in path rehashes in place. A format
that stored only the digest would freeze 2026's parameters into the database forever.

**Every comparison is constant-time and every failure looks identical.** `verify` returns False
for a wrong password, a malformed encoding, and an unknown scheme alike, and it compares with
`hmac.compare_digest`. A caller cannot learn from this module which of those it hit.

**Timing is a disclosure channel, so it is spent deliberately.** A sign-in for an address with
no account would return in microseconds while one with an account spends ~100ms deriving a key,
which is an account-enumeration oracle that no amount of identical status codes can close.
`spend_dummy_verification` is what the router calls on that path: the same work, discarded.

Unicode is normalised to NFKC before hashing. The same characters typed on a phone and on a
desktop keyboard can be different code points, and a password that works on one device and not
the other is indistinguishable from a broken login.
"""

from __future__ import annotations

import base64
import functools
import hashlib
import hmac
import secrets
import unicodedata

SCHEME = "scrypt"

# n = 2**16, r = 8, p = 1: 64 MiB and roughly 100ms per derivation on server hardware. The
# memory is the point — it is what makes a GPU array a poor attack platform — and it is also
# the operational cost, since every concurrent sign-in holds that much. Raising it is a
# settings change plus the rehash-on-sign-in path; the stored hashes need no migration.
DEFAULT_COST_LOG2 = 16
DEFAULT_BLOCK_SIZE = 8
DEFAULT_PARALLELISM = 1

# OpenSSL refuses a derivation whose working set exceeds `maxmem`, and its own default is far
# below what these parameters need. Derived from the parameters rather than hardcoded, so a
# configured cost increase does not turn into an opaque ValueError from libcrypto.
MEMORY_HEADROOM = 2

KEY_BYTES = 32
SALT_BYTES = 16

# Twelve is the floor a memory-hard KDF makes meaningful: it is long enough that an offline
# attack on a stolen row is not the cheapest way in, and short enough that people do not write
# it on a sticky note. The ceiling bounds the work an unauthenticated caller can ask for —
# scrypt's cost is fixed in the parameters, but the input is still hashed and copied.
MIN_LENGTH = 12
MAX_LENGTH = 128

# A password of one repeated character satisfies any length rule and no attacker's dictionary.
# This is not a strength meter — those mostly teach people to append "1!" — it is a floor under
# the degenerate cases, which is the part a length check genuinely misses.
MIN_DISTINCT_CHARACTERS = 6

# Below this, a local part is too short to be a meaningful substring test: rejecting every
# password containing "bo" because the address is bo@example.com would be a bug, not a policy.
MIN_LOCAL_PART_FOR_SIMILARITY = 4


class PasswordRejected(Exception):
    """The password does not meet policy. The message is safe to show a person.

    Deliberately not an `ApiError`: this module has no opinion about HTTP, and the router that
    catches it is the thing that knows a rejected password is a 400 rather than a 401.
    """


def normalise(password: str) -> str:
    return unicodedata.normalize("NFKC", password)


def assert_acceptable(password: str, *, email: str | None = None) -> str:
    """Check policy and return the normalised password, or raise `PasswordRejected`.

    Returning the normalised form rather than the input is what stops a caller from checking
    one string and hashing another — the two would differ exactly when normalisation mattered.
    """
    candidate = normalise(password)
    if len(candidate) < MIN_LENGTH:
        raise PasswordRejected(
            f"password must be at least {MIN_LENGTH} characters"
        )
    if len(candidate) > MAX_LENGTH:
        raise PasswordRejected(f"password must be at most {MAX_LENGTH} characters")
    if candidate != candidate.strip():
        # Leading or trailing whitespace is almost always a paste accident, and one that is
        # invisible in a password field and impossible to reproduce later. Interior spaces are
        # fine — a passphrase is the shape of password this policy is trying to encourage.
        raise PasswordRejected("password must not begin or end with whitespace")
    if len(set(candidate)) < MIN_DISTINCT_CHARACTERS:
        raise PasswordRejected(
            f"password must use at least {MIN_DISTINCT_CHARACTERS} different characters"
        )
    if email:
        local_part = email.split("@", 1)[0].strip().lower()
        if (
            len(local_part) >= MIN_LOCAL_PART_FOR_SIMILARITY
            and local_part in candidate.lower()
        ):
            raise PasswordRejected(
                "password must not contain the local part of the email address"
            )
    return candidate


def hash_password(
    password: str,
    *,
    cost_log2: int = DEFAULT_COST_LOG2,
    block_size: int = DEFAULT_BLOCK_SIZE,
    parallelism: int = DEFAULT_PARALLELISM,
) -> str:
    """Derive a new hash with a fresh salt. Blocking: run it off the event loop."""
    salt = secrets.token_bytes(SALT_BYTES)
    key = _derive(
        normalise(password),
        salt=salt,
        cost_log2=cost_log2,
        block_size=block_size,
        parallelism=parallelism,
    )
    return "$".join(
        (
            SCHEME,
            str(cost_log2),
            str(block_size),
            str(parallelism),
            _b64(salt),
            _b64(key),
        )
    )


def verify(password: str, encoded: str) -> bool:
    """True when `password` produced `encoded`. False for anything else, including garbage.

    Blocking, and deliberately so: the cost is the defence. Run it off the event loop.
    """
    parsed = _parse(encoded)
    if parsed is None:
        return False
    cost_log2, block_size, parallelism, salt, expected = parsed
    try:
        actual = _derive(
            normalise(password),
            salt=salt,
            cost_log2=cost_log2,
            block_size=block_size,
            parallelism=parallelism,
        )
    except (ValueError, OverflowError, MemoryError):
        # Parameters that libcrypto refuses. A stored hash nobody can verify is a failed
        # sign-in, not a 500: the account still has every other credential it had.
        return False
    return hmac.compare_digest(actual, expected)


def needs_rehash(
    encoded: str,
    *,
    cost_log2: int = DEFAULT_COST_LOG2,
    block_size: int = DEFAULT_BLOCK_SIZE,
    parallelism: int = DEFAULT_PARALLELISM,
) -> bool:
    """True when a stored hash was made with weaker parameters than are configured now.

    Only ever answers "weaker", never "different": an operator who lowers the cost has not
    asked for every existing hash to be downgraded on next sign-in.
    """
    parsed = _parse(encoded)
    if parsed is None:
        return True
    stored_cost, stored_block, stored_parallel, _salt, _key = parsed
    return (
        stored_cost < cost_log2
        or stored_block < block_size
        or stored_parallel < parallelism
    )


def spend_dummy_verification(password: str) -> bool:
    """Do a real derivation and throw the answer away. Always False.

    For the sign-in path when there is no account, or an account with no password set. Without
    it, response time alone says which addresses are registered.
    """
    verify(password, _dummy_hash())
    return False


@functools.cache
def _dummy_hash() -> str:
    """One hash of an unguessable value, derived once per process.

    Computed lazily rather than at import so that starting the API — and every test that never
    touches a password — does not pay 100ms for a value it will not use.
    """
    return hash_password(secrets.token_urlsafe(32))


def _derive(
    password: str, *, salt: bytes, cost_log2: int, block_size: int, parallelism: int
) -> bytes:
    n = 1 << cost_log2
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=block_size,
        p=parallelism,
        maxmem=MEMORY_HEADROOM * 128 * n * block_size * parallelism,
        dklen=KEY_BYTES,
    )


def _parse(encoded: str) -> tuple[int, int, int, bytes, bytes] | None:
    parts = encoded.split("$")
    if len(parts) != 6 or parts[0] != SCHEME:
        return None
    try:
        cost_log2, block_size, parallelism = (int(part) for part in parts[1:4])
        salt = base64.b64decode(parts[4], validate=True)
        key = base64.b64decode(parts[5], validate=True)
    except (ValueError, TypeError):
        return None
    # Bounds on values read back from the database. A row rewritten to n = 2**40 would be a
    # denial of service against the process that verifies it, not a weak password.
    if not (1 <= cost_log2 <= 22 and 1 <= block_size <= 64 and 1 <= parallelism <= 16):
        return None
    if not salt or not key:
        return None
    return cost_log2, block_size, parallelism, salt, key


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


__all__ = [
    "DEFAULT_BLOCK_SIZE",
    "DEFAULT_COST_LOG2",
    "DEFAULT_PARALLELISM",
    "MAX_LENGTH",
    "MIN_LENGTH",
    "PasswordRejected",
    "assert_acceptable",
    "hash_password",
    "needs_rehash",
    "normalise",
    "spend_dummy_verification",
    "verify",
]
