"""Reconstruct the pinned source from the production base and candidate task patch."""
import hashlib,json,os,subprocess
from pathlib import Path
O=Path(__file__).resolve().parent;B=O.parents[1];V=B/'conjectures-validator';S=B/'formal-conjectures-production';D=O/'source-reproduction';T=B/'conjectures-tasks';pin=json.loads((V/'pins.lock.json').read_text())['formal_conjectures'];patch=T/'tiers/tier-1/formal-conjectures-audit-fixes.patch'
assert hashlib.sha256(patch.read_bytes()).hexdigest()==pin['patch_sha256']
if not D.exists():subprocess.run(['git','clone','--shared','--no-checkout',str(S),str(D)],check=True,capture_output=True)
def run(*a,**kw):return subprocess.check_output(['git',*a],cwd=D,**kw)
run('checkout','--detach',pin['base_commit']);run('apply','--check',str(patch));run('apply','--index',str(patch));tree=run('write-tree',text=True).strip()
env={**os.environ,'GIT_AUTHOR_NAME':'Conjectures Pool Builder','GIT_AUTHOR_EMAIL':'pool@conjectures.io','GIT_AUTHOR_DATE':'2026-08-03T00:00:00Z','GIT_COMMITTER_NAME':'Conjectures Pool Builder','GIT_COMMITTER_EMAIL':'pool@conjectures.io','GIT_COMMITTER_DATE':'2026-08-03T00:00:00Z'}
commit=run('commit-tree',tree,'-p',pin['base_commit'],input=b'fix(ErdosProblems): correct audited candidate statements\n',env=env).decode().strip();assert commit==pin['commit'],commit
(O/'evidence/source-reproduction.json').write_text(json.dumps({'passed':True,'commit':commit,'tree':tree,'base':pin['base_commit'],'patch_sha256':pin['patch_sha256'],'method':'Clean base checkout; git apply --check and --index; same deterministic commit-tree recipe as scripts/pin_dependencies.sh.'},indent=2)+'\n');print('source commit reproduced',commit)
