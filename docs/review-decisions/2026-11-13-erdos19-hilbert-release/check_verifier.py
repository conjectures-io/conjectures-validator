"""Positive and negative end-to-end controls under the production sandbox profile."""
import dataclasses,json,os,subprocess,sys,time
from pathlib import Path
ROOT=Path('/opt/fc-verifier');O=Path('/release');C=Path('/checks');T=Path('/tasks');sys.path.insert(0,str(ROOT))
from verifier.catalog import load_catalog
from verifier.models import CatalogDeclaration
from verifier.task_generator import generate_task
from verifier.task_loader import load_task_bundle
from verifier.verification import verify
from verifier.workspace import target_validator
NAMES={'HilbertCatalog.Hilbert13.conjecture', 'HilbertCatalog.RankOneAbelianStark.conjecture'}
def check(name,task,body,accepted,reason,provenance=None):
 submission=C/(name+'.lean');submission.write_text(body);bundle=load_task_bundle(task);start=time.monotonic()
 report=verify(task_dir=task,submission_path=submission,project_root=ROOT,expected_task_sha256=bundle.sha256,expected_build_provenance_sha256=provenance,allow_insecure_development=False,allow_test_task=not bundle.manifest.production_eligible)
 row=dataclasses.asdict(report);row.update(case=name,passed=report.accepted==accepted and str(report.reason_code)==reason,seconds=round(time.monotonic()-start,3))
 if accepted:row['passed']=row['passed'] and report.checks.get('production_sandbox') is True
 (C/(name+'.json')).write_text(json.dumps(row,indent=2)+'\n');print(json.dumps({'case':name,'passed':row['passed'],'reason':str(report.reason_code)}),flush=True);assert row['passed'],row

def shard(i):
 plan=json.loads((O/'control-task-plan.json').read_text());rows=plan['tasks'];assert len(rows)==4 and {r['theorem'] for r in rows}==NAMES
 assert plan['repository_commit']==json.loads((O/'catalog.json').read_text())['repository_commit']
 for row in rows[i::4]:
  theorem=row['theorem'];mode=row['mode'];name=theorem.replace('.','-')+'-'+mode;task=T/row['location'];prov=row['build_provenance_sha256']
  deadline=time.monotonic()+3600
  while not task.exists():
   assert time.monotonic()<deadline,'timed out waiting for a compiled control bundle'
   time.sleep(5)
  assert load_task_bundle(task).manifest.task_id==row['task_id']
  check(name+'-wrong-statement',task,'theorem target : True := True.intro\n',False,'STATEMENT_MISMATCH',prov)
  if theorem in NAMES:
   proposition='(fcTypeOfName% '+theorem+')';proposition=proposition if mode=='formalized' else '¬ '+proposition
   check(name+'-sorry',task,f'theorem target : {proposition} := by sorry\n',False,'SUBMISSION_POLICY_VIOLATION',prov)
   check(name+'-axiom',task,f'axiom forged : {proposition}\ntheorem target : {proposition} := forged\n',False,'SUBMISSION_POLICY_VIOLATION',prov)
   if mode=='formalized':check(name+'-source-placeholder',task,f'theorem target : {proposition} := {theorem}\n',False,'SUBMISSION_POLICY_VIOLATION',prov)

def main():
 if len(sys.argv)==3:shard(int(sys.argv[2]));return
 if '--controls-only' in sys.argv:
  assert json.loads((C/'positive-counterexample.json').read_text())['passed'] is True
 else:
  catalog=load_catalog(O/'catalog.json');fixture=CatalogDeclaration.from_dict(json.loads((O/'fixture-source.json').read_text()));fixture_catalog=dataclasses.replace(catalog,declarations=catalog.declarations+(fixture,));task=C/'positive-task'
  if not task.exists():generate_task(catalog=fixture_catalog,declaration=fixture,mode='counterexample',output=task,validate_target=target_validator(ROOT))
  check('positive-counterexample',task,(ROOT/'examples/counterexample/Valid.lean').read_text(),True,'VERIFIED')
 if '--positive-only' in sys.argv:return
 workers=[subprocess.Popen([sys.executable,'-u',__file__,'--shard',str(i)]) for i in range(4)];codes=[w.wait() for w in workers];assert codes==[0]*4,codes
 rows=[json.loads(p.read_text()) for p in C.glob('*.json') if p.name!='summary.json'];assert len(rows)==15 and all(r['passed'] for r in rows)
 result={'passed':True,'cases':15,'new_bundles_exercised':4,'positive_control_verified':True,'insecure_development':False,'uid':os.getuid(),'open_problems_proved':False,'processes':4};(C/'summary.json').write_text(json.dumps(result,indent=2)+'\n');print(result,flush=True)
if __name__=='__main__':main()
