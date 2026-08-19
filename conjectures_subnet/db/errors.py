"""Persistence-level failures.

Domain errors, not transport errors. This package is shared by the submission API and by the
payment, verification, review and reward components, so it must not know about HTTP. Each
consumer maps these to whatever its own interface needs.

`reason_code` is the stable string the API records in `api_rejection_log` and returns to the
miner, so a refusal is machine-readable rather than prose to be scraped.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def violated_constraint(exc: Exception) -> str | None:
    """The constraint or unique index a driver's integrity error names, or None.

    Exists because `IntegrityError` is one exception class for several unrelated failures: a
    uniqueness violation, a CHECK violation, a foreign-key violation. Code that catches it and
    assumes the *one* it was expecting reports every other cause as that one — a duplicate-key
    message for what is actually a malformed row, which sends a reader looking for a second record
    that does not exist.

    PostgreSQL names the offending object in the error, and psycopg exposes it as
    `exc.orig.diag.constraint_name`; for a unique index that is the index name. Read defensively
    through `getattr`, because a driver that exposes no diagnostics should make this return None
    rather than raise a second error while handling the first.

    **None means "could not tell", and a caller must treat that as "not the case I expected".**
    Guessing would restore the bug this exists to prevent, and on a money path an unexplained 500
    is better than a confident wrong answer.
    """
    orig = getattr(exc, "orig", None)
    diag = getattr(orig, "diag", None)
    name = getattr(diag, "constraint_name", None)
    return name if isinstance(name, str) and name else None


class DatabaseError(Exception):
    """Base class for durable-state failures callers are expected to handle."""

    reason_code = "DATABASE_ERROR"

    def __init__(
        self, message: str, *, reason_code: str | None = None, **details: Any
    ) -> None:
        super().__init__(message)
        self.message = message
        if reason_code is not None:
            self.reason_code = reason_code
        self.details: Mapping[str, Any] = details


class RecordNotFound(DatabaseError):
    """The record does not exist, or is not visible to this caller."""

    reason_code = "NOT_FOUND"


class RecordConflict(DatabaseError):
    """The write conflicts with existing durable state and must not be retried as-is."""

    reason_code = "CONFLICT"


class IdempotencyConflict(RecordConflict):
    """The idempotency key was already used with different submission data."""

    reason_code = "IDEMPOTENCY_CONFLICT"


class DuplicateProof(RecordConflict):
    """These exact proof bytes have already been submitted.

    `submissions.proof_digest` is globally unique, so one proof is payable at most once.
    """

    reason_code = "DUPLICATE_PROOF"


class DuplicatePayment(RecordConflict):
    """The payment reference already backs another submission."""

    reason_code = "DUPLICATE_PAYMENT"
