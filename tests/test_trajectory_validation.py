import asyncio
import io
import struct
import time
import zipfile

import pytest

from scripts.trajectory_assets import build_package, example_document
from submission_api.errors import BadRequest
from submission_api.trajectory_schema import PackageError, parse_package
from submission_api.trajectory_validation import ValidationLimits, prepare_upload_isolated


def test_isolated_validation_preserves_summary_digest_and_safe_errors():
    import hashlib
    async def scenario():
        raw = build_package(example_document())
        summary, unchanged, digest, fingerprint, consent, version = await prepare_upload_isolated(raw, {"accepted": True}, 7)
        expected = parse_package(raw)
        fields = set(type(summary.manifest).model_fields)
        assert summary.manifest.model_dump(include=fields) == expected.manifest.model_dump(include=fields)
        assert (summary.events, summary.artifacts, summary.proof_first_seen) == (expected.events, expected.artifacts, expected.proof_first_seen)
        assert unchanged is raw and digest == hashlib.sha256(raw).digest()
        assert fingerprint == summary.fingerprint and version == 7
        assert consent["source_file_sha256"] == "sha256:" + digest.hex()
        with pytest.raises(BadRequest) as caught:
            await prepare_upload_isolated(b"PRIVATE_NONZIP_CONTENT", {})
        assert "PRIVATE_NONZIP_CONTENT" not in str(caught.value.extra)
        assert caught.value.extra["validation_errors"][0]["message"] == "upload a .zip package"
    asyncio.run(scenario())


@pytest.mark.parametrize("field,value", [(12, 64 * 1024 * 1024), (10, 20001)])
def test_directory_limits_run_before_zipfile_allocates_entries(monkeypatch, field, value):
    import submission_api.trajectory_schema as schema
    raw = bytearray(build_package(example_document()))
    end = raw.rfind(b"PK\x05\x06")
    struct.pack_into("<L" if field == 12 else "<H", raw, end + field, value)
    monkeypatch.setattr(zipfile, "ZipFile", lambda *args, **kwargs: pytest.fail("ZipFile must not be constructed"))
    with pytest.raises(PackageError, match="directory exceeds"):
        schema.parse_package(bytes(raw))


def test_directory_counts_real_entries_when_header_lies(monkeypatch):
    import submission_api.trajectory_schema as schema
    raw = bytearray(build_package(example_document()))
    end = raw.rfind(b"PK\x05\x06")
    struct.pack_into("<2H", raw, end + 8, 0, 0)
    monkeypatch.setattr(schema, "MAX_FILES", 1)
    monkeypatch.setattr(zipfile, "ZipFile", lambda *args, **kwargs: pytest.fail("ZipFile must not be constructed"))
    with pytest.raises(PackageError, match="directory exceeds"):
        schema.parse_package(bytes(raw))


def test_zip64_directory_is_checked_even_without_legacy_sentinels(monkeypatch):
    monkeypatch.setattr(zipfile, "ZIP64_LIMIT", 128)
    raw = bytearray(build_package(example_document()))
    assert parse_package(bytes(raw)).events == 10
    record = raw.rfind(b"PK\x06\x06")
    assert record >= 0
    struct.pack_into("<Q", raw, record + 40, 64 * 1024 * 1024)
    end = raw.rfind(b"PK\x05\x06")
    struct.pack_into("<2H2L", raw, end + 8, 10, 10, 100, 0)
    with pytest.raises(PackageError, match="directory exceeds"):
        parse_package(bytes(raw))


@pytest.mark.parametrize("limits", [
    ValidationLimits(wall_seconds=0.01),
    ValidationLimits(cpu_seconds=1, wall_seconds=15),
    ValidationLimits(memory_bytes=64 * 1024 * 1024),
])
def test_bomb_budget_kills_worker_and_next_upload_succeeds(monkeypatch, limits):
    from submission_api.trajectory_intake import UploadBudget
    workers = []
    original = asyncio.create_subprocess_exec
    async def capture(*args, **kwargs):
        worker = await original(*args, **kwargs)
        workers.append(worker)
        return worker
    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    async def scenario():
        raw = build_package(example_document())
        # Millions of tiny valid JSON lines exercise CPU exhaustion without huge test data.
        bomb = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for entry in source.infolist():
                output.writestr(entry.filename, b"{}\n" * 4_000_000 if entry.filename.endswith("episodes.jsonl") else source.read(entry))
        budget = UploadBudget()
        started = time.monotonic()
        with pytest.raises(BadRequest) as caught:
            async with budget.hold("owner"):
                await prepare_upload_isolated(bomb.getvalue(), {}, limits=limits)
        assert caught.value.reason_code == "TRAJECTORY_INVALID"
        assert time.monotonic() - started < 20
        assert all(worker.returncode is not None for worker in workers)
        assert not budget.accounts
        async with budget.hold("owner"):
            assert (await prepare_upload_isolated(raw, {}))[0].events == 10
    asyncio.run(scenario())


def test_cancellation_reaps_worker_and_closes_owned_temporary_file(monkeypatch):
    import submission_api.trajectory_validation as validation
    workers, packages = [], []
    original_spawn = asyncio.create_subprocess_exec
    original_package = validation.FilePackage
    class TrackedPackage(original_package):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            packages.append(self)
    monkeypatch.setattr(validation, "FilePackage", TrackedPackage)
    async def scenario():
        started = asyncio.Event()
        async def capture(*args, **kwargs):
            process = await original_spawn(*args, **kwargs)
            workers.append(process)
            started.set()
            return process
        monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
        task = asyncio.create_task(prepare_upload_isolated(build_package(example_document()), {}))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert workers and all(worker.returncode is not None for worker in workers)
        assert packages and all(package.file.closed for package in packages)
    asyncio.run(scenario())


def test_worker_event_index_disk_limit_is_enforced():
    import json
    raw = build_package(example_document())
    with zipfile.ZipFile(io.BytesIO(raw)) as source:
        events = source.read("chat-log-package/events.jsonl")
    events += b"\n".join(json.dumps({"id": str(i) + "x" * 190, "role": "tool", "type": "message", "timestamp": None}).encode()
                           for i in range(40000)) + b"\n"
    modified = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(modified, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for entry in source.infolist():
            output.writestr(entry.filename, events if entry.filename.endswith("events.jsonl") else source.read(entry))
    raw = modified.getvalue()
    async def scenario():
        with pytest.raises(BadRequest):
            await prepare_upload_isolated(raw, {}, limits=ValidationLimits(index_bytes=4096, wall_seconds=30))
    asyncio.run(scenario())


def test_scratch_free_space_is_checked_before_creating_a_file(monkeypatch):
    from types import SimpleNamespace
    from submission_api import trajectory_storage as storage
    from submission_api.errors import TooManyRequests
    monkeypatch.setattr(storage.shutil, "disk_usage", lambda _: SimpleNamespace(free=6 * 1024 ** 3 + 99))
    monkeypatch.setattr(storage.tempfile, "TemporaryFile", lambda: pytest.fail("No file should be allocated"))
    with pytest.raises(TooManyRequests):
        storage.FilePackage(size_hint=100)


def test_unreferenced_supporting_files_are_decompressed_and_crc_checked():
    raw = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(build_package(example_document()))) as source, zipfile.ZipFile(raw, "w") as output:
        for entry in source.infolist():
            output.writestr(entry, source.read(entry))
        output.writestr("chat-log-package/artifacts/unreferenced.bin", b"UNREFERENCED_PAYLOAD")
    corrupted = raw.getvalue().replace(b"UNREFERENCED_PAYLOAD", b"CORRUPTED___PAYLOAD!")
    assert len(corrupted) == len(raw.getvalue())
    with pytest.raises(PackageError):
        parse_package(corrupted)


def test_scratch_headroom_is_rechecked_during_assembly(monkeypatch):
    from types import SimpleNamespace
    from submission_api import trajectory_storage as storage
    from submission_api.errors import TooManyRequests
    package = storage.FilePackage()
    try:
        monkeypatch.setattr(storage.shutil, "disk_usage", lambda _: SimpleNamespace(free=6 * 1024 ** 3))
        with pytest.raises(TooManyRequests):
            package.write(b"next part")
        assert len(package) == 0 and package.file.tell() == 0
    finally:
        package.close()
