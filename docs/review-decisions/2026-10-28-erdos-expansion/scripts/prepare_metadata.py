"""Prepare reviewed local admission metadata. Does not publish or deploy services."""
import json,hashlib,shutil,subprocess
from pathlib import Path
O=Path(__file__).resolve().parent;B=O.parents[1];T=B/'conjectures-tasks';V=B/'conjectures-validator';M=T/'tiers/tier-1'
def read(p):return json.loads(p.read_text())
def write(p,x):p.write_text(json.dumps(x,indent=2,sort_keys=True,ensure_ascii=False)+'\n')
def baseline(rel):return json.loads(subprocess.check_output(['git','show','e46f6c64:'+rel],cwd=T))
review=read(O/'review.json');catalog=read(O/'catalog.json');by={d['theorem']:d for d in catalog['declarations']};commit=catalog['repository_commit']
selected=baseline('tiers/tier-1/selection-audit.json');targets=baseline('tiers/tier-1/task-targets.json');review['retained_selection']=selected['selected'].copy();review['repository_commit']=commit
for r in review['targets']:
 d=by[r['theorem']];r['production_type_sha256']=d['type_hash'];r['production_type_pretty']=d['type_pretty']
 if r['decision']=='eligible':
  assert d['classification']=='DIRECT_PROP' and not d['contains_sorry_in_type'],r['theorem']
accepted=[x for x in review['targets'] if x['decision']=='eligible']
pr_files=read(O/'evidence/open-pr-files.json');print('PR data type',type(pr_files).__name__)
# File-touch evidence records every relevant PR; a touch alone is not a resolution claim.
if isinstance(pr_files,dict):pr_files=pr_files.get('rows',pr_files.get('pull_requests',pr_files))
pr_by_path={}
for pr in pr_files if isinstance(pr_files,list) else []:
 for path in pr.get('files',[]):
  if isinstance(path,dict):path=path.get('filename',path.get('path'))
  pr_by_path.setdefault(path,[]).append(pr['number'])
for r in accepted:
 identity={'theorem':r['theorem'],'source_path':r['source_path'],'source_family':'erdos','source_problem_number':r['number']}
 selected['selected'].append({**identity,'active_resolution_prs':[],'feasibility_signals':['compact-formal-target'],'open_prs_touching_source':sorted(set(pr_by_path.get(r['source_path'],[]))),'source_status':r['target_source_status'],'upstream_status':'research open'})
 targets['targets'].append({**identity,'reward_target_id':'fc-target:'+r['theorem']})
selected.update(schema_version=3,repository_commit=commit,audit_date_utc='2026-10-28',source_main_commit=review['upstream_commit'],github_open_pr_count=661)
selected['selected'].sort(key=lambda r:r['theorem']);targets['targets'].sort(key=lambda r:r['theorem']);targets['repository_commit']=commit
write(M/'erdos-admission-audit.json',review);selected['target_status_audit_sha256']='sha256:'+hashlib.sha256((M/'erdos-admission-audit.json').read_bytes()).hexdigest();write(M/'selection-audit.json',selected);write(M/'task-targets.json',targets)
reinstated={f'Erdos{n}.erdos_{n}' for n in [282,564,1004]}
held=baseline('tiers/tier-1/held-source-theorems.json');held['holds']=[h for h in held['holds'] if h['theorem'] not in reinstated]
held['repository_commit']=commit
for r in review['targets']:
 if r['decision'] in {'hold_resolution_claim','hold_statement_dispute','exclude_statement_discrepancy'} and r['theorem'] not in {h['theorem'] for h in held['holds']}:
  held['holds'].append({'theorem':r['theorem'],'source_path':r['source_path'],'reward_target_id':'fc-target:'+r['theorem'],'source_type_sha256s':[r['production_type_sha256']],'held_on':'2026-10-28','reason_code':'HOLD_UNVERIFIED_RESOLUTION_CLAIM' if r['decision']=='hold_resolution_claim' else 'HOLD_STATEMENT_SOURCE_DISCREPANCY','finding':r['review_note'],'required_resolution':'Resolve the cited claim or statement discrepancy, then perform a new exact-target admission review.','audit_reference':'erdos-admission-audit.json; '+', '.join(r['references'])})
held['holds'].sort(key=lambda h:h['theorem']);write(M/'held-source-theorems.json',held)
hold_log=subprocess.check_output(['git','show','e46f6c64:tiers/tier-1/HOLDS.md'],cwd=T,text=True)
hold_log='\n'.join(line for line in hold_log.splitlines() if not any(line.startswith('- `'+name+'`') for name in reinstated))+'\n'
hold_log+='\nThe October 28 review resolves three historical holds; see REINSTATEMENTS.md. New intake holds below have no prior admitted bundle to display.\n\n'
for h in held['holds']:
 if h['held_on']=='2026-10-28':hold_log+='- `'+h['theorem']+'` — '+h['held_on']+' — `'+h['reason_code']+' ('+h['finding']+' See erdos-admission-audit.json.)`\n'
(M/'HOLDS.md').write_text(hold_log)
for name in ['retired-source-theorems.json','retired-conjectures.json']:
 r=baseline('tiers/tier-1/'+name);r['repository_commit']=commit
 if name=='retired-conjectures.json':
  # This is the current problem-page display, not the immutable bundle history.
  # Keep reinstated targets out of the closed list; archive their old display rows.
  write(O/'evidence/reinstated-historical-display.json',[x for x in r['retired'] if x['theorem'] in reinstated])
  r['retired']=[x for x in r['retired'] if x['theorem'] not in reinstated]
 write(M/name,r)
# Preserve historical rows, admissions and bundle bytes; only the active hold list changes.
text=subprocess.check_output(['git','show','e46f6c64:tiers/tier-1/REINSTATEMENTS.md'],cwd=T,text=True)
text+='\n## October 28 Erdős expansion (prepared, not deployed)\n\n'
for r in accepted:
 if r['theorem'] in reinstated:text+='- `'+r['theorem']+'`: '+r['review_note']+' See `erdos-admission-audit.json` and the archived primary discussion.\n'
(M/'REINSTATEMENTS.md').write_text(text)
shutil.copyfile(O/'catalog.json',V/'data/catalog.json');shutil.copyfile(O/'catalog-summary.json',V/'data/catalog-summary.json')
write(O/'selection-summary.json',{'new_targets':len(accepted),'new_problem_numbers':len({r['number'] for r in accepted}),'total_targets':len(targets['targets']),'bundles':2*len(targets['targets']),'reinstated':sorted(reinstated),'deployment_allowed':False})
print(read(O/'selection-summary.json'))
