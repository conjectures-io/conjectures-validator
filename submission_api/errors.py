"""Miner-facing error model.

Every failure carries an HTTP status that means what it says and a stable `reason_code` —
either a verifier `ReasonCode` or an API-level code such as `PAYMENT_NOT_FINALIZED` — so a
miner can act on it without scraping prose. Responses use RFC 9457
`application/problem+json`.

The same `reason_code` is what `api_rejection_log` records, which is the only trace a refused
request leaves, since payment-gated intake creates no submission.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from conjectures_subnet.db.errors import (
    DatabaseError,
    DuplicatePayment,
    DuplicateProof,
    IdempotencyConflict,
    RecordConflict,
    RecordNotFound,
)
from verifier.errors import CONFIGURATION_REASONS, ReasonCode, VerifierError

PROBLEM_MEDIA_TYPE = "application/problem+json"

# Bundle and proof policy failures are the miner's to fix and are reported as 422. A reason
# code in CONFIGURATION_REASONS means the validator is misconfigured, not that the miner did
# anything wrong, so it must never be billed to the request as a 4xx.
_REASON_STATUS: Mapping[ReasonCode, int] = {
    ReasonCode.BUNDLE_TOO_LARGE: 413,
    ReasonCode.BUNDLE_MALFORMED: 422,
    ReasonCode.BUNDLE_POLICY_VIOLATION: 422,
    ReasonCode.BUNDLE_MANIFEST_INVALID: 422,
    ReasonCode.BUNDLE_DIGEST_MISMATCH: 422,
    ReasonCode.SUBMISSION_TOO_LARGE: 413,
    ReasonCode.SUBMISSION_NOT_UTF8: 422,
    ReasonCode.SUBMISSION_POLICY_VIOLATION: 422,
    ReasonCode.TASK_COMMITMENT_MISMATCH: 409,
    ReasonCode.INELIGIBLE_TASK: 409,
    ReasonCode.INVALID_ARGUMENT: 400,
}

REASON_MALFORMED_REQUEST = "MALFORMED_REQUEST"
REASON_TASK_NOT_ALLOWED = "TASK_NOT_ALLOWED"
REASON_INTERNAL = "INTERNAL_ERROR"


class ApiError(Exception):
    """A deliberate, miner-visible failure."""

    status_code = 400
    title = "Request rejected"
    reason_code = REASON_MALFORMED_REQUEST

    def __init__(
        self,
        detail: str,
        *,
        status_code: int | None = None,
        reason_code: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        if status_code is not None:
            self.status_code = status_code
        if reason_code is not None:
            self.reason_code = reason_code
        self.extra = dict(extra or {})

    def problem(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "type": "about:blank",
            "title": self.title,
            "status": self.status_code,
            "detail": self.detail,
            "reason_code": self.reason_code,
        }
        body.update(self.extra)
        return body


class BadRequest(ApiError):
    status_code = 400
    title = "Malformed request"


class Unauthorized(ApiError):
    status_code = 401
    title = "Authentication failed"
    reason_code = "SIGNATURE_INVALID"


class PaymentRequired(ApiError):
    status_code = 402
    title = "Payment not confirmed"
    reason_code = "PAYMENT_NOT_FINALIZED"


class Forbidden(ApiError):
    status_code = 403
    title = "Not permitted"
    reason_code = "FORBIDDEN"


class NotFound(ApiError):
    status_code = 404
    title = "Not found"
    reason_code = "NOT_FOUND"


class Conflict(ApiError):
    status_code = 409
    title = "Conflict"
    reason_code = "CONFLICT"


class LengthRequired(ApiError):
    status_code = 411
    title = "Content-Length required"


class PayloadTooLarge(ApiError):
    status_code = 413
    title = "Payload too large"
    reason_code = "BUNDLE_TOO_LARGE"


class UnprocessableEntity(ApiError):
    status_code = 422
    title = "Submission rejected"


class TooManyRequests(ApiError):
    """A per-identity limit, distinct from the middleware's per-IP one.

    Mailing a sign-in link or minting a signing nonce is an action taken against an address
    someone else controls, so the thing to bound is requests per address. The rate-limit
    middleware cannot see that — it only knows who is asking, not who is being asked about.
    """

    status_code = 429
    title = "Too many requests"
    reason_code = "TOO_MANY_REQUESTS"


class ServiceUnavailable(ApiError):
    status_code = 503
    title = "Temporarily unavailable"
    reason_code = "SERVICE_UNAVAILABLE"


def from_verifier_error(exc: VerifierError) -> ApiError:
    """Translate a verifier rejection into a miner-facing problem response."""
    if (
        exc.reason in CONFIGURATION_REASONS
        and exc.reason is not ReasonCode.INVALID_ARGUMENT
    ):
        # The validator is misconfigured; do not tell the miner their bundle was wrong.
        return ServiceUnavailable(
            "the validator cannot process submissions right now",
            reason_code=exc.reason.value,
        )
    return ApiError(
        str(exc),
        status_code=_REASON_STATUS.get(exc.reason, 422),
        reason_code=exc.reason.value,
    )


def from_database_error(exc: DatabaseError) -> ApiError:
    """Translate a persistence failure into a miner-facing problem response.

    The database layer is shared with the workers and stays free of HTTP vocabulary, so the
    mapping to status codes lives here.
    """
    details = dict(exc.details)
    if isinstance(exc, RecordNotFound):
        return NotFound(exc.message, reason_code=exc.reason_code, extra=details)
    if isinstance(
        exc, (DuplicateProof, DuplicatePayment, IdempotencyConflict, RecordConflict)
    ):
        return Conflict(exc.message, reason_code=exc.reason_code, extra=details)
    return ServiceUnavailable(
        "the validator cannot process submissions right now",
        reason_code=exc.reason_code,
    )


def problem_response(error: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content=error.problem(),
        media_type=PROBLEM_MEDIA_TYPE,
    )


async def api_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return problem_response(exc)


async def verifier_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, VerifierError)
    return problem_response(from_verifier_error(exc))


async def database_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DatabaseError)
    return problem_response(from_database_error(exc))


async def validation_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Report which inputs were invalid without echoing the framework's internals.

    `str(RequestValidationError)` embeds absolute source paths and line numbers from the
    handler that raised it, which must not reach a miner.
    """
    errors: list[dict[str, Any]] = []
    for item in getattr(exc, "errors", list)():
        location = item.get("loc") or ()
        errors.append(
            {
                "location": ".".join(str(part) for part in location),
                "message": str(item.get("msg", "invalid value")),
                "type": str(item.get("type", "invalid")),
            }
        )
    return problem_response(
        BadRequest(
            "request is missing or has malformed fields",
            extra={"errors": errors} if errors else None,
        )
    )


async def unhandled_error_handler(_request: Request, _exc: Exception) -> JSONResponse:
    # Never leak internals to a miner. The traceback goes to the logs via the ASGI server.
    return problem_response(
        ApiError("internal error", status_code=500, reason_code=REASON_INTERNAL)
    )
