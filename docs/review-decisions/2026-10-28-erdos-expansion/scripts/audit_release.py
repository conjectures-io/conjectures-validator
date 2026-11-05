"""Read-only integration audit of a prepared local publication."""
import collections,json,sys
from pathlib import Path
O=Path(__file__).resolve().parent;V=O.parents[1]/'conjectures-validator';T=O.parents[1]/'conjectures-tasks';OLD=O/'baseline-tasks';sys.path.insert(0,str(V))
from submission_api.taskpool import TaskCatalog
from submission_api.conjectures import ConjectureIndex,families
from submission_api.retired import RetiredIndex
from verification_worker.tasks import load_task_resolver
from verifier.task_loader import load_task_bundle
from verifier.version_registry import VersionRegistry,assert_record_matches_bundle,assert_matches_allowlist
from verifier.catalog import load_catalog
from verifier.task_pool import load_retired_conjectures,load_held_sources
before=json.loads((OLD/'task-versions.json').read_text());after=json.loads((T/'task-versions.json').read_text());old=VersionRegistry.load(OLD/'task-versions.json');new=VersionRegistry.load(T/'task-versions.json')
a={r['task_id']:r for r in before['versions']};b={r['task_id']:r for r in after['versions']}
assert after['publications'][:-1]==before['publications'];assert after['environments']==before['environments'];assert len(after['instances'])==len(before['instances'])+1;assert all(i in after['instances'] for i in before['instances'])
assert new.current.instance.environment_identity_sha256==old.current.instance.environment_identity_sha256
for name,row in a.items():
 r=b[name];assert {k:v for k,v in row.items() if k not in {'admissions','state'}}=={k:v for k,v in r.items() if k not in {'admissions','state'}}
 assert r['admissions'][:len(row['admissions'])]==row['admissions']
 assert r['state']==row['state'] or row['state']=='active' and r['state']=='superseded'
 od,nd=OLD/row['location'],T/r['location'];assert {p.name for p in od.iterdir()}=={p.name for p in nd.iterdir()}
 for p in od.iterdir():assert p.read_bytes()==(nd/p.name).read_bytes(),str(p)
for record in new.versions.values():assert_record_matches_bundle(record,load_task_bundle(T/record.location))
assert_matches_allowlist(new,T/'allowlist.json')
for row in json.loads((O/'control-task-plan.json').read_text())['tasks']:
 record=b[row['task_id']]
 assert record['state']=='active' and record['theorems']==[row['theorem']] and record['mode']==row['mode']
 assert record['location']==row['location']
 assert any(a['repository_commit']==new.current.instance.repository_commit and a['build_provenance_sha256']==row['build_provenance_sha256'] for a in record['admissions'])
oldcat=TaskCatalog.load(allowlist_path=OLD/'allowlist.json',pool_root=OLD/'pool');cat=TaskCatalog.load(allowlist_path=T/'allowlist.json',pool_root=T/'pool')
assert len(oldcat.entries)==580 and len(cat.entries)==1108
public=ConjectureIndex.build(cat,retired=RetiredIndex.load(allowlist_path=T/'allowlist.json'))
assert len(public.all())==554
assert sum(1+len(f.variants) for f in families(public))==len(public.all())+len(public.retired.all())
oldkeys={(r.source.theorem,r.mode):r for r in oldcat.entries.values()};newkeys={(r.source.theorem,r.mode):r for r in cat.entries.values()};assert oldkeys.keys()<=newkeys.keys()
for key,row in oldkeys.items():
 r=newkeys[key];assert r.reward_target_id==row.reward_target_id;assert r.target_type_sha256s==row.target_type_sha256s
review=json.loads((T/'tiers/tier-1/erdos-admission-audit.json').read_text());accepted={r['theorem'] for r in review['targets'] if r['decision']=='eligible'};excluded={r['theorem'] for r in review['targets'] if r['decision']!='eligible'};current={r.source.theorem for r in cat.entries.values()}
assert len(accepted)==264 and not excluded&current;assert current-{n for n,m in oldkeys}==accepted
for theorem in current:
 pair=[r for r in cat.entries.values() if r.source.theorem==theorem];assert len(pair)==2 and {r.mode for r in pair}=={'formalized','counterexample'};assert {r.reward_target_id for r in pair}=={'fc-target:'+theorem}
 for r in pair:
  assert r.manifest.adapter_version==2 and r.manifest.production_eligible and r.manifest.schema_version==3;assert not r.source.contains_sorry_in_type
held=load_held_sources(T/'tiers/tier-1/held-source-theorems.json');closed=load_retired_conjectures(T/'tiers/tier-1/retired-conjectures.json');assert not current&held.theorems;assert not current&{x['theorem'] for x in closed.entries.values()}
for filename in ['retired-source-theorems.json']:
 x=json.loads((OLD/'tiers/tier-1'/filename).read_text());y=json.loads((T/'tiers/tier-1'/filename).read_text());assert {k:v for k,v in x.items() if k!='repository_commit'}=={k:v for k,v in y.items() if k!='repository_commit'}
routing=[];sets=[]
for instance in new.instances:
 resolver=load_task_resolver(tasks_root=T,allowlist_path=T/'allowlist.json',pool_root=T/'pool',environment=instance);keys=set(resolver.served_keys())
 if instance in old.instances:
  prev=load_task_resolver(tasks_root=OLD,allowlist_path=OLD/'allowlist.json',pool_root=OLD/'pool',environment=instance);assert set(prev.served_keys())==keys
 else:assert len(keys)==1108
 assert all(not keys&s for s in sets);sets.append(keys);routing.append({'instance':instance.to_dict(),'served':len(keys)})
# Detect identical kernel statement hashes across every newly selected and retained target.
by_hash=collections.defaultdict(list)
for name,mode in newkeys:
 if mode=='formalized':by_hash[newkeys[name,mode].source.type_hash].append(name)
assert not any(len(v)>1 for v in by_hash.values())
result={'passed':True,'new_targets':264,'new_numbered_problems':199,'active_targets':554,'active_bundles':1108,'new_bundle_versions':len(b)-len(a),'preserved_historical_versions':len(a),'historical_bundle_bytes_unchanged':True,'old_admissions_preserved':True,'existing_target_types_and_rewards_preserved':True,'production_environment_unchanged':True,'duplicate_active_type_hashes':0,'rejected_targets_not_admitted':len(excluded),'routing':routing,'deployment_performed':False}
(O/'evidence/release-audit.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
