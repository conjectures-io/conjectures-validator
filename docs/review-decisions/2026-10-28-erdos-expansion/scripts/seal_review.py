"""Archive reproducible admission evidence after the candidate's checks pass."""
import gzip,hashlib,json,shutil,tarfile
from pathlib import Path
O=Path(__file__).resolve().parent;B=O.parents[1];V=B/'conjectures-validator';T=B/'conjectures-tasks';D=V/'docs/review-decisions/2026-10-28-erdos-expansion';D.mkdir(parents=True,exist_ok=True)
def cp(src,dst):dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
# Raw captures are immutable dated evidence, compressed to keep repository growth manageable.
for p in sorted((O/'evidence').iterdir()):
 if p.name=='primary' or p.name in {'catalog-extractor.stdout','review-queue.json'}:continue
 if p.is_file():
  if p.stat().st_size>500_000 or p.suffix=='.html':
   (D/'evidence').mkdir(exist_ok=True);(D/'evidence'/(p.name+'.gz')).write_bytes(gzip.compress(p.read_bytes(),mtime=0))
  else:cp(p,D/'evidence'/p.name)
with (D/'evidence/primary-captures.tar.gz').open('wb') as f:
 with gzip.GzipFile(fileobj=f,mode='wb',mtime=0,filename='') as g:
  with tarfile.open(fileobj=g,mode='w|') as archive:
   for p in sorted((O/'evidence/primary').iterdir()):
    if p.is_file():
     info=archive.gettarinfo(p,arcname=p.name);info.uid=info.gid=0;info.uname=info.gname='';info.mtime=0
     with p.open('rb') as source:archive.addfile(info,source)
for name in ['dependency-index.json','compiled-declarations.json']:(D/(name+'.gz')).write_bytes(gzip.compress((O/name).read_bytes(),mtime=0))
for name in ['blocked-problem-dispositions.json','problem-dispositions.json','target-decisions.csv','source-pin.json','selection-summary.json','publication.json','modules.json','retained-paths.json','control-task-plan.json','release-coordinates.json']:
 cp(O/name,D/name)
cp(T/'tiers/tier-1/erdos-admission-audit.json',D/'target-status-audit.json')
for name in ['review.py','prepare_metadata.py','publish_candidate.py','compile.py','build_retained.py','extract_catalog.py','check_catalog_safety.py','CatalogSafetyCheck.lean','audit_release.py','save_baseline.py','fetch_primary.py','fetch_claims.py','check_statement_port.py','check_source_reproduction.py','check_duplicates.py','check_verifier.py','run_runtime.py','prepare_control_plan.py','check_standard_validator.py','prepare_report.py','fixture-source.json','seal_review.py']:
 cp(O/name,D/'scripts'/name)
# These checks must all be successful before this file can be treated as admission evidence.
for name in ['catalog-safety.json','source-reproduction.json','statement-port-comparison.json','release-audit.json','standard-validator-parity.json']:
 assert json.loads((O/'evidence'/name).read_text())['passed'] is True,name
assert json.loads((O/'evidence/runtime/summary.json').read_text())['passed'] is True
for p in sorted((O/'evidence/runtime').glob('*.json')):cp(p,D/'evidence/runtime'/p.name)
checks={str(p.relative_to(D)):'sha256:'+hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(D.rglob('*')) if p.is_file() and p.name!='SHA256SUMS.json'}
(D/'SHA256SUMS.json').write_text(json.dumps(checks,indent=2,sort_keys=True)+'\n')
print('sealed',len(checks),'files in',D)
