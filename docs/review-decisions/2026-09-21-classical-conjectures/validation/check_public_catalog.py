import json,sys
from pathlib import Path
r=Path(__file__).resolve().parent
sys.path.insert(0,str(r/'validator'))
from submission_api.taskpool import TaskCatalog
from submission_api.retired import RetiredIndex
from submission_api.conjectures import ConjectureIndex
from verifier.task_loader import load_task_bundle
allow=r/'tasks/allowlist.json'
catalog=TaskCatalog.load(allowlist_path=allow,pool_root=r/'tasks/pool')
retired=RetiredIndex.load(allowlist_path=allow)
index=ConjectureIndex.build(catalog,retired=retired)
assert len(index.all())==295
names={item.source.theorem:item for item in index.all()}
selected=json.loads((r/'selected.json').read_text())
rows=[]
for candidate in selected:
    item=names[candidate['theorem']]
    assert set(item.task_modes)=={'formalized','counterexample'}
    rows.append({'theorem':item.source.theorem,'slug':item.slug,'modes':list(item.task_modes)})
assert 'Green44.green_44' not in names and 'Erdos96.erdos_96' in names
fixture_count=0
for manifest in (r/'tasks/fixtures').glob('*/*/manifest.json'):
    bundle=load_task_bundle(manifest.parent)
    assert bundle.manifest.repository_commit==catalog.repository_commit
    fixture_count+=1
result={'active_targets':len(index.all()),'retired_targets':len(retired.all()),'bundles':len(catalog.summaries()),'fixtures_repinned_and_loaded':fixture_count,'new_public_entries':rows}
(r/'public-catalog-validation.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k!='new_public_entries'},indent=2))
