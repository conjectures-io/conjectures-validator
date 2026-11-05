import json,pickle,sys,time
from pathlib import Path
ROOT=Path('/opt/fc-verifier');O=Path('/admission');T=Path('/candidate-tasks');sys.path.insert(0,str(ROOT))
from verifier.task_loader import load_task_bundle
from verifier.workspace import target_validator
index=pickle.loads((O/'index.pickle').read_bytes());validate=target_validator(ROOT,dependency_index=index)
rows=[];plan=json.loads((O/'control-task-plan.json').read_text())
for row in plan['tasks']:
 if row['theorem'] not in {'Erdos282.erdos_282','Erdos564.erdos_564','Erdos1004.erdos_1004'}:continue
 deadline=time.monotonic()+3600
 while not (T/row['location']).exists():
  assert time.monotonic()<deadline,'timed out waiting for a compiled control bundle'
  time.sleep(5)
 bundle=load_task_bundle(T/row['location']);start=time.monotonic();digest=validate(T/row['location'],bundle.source,None,row['mode']);assert digest==bundle.manifest.generated_target_type_hash
 rows.append({'theorem':bundle.source.theorem,'mode':row['mode'],'type_hash':digest,'passed':True,'seconds':time.monotonic()-start});print(rows[-1],flush=True)
assert len(rows)==6
(O/'evidence/standard-validator-parity.json').write_text(json.dumps({'passed':True,'bundles':rows},indent=2)+'\n')
