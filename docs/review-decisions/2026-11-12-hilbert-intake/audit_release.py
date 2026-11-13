"""Check every active bundle and preserve all baseline versions and retirements."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TASKS = ROOT / 'output/hilbert-intake-20261112/tasks'
BASE = '4a10571b0e196007207b02b585fad5e46f16abd6'
NAME = 'GRH.generalized_riemann_hypothesis'
sys.path.insert(0, str(ROOT))
from verifier.task_loader import load_task_bundle
from verifier.task_registry import TaskPoolRegistry
from verifier.version_registry import VersionRegistry, assert_matches_allowlist


def baseline(path):
    return json.loads(subprocess.check_output(['git', 'show', BASE + ':' + path], cwd=TASKS))


registry = VersionRegistry.load(TASKS / 'task-versions.json')
assert_matches_allowlist(registry, TASKS / 'allowlist.json')
pool = TaskPoolRegistry.load(TASKS / 'allowlist.json')
doc = json.loads((TASKS / 'task-versions.json').read_text())
active = [r for r in doc['versions'] if r['state'] == 'active']
assert len(active) == 614
old = baseline('task-versions.json')
old_active = {r['theorems'][0] for r in old['versions'] if r['state'] == 'active'}
assert {r['theorems'][0] for r in active} == old_active | {NAME}
for row in active:
    bundle = load_task_bundle(TASKS / row['location'])
    assert bundle.sha256 == row['task_bundle_sha256']
    pool.assert_bundle(bundle)
now = {r['task_id']: r for r in doc['versions']}
for row in old['versions']:
    assert now[row['task_id']] == row, row['task_id']
changed = subprocess.check_output(
    ['git', 'diff', '--name-only', '--diff-filter=CDMRTUXB', BASE, '--', 'versions', 'history', 'pool'],
    cwd=TASKS, text=True)
assert not changed, changed
path = 'tiers/tier-1/retired-source-theorems.json'
before = baseline(path)
after = json.loads((TASKS / path).read_text())
digest = 'sha256:19f4c1959e1bdd118d84801a2fbdc6d73ba2a83678e3c90744dba7ee919bb57c'
assert set(before['source_theorems']) - set(after['source_theorems']) == {NAME}
assert set(before['source_type_sha256']) - set(after['source_type_sha256']) == {digest}
assert set(after['source_theorems']) <= set(before['source_theorems'])
assert set(after['source_type_sha256']) <= set(before['source_type_sha256'])
result = dict(passed=True, active_targets=307, active_bundles=614,
              existing_versions_unchanged=len(old['versions']),
              existing_active_bundles_unchanged=612,
              removed_legacy_retirements=[NAME], deployment_performed=False)
(Path(__file__).parent / 'evidence/release-audit.json').write_text(json.dumps(result, indent=2) + '\n')
print(result)
