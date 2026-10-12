"""Exportable evidence, never a request for private model reasoning.

This format is deliberately JSON text only. Nothing is extracted, executed, rendered as HTML,
or fetched from URLs. Validation establishes structure and linkage, not truth or rights.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

MAX_PACKAGE_BYTES = 32 * 1024 * 1024
RETENTION_DAYS = 90
TERMS_VERSION = "trajectory-review-draft-v1"
Digest = Annotated[str, Field(pattern=r"^sha256:[a-f0-9]{64}$")]
Short = Annotated[str, Field(min_length=1, max_length=500)]
Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9._-]{1,100}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class KnownValue(StrictModel):
    value: Short | None
    unknown_reason: Short | None

    @model_validator(mode="after")
    def honest_unknown(self):
        if (self.value is None) != (self.unknown_reason is not None):
            raise ValueError("use a value or an explanation of why it is unknown, never invent one")
        return self


class Timestamp(StrictModel):
    value: AwareDatetime | None
    unknown_reason: Short | None

    @model_validator(mode="after")
    def honest_unknown(self):
        if (self.value is None) != (self.unknown_reason is not None):
            raise ValueError("timestamp needs a timezone or an honest unknown reason")
        return self


class ModelRun(StrictModel):
    id: Identifier
    provider: KnownValue
    model: KnownValue
    version: KnownValue
    interface: Short
    account_agreement: KnownValue
    terms_reference: KnownValue
    settings: Annotated[str, Field(min_length=1, max_length=4000)]
    export_method: Short
    source_export_sha256: Digest | None
    source_export_unavailable_reason: Short | None

    @model_validator(mode="after")
    def export_evidence(self):
        if (self.source_export_sha256 is None) != (self.source_export_unavailable_reason is not None):
            raise ValueError("source export needs a digest or an explanation")
        return self


class Event(StrictModel):
    sequence: Annotated[int, Field(strict=True, ge=1, le=10000)]
    id: Identifier
    kind: Literal["user_message", "assistant_message", "tool_call", "tool_result", "failure", "revision"]
    visibility: Literal["user_visible"]
    timestamp: Timestamp
    run_id: Identifier | None
    tool_call_id: Identifier | None
    tool_name: Short | None
    content: Annotated[str, Field(max_length=1024 * 1024)]
    empty_content: bool = Field(default=False, strict=True)
    parent_event_id: Identifier | None = None
    artifact_ids: Annotated[list[Identifier], Field(max_length=100)]


class Artifact(StrictModel):
    id: Identifier
    path: Annotated[str, Field(min_length=1, max_length=255, pattern=r"^[a-zA-Z0-9_.\-/]+$")]
    kind: Literal["final_proof", "code", "configuration", "log", "source_export"]
    sha256: Digest
    availability: Literal["embedded", "authoritative_submission", "omitted"]
    content: Annotated[str, Field(max_length=1024 * 1024)] | None
    omission_reason: Short | None

    @model_validator(mode="after")
    def artifact_integrity(self):
        if self.path.startswith("/") or any(p in ("", ".", "..") for p in self.path.split("/")):
            raise ValueError("artifact paths must be safe relative labels")
        if self.availability == "embedded":
            if self.content is None or self.omission_reason is not None:
                raise ValueError("embedded artifacts need content and no omission reason")
            if digest(self.content.encode("utf-8")) != self.sha256:
                raise ValueError("artifact SHA-256 does not match UTF-8 content")
        elif self.content is not None:
            raise ValueError("non-embedded artifact must not carry content")
        elif self.availability == "authoritative_submission":
            if self.kind != "final_proof" or self.omission_reason is not None:
                raise ValueError("only the linked final proof can use authoritative submission storage")
        elif not self.omission_reason:
            raise ValueError("omitted artifacts need a reason")
        return self


class Disclosure(StrictModel):
    field_or_event: Short
    reason: Annotated[str, Field(min_length=1, max_length=2000)]
    impact_on_reproduction: Annotated[str, Field(min_length=1, max_length=2000)]


class Reproduction(StrictModel):
    lean_toolchain: KnownValue
    mathlib_commit: KnownValue
    task_repository_commit: KnownValue
    code_commit: KnownValue
    environment: Annotated[str, Field(min_length=1, max_length=4000)]
    commands: Annotated[list[Short], Field(min_length=1, max_length=100)]
    expected_result: Annotated[str, Field(min_length=1, max_length=4000)]
    limitations: Annotated[str, Field(min_length=1, max_length=4000)]


class Permissions(StrictModel):
    terms_version: Literal["trajectory-review-draft-v1"]
    authentic_user_visible_export: bool = Field(strict=True)
    authorized_to_submit: bool = Field(strict=True)
    secrets_and_personal_data_checked: bool = Field(strict=True)
    third_party_consent_or_removal: bool = Field(strict=True)
    private_review_and_storage_only: bool = Field(strict=True)
    upload_alone_grants_no_commercial_rights: bool = Field(strict=True)


Purpose = Literal["dataset_redistribution", "sublicensing", "evaluation", "training"]


class SourceRights(StrictModel):
    source_id: Short  # run:<id> or artifact:<id>; every source is required for clearance.
    rights_status: Literal["confirmed", "unknown", "prohibited"]
    agreement_or_license: KnownValue
    evidence_reference: KnownValue
    allowed_purposes: Annotated[list[Purpose], Field(max_length=4)]
    restrictions: Annotated[str, Field(min_length=1, max_length=4000)]
    third_party_rights: Annotated[str, Field(min_length=1, max_length=2000)]


class CommercialElection(StrictModel):
    terms_version: Literal["trajectory-commercial-draft-v1"]
    terms_sha256: Digest
    election: Literal["decline", "conditional_grant"]
    purposes: Annotated[list[Purpose], Field(max_length=4)]
    sources: Annotated[list[SourceRights], Field(max_length=1100)]
    explicit_assent: bool = Field(strict=True)

    @model_validator(mode="after")
    def explicit_choice(self):
        if len(set(self.purposes)) != len(self.purposes):
            raise ValueError("commercial purposes must be distinct")
        if self.election == "decline" and (self.purposes or self.explicit_assent):
            raise ValueError("review-only election grants no commercial purposes")
        if self.election == "conditional_grant" and (not self.explicit_assent or not self.purposes):
            raise ValueError("conditional grant needs explicit assent and selected purposes")
        if "sublicensing" in self.purposes and "dataset_redistribution" not in self.purposes:
            raise ValueError("sublicensing requires dataset redistribution")
        if "sublicensing" in self.purposes and not {"training", "evaluation"}.intersection(self.purposes):
            raise ValueError("sublicensing requires evaluation or training")
        return self


class TrajectoryPackage(StrictModel):
    schema_version: Literal["conjectures-trajectory/v1"]
    synthetic: bool = Field(strict=True)
    submission_id: uuid.UUID
    task_id: Annotated[str, Field(min_length=1, max_length=255)]
    task_bundle_sha256: Digest
    final_proof_sha256: Digest
    exported_at: AwareDatetime
    runs: Annotated[list[ModelRun], Field(min_length=1, max_length=100)]
    events: Annotated[list[Event], Field(min_length=2, max_length=10000)]
    artifacts: Annotated[list[Artifact], Field(min_length=1, max_length=1000)]
    reproduction: Reproduction
    completeness: Literal["complete_export", "gaps_disclosed"]
    missing: Annotated[list[Disclosure], Field(max_length=1000)]
    redactions: Annotated[list[Disclosure], Field(max_length=1000)]
    permissions: Permissions
    commercial_election: CommercialElection

    @model_validator(mode="after")
    def coherent_evidence(self):
        run_ids = {r.id for r in self.runs}
        artifact_ids = {a.id for a in self.artifacts}
        if len(run_ids) != len(self.runs) or len(artifact_ids) != len(self.artifacts):
            raise ValueError("run and artifact identifiers must be unique")
        if len({a.path for a in self.artifacts}) != len(self.artifacts):
            raise ValueError("artifact paths must be unique")
        if len({e.id for e in self.events}) != len(self.events):
            raise ValueError("event identifiers must be unique")
        previous_time = None
        seen_events = set()
        calls = {}
        results = set()
        for sequence, event in enumerate(self.events, 1):
            if (event.content == "") != event.empty_content:
                raise ValueError("empty event content must be explicitly marked; never invent output")
            if event.parent_event_id is not None and event.parent_event_id not in seen_events:
                raise ValueError("branch parent must be an earlier event")
            seen_events.add(event.id)
            if event.sequence != sequence:
                raise ValueError("events must have contiguous chronological sequence numbers")
            if event.run_id is not None and event.run_id not in run_ids:
                raise ValueError("unknown model run")
            if event.kind == "assistant_message" and event.run_id is None:
                raise ValueError("assistant messages must identify the model run")
            if not set(event.artifact_ids) <= artifact_ids:
                raise ValueError("event references an unknown artifact")
            when = event.timestamp.value
            if when is not None:
                if when > self.exported_at or (previous_time is not None and when < previous_time):
                    raise ValueError("known timestamps must follow chronology and precede export")
                previous_time = when
            if event.kind in ("tool_call", "tool_result"):
                if not event.tool_call_id or not event.tool_name:
                    raise ValueError("tool calls/results need call id and tool name")
                if event.kind == "tool_call":
                    if event.tool_call_id in calls:
                        raise ValueError("duplicate tool call id")
                    calls[event.tool_call_id] = (event.run_id, event.tool_name)
                else:
                    if event.tool_call_id not in calls or event.tool_call_id in results:
                        raise ValueError("tool result must follow one distinct call")
                    if calls[event.tool_call_id] != (event.run_id, event.tool_name):
                        raise ValueError("tool result must match the call's model run and tool name")
                    results.add(event.tool_call_id)
            elif event.tool_call_id is not None or event.tool_name is not None:
                raise ValueError("tool fields belong only to tool events")
        if calls.keys() != results and not self.missing:
            raise ValueError("missing tool results must be disclosed")
        if self.missing and self.completeness != "gaps_disclosed":
            raise ValueError("missing evidence cannot be declared complete")
        if not any(e.kind == "user_message" for e in self.events) or not any(
            e.kind == "assistant_message" for e in self.events
        ):
            raise ValueError("include user and assistant messages")
        proofs = [a for a in self.artifacts if a.kind == "final_proof"]
        if len(proofs) != 1 or proofs[0].sha256 != self.final_proof_sha256:
            raise ValueError("exactly one final proof must match the linked proof digest")
        if any(a.availability == "omitted" for a in self.artifacts) and not self.missing:
            raise ValueError("omitted artifacts must be disclosed")
        source_ids = [s.source_id for s in self.commercial_election.sources]
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("duplicate rights source")
        if self.commercial_election.election == "conditional_grant":
            expected = {"run:" + r for r in run_ids} | {"artifact:" + a for a in artifact_ids}
            if set(source_ids) != expected:
                raise ValueError("conditional grant requires rights provenance for every run and artifact")
        return self


def digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def canonical_bytes(package: TrajectoryPackage) -> bytes:
    return json.dumps(package.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def evidence_digest(package: TrajectoryPackage) -> bytes:
    # Linkage and export time are excluded: repackaging the same transcript for another proof
    # must collide. Semantic fabrication still needs a human provenance review.
    evidence = package.model_dump(mode="json", include={"runs", "events", "artifacts"})
    return hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False).encode("utf-8")).digest()


def parse_package(raw: bytes) -> TrajectoryPackage:
    if len(raw) > MAX_PACKAGE_BYTES:
        raise ValueError("package exceeds 32 MiB")

    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result

    # Reject excessively nested JSON before a recursive parser can hit its stack limit.
    depth, quoted, escaped = 0, False, False
    for ch in raw:
        if quoted:
            if escaped:
                escaped = False
            elif ch == 92:
                escaped = True
            elif ch == 34:
                quoted = False
        elif ch == 34:
            quoted = True
        elif ch in (91, 123):
            depth += 1
            if depth > 24:
                raise ValueError("JSON nesting exceeds 24 levels")
        elif ch in (93, 125):
            depth -= 1
    data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_keys,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    return TrajectoryPackage.model_validate(data)
