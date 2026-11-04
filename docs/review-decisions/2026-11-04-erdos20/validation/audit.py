import json
import subprocess
import sys
from pathlib import Path

V=Path('/root/.codex/worktrees/erdos-20-with-12/conjectures-validator')
T=Path('/root/.codex/worktrees/erdos-20-tasks/conjectures-tasks')
sys.path.insert(0,str(V))
from verifier.task_loader import load_task_bundle
from verifier.version_registry import VersionRegistry, assert_matches_allowlist
from verifier.task_registry import TaskPoolRegistry

registry=VersionRegistry.load(T/'task-versions.json')
assert_matches_allowlist(registry,T/'allowlist.json')
pool=TaskPoolRegistry.load(T/'allowlist.json')
doc=json.loads((T/'task-versions.json').read_text())
active=[r for r in doc['versions'] if r['state']=='active']
assert len(active)==618
names={r['theorems'][0] for r in active}
assert len(names)==309
assert 'Erdos12.erdos_12.parts.iii' in names
assert 'Erdos456.erdos_456.parts.iii' not in names
assert not {'Erdos12.erdos_12.parts.i','Erdos12.erdos_12.parts.ii'} & names
for r in active:
    bundle=load_task_bundle(T/r['location'])
    assert bundle.sha256==r['task_bundle_sha256']
    pool.assert_bundle(bundle)
changed=subprocess.check_output(['git','diff','--name-only','280d71ce','--','versions','history','pool'],cwd=T,text=True)
assert not changed,changed
before=json.loads(subprocess.check_output(['git','show','280d71ce:task-versions.json'],cwd=T))
now={r['task_id']:r for r in doc['versions']}
for r in before['versions']:
    n=now[r['task_id']]
    for k in ['task_bundle_sha256','location','theorems','mode']:
        assert n[k]==r[k],(r['task_id'],k)
    for admission in r['admissions']:
        assert admission in n['admissions']
result=dict(passed=True,active_targets=309,active_bundles=618,historical_versions_preserved=len(before['versions']))
(Path(__file__).parent/'evidence/release-audit.json').write_text(json.dumps(result,indent=2)+'\n')
print(result)
