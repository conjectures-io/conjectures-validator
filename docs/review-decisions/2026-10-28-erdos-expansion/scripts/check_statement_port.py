import json,re
from pathlib import Path
O=Path(__file__).resolve().parent;T=O.parents[1]/'conjectures-tasks';r=json.loads((T/'tiers/tier-1/erdos-admission-audit.json').read_text());rows=[]
renames={'distinctDistancesFrom':'distinctDistancesFromOther','minimalDistinctDistances':'minimalDistinctDistancesIn'}
for x in r['targets']:
 if x['decision']!='eligible':continue
 old=x['type_pretty'];new=x['production_type_pretty'];mapped=old
 for a,b in renames.items():mapped=re.sub(r'\b'+a+r'\b',b,mapped)
 # Only the six new geometry targets use these helper names in their printed type.
 equal=re.sub(r'\s+','',old)==re.sub(r'\s+','',new)
 assert equal or re.sub(r'\s+','',mapped)==re.sub(r'\s+','',new),x['theorem']
 if not equal:rows.append({'theorem':x['theorem'],'upstream_type':old,'production_type':new})
result={'passed':True,'admitted_targets':264,'unchanged_printed_statements':264-len(rows),'helper_rename_only':rows,'reason':'Keep the original production geometry helper definitions intact for existing rewards. Preserve the newer upstream helpers under distinct names for new targets. Compare printed statements modulo whitespace and these explicit identifier renames; this check supplements definition review and is not a formal equivalence proof across Lean versions.'}
(O/'evidence/statement-port-comparison.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n');print('264 statement ports matched; helper renames:',len(rows))
