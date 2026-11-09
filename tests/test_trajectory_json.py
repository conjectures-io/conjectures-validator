"""Large JSON indexes keep their full contents and the existing strict validation rules."""

import asyncio
import io
import json
import zipfile

import pytest

from scripts.trajectory_assets import build_package, example_document
from submission_api.trajectory_json import READ_BYTES, array_items
from submission_api.trajectory_schema import PackageError, _decode, parse_package
from submission_api.trajectory_validation import ValidationLimits, prepare_upload_isolated


@pytest.mark.parametrize("read_size", [1, 2, 7, READ_BYTES])
@pytest.mark.parametrize(
    "value",
    [
        [],
        [None, True, False, -12, 1.25e-8, 2**80, ""],
        [
            {"text": 'brackets ],} and an escaped quote " and slash \\', "unicode": "α雪🙂"},
            [1, {"nested": [False, '\\"']}],
        ],
    ],
)
def test_array_entries_match_json_across_read_boundaries(read_size, value):
    class ShortReads(io.BytesIO):
        def read(self, size=-1):
            assert size == READ_BYTES
            return super().read(min(size, read_size))

    raw = (" \n" + json.dumps(value, ensure_ascii=False, indent=2) + "\t\r\n").encode()
    assert list(array_items(ShortReads(raw), _decode)) == json.loads(raw)


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b" ",
        b"{}",
        b"null",
        b"[",
        b"[1",
        b"[1,]",
        b"[,1]",
        b"[1,,2]",
        b"[1 2]",
        b"[{}{}]",
        b"[01]",
        b"[1e]",
        b"[true false]",
        b"[NaN]",
        b"[Infinity]",
        b"[{}",
        b"[{]",
        b"[}]",
        b"[] []",
        b"[]x",
        b'["unterminated]',
        b'["bad\\x"]',
        b'["\xff"]',
        b'["unescaped\nnewline"]',
        b'[{"x": 1, "x": 2}]',
    ],
)
def test_malformed_arrays_and_duplicate_keys_remain_rejected(raw):
    with pytest.raises((ValueError, UnicodeError)):
        list(array_items(io.BytesIO(raw), _decode))


def test_nesting_budget_includes_the_outer_array():
    assert list(array_items(io.BytesIO(b"[" * 24 + b"0" + b"]" * 24), _decode))
    with pytest.raises(ValueError, match="JSON nesting exceeds 24 levels"):
        list(array_items(io.BytesIO(b"[" * 25 + b"0" + b"]" * 25), _decode))


def test_first_entry_is_yielded_before_reading_the_rest_of_the_index():
    source = io.BytesIO(b'["first", "' + b"x" * (READ_BYTES * 4) + b'"]')
    entries = array_items(source, _decode)
    assert next(entries) == "first"
    assert source.tell() == READ_BYTES
    assert list(entries) == ["x" * (READ_BYTES * 4)]


def replace_file(raw, filename, content):
    target = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(raw)) as source,
        zipfile.ZipFile(
            target,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as output,
    ):
        for info in source.infolist():
            output.writestr(
                info.filename, content if info.filename.endswith(filename) else source.read(info)
            )
    return target.getvalue()


@pytest.mark.parametrize("filename", ["manifest.json", "validation.json", "artifact-index.json"])
def test_json_files_can_exceed_the_previous_32_mib_limit(filename):
    raw = build_package(example_document())
    with zipfile.ZipFile(io.BytesIO(raw)) as source:
        original = source.read("chat-log-package/" + filename)
    # Whitespace increases the actual uncompressed file size without changing its data.
    large = original + b" " * (32 * 1024 * 1024)
    package = replace_file(raw, filename, large)
    summary = parse_package(package)
    assert (summary.events, summary.artifacts) == (10, 1)


@pytest.mark.parametrize("suffix", [b",]", b"] trailing", b', {"id": "x", "id": "y"}]'])
def test_index_stream_reports_late_errors_with_the_file_location(suffix):
    raw = build_package(example_document())
    with zipfile.ZipFile(io.BytesIO(raw)) as source:
        index = source.read("chat-log-package/artifact-index.json").rstrip()
    with pytest.raises(PackageError) as caught:
        parse_package(replace_file(raw, "artifact-index.json", index[:-1] + suffix))
    assert caught.value.file == "artifact-index.json"


def large_index_package():
    """A 128 MiB index, written incrementally so the fixture also avoids buffering it."""
    target = io.BytesIO()
    document = example_document()
    raw = build_package(document)
    with (
        zipfile.ZipFile(io.BytesIO(raw)) as source,
        zipfile.ZipFile(
            target,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as output,
    ):
        for info in source.infolist():
            if not info.filename.endswith("artifact-index.json"):
                output.writestr(info.filename, source.read(info))
                continue
            with output.open(info.filename, "w") as index:
                index.write(json.dumps(document["artifacts"]).encode()[:-1])
                for number in range(512):
                    entry = {
                        "id": f"extra-{number}",
                        "path": "artifacts/source.txt",
                        "kind": "log",
                        "sha256": "sha256:" + "0" * 64,
                        "included": False,
                        "event_ids": [],
                        "metadata": "x" * (256 * 1024),
                    }
                    index.write(b"," + json.dumps(entry).encode())
                index.write(b"]")
    return target.getvalue()


def test_large_index_passes_the_isolated_worker_with_bounded_memory():
    raw = large_index_package()
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        assert archive.getinfo("chat-log-package/artifact-index.json").file_size > 128 * 1024 * 1024
    # A whole-array decoder needs multiple copies of this index and exceeds this budget.
    limits = ValidationLimits(memory_bytes=384 * 1024 * 1024, wall_seconds=120)
    result = asyncio.run(prepare_upload_isolated(raw, {}, limits=limits))
    assert (result[0].events, result[0].artifacts) == (10, 513)
    assert result[1] == raw
