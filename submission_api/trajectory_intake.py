"""Bounded raw-file envelope and separately attested, pinned consent."""
import hashlib
import json
import time
from contextlib import asynccontextmanager

import anyio
from pydantic import Field

from submission_api.errors import TooManyRequests
from submission_api.ratelimit import SlidingWindowLimiter
from submission_api.trajectory_schema import (
    Permissions, StrictModel, canonical_bytes, evidence_digest, parse_package, check_json_depth,
    SAFE_SEMANTIC_ERRORS,
)
from submission_api.trajectory_terms import ASSENT_STATEMENTS_SHA256, TERMS_SHA256, TERMS_VERSION

MAX_ENVELOPE_BYTES = 128 * 1024 * 1024 + 64 * 1024


class Consent(StrictModel):
    terms_version: str = Field(max_length=64)
    terms_sha256: str = Field(max_length=71)
    assent_statements_sha256: str = Field(max_length=71)
    accepted: bool = Field(strict=True)


class Envelope(StrictModel):
    expected_state_version: int | None = Field(default=None, strict=True, ge=1, le=2147483647)
    package_json: str
    consent: Consent


def prepare_upload(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate envelope key")
            result[key] = value
        return result
    check_json_depth(raw)
    envelope = Envelope.model_validate(json.loads(raw.decode("utf-8"), object_pairs_hook=unique))
    consent = envelope.consent
    if (not consent.accepted or consent.terms_version != TERMS_VERSION
            or consent.terms_sha256 != TERMS_SHA256
            or consent.assent_statements_sha256 != ASSENT_STATEMENTS_SHA256):
        raise ValueError("review the exact pinned terms and assent statement")
    original = envelope.package_json.encode("utf-8")
    package = parse_package(original)  # Validates original UTF-8 text and duplicate keys unchanged.
    template_fields = [package.task_id, *package.reproduction.commands, package.reproduction.environment,
                       package.reproduction.expected_result, package.reproduction.limitations]
    for name in ("lean_toolchain", "mathlib_commit", "task_repository_commit", "code_commit"):
        pin = getattr(package.reproduction, name)
        template_fields.extend([pin.value or "", pin.unknown_reason or ""])
    if not package.synthetic and (any("REPLACE_" in value for value in template_fields) or any(
            run.source_export_unavailable_reason == "No original conversation exists; this is synthetic."
            for run in package.runs)):
        raise ValueError("replace all template placeholders")
    # The checkbox is the authority for permission, not booleans in a hand-edited file.
    values = {k: True for k in Permissions.model_fields if k != "terms_version"}
    package = package.model_copy(update={"permissions": Permissions(terms_version=TERMS_VERSION, **values)})
    canonical = canonical_bytes(package)
    return package, canonical, hashlib.sha256(canonical).digest(), evidence_digest(package), {
        **consent.model_dump(), "source_file_sha256": "sha256:" + hashlib.sha256(original).hexdigest(),
    }, envelope.expected_state_version


class UploadBudget:
    """One 64-MiB parser/stream at a time per worker; no waiting requests hold a DB connection.

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


def safe_errors(error):
    """Only schema-owned field names/indices and standard error codes, never input or messages."""
    from submission_api import trajectory_schema
    names = set(Envelope.model_fields) | set(Consent.model_fields)
    for value in vars(trajectory_schema).values():
        if isinstance(value, type) and issubclass(value, StrictModel):
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
