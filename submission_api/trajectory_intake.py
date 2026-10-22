"""Bounded zip package upload and separately attested, pinned consent."""
import hashlib
import time
from contextlib import asynccontextmanager

import anyio
from pydantic import BaseModel, Field

from submission_api.errors import BadRequest, TooManyRequests
from submission_api.ratelimit import SlidingWindowLimiter
from submission_api.trajectory_schema import (
    MAX_PACKAGE_BYTES, PackageError, StrictModel, parse_package, SAFE_SEMANTIC_ERRORS,
)
from submission_api.trajectory_terms import ASSENT_STATEMENTS_SHA256, TERMS_SHA256, TERMS_VERSION

# The request body is the zip itself; consent rides in the query.
MAX_UPLOAD_BYTES = MAX_PACKAGE_BYTES
PACKAGE_MEDIA_TYPES = ("application/zip", "application/x-zip-compressed", "application/octet-stream")


class Consent(StrictModel):
    terms_version: str = Field(max_length=64)
    terms_sha256: str = Field(max_length=71)
    assent_statements_sha256: str = Field(max_length=71)
    accepted: bool = Field(strict=True)


def check_consent(consent: Consent) -> dict:
    """Checked before any byte of the package is read."""
    if (not consent.accepted or consent.terms_version != TERMS_VERSION
            or consent.terms_sha256 != TERMS_SHA256
            or consent.assent_statements_sha256 != ASSENT_STATEMENTS_SHA256):
        raise BadRequest("Review and accept the exact chat-log terms shown, then retry.",
                         reason_code="TRAJECTORY_TERMS_CHANGED")
    return consent.model_dump()


def prepare_upload(raw: bytes, consent: dict, expected_state_version=None):
    """Validate the zip (in a worker thread); the stored package is the uploaded bytes unchanged."""
    summary = parse_package(raw)
    return summary, raw, hashlib.sha256(raw).digest(), summary.fingerprint, {
        **consent, "source_file_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
    }, expected_state_version


class UploadBudget:
    """One 256-MiB package check at a time per worker; no waiting requests hold a DB connection.

    Process-local limits complement the deployment's IP limiter. Replicas need a shared edge
    limit for a global quota. Failed attempts, including invalid exports, spend a separate budget.
    """
    def __init__(self):
        self.slots = anyio.Semaphore(1)
        self.accounts = set()
        self.successes = SlidingWindowLimiter(limit=3, window_seconds=3600, max_clients=10000)
        self.failures = SlidingWindowLimiter(limit=6, window_seconds=900, max_clients=10000)

    @asynccontextmanager
    async def hold(self, account_id):
        key = str(account_id)
        now = time.monotonic()
        # The existing limiter evicts on check; prune here before the admission gate too.
        # This bounded scan prevents historical saturation from permanently refusing newcomers.
        for budget in (self.failures, self.successes):
            for old, hits in list(budget._hits.items()):
                if not hits or hits[-1] <= now - budget.window_seconds:
                    budget._hits.pop(old, None)
        if key in self.accounts or any(not budget.peek(key, now).allowed or
                (budget.tracked_clients >= budget.max_clients and key not in budget._hits)
                for budget in (self.failures, self.successes)):
            raise TooManyRequests("Trajectory upload budget exhausted; retry later.",
                                  reason_code="TRAJECTORY_RATE_LIMIT")
        try:
            self.slots.acquire_nowait()
        except anyio.WouldBlock:
            raise TooManyRequests("A trajectory upload is in progress; retry shortly.",
                                  reason_code="TRAJECTORY_BUSY") from None
        self.accounts.add(key)
        try:
            yield
        except Exception as error:
            if getattr(error, "status_code", None) != 408:
                self.failures.check(key, time.monotonic())
            raise
        else:
            self.successes.check(key, time.monotonic())
        finally:
            self.accounts.discard(key)
            self.slots.release()


def package_errors(error: PackageError) -> list[dict]:
    """Bounded, schema-owned locations only: package file, line, field names and standard codes."""
    location = {k: v for k, v in (("file", error.file), ("line", error.line)) if v is not None}
    if error.validation is None:
        item = {**location, "path": [], "type": "value_error"}
        if error.message in SAFE_SEMANTIC_ERRORS:
            item["message"] = error.message
        return [item]
    return [{**location, **item} for item in safe_errors(error.validation)]


def safe_errors(error):
    """Only schema-owned field names/indices and standard error codes, never input or messages."""
    from submission_api import trajectory_schema
    names = set(Consent.model_fields)
    for value in vars(trajectory_schema).values():
        if isinstance(value, type) and issubclass(value, BaseModel):
            names.update(value.model_fields)
    result = []
    for error_item in error.errors(include_url=False, include_context=True, include_input=False)[:20]:
        item = {"path": [p if isinstance(p, int) or p in names else "<unknown-field>" for p in error_item["loc"]],
                "type": error_item["type"]}
        message = str(error_item.get("ctx", {}).get("error", ""))
        if error_item["type"] == "value_error" and message in SAFE_SEMANTIC_ERRORS:
            item["message"] = message
        result.append(item)
    return result
