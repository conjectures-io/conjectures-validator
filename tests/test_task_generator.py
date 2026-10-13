import json
from dataclasses import replace

import pytest

from verifier.adapters import ADAPTER_VERSION, ADAPTER_VERSIONS, ADAPTERS
from verifier.errors import ReasonCode, VerifierError
from verifier.models import Classification
from verifier.task_generator import (
    HEADER_IMPORTS,
    MAX_SUBMISSION_BYTES,
    generate_all,
    generate_group_task,
    generate_task,
    group_task_id,
    task_id,
    trusted_group_task_payloads,
    trusted_task_payloads,
)
from verifier.task_loader import load_task, load_task_bundle

from conftest import catalog, declaration, recommit_trusted_files, relabel_adapter_version

LEGACY_HEADER = "import TestFixtures\nimport TaskSupport\n\nnamespace Bounty\n"
MATHLIB_HEADER = "import Mathlib\n" + LEGACY_HEADER


def test_generates_immutable_task_files(tmp_path):
    item = replace(
        declaration(),
        references=("[Source](https://example.com/source)",),
    )
    destination = tmp_path / "task-formalized"
    result = generate_task(
        catalog=catalog(item),
        declaration=item,
        mode="formalized",
        output=destination,
        validate_target=lambda *_: item.type_hash,
    )
    assert result.task_id.startswith("fc-e923379e-")
    assert (destination / "Challenge.lean").is_file()
    assert 'fcTypeOfName% "VerifierFixtures.direct"' in (destination / "Challenge.lean").read_text()
    manifest = json.loads((destination / "manifest.json").read_text())
    source = json.loads((destination / "source-metadata.json").read_text())
    assert manifest["trusted_file_hashes"]["Challenge.lean"].startswith("sha256:")
    assert source["references"] == ["[Source](https://example.com/source)"]
    assert load_task(destination) == result


@pytest.mark.parametrize("grouped", [False, True])
def test_enlarged_submission_policy_gets_a_new_loadable_task_identity(tmp_path, grouped):
    first = declaration(theorem="Fixture.parts.i")
    second = replace(declaration(theorem="Fixture.parts.ii"), type_hash="sha256:" + "2" * 64)
    create = generate_group_task if grouped else generate_task
    target_args = {"declarations": (first, second)} if grouped else {"declaration": first}
    manifests = []
    for limit in (1_000_000, 10 * 1024 * 1024, MAX_SUBMISSION_BYTES):
        destination = tmp_path / str(limit)
        manifest = create(
            catalog=catalog(first, second),
            **target_args,
            mode="formalized",
            output=destination,
            max_submission_bytes=limit,
            validate_target=lambda _path, item, *_: item.type_hash,
        )
        assert load_task_bundle(destination).manifest == manifest
        manifests.append(manifest)
    assert MAX_SUBMISSION_BYTES == 30 * 1024 * 1024
    assert len({manifest.task_id for manifest in manifests}) == len(manifests)
    for manifest in manifests[1:]:
        assert manifest.source_type_hash == manifests[0].source_type_hash
        assert manifest.trusted_file_hashes == manifests[0].trusted_file_hashes


def test_generation_defaults_to_thirty_megabyte_proofs(tmp_path):
    item = declaration()
    result = generate_task(
        catalog=catalog(item), declaration=item, mode="formalized", output=tmp_path / "task",
        validate_target=lambda *_: item.type_hash,
    )
    assert result.max_submission_bytes == 30 * 1024 * 1024


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


def test_legacy_polarity_mode_is_rejected(tmp_path):
    item = declaration()
    with pytest.raises(VerifierError) as error:
        generate_task(
            catalog=catalog(item),
            declaration=item,
            mode="positive",
            output=tmp_path / "task",
            validate_target=lambda *_: item.type_hash,
        )
    assert error.value.reason == ReasonCode.INVALID_TASK_MODE


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


def test_prop_answer_wrapper_requires_an_explicit_adapter(tmp_path):
    item = declaration(classification=Classification.PROP_ANSWER_WRAPPER)
    with pytest.raises(VerifierError) as error:
        generate_task(
            catalog=catalog(item),
            declaration=item,
            mode="formalized",
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
        modes=("formalized",),
        output=tmp_path / "tasks",
        allow_non_open=True,
        validate_target=lambda _task, declaration, *_: declaration.type_hash,
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
        mode="formalized",
        output=tmp_path / "pointer-task",
        allow_non_open=True,
        validate_target=lambda *_: original.type_hash,
    )
    assert result.source_theorem == original.theorem


def test_long_theorem_name_produces_portable_task_id():
    identifier = task_id("a" * 40, f"Namespace.{'veryLong' * 100}", "formalized", 1)
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
        ADAPTER_VERSION,
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
        mode="formalized",
        output=tmp_path / "task",
        allow_non_open=True,
        validate_target=lambda *_: item.type_hash,
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
            mode="formalized",
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
        mode="formalized",
        output=tmp_path / "task",
        allow_non_open=True,
        validate_target=lambda *_: item.type_hash,
    )
    challenge = (tmp_path / "task" / "Challenge.lean").read_text(encoding="utf-8")
    assert "import FormalConjectures.Arxiv.«2303.01089».FurstenbergTimesPTimesQ" in challenge


def test_quoted_module_segment_cannot_inject_commands(tmp_path):
    item = replace(declaration(), module="FormalConjectures.«safe»\naxiom.forged")
    with pytest.raises(VerifierError) as error:
        generate_task(
            catalog=catalog(item),
            declaration=item,
            mode="formalized",
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
        mode="formalized",
        output=tmp_path / "task",
        allow_non_open=True,
        validate_target=lambda *_: item.type_hash,
    )
    challenge = (tmp_path / "task" / "Challenge.lean").read_text(encoding="utf-8")
    assert "\naxiom forged : False" not in challenge
    assert '\\naxiom forged : False' in challenge


def _parts():
    first = declaration(theorem="Fixture.parts.i")
    second = replace(declaration(theorem="Fixture.parts.ii"), type_hash="sha256:" + "2" * 64)
    return first, second


def _generate_current(tmp_path, grouped):
    first, second = _parts()
    destination = tmp_path / ("group" if grouped else "single")
    create = generate_group_task if grouped else generate_task
    target_args = {"declarations": (first, second)} if grouped else {"declaration": first}
    manifest = create(
        catalog=catalog(first, second),
        **target_args,
        mode="formalized",
        output=destination,
        validate_target=lambda _path, item, *_: item.type_hash,
    )
    return destination, manifest


def test_every_adapter_version_has_a_frozen_header_policy():
    assert ADAPTER_VERSIONS == (1, 2)
    assert ADAPTER_VERSION == 2
    assert tuple(HEADER_IMPORTS) == ADAPTER_VERSIONS
    assert HEADER_IMPORTS[1] == ()
    assert HEADER_IMPORTS[2] == ("Mathlib",)
    assert all(adapter.versions == ADAPTER_VERSIONS for adapter in ADAPTERS.values())


@pytest.mark.parametrize("grouped", [False, True])
def test_new_tasks_import_the_entire_pinned_mathlib(tmp_path, grouped):
    task, manifest = _generate_current(tmp_path, grouped)

    challenge = (task / "Challenge.lean").read_text(encoding="utf-8")
    assert (task / "SolutionHeader.lean.txt").read_text(encoding="utf-8") == MATHLIB_HEADER
    assert challenge.startswith(MATHLIB_HEADER + "\ntheorem target")
    assert challenge.count("import ") == 3
    assert manifest.adapter_version == 2
    assert manifest.task_id.endswith("-formalized-v2")
    if grouped:
        sources = json.loads((task / "group-metadata.json").read_text(encoding="utf-8"))["sources"]
    else:
        sources = [json.loads((task / "source-metadata.json").read_text(encoding="utf-8"))]
    assert {source["adapter_version"] for source in sources} == {2}
    assert load_task_bundle(task).manifest == manifest


def test_version_one_payloads_stay_byte_for_byte_and_differ_only_by_the_mathlib_import():
    first, second = _parts()
    commit = catalog().repository_commit
    single = {
        version: trusted_task_payloads(first, "formalized", False, commit, version)
        for version in ADAPTER_VERSIONS
    }
    group = {
        version: trusted_group_task_payloads((first, second), "formalized", False, commit, version)
        for version in ADAPTER_VERSIONS
    }

    assert single[1]["SolutionHeader.lean.txt"] == LEGACY_HEADER.encode()
    assert single[1]["Challenge.lean"] == (
        LEGACY_HEADER
        + '\ntheorem target : fcTypeOfName% "Fixture.parts.i" := by\n  sorry\n\nend Bounty\n'
    ).encode()
    assert group[1]["SolutionHeader.lean.txt"] == LEGACY_HEADER.encode()
    assert group[1]["Challenge.lean"] == (
        LEGACY_HEADER
        + '\ntheorem target_1 : fcTypeOfName% "Fixture.parts.i" := by\n  sorry'
        + '\n\ntheorem target_2 : fcTypeOfName% "Fixture.parts.ii" := by\n  sorry'
        + "\n\nend Bounty\n"
    ).encode()
    for payloads in (single, group):
        for name in ("Challenge.lean", "SolutionHeader.lean.txt"):
            assert payloads[2][name] == b"import Mathlib\n" + payloads[1][name]
        for name in ("SolutionFooter.lean.txt", "comparator-config.json"):
            assert payloads[2][name] == payloads[1][name]
        source_metadata = {
            version: json.loads(payloads[version]["source-metadata.json"])
            for version in ADAPTER_VERSIONS
        }
        assert source_metadata[1] == {**source_metadata[2], "adapter_version": 1}


def test_header_policy_versions_have_distinct_task_identities():
    commit = catalog().repository_commit
    theorems = ("Fixture.parts.i", "Fixture.parts.ii")
    for limit in (1_000_000, 10 * 1024 * 1024, MAX_SUBMISSION_BYTES):
        for mode in ("formalized", "counterexample"):
            single = {
                version: task_id(commit, theorems[0], mode, version, max_submission_bytes=limit)
                for version in ADAPTER_VERSIONS
            }
            grouped = {
                version: group_task_id(commit, theorems, mode, version, max_submission_bytes=limit)
                for version in ADAPTER_VERSIONS
            }
            for identities in (single, grouped):
                assert identities[1].endswith(f"-{mode}-v1")
                assert identities[2].endswith(f"-{mode}-v2")
                digests = {identity.rsplit("-", 3)[1] for identity in identities.values()}
                assert len(digests) == len(ADAPTER_VERSIONS)


def test_unknown_adapter_version_cannot_reconstruct_payloads():
    first, second = _parts()
    commit = catalog().repository_commit
    for build, target in (
        (trusted_task_payloads, first),
        (trusted_group_task_payloads, (first, second)),
    ):
        for version in (0, 3):
            with pytest.raises(VerifierError) as error:
                build(target, "formalized", False, commit, version)
            assert error.value.reason == ReasonCode.INVALID_MANIFEST


@pytest.mark.parametrize("grouped", [False, True])
@pytest.mark.parametrize(
    "header",
    [
        LEGACY_HEADER,
        "import Mathlib\nimport Lean.Elab.Command\nimport TestFixtures\nimport TaskSupport\n\nnamespace Bounty\n",
        "import TestFixtures\nimport TaskSupport\nimport Mathlib\n\nnamespace Bounty\n",
    ],
    ids=["mathlib-removed", "extra-import", "reordered"],
)
def test_mathlib_header_cannot_be_edited_even_when_rehashed(tmp_path, grouped, header):
    task, _manifest = _generate_current(tmp_path, grouped)
    for name in ("Challenge.lean", "SolutionHeader.lean.txt"):
        path = task / name
        path.write_text(
            path.read_text(encoding="utf-8").replace(MATHLIB_HEADER, header, 1),
            encoding="utf-8",
        )
    recommit_trusted_files(task)
    with pytest.raises(VerifierError, match="not produced by the pinned generator") as error:
        load_task_bundle(task)
    assert error.value.reason == ReasonCode.TRUSTED_FILE_MODIFIED


@pytest.mark.parametrize("grouped", [False, True])
def test_mathlib_header_bundle_cannot_be_relabelled_as_version_one(tmp_path, grouped):
    task, manifest = _generate_current(tmp_path, grouped)
    relabel_adapter_version(task, 1)
    relabelled = json.loads((task / "manifest.json").read_text(encoding="utf-8"))
    assert relabelled["task_id"].endswith("-v1") and relabelled["task_id"] != manifest.task_id
    with pytest.raises(
        VerifierError,
        match="pinned generator: Challenge.lean, SolutionHeader.lean.txt$",
    ) as error:
        load_task_bundle(task)
    assert error.value.reason == ReasonCode.TRUSTED_FILE_MODIFIED


@pytest.mark.parametrize("grouped", [False, True])
def test_unsupported_adapter_version_is_rejected_before_reconstruction(tmp_path, grouped):
    task, _manifest = _generate_current(tmp_path, grouped)
    relabel_adapter_version(task, ADAPTER_VERSION + 1)
    with pytest.raises(VerifierError, match="adapter version is not supported") as error:
        load_task_bundle(task)
    assert error.value.reason == ReasonCode.INVALID_MANIFEST
