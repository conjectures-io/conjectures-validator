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
    CommercialElection, Permissions, StrictModel, canonical_bytes, evidence_digest, parse_package,
)
from submission_api.trajectory_terms import (
    ASSENT_STATEMENTS_SHA256, COMMERCIAL_TERMS_SHA256, REVIEW_TERMS_SHA256, REVIEW_TERMS_VERSION,
)

MAX_ENVELOPE_BYTES = 64 * 1024 * 1024 + 64 * 1024


class Consent(StrictModel):
    review_terms_version: str = Field(max_length=64)
    review_terms_sha256: str = Field(max_length=71)
    assent_statements_sha256: str = Field(max_length=71)
    accepted: bool = Field(strict=True)


class Envelope(StrictModel):
    package_json: str
    consent: Consent
    commercial_election: CommercialElection


def prepare_upload(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate envelope key")
            result[key] = value
        return result
    envelope = Envelope.model_validate(json.loads(raw.decode("utf-8"), object_pairs_hook=unique))
    consent = envelope.consent
    if (not consent.accepted or consent.review_terms_version != REVIEW_TERMS_VERSION
            or consent.review_terms_sha256 != REVIEW_TERMS_SHA256
            or consent.assent_statements_sha256 != ASSENT_STATEMENTS_SHA256
            or envelope.commercial_election.terms_sha256 != COMMERCIAL_TERMS_SHA256):
        raise ValueError("review the exact pinned terms and assent statements")
    original = envelope.package_json.encode("utf-8")
    package = parse_package(original)  # Validates original UTF-8 text and duplicate keys unchanged.
    if not package.synthetic and ("REPLACE_" in envelope.package_json or
            "No original conversation exists; this is synthetic." in envelope.package_json):
        raise ValueError("replace all template placeholders")
    # The checkbox is the authority for permission, not booleans in a hand-edited file.
    values = {k: True for k in Permissions.model_fields if k != "terms_version"}
    package = package.model_copy(update={
        "permissions": Permissions(terms_version=REVIEW_TERMS_VERSION, **values),
        "commercial_election": envelope.commercial_election,
    })
    package = type(package).model_validate(package.model_dump())  # Cross-source/scopes rechecked.
    canonical = canonical_bytes(package)
    return package, canonical, hashlib.sha256(canonical).digest(), evidence_digest(package), {
        **consent.model_dump(), "source_file_sha256": "sha256:" + hashlib.sha256(original).hexdigest(),
    }


class UploadBudget:
    """One 32-MiB parser/stream at a time per worker; no waiting requests hold a DB connection.

    Process-local limits complement the deployment's IP limiter. Replicas need a shared edge
    limit for a global quota. Failed attempts, including invalid exports, spend a separate budget.
    """
    def __init__(self):
        self.slots = anyio.Semaphore(1)
        self.accounts = set()
        self.failures = SlidingWindowLimiter(limit=6, window_seconds=900, max_clients=10000)

    @asynccontextmanager
    async def hold(self, account_id):
        key = str(account_id)
        now = time.monotonic()
        # The existing limiter evicts on check; prune here before the admission gate too.
        # This bounded scan prevents historical saturation from permanently refusing newcomers.
        for old, hits in list(self.failures._hits.items()):
            if not hits or hits[-1] <= now - self.failures.window_seconds:
                self.failures._hits.pop(old, None)
        if (key in self.accounts or not self.failures.peek(key, time.monotonic()).allowed
                or self.failures.tracked_clients >= self.failures.max_clients and key not in self.failures._hits):
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
        except Exception:
            self.failures.check(key, time.monotonic())
            raise
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
    return [{"path": [p if isinstance(p, int) or p in names else "<unknown-field>" for p in e["loc"]],
             "type": e["type"]} for e in error.errors(include_url=False, include_context=False, include_input=False)[:20]]
