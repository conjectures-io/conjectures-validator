"""Admission gates for the reviewed September slate; no network or mutation of the pool."""
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, '/root/conjectures-validator')
from verifier.catalog import load_catalog
from verifier.task_policy import production_eligibility

r = Path('/tmp/add-50-20260908')
read = lambda p: json.loads(p.read_text())
selected = read(r / 'selection.json')
names = {x['theorem'] for x in selected}
assert len(selected) == len(names) == 50
catalog = load_catalog(Path('/root/conjectures-validator/data/catalog.json'))
declarations = {x.theorem: x for x in catalog.declarations}
old = read(Path('/tmp/conjectures-tasks/allowlist.json'))['allowed_source_theorems']
retired = read(Path('/tmp/conjectures-tasks/tiers/tier-1/retired-source-theorems.json'))
assert not names.intersection(x['theorem'] for x in old)
assert not names.intersection(retired['source_theorems'])
hashes = {x['type_hash'] for x in selected}
assert len(hashes) == 50
assert not hashes.intersection(x['source_type_sha256'] for x in old)
assert not hashes.intersection(retired['source_type_sha256'])
for row in selected:
    assert declarations[row['theorem']].to_dict() == row
    for mode in ('formalized', 'counterexample'):
        eligible, violations, collisions = production_eligibility(catalog, declarations[row['theorem']], mode)
        assert eligible and not violations and not collisions, (row['theorem'], violations, collisions)

tracker = {}
for block in re.split(r'(?m)(?=^- number:)', (r/'erdosproblems/data/problems.yaml').read_text()):
    number = re.match(r'- number: "(\d+)"', block)
    if number:
        tracker[int(number[1])] = {key: re.search(r'(?m)^  '+key+r':\n    state: "([^"]+)"', block)[1]
                                  for key in ('status', 'informal_status', 'formal_status')}
statuses = {}
for row in selected:
    if row['theorem'].startswith('Erdos'):
        n = int(re.match(r'Erdos(\d+)', row['theorem'])[1])
        status = tracker[n]
        assert status['status'] in {'open', 'falsifiable', 'verifiable', 'decidable'} and status['informal_status'] in {'open', 'falsifiable', 'verifiable', 'decidable'}, (row['theorem'], status)
        assert status['formal_status'] == 'unformalized', (row['theorem'], status)
        statuses[row['theorem']] = status

dependencies = read(r/'type-dependencies-strict.json')
assert {x['theorem'] for x in dependencies} == names and len(dependencies) == 50
assert all(not x['type_has_sorry'] and not x['dependencies_with_nonstandard_axioms'] for x in dependencies)
generation = read(r/'generation-final.json')
assert {(x['theorem'], x['mode']) for x in generation} == {(n, m) for n in names for m in ('formalized', 'counterexample')}
assert len(generation) == 100 and all(x['status'] == 'passed' for x in generation)
battery = read(r/'attack-audit/sweep-final.json')
assert not any(x['unmapped_errors'] for x in battery['shards'])
assert not battery['duplicate_target_hashes']
assert len(battery['attempts']) == 4200
assert {x['theorem'] for x in battery['attempts']} == names
hits = [x for x in battery['attempts'] if x['compiled']]
assert all(x['axioms_report'] for x in hits)
assert all(any('sorryAx' in s for s in x['axioms_report']) for x in hits)
assert len(read(r/'review-notes.json')) == 50
assert set(read(r/'review-notes.json')) == names
upstream = read(r/'upstream-source-checks.json')
assert len(upstream) == 50 and {x['theorem'] for x in upstream} == names
assert all(x['current_category'] == 'research open' for x in upstream)
assert len({(x['theorem'], x['mode'], x['attack']) for x in battery['attempts']}) == 4200
assert len(read(r/'open-prs.json')) == 359
assert all(x['status'] == 'passed' for x in read(r/'pr-file-fetches.json'))

summary = {
    'status': 'passed', 'added_targets': 50, 'added_source_paths': len({x['source_path'] for x in selected}),
    'new_source_paths': len({x['source_path'] for x in selected} - {x['source_path'] for x in old}),
    'target_compile_checks': 100, 'type_dependency_checks': 50, 'current_upstream_open_checks': 50,
    'active_or_retired_identity_collisions': 0, 'known_proof_type_collisions': 0,
    'tracker_statuses': statuses,
    'tactic_attempts': 4200, 'compiled_search_hits_with_sorryAx': len(hits),
    'compiled_hits_without_sorryAx': 0, 'unmapped_diagnostics': 0,
    'tactic_limitations': 'Bounded 20,000-heartbeat probes. Declaration search reused earlier failed probe declarations containing sorryAx; these are rejected, not solutions. This is not an exhaustive proof search.',
    'solution_review_limitations': 'Dated source, forum, PR and selected literature review; no known exact solution found, not a guarantee that no solution exists or has appeared elsewhere.'
}
(r/'admission-gates.json').write_text(json.dumps(summary, indent=2, sort_keys=True)+'\n')
print(json.dumps({k:v for k,v in summary.items() if k != 'tracker_statuses'}, indent=2))
