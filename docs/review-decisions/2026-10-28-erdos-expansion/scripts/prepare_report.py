import collections,csv,hashlib,json
from pathlib import Path
O=Path(__file__).resolve().parent;T=O.parents[1]/'conjectures-tasks';r=json.loads((T/'tiers/tier-1/erdos-admission-audit.json').read_text());by={x['theorem']:x for x in r['targets']};blocked=[];problems=[]
for p in r['problems']:
 targets=[by[n] for n in p['targets']];admitted=[x['theorem'] for x in targets if x['decision']=='eligible'];row={'number':p['number'],'disposition':'admitted_targets' if admitted else 'excluded_from_release','admitted_targets':admitted,'target_decisions':{x['theorem']:x['decision'] for x in targets},'previously_fully_blocked':p['previously_fully_blocked']};problems.append(row)
 if p['previously_fully_blocked']:blocked.append(row)
assert len(blocked)==95;assert {x['number'] for x in blocked if x['admitted_targets']}=={564,1004}
(O/'blocked-problem-dispositions.json').write_text(json.dumps(blocked,indent=2)+'\n');(O/'problem-dispositions.json').write_text(json.dumps(problems,indent=2)+'\n')
with (O/'target-decisions.csv').open('w') as f:
 w=csv.DictWriter(f,fieldnames=['number','theorem','decision','parent_problem_status','source_path','review_note','scope_note','duplicate_of','production_type_sha256'],extrasaction='ignore',lineterminator='\n');w.writeheader();w.writerows(r['targets'])
counts=collections.Counter(x['decision'] for x in r['targets']);print(dict(counts));print('95 blocked: 2 admitted; 93 excluded from this release')
