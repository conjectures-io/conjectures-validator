"""Retire old targets, then build a larger immutable pool from compiled bundles."""
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path('/tmp/pool-release-20260908')
VALIDATOR = ROOT / 'validator'
TASKS = ROOT / 'tasks'
BASE = Path('/opt/conjectures-validator/conjectures-tasks')
META = TASKS / 'tiers/tier-1'
REVIEW = VALIDATOR / 'docs/review-decisions/2026-09-08-production-release'
sys.path.insert(0, str(VALIDATOR))
from verifier.catalog import load_catalog
from verifier.hashing import pretty_json, sha256_bytes
from verifier.task_generator import MAX_SUBMISSION_BYTES, task_id
from verifier.task_loader import load_task_bundle
from verifier.task_pool import (build_task_allowlist, group_task_declarations,
    load_retired_conjectures, load_retired_sources, load_selection_audit,
    load_task_grouping, load_task_targets, select_task_declarations)
from verifier.task_registry import TaskPoolRegistry, TaskNotAllowed

def read(p): return json.loads(p.read_text())
def write(p, x): p.write_text(pretty_json(x))

catalog = load_catalog(VALIDATOR / 'data/catalog.json')
ds = {x.theorem: x for x in catalog.declarations}
withdrawals = read(VALIDATOR / 'docs/review-decisions/2026-09-08-pool-review/withdrawals.json')
removed = {x['theorem']: x for x in withdrawals}
old = {p.name: load_task_bundle(p) for p in (BASE / 'pool/tier-1').iterdir() if p.is_dir()}
assert len(old) == 416
active_removed = {x.manifest.source_theorem for x in old.values()} & removed.keys()
assert len(active_removed) == 6

if sys.argv[1] == 'retire':
    retired = read(META / 'retired-source-theorems.json')
    for name in removed:
        retired['source_theorems'].append(name)
        if name in ds: retired['source_type_sha256'].append(ds[name].type_hash)
    for key in ('source_theorems', 'source_type_sha256'):
        retired[key] = sorted(set(retired[key]))
    write(META / 'retired-source-theorems.json', retired)
    for directory, bundle in old.items():
        if bundle.manifest.source_theorem in active_removed:
            shutil.rmtree(TASKS / 'pool/tier-1' / directory)
    for filename, key in [('task-targets.json', 'targets'), ('selection-audit.json', 'selected')]:
        value = read(META / filename)
        value[key] = [x for x in value[key] if x['theorem'] not in active_removed]
        write(META / filename, value)
    log = (META / 'RETIREMENTS.md').read_text()
    lines = [x for x in log.splitlines() if x.startswith('- ')]
    # Green 72 was already removed. Record that this audit now also denies its old name/type.
    lines = [x.replace('deliberately absent from retired-source-theorems.json, so the corrected upstream statement can be re-audited on its own merits',
                      'the September 8 review also denies this old name and type; a corrected statement requires a new explicit audit') for x in lines]
    for name in sorted(active_removed):
        row = removed[name]
        lines.append(f"- `{name}` — 2026-09-08 — `{row['decision'].upper()} ({row['reason']})`")
    (META / 'RETIREMENTS.md').write_text('# Source theorem retirements\n\n' + '\n'.join(sorted(lines)) + '\n')
    policy = read(TASKS / 'allowlist.json')
    policy['allowed_source_theorems'] = [x for x in policy['allowed_source_theorems'] if x['theorem'] not in active_removed]
    policy['allowed_task_bundles'] = [x for x in policy['allowed_task_bundles'] if not set(x['theorems']) & active_removed]
    policy['audit_date_utc'] = '2026-09-08'
    tier = policy['tier_policies']['tier-1']
    tier.update(pool_size=404, source_theorem_count=202, reward_target_count=202, minimum_erdos_tasks=183)
    for field, filename in [('selection_audit_sha256','selection-audit.json'), ('task_targets_sha256','task-targets.json'), ('retired_source_theorems_sha256','retired-source-theorems.json')]:
        tier[field] = sha256_bytes((META / filename).read_bytes())
    write(TASKS / 'allowlist.json', policy)
    print('Retired six live targets; commit deletions before regenerating historical display data.')
    sys.exit(0)

assert sys.argv[1] == 'rebuild'
names = read(ROOT / 'final-targets.json')
assert len(names) == len(set(names)) == 259
prior = Path('/tmp/pool-review-20260908/tasks/tiers/tier-1')
targets = read(prior / 'task-targets.json')
audit = read(prior / 'selection-audit.json')
targets['repository_commit'] = audit['repository_commit'] = catalog.repository_commit
replacements = read(ROOT / 'replacement-targets.json')
upstream = {x['theorem']: x for x in read(ROOT / 'replacement-upstream-screen.json')}
for name in replacements:
    d = ds[name]
    number = int(d.source_path.rsplit('/', 1)[1].split('.')[0])
    common = dict(theorem=name, source_family='erdos', source_path=d.source_path, source_problem_number=number)
    targets['targets'].append(dict(**common, reward_target_id='fc-target:' + name))
    audit['selected'].append(dict(**common, source_status='verifiable' if number == 366 else 'open',
        upstream_status='research open', active_resolution_prs=[],
        open_prs_touching_source=sorted(x['number'] for x in upstream[name]['open_prs']),
        feasibility_signals=['compact-formal-target', 'partial-results-in-source', 'standard-mathlib-surface']))
for value, key in [(targets,'targets'), (audit,'selected')]:
    value[key] = sorted(value[key], key=lambda x: x['theorem'])
    assert [x['theorem'] for x in value[key]] == names
write(META / 'task-targets.json', targets)
write(META / 'selection-audit.json', audit)
migrations = []
for directory, bundle in old.items():
    m = bundle.manifest
    if m.source_theorem in active_removed: continue
    path = TASKS / 'pool/tier-1' / directory
    value = read(path / 'manifest.json')
    value['max_submission_bytes'] = MAX_SUBMISSION_BYTES
    value['task_id'] = task_id(m.repository_commit, m.source_theorem, m.task_mode, m.adapter_version, max_submission_bytes=MAX_SUBMISSION_BYTES)
    write(path / 'manifest.json', value)
    new = load_task_bundle(path)
    assert m.task_id != new.manifest.task_id
    assert all(bundle.files[f] == new.files[f] for f in bundle.files if f != 'manifest.json')
    migrations.append(dict(theorem=m.source_theorem, mode=m.task_mode, directory=directory,
        old_task_id=m.task_id, new_task_id=new.manifest.task_id, old_sha256=bundle.sha256,
        new_sha256=new.sha256, old_limit=m.max_submission_bytes, new_limit=MAX_SUBMISSION_BYTES,
        trusted_payloads_unchanged=True))
sys.path.insert(0, str(TASKS / 'scripts'))
from rebuild_task_pool import task_directory_name
for name in read(ROOT / 'new-targets.json'):
    for mode in ('formalized', 'counterexample'):
        src = ROOT / 'bundles' / (name.replace('.','-').lower() + '-' + mode)
        b = load_task_bundle(src)
        assert b.manifest.max_submission_bytes == MAX_SUBMISSION_BYTES
        assert b.manifest.repository_commit == catalog.repository_commit
        dst = TASKS / 'pool/tier-1' / task_directory_name(b.manifest)
        if dst.exists():
            assert load_task_bundle(dst).sha256 == b.sha256
        else:
            shutil.copytree(src, dst)
for p in (TASKS / 'pool').rglob('*'): p.chmod(0o755 if p.is_dir() else 0o644)
retired = load_retired_sources(META / 'retired-source-theorems.json')
selection_audit = load_selection_audit(META / 'selection-audit.json')
task_targets = load_task_targets(META / 'task-targets.json')
grouping = load_task_grouping(META / 'task-groups.json')
selected = select_task_declarations(catalog=catalog, retired=retired, selection_audit=selection_audit, task_targets=task_targets, pool_size=259)
bundles = [load_task_bundle(p) for p in sorted((TASKS / 'pool/tier-1').iterdir()) if p.is_dir()]
assert len(bundles) == 518
policy = json.loads(build_task_allowlist(catalog=catalog, retired=retired,
    retired_conjectures=load_retired_conjectures(META / 'retired-conjectures.json'),
    selection_audit=selection_audit, task_targets=task_targets, grouping=grouping,
    selected=group_task_declarations(selected, grouping), bundles=bundles, audit_date_utc='2026-09-08'))
write(TASKS / 'allowlist.json', policy)
registry = TaskPoolRegistry.load(TASKS / 'allowlist.json')
for b in bundles: registry.assert_bundle(b)
for b in old.values():
    try: registry.assert_bundle(b)
    except TaskNotAllowed: pass
    else: raise AssertionError('An old task ID was admitted by the new policy')
assert not ({b.manifest.task_id for b in old.values()} & {b.manifest.task_id for b in bundles})
assert len({d.source_path for d in selected}) == 223
assert sum(d.source_path.startswith('FormalConjectures/ErdosProblems/') for d in selected) == 235
assert len(migrations) == 404
write(REVIEW / 'task-id-migration.json', migrations)
write(REVIEW / 'release-validation.json', dict(previous_tasks_commit='68bb8ea0968994e0f36a2db015dbdbcc13902416',
    source_commit=catalog.repository_commit, lean_toolchain=catalog.lean_toolchain,
    previous_bundles=416, new_bundles=518, active_targets=259, erdos_targets=235, green_targets=24,
    source_paths=223, live_targets_retired=6, new_targets=57, compiled_new_bundles=114,
    retained_bundles_with_unchanged_trusted_payloads=404, previous_ids_denied=416,
    max_proof_bytes=MAX_SUBMISSION_BYTES))
print('Validated 259 targets / 518 bundles, including 57 new targets and 404 immutable payload migrations.')
