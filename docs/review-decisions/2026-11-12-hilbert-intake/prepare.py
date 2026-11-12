"""Add the reviewed GRH target to an isolated checkout of the production tasks pin."""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'output/hilbert-intake-20261112'
TASKS = OUT / 'tasks'
META = TASKS / 'tiers/tier-1'
BASE = '4a10571b0e196007207b02b585fad5e46f16abd6'
NAME = 'GRH.generalized_riemann_hypothesis'
SOURCE = 'FormalConjectures/Millennium/RiemannHypothesis.lean'
UPSTREAM = 'a425ca3eb797fe927cb88c950565fdcbbbbada70'


def write(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + '\n')


def baseline(name):
    return json.loads(subprocess.check_output(
        ['git', 'show', BASE + ':tiers/tier-1/' + name], cwd=TASKS))


catalog = json.loads((ROOT / 'data/catalog.json').read_text())
declaration = next(r for r in catalog['declarations'] if r['theorem'] == NAME)
sources = [
    f'https://github.com/google-deepmind/formal-conjectures/blob/{UPSTREAM}/{SOURCE}',
    'https://github.com/openai/math/blob/4eaee87548250b2682affda7d05d947c05554bae/lean/docs/003.md',
    'https://mathcs.clarku.edu/~djoyce/hilbert/problems.html',
]
note = ('Dirichlet GRH uses Mathlib analytic continuation, positive modulus, primitive '
        'characters, and the parity-correct trivial-zero exclusions. The conductor-one '
        'case agrees with RH; the upstream sanity theorem checks this. It is not the '
        'removed naive Dedekind-zeta statement. The recent quasi-RH proof scope gives '
        '7/8, not the requested 1/2. No accepted full solution was identified in this '
        'bounded review. This is the Dirichlet part of the generalized-zeta theme of '
        'Hilbert #8, not the full number-field ERH.')
entry = {
    'active_resolution_prs': [], 'feasibility_signals': ['standard-mathlib-surface'],
    'open_prs_touching_source': [], 'source_family': 'millennium',
    'source_path': SOURCE, 'source_problem_number': 'RiemannHypothesis',
    'source_status': 'open', 'theorem': NAME, 'upstream_status': 'research open',
}
review = {
    'theorem': NAME, 'number': 'RiemannHypothesis', 'source_path': SOURCE,
    'source_family': 'millennium', 'target_source_status': 'open', 'decision': 'eligible',
    'review_note': note, 'production_type_sha256': declaration['type_hash'],
    'references': sources, 'checked_date': '2026-11-12', 'checked_upstream_commit': UPSTREAM,
    'hilbert_number': 8, 'scope': 'Dirichlet generalized Riemann hypothesis',
}
audit = baseline('selection-audit.json')
targets = baseline('task-targets.json')
status = baseline('erdos-admission-audit.json')
classical = baseline('classical-status-audit.json')
retired = baseline('retired-source-theorems.json')
assert len(targets['targets']) == 306 and NAME not in {r['theorem'] for r in targets['targets']}
assert catalog['repository_commit'] == audit['repository_commit']
# The July 24 migration retired all earlier pool entries for freshness, not
# because GRH was solved. Re-admit exactly this reviewed statement and keep
# every other retirement. The old migration is retained in the intake evidence.
assert NAME in retired['source_theorems']
assert declaration['type_hash'] in retired['source_type_sha256']
assert [r['theorem'] for r in catalog['declarations']
        if r['type_hash'] == declaration['type_hash']] == [NAME]
retired['source_theorems'].remove(NAME)
retired['source_type_sha256'].remove(declaration['type_hash'])
write(META / 'retired-source-theorems.json', retired)
audit['selected'].append(entry)
audit['selected'].sort(key=lambda r: r['theorem'])
audit['audit_date_utc'] = '2026-11-12'
targets['targets'].append({k: entry[k] for k in (
    'theorem', 'source_path', 'source_family', 'source_problem_number')} |
    {'reward_target_id': 'fc-target:' + NAME})
targets['targets'].sort(key=lambda r: r['theorem'])
status['targets'].append(review)
status['named_admission_date_utc'] = '2026-11-12'
classical['candidates'].append({
    'name': 'Generalized Riemann hypothesis for Dirichlet L-functions', 'theorem': NAME,
    'source_path': SOURCE, 'disposition': 'retain_provisionally_open',
    'audit_note': note, 'independent_status_review':
    'Primary upstream open statement and published partial-result scope checked 2026-11-12; '
    'not an exhaustive proof search or an independent second reviewer.',
    'external_sources': sources, 'checked_date': '2026-11-12',
})
classical['supplemental_admissions'] = [{'checked_date': '2026-11-12', 'theorem': NAME,
    'upstream_commit': UPSTREAM, 'production_type_sha256': declaration['type_hash']}]
write(META / 'classical-status-audit.json', classical)
digest = hashlib.sha256((META / 'classical-status-audit.json').read_bytes()).hexdigest()
for item in audit['source_status_sources']:
    if item['family'] in {'wikipedia', 'millennium'}:
        item['revision'] = digest
write(META / 'erdos-admission-audit.json', status)
audit['target_status_audit_sha256'] = 'sha256:' + hashlib.sha256(
    (META / 'erdos-admission-audit.json').read_bytes()).hexdigest()
write(META / 'selection-audit.json', audit)
write(META / 'task-targets.json', targets)
shutil.copyfile(ROOT / 'data/catalog.json', OUT / 'catalog.json')
write(OUT / 'selection.json', [review])
write(OUT / 'counts.json', {'retained_targets': 306, 'added_targets': 1,
                          'targets': 307, 'expected_bundles': 614})
print((OUT / 'counts.json').read_text())
