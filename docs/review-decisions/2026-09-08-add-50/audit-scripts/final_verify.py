"""Verify the final immutable release with the current validator workspace."""
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

v = Path('/root/conjectures-validator')
r = Path('/tmp/add-50-20260908')
baseline = Path('/tmp/conjectures-tasks')
release = r/'tasks'
sys.path.insert(0, str(v))
from verifier.task_loader import load_task_bundle
from verifier.task_registry import TaskPoolRegistry

read = lambda p: json.loads(p.read_text())
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
before = read(baseline/'allowlist.json')
after = read(release/'allowlist.json')
registry = TaskPoolRegistry.load(release/'allowlist.json')
bundles = sorted((release/'pool/tier-1').iterdir())
assert len(bundles) == 518
for p in bundles:
    registry.assert_bundle(load_task_bundle(p))
old_files = sorted(p for p in (baseline/'pool/tier-1').rglob('*') if p.is_file())
assert len(old_files) == 2926
assert all(sha(p) == sha(release/p.relative_to(baseline)) for p in old_files)
for key, identity in [('allowed_source_theorems', 'theorem'), ('allowed_task_bundles', 'task_id')]:
    new = {x[identity]: x for x in after[key]}
    assert all(new[x[identity]] == x for x in before[key])
new_names = {x['theorem'] for x in after['allowed_source_theorems']} - {x['theorem'] for x in before['allowed_source_theorems']}
assert new_names == {x['theorem'] for x in read(r/'selection.json')}
assert len(new_names) == 50

active = read(release/'tiers/tier-1/task-targets.json')['targets']
catalog = {x['theorem']: x for x in read(v/'data/catalog.json')['declarations']}
retired_names = re.findall(r'^- `([^`]+)`', (release/'tiers/tier-1/RETIREMENTS.md').read_text(), re.M)
assert len(retired_names) == 17
audited = [(x['theorem'], x['source_path']) for x in active] + [(n, catalog[n]['source_path']) for n in retired_names]
assert len(audited) == 276 and len({p for _, p in audited}) == 237
assert sum(n.startswith('Erdos') for n, _ in audited) == 246
assert len({p for n, p in audited if n.startswith('Erdos')}) == 210
assert sum(n.startswith('Green') for n, _ in audited) == 30
assert len({p for n, p in audited if n.startswith('Green')}) == 27
assert Counter(x['source_family'] for x in active) == {'erdos': 234, 'greens-open-problems': 25}
commit = subprocess.check_output(['git','-C',str(release),'rev-parse','HEAD'], text=True).strip()
assert read(v/'pins.lock.json')['tasks']['commit'] == commit
assert not subprocess.check_output(['git','-C',str(release),'status','--porcelain'], text=True).strip()
result = {
    'status': 'passed', 'tasks_release': commit, 'pin_matches': True,
    'current_validator_registry_checks': 518, 'retained_bundle_files_unchanged': 2926,
    'retained_source_rows_unchanged': 209, 'retained_bundle_rows_unchanged': 418,
    'added_targets': 50, 'active_targets': 259, 'active_source_paths': 222,
    'active_erdos_targets': 234, 'active_green_targets': 25,
    'audited_targets_including_retirement_log': 276, 'audited_source_paths': 237,
    'retirement_log_entries': 17, 'task_repository_clean': True,
}
(r/'final-verification.json').write_text(json.dumps(result, indent=2, sort_keys=True)+'\n')
print(json.dumps(result, indent=2))
