"""Chat-log packages: one zip holding the observable record of an AI research session.

The zip is read in memory and never extracted. Only what links the record to a proof is checked:
the manifest, the events, the artifact index, the proof link and the hashes of included files.
README.md, episodes.jsonl and sample.html travel as they are; the site never renders them.
Validation establishes structure and linkage, not truth or rights.
"""
from __future__ import annotations

import hashlib
import io
import json
import posixpath
import stat
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

FORMAT = "conjectures-chat-log/v2"
MAX_PACKAGE_BYTES = 64 * 1024 * 1024
MAX_UNPACKED_BYTES = 512 * 1024 * 1024
MAX_FILES = 20_000
MAX_JSON_FILE_BYTES = 32 * 1024 * 1024
MAX_EVENT_LINE_BYTES = 64 * 1024 * 1024
RETENTION_DAYS = 90
TERMS_VERSION = "chat-log-terms-v5"
DEPOSIT_HOURS = 24
REQUIRED_FILES = ("README.md", "events.jsonl", "episodes.jsonl", "sample.html", "artifact-index.json",
                  "manifest.json", "validation.json")
Digest = Annotated[str, Field(pattern=r"^sha256:[a-f0-9]{64}$")]
Name = Annotated[str, Field(min_length=1, max_length=200)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class OpenModel(BaseModel):
    """Required fields are checked; anything else the agent records is kept as given."""
    model_config = ConfigDict(extra="allow", str_strip_whitespace=False)


class Manifest(OpenModel):
    format: Literal["conjectures-chat-log/v2"]
    synthetic: bool = Field(default=False, strict=True)
    # Null when logs for a new bounty are uploaded before the proof submission exists.
    submission_id: uuid.UUID | None
    task_id: Annotated[str, Field(min_length=1, max_length=255)]
    task_bundle_sha256: Digest
    final_proof_sha256: Digest
    exported_at: AwareDatetime


class EventRecord(OpenModel):
    id: Name
    role: Literal["user", "assistant", "tool", "system"]
    type: Annotated[str, Field(min_length=1, max_length=100)]
    timestamp: AwareDatetime | None
    artifact_ids: Annotated[list[Name], Field(max_length=1000)] = []


class ArtifactEntry(OpenModel):
    id: Name
    path: Annotated[str, Field(min_length=1, max_length=1024)]
    kind: Annotated[str, Field(min_length=1, max_length=100)]
    sha256: Digest
    included: bool = Field(strict=True)
    event_ids: Annotated[list[Name], Field(max_length=10_000)] = []


class PackageError(ValueError):
    """A rejected package. Messages come only from SAFE_SEMANTIC_ERRORS or a ValidationError."""
    def __init__(self, message: str = "", *, file: str | None = None, line: int | None = None,
                 validation: ValidationError | None = None):
        super().__init__(message or "invalid package")
        self.message, self.file, self.line, self.validation = message, file, line, validation


@dataclass(frozen=True)
class PackageSummary:
    manifest: Manifest
    fingerprint: bytes
    proof_first_seen: datetime | None
    events: int
    artifacts: int


def digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _nonfinite(_):
    raise ValueError("nonfinite JSON")


def _decode(raw: bytes):
    check_json_depth(raw)
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_keys, parse_constant=_nonfinite)


def _safe(error: Exception) -> str:
    return str(error) if str(error) in SAFE_SEMANTIC_ERRORS else ""


def _safe_path(name: str) -> bool:
    parts = name.split("/")
    return bool(name) and not name.startswith("/") and "\\" not in name and ".." not in parts and ":" not in parts[0]


def _entries(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > MAX_FILES:
        raise PackageError("too many files in the package")
    names, unpacked = {}, 0
    for info in infos:
        if info.flag_bits & 0x1:
            raise PackageError("encrypted zip entries are not accepted")
        if stat.S_ISLNK(info.external_attr >> 16):
            raise PackageError("links are not allowed in the package")
        if not _safe_path(info.filename):
            raise PackageError("unsafe path in the package")
        if info.filename in names:
            raise PackageError("duplicate path in the package")
        unpacked += info.file_size
        if unpacked > MAX_UNPACKED_BYTES:
            raise PackageError("the package unpacks to more than 512 MiB")
        if not info.is_dir():
            names[info.filename] = info
    return names


def _root(names) -> str:
    """The package folder may be zipped as is, or inside one top-level folder."""
    if "manifest.json" in names:
        return ""
    tops = {name.split("/", 1)[0] for name in names}
    if len(tops) == 1 and f"{next(iter(tops))}/manifest.json" in names:
        return next(iter(tops)) + "/"
    return ""


def _json_file(archive, info, file):
    if info.file_size > MAX_JSON_FILE_BYTES:
        raise PackageError("a JSON file in the package is too large", file=file)
    try:
        return _decode(archive.read(info))
    except (ValueError, UnicodeError, RecursionError) as error:
        raise PackageError(_safe(error) or "invalid JSON", file=file) from None


def _lines(archive, info, file):
    with archive.open(info) as source:
        for number, raw in enumerate(source, 1):
            raw = raw.rstrip(b"\r\n")
            if not raw:
                raise PackageError("blank lines are not allowed", file=file, line=number)
            if len(raw) > MAX_EVENT_LINE_BYTES:
                raise PackageError("a line is too large", file=file, line=number)
            yield number, raw


def parse_package(raw: bytes) -> PackageSummary:
    """Check a zip package held in memory; memory beyond the zip is bounded by one line or file."""
    if len(raw) > MAX_PACKAGE_BYTES:
        raise PackageError("package exceeds 64 MiB")
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
        names = _entries(archive)
        root = _root(names)
        for required in REQUIRED_FILES:
            if root + required not in names:
                raise PackageError("the package is missing a required file", file=required)
        try:
            manifest = Manifest.model_validate(_json_file(archive, names[root + "manifest.json"], "manifest.json"))
        except ValidationError as error:
            raise PackageError(file="manifest.json", validation=error) from None

        fingerprint = hashlib.sha256()
        event_times, roles, events = {}, set(), 0
        for number, line in _lines(archive, names[root + "events.jsonl"], "events.jsonl"):
            try:
                event = EventRecord.model_validate(_decode(line))
            except ValidationError as error:
                raise PackageError(file="events.jsonl", line=number, validation=error) from None
            except (ValueError, UnicodeError, RecursionError) as error:
                raise PackageError(_safe(error) or "invalid JSON", file="events.jsonl", line=number) from None
            if event.id in event_times:
                raise PackageError("event ids must be unique", file="events.jsonl", line=number)
            if event.timestamp is not None and event.timestamp > manifest.exported_at + timedelta(minutes=5):
                raise PackageError("event timestamps must not be later than exported_at", file="events.jsonl", line=number)
            event_times[event.id] = event.timestamp
            roles.add(event.role)
            events += 1
            fingerprint.update(line + b"\n")
        if events < 2 or not {"user", "assistant"} <= roles:
            raise PackageError("include user and assistant messages", file="events.jsonl")

        for number, line in _lines(archive, names[root + "episodes.jsonl"], "episodes.jsonl"):
            try:
                _decode(line)
            except (ValueError, UnicodeError, RecursionError) as error:
                raise PackageError(_safe(error) or "invalid JSON", file="episodes.jsonl", line=number) from None
        _json_file(archive, names[root + "validation.json"], "validation.json")

        index = _json_file(archive, names[root + "artifact-index.json"], "artifact-index.json")
        if not isinstance(index, list):
            raise PackageError("artifact-index.json must be a JSON array", file="artifact-index.json")
        artifacts, proofs = set(), []
        for position, item in enumerate(index, 1):
            try:
                entry = ArtifactEntry.model_validate(item)
            except ValidationError as error:
                raise PackageError(file="artifact-index.json", line=position, validation=error) from None
            if entry.id in artifacts:
                raise PackageError("artifact ids must be unique", file="artifact-index.json", line=position)
            artifacts.add(entry.id)
            if any(event_id not in event_times for event_id in entry.event_ids):
                raise PackageError("an artifact links an unknown event", file="artifact-index.json", line=position)
            if entry.kind == "final_proof":
                proofs.append(entry)
            if entry.included:
                path = posixpath.normpath(entry.path)
                if not _safe_path(path) or root + path not in names:
                    raise PackageError("an included artifact is missing from the package", file="artifact-index.json", line=position)
                hashed = hashlib.sha256()
                with archive.open(names[root + path]) as source:
                    while chunk := source.read(1024 * 1024):
                        hashed.update(chunk)
                if "sha256:" + hashed.hexdigest() != entry.sha256:
                    raise PackageError("artifact SHA-256 does not match the file", file="artifact-index.json", line=position)
        if len(proofs) != 1 or proofs[0].sha256 != manifest.final_proof_sha256:
            raise PackageError("exactly one final proof must match the linked proof digest", file="artifact-index.json")
        times = [event_times[event_id] for event_id in proofs[0].event_ids if event_times[event_id] is not None]
        if not times:
            raise PackageError("a timestamped event must show the final proof", file="artifact-index.json")
    except zipfile.BadZipFile:
        raise PackageError("upload a .zip package") from None
    except (zipfile.LargeZipFile, NotImplementedError, EOFError, OSError):
        raise PackageError("the zip could not be read") from None
    return PackageSummary(manifest=manifest, fingerprint=fingerprint.digest(), proof_first_seen=min(times),
                          events=events, artifacts=len(artifacts))


def package_schema() -> dict:
    """JSON Schema for the three checked parts: manifest.json, one events.jsonl line, one index entry."""
    return {
        "title": "Conjectures chat-log package (conjectures-chat-log/v2)",
        "description": ("A zip holding README.md, events.jsonl, episodes.jsonl, sample.html, "
                        "artifact-index.json (an array of artifact entries), artifacts/, manifest.json and "
                        "validation.json. Only these three parts are checked; extra fields are kept."),
        "$defs": {"manifest": Manifest.model_json_schema(), "event": EventRecord.model_json_schema(),
                  "artifact": ArtifactEntry.model_json_schema()},
    }


def check_json_depth(raw: bytes):
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


SAFE_SEMANTIC_ERRORS = frozenset((
    'JSON nesting exceeds 24 levels',
    'a JSON file in the package is too large',
    'a line is too large',
    'a timestamped event must show the final proof',
    'an artifact links an unknown event',
    'an included artifact is missing from the package',
    'artifact SHA-256 does not match the file',
    'artifact ids must be unique',
    'artifact-index.json must be a JSON array',
    'blank lines are not allowed',
    'duplicate JSON object key',
    'duplicate path in the package',
    'encrypted zip entries are not accepted',
    'event ids must be unique',
    'event timestamps must not be later than exported_at',
    'exactly one final proof must match the linked proof digest',
    'include user and assistant messages',
    'invalid JSON',
    'links are not allowed in the package',
    'nonfinite JSON',
    'package exceeds 64 MiB',
    'the package is missing a required file',
    'the package unpacks to more than 512 MiB',
    'the zip could not be read',
    'too many files in the package',
    'unsafe path in the package',
    'upload a .zip package',
))
