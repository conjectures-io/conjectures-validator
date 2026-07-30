"""Persistence-level failures.

Domain errors, not transport errors. This package is shared by the submission API and by the
payment, verification, review and reward components, so it must not know about HTTP. Each
consumer maps these to whatever its own interface needs.

`reason_code` is the stable string the API records in `api_rejection_log` and returns to the
miner, so a refusal is machine-readable rather than prose to be scraped.
"""

from __future__ import annotations

from typing import Any, Mapping


class DatabaseError(Exception):
    """Base class for durable-state failures callers are expected to handle."""

    reason_code = "DATABASE_ERROR"

    def __init__(self, message: str, *, reason_code: str | None = None, **details: Any) -> None:
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
