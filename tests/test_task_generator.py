import json
from dataclasses import replace

import pytest

from verifier.errors import ReasonCode, VerifierError
from verifier.models import Classification
from verifier.task_generator import generate_all, generate_task, task_id
from verifier.task_loader import load_task

from conftest import catalog, declaration


def test_generates_immutable_task_files(tmp_path):
    item = declaration()
    destination = tmp_path / "task-positive"
    result = generate_task(
        catalog=catalog(item),
        declaration=item,
        mode="positive",
        output=destination,
        validate_target=lambda *_: "sha256:" + "2" * 64,
    )
    assert result.task_id.startswith("fc-e923379e-")
    assert (destination / "Challenge.lean").is_file()
    assert "type_of% VerifierFixtures.direct" in (destination / "Challenge.lean").read_text()
    manifest = json.loads((destination / "manifest.json").read_text())
    assert manifest["trusted_file_hashes"]["Challenge.lean"].startswith("sha256:")
    assert load_task(destination) == result


def test_adapter_required_is_stable_reason(tmp_path):
    item = declaration(classification=Classification.GENERAL_VALUE_ANSWER)
    with pytest.raises(VerifierError) as error:
        generate_task(
            catalog=catalog(item),
            declaration=item,
            mode="answer",
            output=tmp_path / "task",
            validate_target=lambda *_: "sha256:" + "2" * 64,
        )
    assert error.value.reason == ReasonCode.ADAPTER_REQUIRED


def test_generate_all_records_every_skip(tmp_path):
    direct = declaration()
    general = declaration(theorem="Fixture.general", classification=Classification.GENERAL_VALUE_ANSWER)
    result = generate_all(
        catalog=catalog(direct, general),
        declarations=(direct, general),
        modes=("positive",),
        output=tmp_path / "tasks",
        validate_target=lambda *_: "sha256:" + "2" * 64,
    )
    assert result["generated"] == 1
    assert result["skipped_adapter_required"] == 1
    assert len(result["tasks"]) + len(result["skipped"]) == 2


def test_explicit_pointer_resolves_to_original(tmp_path):
    original = declaration()
    pointer = declaration(theorem="Fixture.pointer", classification=Classification.POINTER_DECLARATION)
    pointer = replace(pointer, pointer_target=original.theorem, supported_modes=())
    result = generate_task(
        catalog=catalog(original, pointer),
        declaration=pointer,
        mode="positive",
        output=tmp_path / "pointer-task",
        validate_target=lambda *_: "sha256:" + "2" * 64,
    )
    assert result.source_theorem == original.theorem


def test_long_theorem_name_produces_portable_task_id():
    identifier = task_id("a" * 40, f"Namespace.{'veryLong' * 100}", "positive", 1)
    assert len(identifier.encode("utf-8")) < 255
