import json,subprocess,os,time
from pathlib import Path
O=Path('/admission');S=Path('/opt/fc-verifier/vendor/formal-conjectures');T=Path('/candidate-tasks');ROOT=S.parents[1]
a=json.loads((T/'tiers/tier-1/selection-audit.json').read_text())
paths=sorted({x['source_path'] for x in a['selected']})
(O/'retained-paths.json').write_text(json.dumps(paths,indent=2)+'\n')
modules=['.'.join('«'+x+'»' if x.isdigit() else x for x in p[:-5].split('/')) for p in paths]
start=time.monotonic()
with (O/'evidence/retained-build.log').open('w') as f:
 r=subprocess.run(['lake','--wfail','build',*modules],cwd=S,env={**os.environ,'LEAN_NUM_THREADS':'12'},stdout=f,stderr=subprocess.STDOUT)
(O/'evidence/retained-build-result.json').write_text(json.dumps({'exit_code':r.returncode,'modules':len(modules),'seconds':time.monotonic()-start},indent=2)+'\n')
if r.returncode:raise SystemExit(r.returncode)
with (O/'evidence/tools-build.log').open('w') as f:
 r=subprocess.run(['lake','build','catalog_extractor','dependency_extractor','task_inspector','TestFixtures.CatalogSafety','TestFixtures.Counterexample'],cwd=ROOT,stdout=f,stderr=subprocess.STDOUT)
raise SystemExit(r.returncode)
