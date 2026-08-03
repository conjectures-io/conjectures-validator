import json
from dataclasses import replace

import pytest

from verifier.errors import ReasonCode, VerifierError
from verifier.models import Classification
from verifier.task_generator import (
    generate_all,
    generate_group_task,
    generate_task,
    group_task_id,
    task_id,
)
from verifier.task_loader import load_task, load_task_bundle

from conftest import catalog, declaration


def test_generates_immutable_task_files(tmp_path):
    item = replace(
        declaration(),
        references=("[Source](https://example.com/source)",),
    )
    destination = tmp_path / "task-positive"
    result = generate_task(
        catalog=catalog(item),
        declaration=item,
        mode="positive",
        output=destination,
        allow_non_open=True,
        validate_target=lambda *_: "sha256:" + "2" * 64,
    )
    assert result.task_id.startswith("fc-e923379e-")
    assert (destination / "Challenge.lean").is_file()
    assert 'fcTypeOfName% "VerifierFixtures.direct"' in (destination / "Challenge.lean").read_text()
    manifest = json.loads((destination / "manifest.json").read_text())
    source = json.loads((destination / "source-metadata.json").read_text())
    assert manifest["trusted_file_hashes"]["Challenge.lean"].startswith("sha256:")
    assert source["references"] == ["[Source](https://example.com/source)"]
    assert load_task(destination) == result


def test_formalized_mode_is_the_exact_production_source_type(tmp_path):
    item = declaration()
    destination = tmp_path / "task-formalized"
    result = generate_task(
        catalog=catalog(item),
        declaration=item,
        mode="formalized",
        output=destination,
        validate_target=lambda *_: item.type_hash,
    )
    challenge = (destination / "Challenge.lean").read_text(encoding="utf-8")
    assert result.production_eligible
    assert result.generated_target_type_hash == result.source_type_hash
    assert 'theorem target : fcTypeOfName% "VerifierFixtures.direct"' in challenge
    assert "¬" not in challenge
    assert load_task(destination) == result


def test_counterexample_mode_is_a_distinct_production_target(tmp_path):
    item = declaration()
    counterexample_hash = "sha256:" + "2" * 64
    destination = tmp_path / "task-counterexample"
    result = generate_task(
        catalog=catalog(item),
        declaration=item,
        mode="counterexample",
        output=destination,
        validate_target=lambda *_: counterexample_hash,
    )
    challenge = (destination / "Challenge.lean").read_text(encoding="utf-8")
    assert result.production_eligible
    assert result.generated_target_type_hash == counterexample_hash
    assert result.generated_target_type_hash != result.source_type_hash
    assert 'theorem target : ¬ (fcTypeOfName% "VerifierFixtures.direct")' in challenge
    assert load_task(destination) == result


def test_counterexample_generation_rejects_source_hash_as_target(tmp_path):
    item = declaration()
    with pytest.raises(VerifierError) as error:
        generate_task(
            catalog=catalog(item),
            declaration=item,
            mode="counterexample",
            output=tmp_path / "task-counterexample",
            validate_target=lambda *_: item.type_hash,
        )
    assert error.value.reason == ReasonCode.STATEMENT_MISMATCH


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
        allow_non_open=True,
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
        allow_non_open=True,
        validate_target=lambda *_: "sha256:" + "2" * 64,
    )
    assert result.source_theorem == original.theorem


def test_long_theorem_name_produces_portable_task_id():
    identifier = task_id("a" * 40, f"Namespace.{'veryLong' * 100}", "positive", 1)
    assert len(identifier.encode("utf-8")) < 255


def test_generates_all_of_task_with_exact_individual_targets(tmp_path):
    first = declaration(theorem="Fixture.parts.i")
    second = replace(
        declaration(theorem="Fixture.parts.ii"),
        type_hash="sha256:" + "2" * 64,
    )
    destination = tmp_path / "task-group"

    result = generate_group_task(
        catalog=catalog(first, second),
        declarations=(first, second),
        mode="formalized",
        output=destination,
        validate_target=lambda _task, declaration, _generated, _mode, _target: declaration.type_hash,
    )

    challenge = (destination / "Challenge.lean").read_text(encoding="utf-8")
    bundle = load_task_bundle(destination)
    assert result.schema_version == 2
    assert result.task_id == group_task_id(
        catalog(first, second).repository_commit,
        (first.theorem, second.theorem),
        "formalized",
        1,
    )
    assert result.theorem_names == ("Bounty.target_1", "Bounty.target_2")
    assert 'fcTypeOfName% "Fixture.parts.i"' in challenge
    assert 'fcTypeOfName% "Fixture.parts.ii"' in challenge
    assert bundle.sources == (first, second)


def test_production_generation_rejects_solved_and_already_proved_sources(tmp_path):
    solved = declaration(category="research solved")
    proved = replace(declaration(), contains_sorry_in_value=False, depends_on_sorry=False, transitive_axioms=())
    sorry_in_statement = replace(declaration(), contains_sorry_in_type=True)
    for index, item in enumerate((solved, proved, sorry_in_statement)):
        with pytest.raises(VerifierError) as error:
            generate_task(
                catalog=catalog(item),
                declaration=item,
                mode="formalized",
                output=tmp_path / f"task-{index}",
                validate_target=lambda *_: item.type_hash,
            )
        assert error.value.reason == ReasonCode.INELIGIBLE_TASK


def test_production_generation_rejects_matching_proved_declaration(tmp_path):
    open_source = declaration()
    proved = replace(
        declaration(theorem="Fixture.alreadyProved"),
        contains_sorry_in_value=False,
        depends_on_sorry=False,
        transitive_axioms=(),
    )
    with pytest.raises(VerifierError) as error:
        generate_task(
            catalog=catalog(open_source, proved),
            declaration=open_source,
            mode="formalized",
            output=tmp_path / "task",
            validate_target=lambda *_: open_source.type_hash,
        )
    assert error.value.reason == ReasonCode.INELIGIBLE_TASK


def test_counterexample_generation_rejects_matching_proved_negation(tmp_path):
    open_source = declaration()
    counterexample_hash = "sha256:" + "2" * 64
    proved_negation = replace(
        declaration(theorem="Fixture.alreadyRefuted"),
        type_hash=counterexample_hash,
        contains_sorry_in_value=False,
        depends_on_sorry=False,
        transitive_axioms=(),
    )
    with pytest.raises(VerifierError) as error:
        generate_task(
            catalog=catalog(open_source, proved_negation),
            declaration=open_source,
            mode="counterexample",
            output=tmp_path / "task",
            validate_target=lambda *_: counterexample_hash,
        )
    assert error.value.reason == ReasonCode.INELIGIBLE_TASK


def test_formalized_generation_rejects_a_transformed_compiled_target(tmp_path):
    open_source = declaration()
    with pytest.raises(VerifierError) as error:
        generate_task(
            catalog=catalog(open_source),
            declaration=open_source,
            mode="formalized",
            output=tmp_path / "task",
            validate_target=lambda *_: "sha256:" + "2" * 64,
        )
    assert error.value.reason == ReasonCode.STATEMENT_MISMATCH


def test_non_open_override_marks_task_as_testing_only(tmp_path):
    item = declaration(category="test")
    result = generate_task(
        catalog=catalog(item),
        declaration=item,
        mode="positive",
        output=tmp_path / "task",
        allow_non_open=True,
        validate_target=lambda *_: "sha256:" + "2" * 64,
    )
    assert not result.production_eligible


def test_finite_answer_pretty_type_is_never_spliced_into_lean(tmp_path):
    injected = "Nat := by sorry\ninitialize unsafe payload\ndef replacement : Nat"
    item = replace(
        declaration(classification=Classification.FINITE_ANSWER),
        answer_occurrences=({"kind": "value", "type_pretty": injected},),
        finite_constructors=("Fixture.first", "Fixture.second"),
        supported_modes=("answer",),
    )
    generate_task(
        catalog=catalog(item),
        declaration=item,
        mode="answer",
        output=tmp_path / "task",
        allow_non_open=True,
        validate_target=lambda *_: "sha256:" + "2" * 64,
    )
    challenge = (tmp_path / "task" / "Challenge.lean").read_text(encoding="utf-8")
    assert injected not in challenge
    assert 'fcAnswerType% "VerifierFixtures.direct"' in challenge


def test_unsafe_module_name_is_rejected_before_writing_task(tmp_path):
    item = replace(declaration(), module="TestFixtures/-x-/initialize")
    with pytest.raises(VerifierError) as error:
        generate_task(
            catalog=catalog(item),
            declaration=item,
            mode="positive",
            output=tmp_path / "task",
            allow_non_open=True,
            validate_target=lambda *_: "sha256:" + "2" * 64,
        )
    assert error.value.reason == ReasonCode.INVALID_MANIFEST


def test_quoted_module_segment_with_dots_is_accepted(tmp_path):
    item = replace(
        declaration(),
        module="FormalConjectures.Arxiv.«2303.01089».FurstenbergTimesPTimesQ",
    )
    generate_task(
        catalog=catalog(item),
        declaration=item,
        mode="positive",
        output=tmp_path / "task",
        allow_non_open=True,
        validate_target=lambda *_: "sha256:" + "2" * 64,
    )
    challenge = (tmp_path / "task" / "Challenge.lean").read_text(encoding="utf-8")
    assert "import FormalConjectures.Arxiv.«2303.01089».FurstenbergTimesPTimesQ" in challenge


def test_quoted_module_segment_cannot_inject_commands(tmp_path):
    item = replace(declaration(), module="FormalConjectures.«safe»\naxiom.forged")
    with pytest.raises(VerifierError) as error:
        generate_task(
            catalog=catalog(item),
            declaration=item,
            mode="positive",
            output=tmp_path / "task",
            allow_non_open=True,
            validate_target=lambda *_: "sha256:" + "2" * 64,
        )
    assert error.value.reason == ReasonCode.INVALID_MANIFEST


def test_source_name_is_encoded_as_a_lean_string_literal(tmp_path):
    injected = 'Fixture.bad"\naxiom forged : False'
    item = replace(declaration(), theorem=injected)
    generate_task(
        catalog=catalog(item),
        declaration=item,
        mode="positive",
        output=tmp_path / "task",
        allow_non_open=True,
        validate_target=lambda *_: "sha256:" + "2" * 64,
    )
    challenge = (tmp_path / "task" / "Challenge.lean").read_text(encoding="utf-8")
    assert "\naxiom forged : False" not in challenge
    assert '\\naxiom forged : False' in challenge
