"""Committed task identity survives a runtime budget increase."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from conftest import catalog, declaration, manifest
from verifier.errors import ReasonCode, VerifierError
from verifier.task_generator import generate_task
from verifier.task_loader import load_task_bundle
from verifier.timeouts import verification_timeout_seconds
from verifier import verification


@pytest.mark.parametrize('declared', [3600, 14400])
def test_existing_and_new_task_bytes_remain_committed(tmp_path, declared):
    item = declaration()
    task = tmp_path / 'task'
    generate_task(
        catalog=catalog(item), declaration=item, mode='formalized', output=task,
        timeout_seconds=declared, validate_target=lambda *_: item.type_hash,
    )
    before = load_task_bundle(task)
    assert before.manifest.timeout_seconds == declared
    assert verification_timeout_seconds(before.manifest.timeout_seconds) == 14400
    after = load_task_bundle(task)
    assert after.sha256 == before.sha256
    assert after.manifest == before.manifest


def test_new_tasks_default_to_four_hours_and_reject_longer_budgets(tmp_path):
    item = declaration()
    args = dict(catalog=catalog(item), declaration=item, mode='formalized',
                validate_target=lambda *_: item.type_hash)
    generate_task(**args, output=tmp_path / 'default')
    assert load_task_bundle(tmp_path / 'default').manifest.timeout_seconds == 14400
    with pytest.raises(VerifierError, match='timeout'):
        generate_task(**args, output=tmp_path / 'excessive', timeout_seconds=14401)


@pytest.mark.parametrize('production,expected_remaining', [(True, 7200), (False, 0)])
def test_verifier_uses_four_hour_deadline_for_old_production_tasks(
    monkeypatch, tmp_path, production, expected_remaining
):
    task_manifest = replace(manifest(), timeout_seconds=3600, production_eligible=production)
    bundle = SimpleNamespace(manifest=task_manifest, sha256='sha256:' + 'a' * 64, files={})
    monkeypatch.setattr(verification, 'load_task_bundle', lambda _: bundle)
    # Advance two hours after verification starts, without waiting or compiling Lean.
    ticks = iter([0])
    monkeypatch.setattr(verification.time, 'monotonic_ns', lambda: next(ticks, 7200 * 10**9))
    monkeypatch.setattr(verification, 'assert_dependency_pins', lambda _: None)
    for name in ('formal_conjectures_pin', 'repository_commit'):
        monkeypatch.setattr(verification, name, lambda _: task_manifest.repository_commit)
    monkeypatch.setattr(verification, 'load_submission', lambda *_: SimpleNamespace(text='', sha256='sha256:' + 'b' * 64))
    monkeypatch.setattr(verification, 'check_submission', lambda *_: SimpleNamespace(valid=True))
    monkeypatch.setattr(verification, 'resolve_tools', lambda *a, **k: SimpleNamespace(sandbox_mode='landrun+seccomp'))
    monkeypatch.setattr(verification, 'production_sandbox_available', lambda *_: True)
    monkeypatch.setattr(verification, 'create_workspace', lambda **k: SimpleNamespace(root=tmp_path, retained=False))
    monkeypatch.setattr(verification, 'cleanup_workspace', lambda _: None)
    monkeypatch.setattr(verification, 'trusted_environment', lambda *_: {})
    monkeypatch.setattr(verification, 'tool_path', lambda *_: tmp_path / 'lake')
    budgets = []
    def build(*args):
        budgets.append(args[-1])
        return SimpleNamespace(timed_out=False, exit_code=1, stdout='', stderr='test stop')
    monkeypatch.setattr(verification, 'build_challenge', build)
    result = verification.verify(
        task_dir=tmp_path, submission_path=tmp_path / 'Main.lean', project_root=tmp_path,
        expected_task_sha256=bundle.sha256, allow_test_task=True,
    )
    assert budgets == ([expected_remaining] if expected_remaining else [])
    assert result.reason_code == (ReasonCode.CHALLENGE_BUILD_FAILED if production else ReasonCode.TIMEOUT)
    assert result.task_timeout_seconds == 3600
    assert result.effective_timeout_seconds == (14400 if production else 3600)
