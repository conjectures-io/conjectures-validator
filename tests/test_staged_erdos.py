"""The staged importer must preserve admission boundaries and answer holes."""

from pathlib import Path

from scripts.staged_erdos.prepare import blockers, problem_number


def declaration(**changes):
    return {
        "theorem": "Erdos5.erdos_5",
        "type_hash": "sha256:example",
        "classification": "DIRECT_PROP",
        "question_kind": "yes_no",
        "depends_on_sorry": True,
        "contains_sorry_in_type": False,
        "type_axioms": ["propext", "Classical.choice", "Quot.sound"],
        **changes,
    }


def test_question_metadata_does_not_turn_value_placeholder_into_proof_task():
    d = declaration(question_kind="value")
    assert "value_answer_adapter_required" in blockers(d, set(), set(), set())


def test_indirect_type_hole_is_blocked_even_without_literal_sorry_in_type():
    d = declaration(type_axioms=["sorryAx"], contains_sorry_in_type=False)
    assert "type_depends_on_unfilled_definition" in blockers(d, set(), set(), set())


def test_upstream_open_label_does_not_remove_existing_hold_or_retirement():
    d = declaration()
    assert set(blockers(d, {d["theorem"]}, {d["theorem"]}, set())) == {
        "existing_hold_requires_review",
        "existing_retirement_requires_review",
    }


def test_proved_type_collision_is_blocked():
    d = declaration()
    assert blockers(d, set(), set(), {d["type_hash"]}) == ["compiled_proved_type_collision"]


def test_direct_question_without_holes_is_candidate():
    assert blockers(declaration(), set(), set(), set()) == []


def test_number_requires_exact_erdos_source_path():
    assert problem_number("FormalConjectures/ErdosProblems/328.lean") == 328
    for path in (
        "FormalConjectures/GreensOpenProblems/328.lean",
        "FormalConjectures/ErdosProblems/../328.lean",
        "FormalConjectures/ErdosProblems/328.txt",
        "/FormalConjectures/ErdosProblems/328.lean",
    ):
        assert problem_number(path) is None


def test_reused_workspace_rebuilds_and_rejects_a_mismatched_second_target(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import pytest

    import verifier.environment as environment
    import verifier.workspace as workspace
    from scripts.staged_erdos import validation
    from scripts.staged_erdos.validation import StagedValidator

    root = tmp_path / "workspace"
    root.mkdir()
    paths = SimpleNamespace(root=root)
    created, built = [], []

    def create(**kwargs):
        created.append(True)
        for name, content in kwargs["task_files"].items():
            (root / name).write_bytes(content)
        return paths

    stale = root / ".lake/build/lib/lean/Challenge.olean"

    def build(*args):
        assert not stale.exists()
        built.append((root / "Challenge.lean").read_text())
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_bytes(b"old compiled target")
        return SimpleNamespace(timed_out=False, exit_code=0)

    inspections = [True, False]

    def inspect(**kwargs):
        return {
            "source_hash": "source",
            "target_hash": "target",
            "matches": inspections.pop(0),
            "source_category": "research open",
            "source_declaration_kind": "theorem",
            "source_depends_on_sorry": True,
            "source_has_formal_proof": False,
            "target_contains_sorry": False,
            "source_axioms": ("sorryAx",),
        }

    monkeypatch.setattr(environment, "tool_path", lambda *args: Path("/test/lake"))
    monkeypatch.setattr(environment, "trusted_environment", lambda *args: {})
    monkeypatch.setattr(workspace, "create_workspace", create)
    monkeypatch.setattr(validation, "compile_challenge", build)
    monkeypatch.setattr(workspace, "inspect_generated_target", inspect)
    validator = StagedValidator(tmp_path)
    task = tmp_path / "task"
    task.mkdir()
    (task / "comparator-config.json").write_text("{}")
    (task / "Challenge.lean").write_text("first target")
    source = SimpleNamespace(
        type_hash="source",
        module="Example",
        theorem="Example.target",
        classification=SimpleNamespace(value="DIRECT_PROP"),
        transitive_axioms=("sorryAx",),
    )
    assert validator(task, source, None, "formalized") == "target"
    (task / "Challenge.lean").write_text("different target")
    with pytest.raises(ValueError, match="differs from catalog"):
        validator(task, source, None, "formalized")
    assert len(created) == 1
    assert built == ["first target", "different target"]
