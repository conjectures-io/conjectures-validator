"""Run local end-to-end checks with the worker's isolation settings. Does not deploy."""
import json,subprocess,sys
from pathlib import Path
O=Path(__file__).resolve().parent;B=O.parents[1];checks=O/'evidence/runtime';checks.mkdir(exist_ok=True);checks.chmod(0o777)
image='erdos-final-validation:20261104'
argv=['docker','run','--rm','--name','erdos20-runtime-20261104','--ipc','none','--network','none','--read-only','--user','10001:10001','--security-opt','no-new-privileges:true','--cap-drop','ALL','--pids-limit','1024','--memory','16g','--memory-swap','16g','--cpus','8','--ulimit','nofile=1024:1024','--ulimit','core=0:0','--tmpfs','/tmp:rw,nosuid,nodev,noexec,size=2g','--tmpfs','/opt/fc-verifier/.work:rw,nosuid,nodev,noexec,size=2g,uid=10001,gid=10001,mode=0700','--volume',str(O)+':/release:ro','--volume','/root/.codex/worktrees/erdos-20-tasks/conjectures-tasks:/tasks:ro','--volume','/root/.codex/worktrees/erdos-20-with-12/conjectures-validator/verifier:/opt/fc-verifier/verifier:ro','--volume',str(checks)+':/checks:rw','--entrypoint','python3',image,'/release/check_verifier.py',*sys.argv[1:]]
(O/'evidence/runtime-profile.json').write_text(json.dumps({'argv':argv,'image_id':subprocess.check_output(['docker','image','inspect','--format','{{.Id}}',image],text=True).strip(),'production_isolation':True,'deployment_performed':False},indent=2)+'\n')
raise SystemExit(subprocess.run(argv).returncode)
