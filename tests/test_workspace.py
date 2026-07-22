from pathlib import Path

from verifier.submission import Submission
from verifier.workspace import cleanup_workspace, create_workspace, package_solution


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
        submission = Submission(tmp_path / "Main.lean", "theorem target : True := by trivial\n", b"", "sha256:x")
        package_solution(first, task_files, submission)
        assert "theorem target" in first.solution.read_text()
    finally:
        cleanup_workspace(first)
        cleanup_workspace(second)
