import hashlib, json
from pathlib import Path

r=Path(__file__).resolve().parent
results={}
for label, before, after in [
    ('new_targets_match_audited_definitions','audited-definitions.json','release-new-definitions.json'),
    ('retained_targets_preserve_definitions','retained-base-definitions.json','release-retained-definitions.json'),
]:
    old={x['theorem']:x for x in json.loads((r/before).read_text())}
    new={x['theorem']:x for x in json.loads((r/after).read_text())}
    assert old.keys()==new.keys()
    differences=[]
    for name, item in new.items():
        assert not item['type_has_sorry'] and not item['nonstandard_statement_axioms'],name
        previous=old[name]
        # A file move changes ownership, not the definition itself.
        def index(row):
            return {d['name']:{k:d[k] for k in ['type','value']} for d in row['definitions']}
        a=index(previous);b=index(item)
        changed=[n for n in a.keys()|b.keys() if a.get(n)!=b.get(n)]
        if previous['type']!=item['type'] or changed:
            differences.append({'theorem':name,'type_changed':previous['type']!=item['type'],
                                'changed_definitions':changed})
    results[label]={'targets':len(new),'differences':differences,
                   'before_sha256':hashlib.sha256((r/before).read_bytes()).hexdigest(),
                   'after_sha256':hashlib.sha256((r/after).read_bytes()).hexdigest()}
(r/'definition-comparison.json').write_text(json.dumps(results,indent=2)+'\n')
print(json.dumps(results,indent=2))
assert not any(v['differences'] for v in results.values()), 'Definition changes require review'
