from __future__ import annotations

import json
import struct
import zlib
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from conftest import manifest as task_manifest
from verifier import bundle as bundle_module
from verifier.bundle import (
    BUNDLE_FORMAT,
    BUNDLE_SCHEMA_VERSION,
    MANIFEST_NAME,
    PROOF_NAME,
    admit_proof_bundle,
    bundle_verdict,
    load_proof_bundle,
    read_bundle_file,
)
from verifier.errors import ReasonCode, VerifierError
from verifier.hashing import sha256_bytes


STORED = 0
DEFLATED = 8
HOTKEY = "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"
TASK_DIGEST = "sha256:" + "ab" * 32
VALID_PROOF = b"theorem target : type_of% VerifierFixtures.direct := by\n  trivial\n"


def _deflate(data: bytes) -> bytes:
    compressor = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    return compressor.compress(data) + compressor.flush()


@dataclass
class Entry:
    """One archive entry with independent control over every header field.

    Central-directory values default from the payload; every ``local_*`` field defaults to
    the matching central value so a test can make exactly one of them disagree.
    """

    name: bytes
    data: bytes
    method: int = DEFLATED
    flags: int = 0
    extra: bytes = b""
    comment: bytes = b""
    create_system: int = 3
    unix_mode: int = 0o644
    msdos_attr: int = 0
    disk_start: int = 0
    crc: int | None = None
    file_size: int | None = None
    compress_size: int | None = None
    compressed: bytes | None = None
    local_name: bytes | None = None
    local_flags: int | None = None
    local_method: int | None = None
    local_crc: int | None = None
    local_file_size: int | None = None
    local_compress_size: int | None = None
    local_extra: bytes | None = None

    def payload(self) -> bytes:
        if self.compressed is not None:
            return self.compressed
        return self.data if self.method == STORED else _deflate(self.data)

    def values(self) -> tuple[int, int, int]:
        payload = self.payload()
        crc = zlib.crc32(self.data) & 0xFFFFFFFF if self.crc is None else self.crc
        file_size = len(self.data) if self.file_size is None else self.file_size
        compress_size = len(payload) if self.compress_size is None else self.compress_size
        return crc, file_size, compress_size

    def local_header(self) -> bytes:
        crc, file_size, compress_size = self.values()
        name = self.name if self.local_name is None else self.local_name
        extra = self.extra if self.local_extra is None else self.local_extra
        header = struct.pack(
            "<4s5H3L2H",
            b"PK\x03\x04",
            20,
            self.flags if self.local_flags is None else self.local_flags,
            self.method if self.local_method is None else self.local_method,
            0,
            0,
            crc if self.local_crc is None else self.local_crc,
            compress_size if self.local_compress_size is None else self.local_compress_size,
            file_size if self.local_file_size is None else self.local_file_size,
            len(name),
            len(extra),
        )
        return header + name + extra + self.payload()

    def central_header(self, offset: int) -> bytes:
        crc, file_size, compress_size = self.values()
        external_attr = (self.unix_mode << 16) | self.msdos_attr
        header = struct.pack(
            "<4s6H3L5H2L",
            b"PK\x01\x02",
            (self.create_system << 8) | 20,
            20,
            self.flags,
            self.method,
            0,
            0,
            crc,
            compress_size,
            file_size,
            len(self.name),
            len(self.extra),
            len(self.comment),
            self.disk_start,
            0,
            external_attr,
            offset,
        )
        return header + self.name + self.extra + self.comment


@dataclass
class Archive:
    entries: list[Entry] = field(default_factory=list)
    prefix: bytes = b""
    comment: bytes = b""
    trailer: bytes = b""
    declared_entries: int | None = None
    central_offset_delta: int = 0
    central_size_delta: int = 0

    def build(self) -> bytes:
        body = bytearray(self.prefix)
        offsets = []
        for entry in self.entries:
            offsets.append(len(body))
            body += entry.local_header()
        central_offset = len(body)
        central = bytearray()
        for entry, offset in zip(self.entries, offsets, strict=True):
            central += entry.central_header(offset)
        body += central
        count = len(self.entries) if self.declared_entries is None else self.declared_entries
        body += struct.pack(
            "<4s4H2LH",
            b"PK\x05\x06",
            0,
            0,
            count,
            count,
            len(central) + self.central_size_delta,
            central_offset + self.central_offset_delta,
            len(self.comment),
        )
        return bytes(body) + self.comment + self.trailer


def manifest_json(**overrides: object) -> bytes:
    value: dict[str, object] = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "format": BUNDLE_FORMAT,
        "task_id": "fixture",
        "task_bundle_sha256": TASK_DIGEST,
        "proof_path": PROOF_NAME,
        "proof_sha256": sha256_bytes(VALID_PROOF),
        "proof_bytes": len(VALID_PROOF),
        "miner_hotkey": HOTKEY,
    }
    for key, item in overrides.items():
        if item is None:
            value.pop(key, None)
        else:
            value[key] = item
    return json.dumps(value, indent=2).encode("utf-8")


def archive(*, manifest: bytes | None = None, proof: bytes = VALID_PROOF, **options: object) -> Archive:
    return Archive(
        entries=[
            Entry(name=MANIFEST_NAME.encode(), data=manifest_json() if manifest is None else manifest),
            Entry(name=PROOF_NAME.encode(), data=proof),
        ],
        **options,
    )


def valid_bundle(**kwargs: object) -> bytes:
    return archive(**kwargs).build()


def rejection(raw: bytes) -> ReasonCode:
    with pytest.raises(VerifierError) as caught:
        load_proof_bundle(raw)
    return caught.value.reason


# --- the accepted shape -------------------------------------------------------------


def test_maximum_stored_proof_fits_bundle_and_honors_task_limit():
    limit = 10 * 1024 * 1024
    padding_size = limit - len(VALID_PROOF)
    line = b"--" + b"a" * 997 + b"\n"
    proof = VALID_PROOF + line * (padding_size // len(line)) + b" " * (padding_size % len(line))
    candidate = archive(
        proof=proof,
        manifest=manifest_json(proof_bytes=len(proof), proof_sha256=sha256_bytes(proof)),
    )
    candidate.entries[1].method = STORED
    raw = candidate.build()
    assert limit < len(raw) <= bundle_module.MAX_BUNDLE_BYTES
    assert load_proof_bundle(raw).proof.raw == proof
    assert admit_proof_bundle(
        raw,
        task_manifest=replace(task_manifest(), max_submission_bytes=limit),
        expected_task_sha256=TASK_DIGEST,
        expected_hotkey=HOTKEY,
    ).proof.raw == proof
    with pytest.raises(VerifierError) as caught:
        load_proof_bundle(raw, max_proof_bytes=1_000_000)
    assert caught.value.reason == ReasonCode.BUNDLE_TOO_LARGE


def test_proof_one_byte_over_global_limit_is_rejected():
    proof = b" " * (10 * 1024 * 1024 + 1)
    candidate = archive(
        proof=proof,
        manifest=manifest_json(proof_bytes=len(proof), proof_sha256=sha256_bytes(proof)),
    )
    candidate.entries[1].method = STORED
    assert rejection(candidate.build()) == ReasonCode.BUNDLE_TOO_LARGE


def test_valid_bundle_is_admitted():
    raw = valid_bundle()
    result = load_proof_bundle(raw)
    assert result.sha256 == sha256_bytes(raw)
    assert result.bytes_length == len(raw)
    assert result.proof.raw == VALID_PROOF
    assert result.proof.sha256 == sha256_bytes(VALID_PROOF)
    assert result.manifest.task_id == "fixture"
    assert result.manifest.miner_hotkey == HOTKEY
    assert result.manifest.solver_name is None


def test_stored_entries_are_admitted():
    entries = [replace(entry, method=STORED) for entry in archive().entries]
    result = load_proof_bundle(Archive(entries=entries).build())
    assert result.proof.raw == VALID_PROOF


def test_optional_solver_metadata_is_preserved():
    raw = valid_bundle(manifest=manifest_json(solver={"name": "my-solver", "version": "1.2.3"}))
    result = load_proof_bundle(raw)
    assert (result.manifest.solver_name, result.manifest.solver_version) == ("my-solver", "1.2.3")


def test_unix_mode_zero_is_admitted():
    # Archives written by some libraries leave external attributes unset.
    entries = [replace(entry, unix_mode=0, create_system=0) for entry in archive().entries]
    assert load_proof_bundle(Archive(entries=entries).build()).proof.raw == VALID_PROOF


# --- interoperability with ordinary archive writers ---------------------------------
#
# The hand-built archives above give precise control but could drift from what real
# tooling emits. These lock in that a bundle produced the way a miner would actually
# produce one is admitted.


def _zipfile_bundle(tmp_path, compression, *, from_disk=False):
    import zipfile

    target = tmp_path / "bundle.zip"
    if from_disk:
        (tmp_path / MANIFEST_NAME).write_bytes(manifest_json())
        (tmp_path / PROOF_NAME).write_bytes(VALID_PROOF)
    with zipfile.ZipFile(target, "w", compression) as handle:
        for name, data in ((MANIFEST_NAME, manifest_json()), (PROOF_NAME, VALID_PROOF)):
            if from_disk:
                handle.write(tmp_path / name, arcname=name)
            else:
                handle.writestr(name, data)
    return target.read_bytes()


@pytest.mark.parametrize("compression", [0, 8])
def test_zipfile_written_bundles_are_admitted(tmp_path, compression):
    result = load_proof_bundle(_zipfile_bundle(tmp_path, compression))
    assert result.proof.raw == VALID_PROOF


def test_zipfile_bundle_written_from_disk_is_admitted(tmp_path):
    # Exercises the path where the writer copies real file modes into external_attr.
    result = load_proof_bundle(_zipfile_bundle(tmp_path, 8, from_disk=True))
    assert result.proof.raw == VALID_PROOF


def test_the_reference_builder_produces_an_admitted_bundle(tmp_path):
    """scripts/build_submission_bundle.py is the miner-facing spec; keep it honest."""
    import importlib.util

    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "build_submission_bundle", root / "scripts" / "build_submission_bundle.py"
    )
    assert spec and spec.loader
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)

    proof = tmp_path / "Main.lean"
    proof.write_bytes(VALID_PROOF)
    output = tmp_path / "submission.zip"
    assert (
        builder.main(
            [
                "--proof",
                str(proof),
                "--task-id",
                "fixture",
                "--task-sha256",
                TASK_DIGEST,
                "--hotkey",
                HOTKEY,
                "--solver-name",
                "reference",
                "--solver-version",
                "1.0.0",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    result = load_proof_bundle(output.read_bytes())
    assert result.proof.raw == VALID_PROOF
    assert result.manifest.solver_name == "reference"
    assert result.sha256 == sha256_bytes(output.read_bytes())


def test_the_reference_builder_refuses_to_overwrite(tmp_path):
    import importlib.util

    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "build_submission_bundle", root / "scripts" / "build_submission_bundle.py"
    )
    assert spec and spec.loader
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)

    proof = tmp_path / "Main.lean"
    proof.write_bytes(VALID_PROOF)
    output = tmp_path / "submission.zip"
    output.write_bytes(b"existing")
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        builder.main(
            [
                "--proof", str(proof),
                "--task-id", "fixture",
                "--task-sha256", TASK_DIGEST,
                "--hotkey", HOTKEY,
                "--output", str(output),
            ]
        )


def test_info_zip_extended_timestamp_extras_are_tolerated():
    # Info-ZIP writes a 24-byte UT/ux extra field, and the local and central copies
    # legitimately differ in length, so extras are bounded but never cross-checked.
    entries = archive().entries
    entries = [
        replace(entry, extra=b"UT\x05\x00\x03\x00\x00\x00\x00", local_extra=b"UT\x09\x00\x03" + b"\x00" * 9)
        for entry in entries
    ]
    assert load_proof_bundle(Archive(entries=entries).build()).proof.raw == VALID_PROOF


# --- entry-set attacks --------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        b"../../etc/passwd",
        b"/etc/passwd",
        b"..\\..\\Main.lean",
        b"proof/Main.lean",
        b"main.lean",
        b"Main.lean ",
        b"Main.lean.",
        "Main‮lean".encode("utf-8"),
        "Main.leań".encode("utf-8"),
        b"Main.lean\x00",
    ],
)
def test_proof_entry_name_must_be_exact(name):
    entries = archive().entries
    entries[1] = replace(entries[1], name=name)
    assert rejection(Archive(entries=entries).build()) is ReasonCode.BUNDLE_POLICY_VIOLATION


def test_entry_order_is_fixed():
    entries = list(reversed(archive().entries))
    assert rejection(Archive(entries=entries).build()) is ReasonCode.BUNDLE_POLICY_VIOLATION


def test_extra_entry_is_rejected():
    entries = archive().entries + [Entry(name=b"notes.md", data=b"hello")]
    assert rejection(Archive(entries=entries).build()) is ReasonCode.BUNDLE_POLICY_VIOLATION


def test_missing_entry_is_rejected():
    assert rejection(Archive(entries=archive().entries[:1]).build()) is ReasonCode.BUNDLE_POLICY_VIOLATION


def test_duplicate_entry_name_is_rejected():
    first = archive().entries[0]
    assert rejection(Archive(entries=[first, first]).build()) is ReasonCode.BUNDLE_POLICY_VIOLATION


def test_nested_archive_is_rejected():
    entries = archive().entries
    entries[1] = replace(entries[1], name=b"inner.zip", data=valid_bundle())
    assert rejection(Archive(entries=entries).build()) is ReasonCode.BUNDLE_POLICY_VIOLATION


def test_directory_entry_is_rejected():
    entries = archive().entries + [Entry(name=b"dir/", data=b"", msdos_attr=0x10)]
    assert rejection(Archive(entries=entries).build()) is ReasonCode.BUNDLE_POLICY_VIOLATION


# --- entry metadata attacks ---------------------------------------------------------


def test_symlink_mode_is_rejected():
    entries = archive().entries
    entries[1] = replace(entries[1], unix_mode=0o120777)
    assert rejection(Archive(entries=entries).build()) is ReasonCode.BUNDLE_POLICY_VIOLATION


@pytest.mark.parametrize("mode", [0o104755, 0o102755, 0o101755, 0o100755])
def test_setuid_setgid_sticky_and_exec_bits_are_rejected(mode):
    entries = archive().entries
    entries[1] = replace(entries[1], unix_mode=mode)
    assert rejection(Archive(entries=entries).build()) is ReasonCode.BUNDLE_POLICY_VIOLATION


@pytest.mark.parametrize("mode", [0o020644, 0o060644, 0o010644, 0o140644])
def test_device_fifo_and_socket_modes_are_rejected(mode):
    entries = archive().entries
    entries[1] = replace(entries[1], unix_mode=mode)
    assert rejection(Archive(entries=entries).build()) is ReasonCode.BUNDLE_POLICY_VIOLATION


def test_msdos_directory_attribute_is_rejected():
    entries = archive().entries
    entries[1] = replace(entries[1], msdos_attr=0x10)
    assert rejection(Archive(entries=entries).build()) is ReasonCode.BUNDLE_POLICY_VIOLATION


@pytest.mark.parametrize("flag", [0x0001, 0x0008, 0x0020, 0x0040, 0x2000])
def test_prohibited_general_purpose_flags_are_rejected(flag):
    entries = archive().entries
    entries[1] = replace(entries[1], flags=flag, local_flags=flag)
    assert rejection(Archive(entries=entries).build()) is ReasonCode.BUNDLE_POLICY_VIOLATION


def test_utf8_and_deflate_level_flags_are_allowed():
    entries = [replace(entry, flags=0x0806, local_flags=0x0806) for entry in archive().entries]
    assert load_proof_bundle(Archive(entries=entries).build()).proof.raw == VALID_PROOF


@pytest.mark.parametrize("method", [1, 6, 9, 12, 14, 93, 99])
def test_unsupported_compression_methods_are_rejected(method):
    entries = archive().entries
    entries[1] = replace(entries[1], method=method, local_method=method)
    assert rejection(Archive(entries=entries).build()) is ReasonCode.BUNDLE_POLICY_VIOLATION


def test_oversized_extra_field_is_rejected():
    entries = archive().entries
    entries[1] = replace(entries[1], extra=b"\x00" * (bundle_module.MAX_EXTRA_BYTES + 1))
    assert rejection(Archive(entries=entries).build()) is ReasonCode.BUNDLE_POLICY_VIOLATION


def test_entry_comment_is_rejected():
    entries = archive().entries
    entries[1] = replace(entries[1], comment=b"note")
    assert rejection(Archive(entries=entries).build()) is ReasonCode.BUNDLE_POLICY_VIOLATION


def test_nonzero_disk_start_is_rejected():
    entries = archive().entries
    entries[1] = replace(entries[1], disk_start=1)
    assert rejection(Archive(entries=entries).build()) is ReasonCode.BUNDLE_POLICY_VIOLATION


# --- local/central disagreement -----------------------------------------------------


@pytest.mark.parametrize(
    "override",
    [
        {"local_name": b"Other.lean"},
        {"local_flags": 0x0002},
        {"local_method": STORED},
        {"local_crc": 12345},
        {"local_file_size": 4},
        {"local_compress_size": 4},
    ],
)
def test_local_header_must_agree_with_central_directory(override):
    entries = archive().entries
    entries[1] = replace(entries[1], **override)
    assert rejection(Archive(entries=entries).build()) is ReasonCode.BUNDLE_POLICY_VIOLATION


def test_corrupt_local_signature_is_rejected():
    raw = bytearray(valid_bundle())
    raw[0:4] = b"PK\x03\x05"
    assert rejection(bytes(raw)) is ReasonCode.BUNDLE_POLICY_VIOLATION


# --- container attacks --------------------------------------------------------------


def test_prepended_stub_is_rejected():
    assert rejection(valid_bundle(prefix=b"MZ" + b"\x00" * 128)) is ReasonCode.BUNDLE_POLICY_VIOLATION


def test_trailing_bytes_are_rejected():
    assert rejection(valid_bundle(trailer=b"\x00" * 64)) is ReasonCode.BUNDLE_POLICY_VIOLATION


def test_archive_comment_is_rejected():
    assert rejection(valid_bundle(comment=b"harmless looking note")) is ReasonCode.BUNDLE_POLICY_VIOLATION


def test_entry_count_lie_is_rejected():
    assert rejection(valid_bundle(declared_entries=3)) is ReasonCode.BUNDLE_POLICY_VIOLATION


def test_central_directory_offset_lie_is_rejected():
    assert rejection(valid_bundle(central_offset_delta=-4)) is ReasonCode.BUNDLE_POLICY_VIOLATION


def test_central_directory_size_lie_is_rejected():
    assert rejection(valid_bundle(central_size_delta=8)) is ReasonCode.BUNDLE_POLICY_VIOLATION


def test_missing_end_of_central_directory_is_rejected():
    assert rejection(valid_bundle()[:-4]) is ReasonCode.BUNDLE_POLICY_VIOLATION


def test_empty_input_is_rejected():
    assert rejection(b"") is ReasonCode.BUNDLE_MALFORMED


def test_non_archive_input_is_rejected():
    assert rejection(b"not a zip at all, just text" * 8) is ReasonCode.BUNDLE_POLICY_VIOLATION


def test_oversized_bundle_is_rejected():
    assert rejection(b"PK\x03\x04" + b"\x00" * (bundle_module.MAX_BUNDLE_BYTES + 1)) is (
        ReasonCode.BUNDLE_TOO_LARGE
    )


# --- decompression and integrity ----------------------------------------------------


def test_compression_bomb_is_rejected():
    entries = archive().entries
    entries[1] = replace(entries[1], data=b"\x00" * (bundle_module.MAX_SUBMISSION_BYTES + 1))
    assert rejection(Archive(entries=entries).build()) is ReasonCode.BUNDLE_TOO_LARGE


def test_declared_size_lie_is_rejected():
    # The declared size is small but the stream expands far past it: the bounded read,
    # not the declared size, decides.
    payload = b"A" * 900_000
    entries = archive().entries
    entries[1] = replace(entries[1], data=payload, file_size=10, local_file_size=10)
    assert rejection(Archive(entries=entries).build()) is ReasonCode.BUNDLE_MALFORMED


def test_crc_mismatch_is_rejected():
    entries = archive().entries
    entries[1] = replace(entries[1], crc=0xDEADBEEF, local_crc=0xDEADBEEF)
    assert rejection(Archive(entries=entries).build()) is ReasonCode.BUNDLE_MALFORMED


def test_truncated_deflate_stream_is_rejected():
    entries = archive().entries
    payload = _deflate(VALID_PROOF)
    entries[1] = replace(
        entries[1],
        compressed=payload[: len(payload) // 2],
        compress_size=len(payload) // 2,
        local_compress_size=len(payload) // 2,
    )
    assert rejection(Archive(entries=entries).build()) is ReasonCode.BUNDLE_MALFORMED


def test_corrupt_deflate_stream_is_rejected():
    entries = archive().entries
    entries[1] = replace(entries[1], compressed=b"\xff" * 40, compress_size=40, local_compress_size=40)
    assert rejection(Archive(entries=entries).build()) is ReasonCode.BUNDLE_MALFORMED


def test_stored_entry_size_mismatch_is_rejected():
    entries = archive().entries
    entries[1] = replace(
        entries[1], method=STORED, local_method=STORED, file_size=5, local_file_size=5
    )
    assert rejection(Archive(entries=entries).build()) is ReasonCode.BUNDLE_MALFORMED


# --- proof content policy -----------------------------------------------------------


def test_oversized_proof_is_rejected():
    proof = b"-- " + b"a" * bundle_module.MAX_SUBMISSION_BYTES + b"\n"
    assert rejection(valid_bundle(proof=proof)) is ReasonCode.BUNDLE_TOO_LARGE


def test_non_utf8_proof_is_rejected():
    raw = valid_bundle(
        manifest=manifest_json(proof_sha256=sha256_bytes(b"\xff\xfe"), proof_bytes=2),
        proof=b"\xff\xfe",
    )
    assert rejection(raw) is ReasonCode.SUBMISSION_NOT_UTF8


def test_nul_byte_in_proof_is_rejected():
    proof = b"theorem target : True := by\x00 trivial\n"
    raw = valid_bundle(
        manifest=manifest_json(proof_sha256=sha256_bytes(proof), proof_bytes=len(proof)),
        proof=proof,
    )
    assert rejection(raw) is ReasonCode.SUBMISSION_POLICY_VIOLATION


# --- manifest policy ----------------------------------------------------------------


def test_manifest_duplicate_keys_are_rejected():
    raw = b'{"schema_version": 1, "schema_version": 1, "format": "conjectures-submission/v1"}'
    assert rejection(valid_bundle(manifest=raw)) is ReasonCode.BUNDLE_MANIFEST_INVALID


def test_manifest_json_constants_are_rejected():
    broken = manifest_json().replace(b'"proof_bytes": %d' % len(VALID_PROOF), b'"proof_bytes": NaN')
    assert rejection(valid_bundle(manifest=broken)) is ReasonCode.BUNDLE_MANIFEST_INVALID


def test_manifest_unknown_field_is_rejected():
    assert rejection(valid_bundle(manifest=manifest_json(surprise="x"))) is (
        ReasonCode.BUNDLE_MANIFEST_INVALID
    )


@pytest.mark.parametrize(
    "missing",
    ["schema_version", "format", "task_id", "task_bundle_sha256", "proof_path", "proof_sha256", "proof_bytes", "miner_hotkey"],
)
def test_manifest_missing_field_is_rejected(missing):
    overrides = {missing: None}
    assert rejection(valid_bundle(manifest=manifest_json(**overrides))) is (
        ReasonCode.BUNDLE_MANIFEST_INVALID
    )


@pytest.mark.parametrize(
    "override",
    [
        {"schema_version": 2},
        {"schema_version": True},
        {"format": "conjectures-submission/v2"},
        {"task_id": "Fixture"},
        {"task_id": "../fixture"},
        {"task_bundle_sha256": "ab" * 32},
        {"task_bundle_sha256": "sha256:" + "AB" * 32},
        {"proof_path": "proof/Main.lean"},
        {"proof_sha256": "nope"},
        {"proof_bytes": 0},
        {"proof_bytes": -1},
        {"proof_bytes": 1.0},
        {"proof_bytes": bundle_module.MAX_SUBMISSION_BYTES + 1},
        {"miner_hotkey": "not-an-address"},
        {"miner_hotkey": "0OIl" * 12},
        {"solver": {"name": "x"}},
        {"solver": {"name": "x", "version": "y", "extra": "z"}},
        {"solver": {"name": "bad name", "version": "1"}},
        {"solver": {"name": "x", "version": "a" * 65}},
    ],
)
def test_manifest_field_validation(override):
    assert rejection(valid_bundle(manifest=manifest_json(**override))) is (
        ReasonCode.BUNDLE_MANIFEST_INVALID
    )


def test_oversized_manifest_is_rejected():
    # Structurally valid JSON, padded past the manifest limit with insignificant space.
    padded = b"{\n" + b" " * (bundle_module.MAX_MANIFEST_BYTES + 1) + manifest_json()[2:]
    assert len(padded) > bundle_module.MAX_MANIFEST_BYTES
    assert json.loads(padded)["task_id"] == "fixture"
    assert rejection(valid_bundle(manifest=padded)) is ReasonCode.BUNDLE_TOO_LARGE


def test_manifest_not_an_object_is_rejected():
    assert rejection(valid_bundle(manifest=b"[1, 2, 3]")) is ReasonCode.BUNDLE_MANIFEST_INVALID


def test_declared_proof_digest_must_match_the_archived_proof():
    other = sha256_bytes(b"different proof")
    assert rejection(valid_bundle(manifest=manifest_json(proof_sha256=other))) is (
        ReasonCode.BUNDLE_DIGEST_MISMATCH
    )


def test_declared_proof_length_must_match_the_archived_proof():
    assert rejection(valid_bundle(manifest=manifest_json(proof_bytes=len(VALID_PROOF) + 1))) is (
        ReasonCode.BUNDLE_DIGEST_MISMATCH
    )


# --- task and identity binding ------------------------------------------------------


def test_admit_binds_task_and_hotkey():
    fixture = task_manifest()
    result = admit_proof_bundle(
        valid_bundle(),
        task_manifest=fixture,
        expected_task_sha256=TASK_DIGEST,
        expected_hotkey=HOTKEY,
    )
    assert result.manifest.task_id == fixture.task_id


def test_admit_rejects_a_mismatched_task_id():
    with pytest.raises(VerifierError) as caught:
        admit_proof_bundle(
            valid_bundle(manifest=manifest_json(task_id="other-task")),
            task_manifest=task_manifest(),
            expected_task_sha256=TASK_DIGEST,
            expected_hotkey=HOTKEY,
        )
    assert caught.value.reason is ReasonCode.BUNDLE_MANIFEST_INVALID


def test_admit_rejects_a_mismatched_task_digest():
    with pytest.raises(VerifierError) as caught:
        admit_proof_bundle(
            valid_bundle(),
            task_manifest=task_manifest(),
            expected_task_sha256="sha256:" + "cd" * 32,
            expected_hotkey=HOTKEY,
        )
    assert caught.value.reason is ReasonCode.TASK_COMMITMENT_MISMATCH


def test_admit_rejects_a_mismatched_hotkey():
    with pytest.raises(VerifierError) as caught:
        admit_proof_bundle(
            valid_bundle(),
            task_manifest=task_manifest(),
            expected_task_sha256=TASK_DIGEST,
            expected_hotkey="5C4hrfjw9DjXZTzV3MwzrrAr9P1MJhSrvWGWqi1eSuyUpnhM",
        )
    assert caught.value.reason is ReasonCode.BUNDLE_MANIFEST_INVALID


def test_admit_rejects_an_uncommitted_task_digest():
    with pytest.raises(VerifierError) as caught:
        admit_proof_bundle(
            valid_bundle(),
            task_manifest=task_manifest(),
            expected_task_sha256="not-a-digest",
            expected_hotkey=HOTKEY,
        )
    assert caught.value.reason is ReasonCode.INVALID_ARGUMENT


def test_admit_runs_the_static_lean_policy_scanner():
    proof = b"import Lean\ntheorem target : True := by trivial\n"
    raw = valid_bundle(
        manifest=manifest_json(proof_sha256=sha256_bytes(proof), proof_bytes=len(proof)),
        proof=proof,
    )
    with pytest.raises(VerifierError) as caught:
        admit_proof_bundle(
            raw,
            task_manifest=task_manifest(),
            expected_task_sha256=TASK_DIGEST,
            expected_hotkey=HOTKEY,
        )
    assert caught.value.reason is ReasonCode.SUBMISSION_POLICY_VIOLATION
    assert "import is prohibited" in str(caught.value)


def test_admit_honours_the_task_submission_byte_limit():
    fixture = task_manifest()
    assert fixture.max_submission_bytes == 10000
    proof = b"-- " + b"a" * 20000 + b"\n"
    raw = valid_bundle(
        manifest=manifest_json(proof_sha256=sha256_bytes(proof), proof_bytes=len(proof)),
        proof=proof,
    )
    with pytest.raises(VerifierError) as caught:
        admit_proof_bundle(
            raw,
            task_manifest=fixture,
            expected_task_sha256=TASK_DIGEST,
            expected_hotkey=HOTKEY,
        )
    assert caught.value.reason is ReasonCode.BUNDLE_TOO_LARGE


# --- out-of-process scanner ---------------------------------------------------------


def test_verdict_is_admitted_for_a_valid_bundle():
    verdict = bundle_verdict(valid_bundle())
    assert verdict["admitted"] is True
    assert verdict["reason_code"] == ReasonCode.VERIFIED.value
    assert verdict["proof_sha256"] == sha256_bytes(VALID_PROOF)
    assert verdict["manifest"]["task_id"] == "fixture"


def test_verdict_is_fail_closed_for_a_hostile_bundle():
    verdict = bundle_verdict(valid_bundle(trailer=b"\x00" * 8))
    assert verdict["admitted"] is False
    assert verdict["reason_code"] == ReasonCode.BUNDLE_POLICY_VIOLATION.value
    assert "bundle_sha256" not in verdict


def test_read_bundle_file_rejects_a_symlink(tmp_path):
    target = tmp_path / "bundle.zip"
    target.write_bytes(valid_bundle())
    link = tmp_path / "link.zip"
    link.symlink_to(target)
    with pytest.raises(VerifierError) as caught:
        read_bundle_file(link)
    assert caught.value.reason is ReasonCode.BUNDLE_POLICY_VIOLATION


def test_read_bundle_file_rejects_a_directory(tmp_path):
    with pytest.raises(VerifierError) as caught:
        read_bundle_file(tmp_path)
    assert caught.value.reason is ReasonCode.BUNDLE_POLICY_VIOLATION


def test_read_bundle_file_bounds_the_read(tmp_path):
    target = tmp_path / "bundle.zip"
    target.write_bytes(b"\x00" * 4096)
    with pytest.raises(VerifierError) as caught:
        read_bundle_file(target, max_bytes=1024)
    assert caught.value.reason is ReasonCode.BUNDLE_TOO_LARGE


def test_read_bundle_file_round_trips(tmp_path):
    target = tmp_path / "bundle.zip"
    raw = valid_bundle()
    target.write_bytes(raw)
    assert read_bundle_file(target) == raw
