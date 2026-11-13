"""Compile and publish into the isolated candidate checkout; no live services."""
import atexit,dataclasses,json,pickle,sys,time
from pathlib import Path
ROOT=Path('/opt/fc-verifier');OUT=Path('/admission');TASKS=Path('/candidate-tasks')
sys.path.insert(0,str(ROOT))
from verifier.catalog import load_catalog
from verifier.hashing import sha256_bytes
from verifier.incremental import SelectedTarget,publish_release
from verifier.publication import checkout_writer,commit_publication
from verifier.task_pool import (build_task_allowlist,group_task_declarations,load_held_sources,load_retired_conjectures,load_retired_sources,load_selection_audit,load_task_grouping,load_task_targets,select_task_declarations)
from verifier.task_policy import PRODUCTION_TASK_MODES
from verifier.task_registry import TaskPoolRegistry
from verifier.task_store import TaskVersionStore
from verifier.task_versions import derive_dependency_index
from verifier.version_registry import Instance,VersionRegistry,assert_matches_allowlist_bytes
class CandidateValidator:
 def __init__(self,project,index):self.project=project;self.index=index;self.validator=None
 def __call__(self,*args):
  if self.validator is None:
   from scripts.staged_erdos.validation import StagedValidator
   self.validator=StagedValidator(self.project,self.index);atexit.register(self.validator.close)
  return self.validator(*args)
def main():
 m=TASKS/'tiers/tier-1';cat=load_catalog(OUT/'catalog.json');ret=load_retired_sources(m/'retired-source-theorems.json');held=load_held_sources(m/'held-source-theorems.json');retcon=load_retired_conjectures(m/'retired-conjectures.json');audit=load_selection_audit(m/'selection-audit.json');targets=load_task_targets(m/'task-targets.json');grouping=load_task_grouping(m/'task-groups.json')
 selected=select_task_declarations(catalog=cat,retired=ret,held=held,selection_audit=audit,task_targets=targets,pool_size=len(targets.targets));groups=group_task_declarations(selected,grouping)
 print('selected',len(selected),flush=True);start=time.monotonic()
 if '--reuse-index' in sys.argv:
  index=pickle.loads((OUT/'index.pickle').read_bytes())
  assert index.repository_commit==cat.repository_commit
  assert set(index.dependencies)=={d.theorem for d in selected}
 else:
  index=derive_dependency_index(project_root=ROOT,declarations=selected)
 (OUT/'index.pickle').write_bytes(pickle.dumps(index)) # Locally generated intermediate, never downloaded.
 doc={'repository_commit':index.repository_commit,'environment':index.environment.to_dict(),'closure_report_sha256':index.report_sha256,'dependencies':{k:v.document for k,v in index.dependencies.items()},'build_provenance':{k:v.document for k,v in index.provenance.items()}}
 (OUT/'dependency-index.json').write_text(json.dumps(doc,sort_keys=True,separators=(',',':'))+'\n')
 print('derived dependencies in',time.monotonic()-start,flush=True)
 # Detect semantic drift before writing a publication. Import-only rotations are expected.
 previous=VersionRegistry.load(TASKS/'task-versions.json');old_doc=json.loads((TASKS/'task-versions.json').read_text());oldactive={r['theorems'][0]:r for r in old_doc['versions'] if r['state']=='active' and r['mode']=='formalized'}
 changes=[]
 for name,row in oldactive.items():
  if name not in index.dependencies:continue
  old=json.loads((TASKS/row['location']/'task-version.json').read_text())['dependency'];new=index.dependencies[name].document
  oldc={json.dumps(c['name']):c for c in old['local_constants']};newc={json.dumps(c['name']):c for c in new['local_constants']}
  diff=[{'name':json.loads(k),'before':oldc.get(k),'after':newc.get(k)} for k in sorted(oldc.keys()|newc.keys()) if oldc.get(k)!=newc.get(k)]
  if diff:changes.append({'theorem':name,'changes':diff})
 (OUT/'evidence/retained-dependency-comparison.json').write_text(json.dumps({'retained_targets':len(oldactive),'changes':changes},indent=2)+'\n')
 if changes:
  review_path=OUT/'evidence/retained-dependency-review.json'
  if not review_path.exists():
   raise RuntimeError('Review changed retained dependency constants before publication')
  review=json.loads(review_path.read_text())
  assert review['comparison_sha256']==sha256_bytes((OUT/'evidence/retained-dependency-comparison.json').read_bytes())
  assert review['approved'] is True
 with checkout_writer(TASKS):
  before=(TASKS/'task-versions.json').read_bytes();previous=VersionRegistry.from_bytes(before)
  def allowlist_for(bundles):return build_task_allowlist(catalog=cat,retired=ret,held=held,retired_conjectures=retcon,selection_audit=audit,task_targets=targets,grouping=grouping,selected=groups,bundles=bundles,audit_date_utc='2026-11-12',tier='tier-1')
  result,allowlist=publish_release(catalog=cat,targets=[SelectedTarget(d,'tier-1',PRODUCTION_TASK_MODES) for d in selected],index=index,store=TaskVersionStore(TASKS/'versions'),previous=previous,instance=Instance(index.repository_commit,index.environment.sha256),allowlist_for=allowlist_for,exit_states={**{x:'retired' for x in ret.theorems},**{x:'held' for x in held.theorems}},project_root=ROOT,validate_target=CandidateValidator(ROOT,index),jobs=4,adapter_version=2)
  checked=TaskPoolRegistry.from_bytes(allowlist)
  for bundle in result.bundles:checked.assert_bundle(bundle)
  assert_matches_allowlist_bytes(result.registry,allowlist)
  commit_publication(TASKS,previous_registry_sha256=sha256_bytes(before),registry=result.registry.to_bytes(),allowlist=allowlist)
 (OUT/'publication.json').write_text(json.dumps({'passed':True,'deployment_allowed':False,'counts':result.counts,'seconds':time.monotonic()-start,'decisions':[dataclasses.asdict(x) for x in result.decisions]},indent=2)+'\n');print(result.counts,flush=True)
if __name__=='__main__':main()
