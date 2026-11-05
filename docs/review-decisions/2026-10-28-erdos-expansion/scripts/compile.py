import subprocess,json,os,time
from pathlib import Path
modules=json.loads(Path('/admission/modules.json').read_text())
t=time.monotonic()
with Path('/admission/evidence/production-build.log').open('w') as f:
 p=subprocess.run(['lake','--wfail','build',*modules],cwd='/opt/fc-verifier/vendor/formal-conjectures',env={**os.environ,'LEAN_NUM_THREADS':'12'},stdout=f,stderr=subprocess.STDOUT)
Path('/admission/evidence/production-build-result.json').write_text(json.dumps({'exit_code':p.returncode,'modules':len(modules),'seconds':time.monotonic()-t},indent=2)+'\n')
raise SystemExit(p.returncode)
