from pathlib import Path
from datetime import datetime, timezone
import hashlib,json,shutil,subprocess,tarfile
R=Path('/tmp/pool-release-20260908');V=R/'validator';D=V/'docs/review-decisions/2026-09-08-production-release'
def read(p): return json.loads(p.read_text())
def write(p,v): p.write_text(json.dumps(v,indent=2,sort_keys=True)+'\n')
def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
commit=subprocess.check_output(['git','-C',str(R/'tasks'),'rev-parse','HEAD'],text=True).strip()
pins=json.loads(subprocess.check_output(['git','-C',str(V),'show','b2f0c3306e63e5861d2950223d154c3000ed9cf8:pins.lock.json'],text=True))
pins['tasks']['commit']=commit
(V/'pins.lock.json').write_text(json.dumps(pins,indent=2)+'\n')
generation=read(R/'generation.json')+read(R/'generation-last.json')
assert len(generation)==114 and all(x['status']=='passed' for x in generation)
write(D/'generation.json',generation)
battery=read(R/'attack-audit/sweep-summary.json');attempts=battery['attempts']
assert len(attempts)==672 and not any(s['unmapped_errors'] for s in battery['shards'])
hits=[x for x in attempts if x['compiled']]
assert len(hits)==32 and all(x['axioms_report'] and all('sorryAx' in a for a in x['axioms_report']) for x in hits)
write(D/'replacement-tactic-summary.json',dict(attempts=672,theorems=8,bundles=16,tactics_per_bundle=42,failed_elaborations=640,compile_hits=32,compile_hits_with_sorryAx=32,axiom_clean_hits=0,unmapped_errors=0,duplicate_catalog_types=battery['duplicate_target_hashes']))
with tarfile.open(D/'replacement-tactic-evidence.tar.gz','w:gz') as tar:
 for path in ['sweep-summary.json','raw','sources']:
  tar.add(R/'attack-audit'/path,arcname=path)
for name in ['generation.log','generation-last.log','tests-pool.log','tests-pool.xml','tests-bundle.log','tests-bundle.xml','lint.log','battery.log']:
 shutil.copy2(R/name,D/name)
for p in (D/'audit-scripts').iterdir():
 if (R/p.name).is_file(): shutil.copy2(R/p.name,p)
shutil.copy2(R/'seal_evidence.py',D/'audit-scripts/seal_evidence.py')
p=D/'REVIEW.md';s=p.read_text().replace('The full SQL migration set matches the ORM across **811 schema objects**. Final compilation,\ntactic-screen, release-admission, and deployment records are linked by the evidence manifest.',
'The additional pool, retired-display, and worker suite passed **73 tests**. The full SQL migration set matches the ORM across **811 schema objects**. All **114 new bundles** compiled and passed independent target inspection.\n\nThe replacement tactic screen made **672 attempts** across 16 bundles. Its 32 compile hits all\ndepended on `sorryAx`; none was an admissible proof. No target-type collision was found in the\npinned catalog. Compilation, tactic-screen, and release-admission records are linked by the\nevidence manifest. Production activation is recorded separately after image construction.')
p.write_text(s)
revisions=read(D/'revisions.json');revisions['released_tasks_commit']=commit;write(D/'revisions.json',revisions)
implementation=[]
for name in subprocess.check_output(['git','-C',str(V),'diff','b2f0c3306e63e5861d2950223d154c3000ed9cf8','--name-only'],text=True).splitlines():
 p=V/name
 if p.is_file() and not name.startswith('docs/review-decisions/'):
  implementation.append(dict(path=name,sha256=digest(p),bytes=p.stat().st_size))
write(D/'implementation-files.json',implementation)
for n in ['2026-09-08-add-50','2026-09-08-pool-review']:
 base=V/'docs/review-decisions'/n
 for f in read(base/'evidence-manifest.json')['files']:
  assert digest(base/f['path'])==f['sha256'],f['path']
manifest=dict(created_at_utc=datetime.now(timezone.utc).isoformat(),tasks_commit=commit,files=[dict(path=str(p.relative_to(D)),sha256=digest(p),bytes=p.stat().st_size) for p in sorted(D.rglob('*')) if p.is_file() and p.name!='evidence-manifest.json'])
write(D/'evidence-manifest.json',manifest)
print('Sealed',len(manifest['files']),'release evidence files; both prior report seals verified.')
