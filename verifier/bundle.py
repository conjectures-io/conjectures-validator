"""Exact-shape admission of a miner proof bundle.

A submission bundle is a ZIP archive holding exactly two entries:

    submission.json   strict JSON manifest, bounded
    Main.lean         the untrusted proof

Nothing in this module ever extracts the archive. Entry names are only ever compared
against a two-element allowlist and are never used as filesystem paths; entry bytes are
decompressed into bounded memory and handed back to the caller, which writes the proof
itself under a name it chooses. That removes path traversal, symlink, and special-file
attacks structurally rather than sanitizing against them.

The archive is parsed here with `struct` and `zlib` rather than `zipfile` so admission
depends on one explicit reading of the format instead of on the standard library's
tolerance for ambiguous archives, and so no part of a hostile archive reaches a parser
this module does not control.
"""

from __future__ import annotations

import json
import os
import re
import stat
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from verifier.errors import ReasonCode, VerifierError
from verifier.hashing import is_sha256, sha256_bytes
from verifier.models import TaskManifest
from verifier.static_checks import check_submission
from verifier.submission import Submission, load_submission_bytes
from verifier.task_generator import MAX_SUBMISSION_BYTES


BUNDLE_FORMAT = "conjectures-submission/v1"
BUNDLE_SCHEMA_VERSION = 1
BUNDLE_MEDIA_TYPE = "application/zip"

MANIFEST_NAME = "submission.json"
PROOF_NAME = "Main.lean"
BUNDLE_ENTRY_NAMES = (MANIFEST_NAME, PROOF_NAME)

# Leave room for a stored (uncompressed) maximum-sized proof and ZIP metadata.
MAX_BUNDLE_BYTES = 12 * 1024 * 1024
MAX_MANIFEST_BYTES = 16 * 1024
MAX_COMPRESSION_RATIO = 200
# Ordinary writers put extended timestamp and uid/gid records here (Info-ZIP writes 24
# bytes). The field is ignored entirely; the cap exists only to bound header parsing.
MAX_EXTRA_BYTES = 256

STORED = 0
DEFLATED = 8
PERMITTED_METHODS = frozenset({STORED, DEFLATED})

# Bit 1/2 are deflate level hints; bit 11 marks UTF-8 names. Every other flag bit is
# rejected, which covers encryption (0), data descriptors (3), patched data (5), strong
# encryption (6), and masked header values (13).
PERMITTED_FLAG_BITS = 0x0806

LOCAL_HEADER_SIGNATURE = b"PK\x03\x04"
CENTRAL_HEADER_SIGNATURE = b"PK\x01\x02"
EOCD_SIGNATURE = b"PK\x05\x06"
LOCAL_HEADER_SIZE = 30
CENTRAL_HEADER_SIZE = 46
EOCD_SIZE = 22

CREATE_SYSTEM_UNIX = 3
MSDOS_DIRECTORY_ATTRIBUTE = 0x10

MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "format",
        "task_id",
        "task_bundle_sha256",
        "proof_path",
        "proof_sha256",
        "proof_bytes",
        "miner_hotkey",
        "solver",
    }
)
REQUIRED_MANIFEST_FIELDS = MANIFEST_FIELDS - {"solver"}
SOLVER_FIELDS = frozenset({"name", "version"})

TASK_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,254}$")
# Exactly 48 characters, matching the `ss58` domain in deploy/migrate/sql: a Bittensor
# address on prefix 42 is always that length. Accepting 47 here would admit a bundle that
# the database then refuses at INSERT, turning a miner's bad input into a server error.
SS58_ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{48}$")
SOLVER_TOKEN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@dataclass(frozen=True)
class BundleManifest:
    schema_version: int
    format: str
    task_id: str
    task_bundle_sha256: str
    proof_path: str
    proof_sha256: str
    proof_bytes: int
    miner_hotkey: str
    solver_name: str | None = None
    solver_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "format": self.format,
            "task_id": self.task_id,
            "task_bundle_sha256": self.task_bundle_sha256,
            "proof_path": self.proof_path,
            "proof_sha256": self.proof_sha256,
            "proof_bytes": self.proof_bytes,
            "miner_hotkey": self.miner_hotkey,
        }
        if self.solver_name is not None:
            result["solver"] = {"name": self.solver_name, "version": self.solver_version}
        return result


@dataclass(frozen=True)
class ProofBundle:
    sha256: str
    bytes_length: int
    manifest: BundleManifest
    manifest_raw: bytes
    proof: Submission


@dataclass(frozen=True)
class _CentralEntry:
    name: bytes
    create_system: int
    flag_bits: int
    method: int
    crc: int
    compress_size: int
    file_size: int
    external_attr: int
    header_offset: int


def _malformed(message: str) -> VerifierError:
    return VerifierError(ReasonCode.BUNDLE_MALFORMED, f"submission bundle is malformed: {message}")


def _violation(message: str) -> VerifierError:
    return VerifierError(ReasonCode.BUNDLE_POLICY_VIOLATION, f"submission bundle policy: {message}")


def _manifest_invalid(message: str) -> VerifierError:
    return VerifierError(
        ReasonCode.BUNDLE_MANIFEST_INVALID,
        f"submission bundle manifest is invalid: {message}",
    )


def _parse_end_of_central_directory(raw: bytes) -> tuple[int, int]:
    """Return the central directory offset and size, requiring a bare trailing EOCD."""
    if len(raw) < EOCD_SIZE + LOCAL_HEADER_SIZE:
        raise _malformed("archive is too small to contain an entry")
    if not raw.startswith(LOCAL_HEADER_SIGNATURE):
        # A leading stub means the bytes before the first local header are unaccounted
        # for, which is how self-extracting and polyglot archives are built.
        raise _violation("archive must begin with a local file header")
    start = len(raw) - EOCD_SIZE
    if raw[start : start + 4] != EOCD_SIGNATURE:
        raise _violation("archive must end with a comment-free end-of-central-directory record")
    (
        disk_number,
        central_directory_disk,
        entries_on_disk,
        total_entries,
        central_directory_size,
        central_directory_offset,
        comment_length,
    ) = struct.unpack("<4H2LH", raw[start + 4 : start + EOCD_SIZE])
    if comment_length:
        raise _violation("archive comments are prohibited")
    if disk_number or central_directory_disk:
        raise _violation("split or multi-disk archives are prohibited")
    if entries_on_disk != total_entries:
        raise _malformed("end-of-central-directory entry counts disagree")
    if total_entries != len(BUNDLE_ENTRY_NAMES):
        raise _violation(
            f"archive must contain exactly {len(BUNDLE_ENTRY_NAMES)} entries, found {total_entries}"
        )
    if central_directory_offset + central_directory_size != start:
        raise _violation("central directory must end exactly at the end-of-central-directory record")
    return central_directory_offset, central_directory_size


def _parse_central_directory(raw: bytes, offset: int, size: int) -> tuple[_CentralEntry, ...]:
    entries: list[_CentralEntry] = []
    cursor = offset
    limit = offset + size
    for _ in range(len(BUNDLE_ENTRY_NAMES)):
        if cursor + CENTRAL_HEADER_SIZE > limit:
            raise _malformed("central directory is truncated")
        if raw[cursor : cursor + 4] != CENTRAL_HEADER_SIGNATURE:
            raise _malformed("central directory entry signature is invalid")
        (
            version_made_by,
            _version_needed,
            flag_bits,
            method,
            _time,
            _date,
            crc,
            compress_size,
            file_size,
            name_length,
            extra_length,
            comment_length,
            disk_start,
            _internal_attr,
            external_attr,
            header_offset,
        ) = struct.unpack("<4H2H3L5H2L", raw[cursor + 4 : cursor + CENTRAL_HEADER_SIZE])
        cursor += CENTRAL_HEADER_SIZE
        if extra_length > MAX_EXTRA_BYTES:
            raise _violation("central directory extra field exceeds its limit")
        if comment_length:
            raise _violation("entry comments are prohibited")
        if disk_start:
            raise _violation("split or multi-disk archives are prohibited")
        end = cursor + name_length + extra_length
        if end > limit:
            raise _malformed("central directory entry is truncated")
        name = raw[cursor : cursor + name_length]
        cursor = end
        entries.append(
            _CentralEntry(
                name=name,
                create_system=version_made_by >> 8,
                flag_bits=flag_bits,
                method=method,
                crc=crc,
                compress_size=compress_size,
                file_size=file_size,
                external_attr=external_attr,
                header_offset=header_offset,
            )
        )
    if cursor != limit:
        raise _malformed("central directory has trailing bytes")
    return tuple(entries)


def _check_entry_policy(entry: _CentralEntry, expected_name: str, max_bytes: int) -> None:
    if entry.name != expected_name.encode("ascii"):
        raise _violation(
            "archive entries must be exactly "
            + ", ".join(BUNDLE_ENTRY_NAMES)
            + f" in that order, found {entry.name!r}"
        )
    if entry.flag_bits & ~PERMITTED_FLAG_BITS:
        raise _violation(
            f"entry {expected_name} sets prohibited general-purpose flags "
            f"0x{entry.flag_bits & ~PERMITTED_FLAG_BITS:04X}"
        )
    if entry.method not in PERMITTED_METHODS:
        raise _violation(f"entry {expected_name} uses unsupported compression method {entry.method}")
    if entry.external_attr & MSDOS_DIRECTORY_ATTRIBUTE:
        raise _violation(f"entry {expected_name} is marked as a directory")
    if entry.create_system == CREATE_SYSTEM_UNIX:
        mode = entry.external_attr >> 16
        file_type = stat.S_IFMT(mode)
        if file_type not in {0, stat.S_IFREG}:
            raise _violation(f"entry {expected_name} is not a regular file")
        if mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
            raise _violation(f"entry {expected_name} sets setuid, setgid, or sticky bits")
        if mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
            raise _violation(f"entry {expected_name} is marked executable")
    if entry.file_size > max_bytes:
        raise VerifierError(
            ReasonCode.BUNDLE_TOO_LARGE,
            f"entry {expected_name} declares {entry.file_size} bytes, above the {max_bytes}-byte limit",
        )
    if entry.method == STORED and entry.compress_size != entry.file_size:
        raise _malformed(f"stored entry {expected_name} has mismatched sizes")
    if entry.file_size > entry.compress_size * MAX_COMPRESSION_RATIO:
        raise _violation(
            f"entry {expected_name} exceeds the {MAX_COMPRESSION_RATIO}:1 compression ratio limit"
        )


def _entry_data(
    raw: bytes, entry: _CentralEntry, expected_name: str, max_bytes: int, limit: int
) -> tuple[bytes, int]:
    """Cross-check the local header against the central directory, then decompress.

    Returns the entry bytes and the offset just past its compressed data.
    """
    offset = entry.header_offset
    if offset + LOCAL_HEADER_SIZE > limit:
        raise _malformed(f"local header for {expected_name} is out of range")
    if raw[offset : offset + 4] != LOCAL_HEADER_SIGNATURE:
        raise _malformed(f"local header signature for {expected_name} is invalid")
    (
        _version_needed,
        flag_bits,
        method,
        _time,
        _date,
        crc,
        compress_size,
        file_size,
        name_length,
        extra_length,
    ) = struct.unpack("<3H2H3L2H", raw[offset + 4 : offset + LOCAL_HEADER_SIZE])
    if extra_length > MAX_EXTRA_BYTES:
        raise _violation(f"local extra field for {expected_name} exceeds its limit")
    name_start = offset + LOCAL_HEADER_SIZE
    data_start = name_start + name_length + extra_length
    if data_start > limit:
        raise _malformed(f"local header for {expected_name} is truncated")
    # Any disagreement between the two copies of an entry's metadata means different ZIP
    # readers can be made to see different archives. Refuse rather than pick a winner.
    if (
        raw[name_start : name_start + name_length] != entry.name
        or flag_bits != entry.flag_bits
        or method != entry.method
        or crc != entry.crc
        or compress_size != entry.compress_size
        or file_size != entry.file_size
    ):
        raise _violation(
            f"local header for {expected_name} disagrees with the central directory"
        )
    data_end = data_start + compress_size
    if data_end > limit:
        raise _malformed(f"entry data for {expected_name} runs past the central directory")
    compressed = raw[data_start:data_end]
    if method == STORED:
        data = compressed
    else:
        decompressor = zlib.decompressobj(-zlib.MAX_WBITS)
        try:
            # Bound the output rather than trusting the declared size: this, not the
            # ratio check, is what stops a decompression bomb.
            data = decompressor.decompress(compressed, max_bytes + 1)
        except zlib.error as exc:
            raise _malformed(f"entry {expected_name} is not valid deflate data: {exc}") from exc
        if decompressor.unconsumed_tail:
            raise VerifierError(
                ReasonCode.BUNDLE_TOO_LARGE,
                f"entry {expected_name} decompresses past the {max_bytes}-byte limit",
            )
        if not decompressor.eof:
            raise _malformed(f"entry {expected_name} has a truncated deflate stream")
    if len(data) != file_size:
        raise _malformed(
            f"entry {expected_name} decompressed to {len(data)} bytes, declared {file_size}"
        )
    if zlib.crc32(data) & 0xFFFFFFFF != crc:
        raise _malformed(f"entry {expected_name} failed its CRC-32 check")
    return data, data_end


def _strict_json_object(raw: bytes) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key: {key}")
            result[key] = value
        return result

    if b"\x00" in raw:
        raise _manifest_invalid("manifest contains a NUL byte")
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=unique,
            parse_constant=lambda item: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant: {item}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise _manifest_invalid(f"manifest is not strict UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise _manifest_invalid("manifest must be a JSON object")
    return value


def _solver(value: object) -> tuple[str | None, str | None]:
    if value is None:
        return None, None
    if not isinstance(value, dict) or set(value) != SOLVER_FIELDS:
        raise _manifest_invalid("solver must be an object with exactly name and version")
    name = value["name"]
    version = value["version"]
    if (
        not isinstance(name, str)
        or not isinstance(version, str)
        or SOLVER_TOKEN.fullmatch(name) is None
        or SOLVER_TOKEN.fullmatch(version) is None
    ):
        raise _manifest_invalid("solver name and version must match [A-Za-z0-9._-]{1,64}")
    return name, version


def parse_manifest(raw: bytes) -> BundleManifest:
    if len(raw) > MAX_MANIFEST_BYTES:
        raise VerifierError(
            ReasonCode.BUNDLE_TOO_LARGE,
            f"manifest exceeds the {MAX_MANIFEST_BYTES}-byte limit",
        )
    value = _strict_json_object(raw)
    unknown = set(value) - MANIFEST_FIELDS
    if unknown:
        raise _manifest_invalid("unknown manifest fields: " + ", ".join(sorted(unknown)))
    missing = REQUIRED_MANIFEST_FIELDS - set(value)
    if missing:
        raise _manifest_invalid("missing manifest fields: " + ", ".join(sorted(missing)))
    if type(value["schema_version"]) is not int or value["schema_version"] != BUNDLE_SCHEMA_VERSION:
        raise _manifest_invalid(f"schema_version must be {BUNDLE_SCHEMA_VERSION}")
    if value["format"] != BUNDLE_FORMAT:
        raise _manifest_invalid(f"format must be {BUNDLE_FORMAT}")
    task_id = value["task_id"]
    if not isinstance(task_id, str) or TASK_ID.fullmatch(task_id) is None:
        raise _manifest_invalid("task_id is not a valid task identifier")
    if not is_sha256(value["task_bundle_sha256"]):
        raise _manifest_invalid("task_bundle_sha256 is not a lowercase sha256: digest")
    if value["proof_path"] != PROOF_NAME:
        raise _manifest_invalid(f"proof_path must be {PROOF_NAME}")
    if not is_sha256(value["proof_sha256"]):
        raise _manifest_invalid("proof_sha256 is not a lowercase sha256: digest")
    proof_bytes = value["proof_bytes"]
    if type(proof_bytes) is not int or not 0 < proof_bytes <= MAX_SUBMISSION_BYTES:
        raise _manifest_invalid("proof_bytes must be a positive integer within the submission limit")
    hotkey = value["miner_hotkey"]
    if not isinstance(hotkey, str) or SS58_ADDRESS.fullmatch(hotkey) is None:
        raise _manifest_invalid("miner_hotkey is not a valid SS58 address")
    solver_name, solver_version = _solver(value.get("solver"))
    return BundleManifest(
        schema_version=value["schema_version"],
        format=value["format"],
        task_id=task_id,
        task_bundle_sha256=value["task_bundle_sha256"],
        proof_path=value["proof_path"],
        proof_sha256=value["proof_sha256"],
        proof_bytes=proof_bytes,
        miner_hotkey=hotkey,
        solver_name=solver_name,
        solver_version=solver_version,
    )


def load_proof_bundle(raw: bytes, *, max_proof_bytes: int = MAX_SUBMISSION_BYTES) -> ProofBundle:
    """Admit a submission bundle, or raise VerifierError with a stable reason code."""
    if not isinstance(raw, bytes):
        raise TypeError("submission bundle must be bytes")
    if len(raw) > MAX_BUNDLE_BYTES:
        raise VerifierError(
            ReasonCode.BUNDLE_TOO_LARGE,
            f"submission bundle is {len(raw)} bytes, above the {MAX_BUNDLE_BYTES}-byte limit",
        )
    if max_proof_bytes > MAX_SUBMISSION_BYTES:
        raise VerifierError(
            ReasonCode.INVALID_ARGUMENT,
            "proof limit exceeds the verifier submission policy",
        )
    central_offset, central_size = _parse_end_of_central_directory(raw)
    entries = _parse_central_directory(raw, central_offset, central_size)
    caps = {MANIFEST_NAME: MAX_MANIFEST_BYTES, PROOF_NAME: max_proof_bytes}
    for entry, expected_name in zip(entries, BUNDLE_ENTRY_NAMES, strict=True):
        _check_entry_policy(entry, expected_name, caps[expected_name])
    if entries[0].header_offset != 0:
        raise _violation("the first entry must start at offset zero")
    if sum(entry.compress_size for entry in entries) > len(raw):
        raise _malformed("declared entry sizes exceed the archive length")

    data: dict[str, bytes] = {}
    previous_end = 0
    for entry, expected_name in zip(entries, BUNDLE_ENTRY_NAMES, strict=True):
        if entry.header_offset < previous_end:
            raise _violation("archive entries must not overlap")
        data[expected_name], previous_end = _entry_data(
            raw, entry, expected_name, caps[expected_name], central_offset
        )

    manifest = parse_manifest(data[MANIFEST_NAME])
    proof = load_submission_bytes(data[PROOF_NAME], max_proof_bytes)
    if manifest.proof_bytes != len(proof.raw) or manifest.proof_sha256 != proof.sha256:
        raise VerifierError(
            ReasonCode.BUNDLE_DIGEST_MISMATCH,
            "manifest proof digest or length does not match the archived proof",
        )
    return ProofBundle(
        sha256=sha256_bytes(raw),
        bytes_length=len(raw),
        manifest=manifest,
        manifest_raw=data[MANIFEST_NAME],
        proof=proof,
    )


def admit_proof_bundle(
    raw: bytes,
    *,
    task_manifest: TaskManifest,
    expected_task_sha256: str,
    expected_hotkey: str,
) -> ProofBundle:
    """Admit a bundle and bind it to one authenticated miner and one committed task.

    Runs the existing static Lean policy scanner as an admission-time fast-fail. Comparator
    and the Lean kernel remain the authoritative correctness checks.
    """
    if not is_sha256(expected_task_sha256):
        raise VerifierError(
            ReasonCode.INVALID_ARGUMENT,
            "expected task digest is not a lowercase SHA-256 commitment",
        )
    bundle = load_proof_bundle(raw, max_proof_bytes=task_manifest.max_submission_bytes)
    if bundle.manifest.task_id != task_manifest.task_id:
        raise _manifest_invalid("manifest task_id does not match the requested task")
    if bundle.manifest.task_bundle_sha256 != expected_task_sha256:
        raise VerifierError(
            ReasonCode.TASK_COMMITMENT_MISMATCH,
            "manifest task_bundle_sha256 does not match the committed task digest",
        )
    if bundle.manifest.miner_hotkey != expected_hotkey:
        raise _manifest_invalid("manifest miner_hotkey does not match the authenticated hotkey")
    static = check_submission(bundle.proof.text, task_manifest)
    if not static.valid:
        raise VerifierError(
            ReasonCode.SUBMISSION_POLICY_VIOLATION,
            "; ".join(static.violations),
        )
    return bundle


def read_bundle_file(path: Path, max_bytes: int = MAX_BUNDLE_BYTES) -> bytes:
    """Read a bundle from disk with the same no-follow bounded discipline as a proof file."""
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise _malformed(f"submission bundle not found: {path}") from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise _violation("submission bundle must be one regular file, not a symlink")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise _malformed(f"cannot open submission bundle safely: {path}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise _violation("submission bundle changed while opening")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(max_bytes + 1)
    finally:
        os.close(descriptor)
    if len(raw) > max_bytes:
        raise VerifierError(
            ReasonCode.BUNDLE_TOO_LARGE,
            f"submission bundle exceeds the {max_bytes}-byte limit",
        )
    return raw


def bundle_verdict(raw: bytes, *, max_proof_bytes: int = MAX_SUBMISSION_BYTES) -> Mapping[str, Any]:
    """Fail-closed JSON-shaped verdict, used by the out-of-process scanner."""
    try:
        bundle = load_proof_bundle(raw, max_proof_bytes=max_proof_bytes)
    except VerifierError as exc:
        return {"admitted": False, "reason_code": exc.reason.value, "message": str(exc)}
    return {
        "admitted": True,
        "reason_code": ReasonCode.VERIFIED.value,
        "message": "",
        "bundle_sha256": bundle.sha256,
        "bundle_bytes": bundle.bytes_length,
        "proof_sha256": bundle.proof.sha256,
        "proof_bytes": len(bundle.proof.raw),
        "manifest": dict(bundle.manifest.to_dict()),
    }
