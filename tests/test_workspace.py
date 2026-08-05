import json
from pathlib import Path

import pytest

from verifier.errors import VerifierError
from verifier.submission import Submission
from verifier.workspace import (
    WorkspacePaths,
    _local_package_sources,
    _remove_answer_postpone_library,
    build_challenge,
    cleanup_workspace,
    create_workspace,
    package_solution,
)


def test_workspace_is_fresh_and_packages_only_one_submission(tmp_path):
    task = tmp_path / "task"
    task.mkdir()
    (task / "Challenge.lean").write_text("theorem target : True := by sorry\n")
    (task / "comparator-config.json").write_text("{}\n")
    (task / "SolutionHeader.lean.txt").write_text("namespace Bounty\n")
    (task / "SolutionFooter.lean.txt").write_text("\nend Bounty\n")
    project = Path(__file__).resolve().parent.parent
    task_files = {path.name: path.read_bytes() for path in task.iterdir()}
    first = create_workspace(task_files=task_files, project_root=project, workspace_parent=tmp_path / "work")
    second = create_workspace(task_files=task_files, project_root=project, workspace_parent=tmp_path / "work")
    try:
        assert first.root != second.root
        submission = Submission(
            tmp_path / "Main.lean",
            "theorem target : True := by trivial\n",
            b"",
            "sha256:x",
        )
        package_solution(first, task_files, submission)
        assert "theorem target" in first.solution.read_text()
        manifest = json.loads((first.root / "lake-manifest.json").read_text())
        overrides = json.loads((first.root / ".lake" / "package-overrides.json").read_text())
        assert [package["name"] for package in manifest["packages"]] == [
            "formal_conjectures_verifier"
        ]
        assert overrides["packages"]
        assert all(package["type"] == "path" for package in overrides["packages"])
        assert all(Path(package["dir"]).is_absolute() for package in overrides["packages"])
        assert all(
            Path(package["dir"]).is_relative_to(first.root)
            for package in overrides["packages"]
        )
        assert not any("url" in package or "rev" in package for package in overrides["packages"])
        assert all((Path(package["dir"]) / ".lake").is_dir() for package in overrides["packages"])
        assert not (Path(overrides["packages"][0]["dir"]) / ".work").exists()
        workspace_lakefile = (first.root / "lakefile.toml").read_text(encoding="utf-8")
        assert workspace_lakefile.count('weak.google.answer = "always_true"') == 2
        assert 'weak.google.answer = "postpone"' not in workspace_lakefile
        formal_conjectures = next(
            package for package in overrides["packages"]
            if package["name"] == "formal_conjectures"
        )
        formal_config = (
            Path(formal_conjectures["dir"]) / formal_conjectures["configFile"]
        ).read_text(encoding="utf-8")
        assert 'name = "FormalConjectures"' in formal_config
        assert 'name = "FormalConjecturesAnswerPostpone"' not in formal_config
        assert not (Path(formal_conjectures["dir"]) / ".lake" / "config").exists()
    finally:
        cleanup_workspace(first)
        cleanup_workspace(second)


def test_challenge_build_does_not_update_dependencies(tmp_path, monkeypatch):
    calls = []

    def fake_run_process(args, **kwargs):
        calls.append((args, kwargs))
        return object()

    monkeypatch.setattr("verifier.workspace.run_process", fake_run_process)
    paths = WorkspacePaths(
        tmp_path,
        tmp_path / "Challenge.lean",
        tmp_path / "Solution.lean",
        tmp_path / "config.json",
        False,
    )
    result = build_challenge(paths, Path("/trusted/lake"), {"HOME": "/tmp/home"}, 17)
    assert result is not None
    assert calls == [
        (
            ("/trusted/lake", "build", "Challenge"),
            {"cwd": tmp_path, "timeout_seconds": 17, "env": {"HOME": "/tmp/home"}},
        )
    ]


def test_answer_mode_sanitizer_fails_closed_on_unknown_layout(tmp_path):
    config = tmp_path / "lakefile.toml"
    config.write_text('name = "formal_conjectures"\n', encoding="utf-8")

    with pytest.raises(VerifierError, match="unexpected answer-postpone library layout"):
        _remove_answer_postpone_library(config)


def test_local_package_graph_rejects_path_escape(tmp_path):
    (tmp_path / "lake-manifest.json").write_text(
        json.dumps(
            {
                "packages": [
                    {
                        "type": "path",
                        "name": "escaped",
                        "dir": "../outside",
                        "configFile": "lakefile.toml",
                        "manifestFile": "lake-manifest.json",
                    }
                ]
            }
        )
    )

    with pytest.raises(VerifierError, match="escapes the trusted project"):
        _local_package_sources(tmp_path)
