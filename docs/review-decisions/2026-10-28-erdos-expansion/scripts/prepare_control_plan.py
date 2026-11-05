import json,pickle,sys
from pathlib import Path
ROOT=Path('/opt/fc-verifier');O=Path('/admission');sys.path.insert(0,str(ROOT))
from verifier.catalog import load_catalog
from verifier.incremental import SelectedTarget,plan_release
from verifier.task_policy import PRODUCTION_TASK_MODES
NAMES={'Erdos5.erdos_5','Erdos65.erdos_65.parts.ii','Erdos282.erdos_282','Erdos564.erdos_564','Erdos604.erdos_604.variants.average','Erdos918.erdos_918.parts.i','Erdos1004.erdos_1004','Erdos1154.erdos_1154.parts.i'}
index=pickle.loads((O/'index.pickle').read_bytes());cat=load_catalog(O/'catalog.json');selected=[d for d in cat.declarations if d.theorem in NAMES];assert len(selected)==8
plans=plan_release(catalog=cat,targets=[SelectedTarget(d,'tier-1',PRODUCTION_TASK_MODES) for d in selected],index=index,adapter_versions={(d.theorem,m):2 for d in selected for m in PRODUCTION_TASK_MODES})
rows=[{'task_id':p.task_id,'theorem':p.declaration.theorem,'mode':p.mode,'location':'versions/'+p.task_id,'build_provenance_sha256':index.provenance[p.declaration.theorem].sha256} for p in plans]
(O/'control-task-plan.json').write_text(json.dumps({'repository_commit':index.repository_commit,'tasks':rows},indent=2)+'\n');print('planned',len(rows),'control bundles')
