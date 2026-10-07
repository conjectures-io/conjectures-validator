"""Version-2 identities: what rotates a task ID, what never does, and what fails closed."""

from __future__ import annotations

import json
import os

import pytest

from version_fixtures import (
    COMMIT_A,
    MATHLIB_COMMIT,
    declaration,
    definition,
    index,
    node_type,
    standard,
)
from verifier.errors import ReasonCode, VerifierError
from verifier.models import Catalog
from verifier.task_generator import plan_task_version
from verifier.task_versions import (
    EnvironmentIdentity,
    check_inspected_closure,
    parse_closure_report,
    parse_task_version_document,
    read_tree_file,
)

PAIR = "FormalConjectures.Problems.Pair"
SOLO = "FormalConjectures.Problems.Solo"


def declarations():
    return [declaration("Pair.first", PAIR), declaration("Pair.second", PAIR), declaration("Solo.third", SOLO)]


def ids(env, decls=None, environment=None):
    decls = decls or declarations()
    built = index(env, decls, environment=environment)
    catalog = Catalog(1, COMMIT_A, env.toolchain, MATHLIB_COMMIT, "test", 0, tuple(decls))
    return {
        (item.theorem, mode): plan_task_version(
            catalog=catalog,
            declaration=item,
            mode=mode,
            environment=built.environment,
            dependency=built.dependencies[item.theorem],
        ).task_id
        for item in decls
        for mode in ("formalized", "counterexample")
    }, built


def test_identical_inputs_give_identical_task_ids(tmp_path):
    first, _ = ids(standard(tmp_path / "a"))
    second, _ = ids(standard(tmp_path / "b"))
    assert first == second
    assert len(set(first.values())) == 6


def test_a_sibling_edit_in_the_same_problem_module_rotates_only_the_sibling(tmp_path):
    env = standard(tmp_path)
    before, before_index = ids(env)
    # Pair.second's statement changes. Pair.first lives in the same module and shares a
    # definition with it, but reaches nothing that changed.
    env.write_module(PAIR, "-- edited for second only\n")
    env.statements["Pair.second"][1]["Pair.second"] = node_type("Pair.secondTypeEdited")
    after, after_index = ids(env)
    assert after[("Pair.first", "formalized")] == before[("Pair.first", "formalized")]
    assert after[("Pair.first", "counterexample")] == before[("Pair.first", "counterexample")]
    assert after[("Solo.third", "formalized")] == before[("Solo.third", "formalized")]
    assert after[("Pair.second", "formalized")] != before[("Pair.second", "formalized")]
    assert after[("Pair.second", "counterexample")] != before[("Pair.second", "counterexample")]
    # The module text did change: that is current build provenance, not task identity.
    assert after_index.provenance["Pair.first"].sha256 != before_index.provenance["Pair.first"].sha256


def test_a_named_definition_body_change_rotates_exactly_its_transitive_users(tmp_path):
    env = standard(tmp_path)
    before, _ = ids(env)
    # Same printed statements and type hashes; only the shared definition's body differs.
    env.statements["Pair.first"][1]["Shared.helper"] = definition("helper.v2")
    env.statements["Pair.second"][1]["Shared.helper"] = definition("helper.v2")
    after, _ = ids(env)
    changed = {key for key in before if before[key] != after[key]}
    assert changed == {
        ("Pair.first", "formalized"), ("Pair.first", "counterexample"),
        ("Pair.second", "formalized"), ("Pair.second", "counterexample"),
    }


def test_a_target_specific_definition_change_rotates_only_that_target(tmp_path):
    env = standard(tmp_path)
    before, _ = ids(env)
    env.statements["Pair.first"][1]["Pair.onlyFirst"] = definition("only.v2")
    after, _ = ids(env)
    assert {key[0] for key in before if before[key] != after[key]} == {"Pair.first"}


def test_no_statement_hash_shortcut_the_type_hash_alone_does_not_decide(tmp_path):
    env = standard(tmp_path)
    decls = declarations()
    before, _ = ids(env, decls)
    env.statements["Pair.first"][1]["Pair.onlyFirst"] = definition("only.v3")
    after, _ = ids(env, decls)
    # The cataloged type hash of Pair.first is byte-identical in both runs.
    assert decls[0].type_hash == declaration("Pair.first", PAIR).type_hash
    assert before[("Pair.first", "formalized")] != after[("Pair.first", "formalized")]


def test_an_import_graph_change_rotates_its_module_and_nothing_else(tmp_path):
    env = standard(tmp_path)
    before, _ = ids(env)
    env.imports[SOLO] = ["FormalConjecturesUtil", "FormalConjectures.Shared"]
    after, _ = ids(env)
    assert {key[0] for key in before if before[key] != after[key]} == {"Solo.third"}


def test_a_transitive_shared_import_text_change_is_provenance_not_identity(tmp_path):
    env = standard(tmp_path)
    before, before_index = ids(env)
    # A comment edit in a module every target imports: no declaration any statement reaches
    # changed, and the import graph is the same.
    env.write_module("FormalConjecturesUtil", "-- util, reformatted\n")
    after, after_index = ids(env)
    assert after == before
    assert all(
        after_index.provenance[theorem].sha256 != before_index.provenance[theorem].sha256
        for theorem in ("Pair.first", "Pair.second", "Solo.third")
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("lean_toolchain", "leanprover/lean4:v4.36.0"),
        ("lean_commit", "f" * 40),
        ("mathlib_commit", "d" * 40),
        ("lake_packages", (("mathlib", "d" * 40),)),
        ("comparator_commit", "9" * 40),
        ("lean4export_commit", "8" * 40),
        ("task_support_sha256", "sha256:" + "2" * 64),
        ("workspace_template_sha256", "sha256:" + "3" * 64),
        ("generator_version", "fc-task-generator-v2.1"),
        ("verification_policy", {"version": "fc-verification-policy-v2"}),
    ],
)
def test_every_environment_component_rotates_every_task_id(tmp_path, field, value):
    env = standard(tmp_path)
    before, _ = ids(env)
    changed = env.environment(**{field: value})
    after_env = changed
    if field == "lean_commit":
        env.lean_commit = value
    after, _ = ids(env, environment=after_env)
    assert all(before[key] != after[key] for key in before)


def test_the_task_version_file_round_trips_and_rejects_tampering(tmp_path):
    env = standard(tmp_path)
    built = index(env, declarations())
    from verifier.task_versions import TaskVersionDocument
    from verifier.hashing import pretty_json

    document = TaskVersionDocument(built.environment, built.dependencies["Pair.first"], None)
    content = pretty_json(document.to_dict()).encode()
    assert parse_task_version_document(content) == document
    value = json.loads(content)
    value["dependency"]["local_constants"][0]["sha256"] = "sha256:" + "0" * 64
    with pytest.raises(VerifierError) as raised:
        parse_task_version_document(pretty_json(value).encode())
    assert raised.value.reason == ReasonCode.TRUSTED_FILE_MODIFIED


def _report(env, **mutate):
    raw = json.loads(env.report([(PAIR, "Pair.first")]))
    for key, function in mutate.items():
        function(raw)
    return json.dumps(raw).encode()


def _parse(env, content):
    return parse_closure_report(
        content,
        expected_roots=env.trees.local_roots(),
        expected_lean_commit=env.lean_commit,
        expected_targets=[(PAIR, "Pair.first")],
    )


def test_a_closure_from_another_lean_is_an_environment_mismatch(tmp_path):
    env = standard(tmp_path)
    with pytest.raises(VerifierError) as raised:
        _parse(env, _report(env, lean=lambda raw: raw.update(lean_githash="0" * 40)))
    assert raised.value.reason == ReasonCode.ENVIRONMENT_MISMATCH


def test_a_closure_for_other_targets_or_with_duplicate_keys_is_refused(tmp_path):
    env = standard(tmp_path)
    content = env.report([(PAIR, "Pair.second")])
    with pytest.raises(VerifierError, match="exactly the requested targets"):
        _parse(env, content)
    with pytest.raises(VerifierError, match="strict JSON"):
        _parse(env, b'{"schema_version": 1, "schema_version": 1}')


def test_a_caller_asserted_closure_cannot_omit_the_statement_itself(tmp_path):
    env = standard(tmp_path)
    content = _report(
        env,
        drop=lambda raw: raw["targets"][0].update(
            local_constants=[item for item in raw["targets"][0]["local_constants"] if item["name"] != ["Pair", "first"]]
        ),
    )
    report = _parse(env, content)
    from verifier.task_versions import dependency_identity

    with pytest.raises(VerifierError, match="does not contain Pair.first"):
        dependency_identity(report, env.trees, report.targets[0], statement_type_sha256=declaration("Pair.first", PAIR).type_hash)


def test_a_poisoned_or_misplaced_olean_fails_closed(tmp_path):
    env = standard(tmp_path)
    elsewhere = tmp_path / "poisoned" / "Shared.olean"
    elsewhere.parent.mkdir()
    elsewhere.write_bytes(b"")

    def move(raw):
        for row in raw["modules"]:
            if row["module"] == ["FormalConjectures", "Shared"]:
                row["olean"] = str(elsewhere)

    report = _parse(env, _report(env, move=move))
    from verifier.task_versions import dependency_identity

    with pytest.raises(VerifierError, match="exactly one local build"):
        dependency_identity(report, env.trees, report.targets[0], statement_type_sha256=declaration("Pair.first", PAIR).type_hash)


def test_an_external_module_from_an_unpinned_location_fails_closed(tmp_path):
    env = standard(tmp_path)
    rogue = tmp_path / "rogue" / "Mathlib.olean"
    rogue.parent.mkdir()
    rogue.write_bytes(b"")

    def move(raw):
        for row in raw["modules"]:
            if row["module"] == ["Mathlib"]:
                row["olean"] = str(rogue)

    report = _parse(env, _report(env, move=move))
    from verifier.task_versions import dependency_identity

    with pytest.raises(VerifierError, match="toolchain or a pinned package"):
        dependency_identity(report, env.trees, report.targets[0], statement_type_sha256=declaration("Pair.first", PAIR).type_hash)


def test_a_local_constant_outside_the_import_closure_fails_closed(tmp_path):
    env = standard(tmp_path)

    def foreign(raw):
        raw["targets"][0]["local_constants"].append(
            {"content": definition("x"), "module": ["FormalConjectures", "Problems", "Solo"], "name": ["Solo", "leak"]}
        )

    report = _parse(env, _report(env, foreign=foreign))
    from verifier.task_versions import dependency_identity

    with pytest.raises(VerifierError, match="local import closure"):
        dependency_identity(report, env.trees, report.targets[0], statement_type_sha256=declaration("Pair.first", PAIR).type_hash)


def test_a_malformed_expression_dag_fails_closed(tmp_path):
    env = standard(tmp_path)

    def forward(raw):
        raw["targets"][0]["local_constants"][0]["content"]["nodes"] = [["A", 1, 1], ["S", "z"]]

    with pytest.raises(VerifierError, match="post-order"):
        _parse(env, _report(env, forward=forward))


def test_symlinked_trusted_sources_are_never_followed(tmp_path):
    env = standard(tmp_path)
    target = tmp_path / "outside.lean"
    target.write_text("-- outside the trusted tree\n")
    path = env.source / "FormalConjectures" / "Shared.lean"
    path.unlink()
    os.symlink(target, path)
    with pytest.raises(VerifierError):
        read_tree_file(env.source, ("FormalConjectures", "Shared.lean"), 1024)
    directory = env.source / "FormalConjectures" / "Problems"
    moved = tmp_path / "moved-problems"
    directory.rename(moved)
    os.symlink(moved, directory)
    with pytest.raises(VerifierError):
        read_tree_file(env.source, ("FormalConjectures", "Problems", "Solo.lean"), 1024)
    for unsafe in (("..", "x.lean"), ("a", "", "b"), ("/etc", "passwd")):
        with pytest.raises(VerifierError):
            read_tree_file(env.source, unsafe, 1024)


def test_inspected_closure_must_match_the_committed_identity_and_provenance(tmp_path):
    env = standard(tmp_path)
    decl = declaration("Pair.first", PAIR)
    built = index(env, [decl])
    content = env.report([(PAIR, "Pair.first")], canonical=False)
    checked = check_inspected_closure(
        content,
        trees=env.trees,
        environment=built.environment,
        declaration=decl,
        expected_dependency_sha256=built.dependencies["Pair.first"].sha256,
        expected_build_provenance_sha256=built.provenance["Pair.first"].sha256,
    )
    assert checked.dependency.sha256 == built.dependencies["Pair.first"].sha256
    with pytest.raises(VerifierError) as dependency:
        check_inspected_closure(
            content, trees=env.trees, environment=built.environment, declaration=decl,
            expected_dependency_sha256="sha256:" + "0" * 64, expected_build_provenance_sha256=None,
        )
    assert dependency.value.reason == ReasonCode.DEPENDENCY_IDENTITY_MISMATCH
    with pytest.raises(VerifierError) as provenance:
        check_inspected_closure(
            content, trees=env.trees, environment=built.environment, declaration=decl,
            expected_dependency_sha256=built.dependencies["Pair.first"].sha256,
            expected_build_provenance_sha256="sha256:" + "0" * 64,
        )
    assert provenance.value.reason == ReasonCode.ENVIRONMENT_MISMATCH
    with pytest.raises(VerifierError, match="did not report"):
        check_inspected_closure(
            None, trees=env.trees, environment=built.environment, declaration=decl,
            expected_dependency_sha256=built.dependencies["Pair.first"].sha256,
            expected_build_provenance_sha256=None,
        )


def test_environment_identity_parsing_is_strict(tmp_path):
    env = standard(tmp_path)
    value = env.environment().to_dict()
    assert EnvironmentIdentity.from_dict(value).sha256 == env.environment().sha256
    for broken in (
        {**value, "extra": 1},
        {**value, "lean_commit": "short"},
        {**value, "lake_packages": [["b", "1" * 40], ["a", "2" * 40]]},
        {**value, "schema_version": 2},
    ):
        with pytest.raises(VerifierError):
            EnvironmentIdentity.from_dict(broken)


# --- coherence of the compiled environment ---------------------------------------------------


class FakeLake:
    """Stands in for `lake build --no-build` and `lake env dependency_extractor`."""

    def __init__(self, env, *, stale=False, during=None):
        self.env = env
        self.stale = stale
        self.during = during
        self.commands = []

    def __call__(self, args, *, cwd, timeout_seconds, env):
        from verifier.models import ProcessResult

        self.commands.append(tuple(args))
        if "--no-build" in args:
            code = 1 if self.stale else 0
            return ProcessResult(tuple(args), code, "", "Pair needs rebuilding" if self.stale else "", 1)
        request = json.loads(Path(args[-2]).read_text())
        targets = [(item["module"], item["theorem"]) for item in request["targets"]]
        Path(args[-1]).write_bytes(self.env.report(targets))
        if self.during is not None:
            self.during(self.env)
        return ProcessResult(tuple(args), 0, "", "", 1)


def derive(env, lake):
    from verifier.task_versions import derive_dependency_index

    return derive_dependency_index(
        project_root=env.project,
        declarations=[declaration("Pair.first", PAIR)],
        environment=env.environment(),
        command_runner=lake,
        repository_commit=lambda path: COMMIT_A,
        check_pins=False,
    )


from pathlib import Path  # noqa: E402


def test_derivation_requires_lake_to_report_the_build_up_to_date(tmp_path):
    env = standard(tmp_path)
    lake = FakeLake(env)
    built = derive(env, lake)
    assert "Pair.first" in built.dependencies
    assert lake.commands[0][1:3] == ("build", "--no-build")
    assert PAIR in lake.commands[0]


def test_a_source_edited_after_compiling_stops_derivation_before_extraction(tmp_path):
    env = standard(tmp_path)
    # The named definition's body was edited in source but the .olean is stale: Lake's own
    # trace check sees it, so no old compiled closure is ever paired with the new text.
    env.sources[PAIR] = "-- body edited, not rebuilt\n"
    lake = FakeLake(env, stale=True)
    with pytest.raises(VerifierError) as raised:
        derive(env, lake)
    assert raised.value.reason == ReasonCode.ENVIRONMENT_MISMATCH
    assert "not up to date" in str(raised.value)
    assert all("--no-build" in command for command in lake.commands)


def test_a_source_or_artifact_changing_during_extraction_fails_closed(tmp_path):
    env = standard(tmp_path)

    def edit_source(env):
        (env.source / "FormalConjectures" / "Shared.lean").write_text("-- edited mid-run\n")

    with pytest.raises(VerifierError, match="changed during derivation"):
        derive(env, FakeLake(env, during=edit_source))

    env2 = standard(tmp_path / "second")

    def swap_olean(env):
        olean = env.source / ".lake/build/lib/lean/FormalConjectures/Shared.olean"
        olean.write_bytes(b"swapped artifact")

    with pytest.raises(VerifierError, match="changed during derivation"):
        derive(env2, FakeLake(env2, during=swap_olean))


def test_build_provenance_binds_compiled_artifacts_so_a_tampered_olean_is_caught(tmp_path):
    env = standard(tmp_path)
    decl = declaration("Pair.first", PAIR)
    published = index(env, [decl])
    # Later, the instance's cached artifact is altered without touching any source text.
    (env.source / ".lake/build/lib/lean/FormalConjectures/Shared.olean").write_bytes(b"tampered")
    with pytest.raises(VerifierError) as raised:
        check_inspected_closure(
            env.report([(PAIR, "Pair.first")], canonical=False),
            trees=env.trees,
            environment=published.environment,
            declaration=decl,
            expected_dependency_sha256=published.dependencies["Pair.first"].sha256,
            expected_build_provenance_sha256=published.provenance["Pair.first"].sha256,
        )
    assert raised.value.reason == ReasonCode.ENVIRONMENT_MISMATCH


def test_an_unusable_work_directory_is_an_identity_failure_not_a_crash(tmp_path):
    """Combined-release integration finding: `doctor` run as uid 0 with capabilities dropped
    cannot create its identity home in the 10001-owned work tmpfs. That OSError escaped the
    identity derivation and crashed the whole doctor (INTERNAL_ERROR, no report), hiding the
    `unprivileged: false` the release's root-user sandbox control checks. It must surface as an
    environment-identity failure, which the doctor reports in `task_versions.error`."""
    from verifier.task_versions import _lean_githash

    project = tmp_path / "project"
    project.mkdir()
    (project / ".work").write_text("not a directory")  # unwritable for any uid, root included
    with pytest.raises(VerifierError, match="cannot ask the pinned Lean for its commit") as raised:
        _lean_githash(project)
    assert raised.value.reason == ReasonCode.ENVIRONMENT_MISMATCH
