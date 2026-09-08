import pathlib,json,sys,subprocess
sys.path.insert(0,'/root/conjectures-validator')
from verifier.environment import tool_path,trusted_environment
r=pathlib.Path('/tmp/add-50-20260908');p=pathlib.Path('/tmp/conjectures-validator-a8d559d-erdos564')
rr=json.loads((r/'selection.json').read_text())
(r/'type-input-final.json').write_text(json.dumps({'modules':sorted({x['module'] for x in rr}),'theorems':[x['theorem'] for x in rr]}))
h=r/'lean-home';(h/'.tmp').mkdir(parents=True,exist_ok=True)
cmd=[str(tool_path(p,'lake')),'env','lean','--run',str(r/'TypeDependenciesStrict.lean'),str(r/'type-input-final.json')]
x=subprocess.run(cmd,cwd=p,env=trusted_environment(p,h),capture_output=True,text=True,timeout=300)
(r/'type-dependencies-strict.log').write_text(x.stdout+'\n'+x.stderr)
print(x.returncode,x.stdout[:1000],x.stderr[:1000])
if x.returncode==0:
 data=json.loads(x.stdout);(r/'type-dependencies-strict.json').write_text(json.dumps(data,indent=2));print('checked',len(data),'tainted',[(x['theorem'],x['dependencies_with_nonstandard_axioms']) for x in data if x['dependencies_with_nonstandard_axioms']])
