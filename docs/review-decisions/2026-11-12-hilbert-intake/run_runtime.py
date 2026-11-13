"""Reproduce the local admission controls; never install or activate a worker."""
import json
import subprocess
from pathlib import Path

AUDIT = Path(__file__).resolve().parent
ROOT = AUDIT.parents[2]
OUT = ROOT / 'output/hilbert-intake-20261112'
CHECKS = OUT / 'runtime'
CHECKS.mkdir(exist_ok=True)
CHECKS.chmod(0o777)
IMAGE = 'formal-conjectures-verifier:erdos20-20261105'
argv = [
    'docker', 'run', '--rm', '--name', 'hilbert-grh-runtime-20261112',
    '--ipc', 'none', '--network', 'none', '--read-only', '--user', '10001:10001',
    '--security-opt', 'no-new-privileges:true', '--cap-drop', 'ALL',
    '--pids-limit', '1024', '--memory', '16g', '--memory-swap', '16g', '--cpus', '8',
    '--ulimit', 'nofile=1024:1024', '--ulimit', 'core=0:0',
    '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=2g',
    '--tmpfs', '/opt/fc-verifier/.work:rw,nosuid,nodev,noexec,size=2g,uid=10001,gid=10001,mode=0700',
    '--volume', f'{OUT}:/release:ro', '--volume', f'{OUT / "tasks"}:/tasks:ro',
    '--volume', f'{CHECKS}:/checks', '--volume', f'{AUDIT}:/audit:ro',
    '--volume', f'{ROOT / "verifier"}:/opt/fc-verifier/verifier:ro',
    '--entrypoint', 'python3', IMAGE, '/audit/check_verifier.py',
]
(OUT / 'runtime-profile.json').write_text(json.dumps({
    'argv': argv,
    'image_id': subprocess.check_output(
        ['docker', 'image', 'inspect', '--format', '{{.Id}}', IMAGE], text=True).strip(),
    'production_isolation': True, 'deployment_performed': False,
}, indent=2) + '\n')
if __name__ == '__main__':
    raise SystemExit(subprocess.run(argv).returncode)
