import hashlib,json,os,subprocess,tempfile
from pathlib import Path
r=Path('/release');fc=Path('/opt/fc-verifier/vendor/formal-conjectures')
pins=json.loads((r/'validator/pins.lock.json').read_text())['formal_conjectures']
patch=r/'tasks/tiers/tier-1/formal-conjectures-audit-fixes.patch'
assert hashlib.sha256(patch.read_bytes()).hexdigest()==pins['patch_sha256']
with tempfile.TemporaryDirectory(prefix='patch-reproduction-') as tmp:
    env=os.environ.copy();env['GIT_INDEX_FILE']=str(Path(tmp)/'index')
    for who in ['AUTHOR','COMMITTER']:
        env['GIT_'+who+'_NAME']='Conjectures Pool Builder'
        env['GIT_'+who+'_EMAIL']='pool@conjectures.io'
        env['GIT_'+who+'_DATE']='2026-08-03T00:00:00Z'
    def git(*args,**kw):return subprocess.check_output(['git','-C',str(fc),*args],env=env,**kw).decode().strip()
    git('read-tree',pins['base_commit'])
    git('apply','--cached',str(patch))
    tree=git('write-tree')
    commit=git('commit-tree',tree,'-p',pins['base_commit'],input=b'fix(ErdosProblems): correct audited candidate statements\n')
    assert commit==pins['commit']
(r/'patch-reproduction.json').write_text(json.dumps({'base':pins['base_commit'],'tree':tree,'reproduced_commit':commit,'patch_sha256':pins['patch_sha256'],'status':'passed'},indent=2)+'\n')
print('Patch reproduced:',commit)
