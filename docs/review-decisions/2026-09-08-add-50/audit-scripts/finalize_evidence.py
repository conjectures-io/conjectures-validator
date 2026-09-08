"""Assemble evidence for exactly the final 50 targets, retaining original runs."""
import hashlib
import json
import re
from pathlib import Path

r = Path('/tmp/add-50-20260908')
read = lambda p: json.loads((r/p).read_text())
def write(p, data):
    (r/p).write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False)+'\n')

selected = read('selection.json')
names = {x['theorem'] for x in selected}
for old, replacement, final in [
    ('generation-final.json', 'generation-overlap-replacement.json', 'generation-final.json'),
    ('type-dependencies-strict.json', 'type-dependencies-overlap.json', 'type-dependencies-strict.json'),
]:
    original = old.replace('.json', '-before-overlap.json')
    if not (r/original).exists():
        (r/original).write_bytes((r/old).read_bytes())
    merged = [x for x in read(original) if x['theorem'] in names] + read(replacement)
    assert len(merged) == (100 if old.startswith('generation') else 50)
    write(final, merged)

a = read('attack-audit/sweep-summary.json')
b = read('attack-audit-overlap/sweep-summary.json')
attempts = [x for x in a['attempts'] + b['attempts'] if x['theorem'] in names]
assert len(attempts) == 4200
hits = [x for x in attempts if x['compiled']]
assert len(hits) == 200
assert all(any('sorryAx' in line for line in x['axioms_report']) for x in hits)
write('attack-audit/sweep-final.json', {
    'schema_version': 1,
    'aggregate': dict(a['aggregate'], shards=10, elapsed_seconds=a['aggregate']['elapsed_seconds']+b['aggregate']['elapsed_seconds']),
    'baseline': a['baseline'],
    'provenance': {
        'original_run': 'attack-audit/sweep-summary.json',
        'replacement_run': 'attack-audit-overlap/sweep-summary.json',
        'excluded_from_original': ['Green94.green_94'],
        'included_from_replacement': ['Erdos700.erdos_700.parts.ii'],
        'total_raw_attempts': 4284,
        'final_slate_attempts': 4200,
        'note': 'Raw runs are unmodified; this view filters to the admitted slate. Elapsed time includes the discarded target. Shard diagnostics below retain the original run provenance.'
    },
    'attempts': attempts,
    'duplicate_target_hashes': {},
    'shards': [dict(run=run, **{k:v for k,v in shard.items() if k != 'attempts'})
               for run, data in [('original', a), ('replacement', b)] for shard in data['shards']],
    'finished_at_utc': b['finished_at_utc'],
})

prs = read('open-prs.json')
write('selected-prs-final.json', [
    {'theorem': row['theorem'], 'prs': [
        {k:pr[k] for k in ('number','title','html_url','body','created_at','updated_at')}
        for pr in prs if row['source_path'] in {f['path'] for f in pr['files']}
    ]} for row in selected
])

source_rows = []
pinned = Path('/tmp/conjectures-validator-a8d559d-erdos564/vendor/formal-conjectures')
if not pinned.exists():
    raise RuntimeError('Locate the trusted pinned Formal Conjectures source')
for row in selected:
    p = r/'formal-conjectures'/row['source_path']
    s = p.read_text()
    short = row['theorem'].split('.', 1)[1]
    m = re.search(r'\b(?:theorem|lemma)\s+'+re.escape(short)+r'(?=\s|:|\{|\()', s)
    assert m, row['theorem']
    categories = list(re.finditer(r'@\[category\s+([^,\]\n]+)', s[:m.start()]))
    category = categories[-1][1]
    assert category == 'research open', (row['theorem'], category)
    original = pinned/row['source_path']
    source_rows.append({
        'theorem': row['theorem'], 'source_path': row['source_path'],
        'current_category': category,
        'current_declaration_line': s[:m.start()].count('\n')+1,
        'pinned_file_sha256': hashlib.sha256(original.read_bytes()).hexdigest(),
        'current_file_sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
        'source_file_changed': original.read_bytes() != p.read_bytes(),
    })
write('upstream-source-checks.json', source_rows)
print('Merged 100 compilations, 50 strict dependency checks, 4200 final probes; all 50 current declarations are research open.')
