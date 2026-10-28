import collections,json,subprocess
from pathlib import Path
O=Path(__file__).resolve().parent;V=O.parents[1]/'conjectures-validator';T=O.parents[1]/'conjectures-tasks'
old=json.loads(subprocess.check_output(['git','show','f1e877f1:data/catalog.json'],cwd=V));new=json.loads((O/'catalog.json').read_text());review=json.loads((T/'tiers/tier-1/erdos-admission-audit.json').read_text());admitted={r['theorem'] for r in review['targets'] if r['decision']=='eligible'}
by_hash=collections.defaultdict(set)
for r in old['declarations']+new['declarations']:by_hash[r['type_hash']].add(r['theorem'])
hits=[{'theorem':r['theorem'],'others':sorted(by_hash[r['type_hash']]-{r['theorem']})} for r in new['declarations'] if r['theorem'] in admitted and len(by_hash[r['type_hash']])>1]
result={'new_targets':len(admitted),'baseline_catalog_declarations':len(old['declarations']),'production_catalog_declarations':len(new['declarations']),'exact_type_hash_collisions':hits,'semantic_duplicates_excluded':[{'theorem':r['theorem'],'duplicate_of':r['duplicate_of'],'reason':r['review_note']} for r in review['targets'] if r['decision']=='exclude_duplicate'],'limitations':'Different expressions can denote equivalent questions; exact hashes supplement the documented manual semantic review and do not prove absence of all equivalences.'}
(O/'evidence/duplicate-screen.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'collisions':hits,'semantic_exclusions':len(result['semantic_duplicates_excluded'])},indent=2))
