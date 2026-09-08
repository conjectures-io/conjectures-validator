"""Reproduce the local September 8 retirement and 10 MB policy release.

Run retire in a clean clone of the preceding release, commit the deletions, run
scripts/generate_retired_conjectures.py, then run rebuild and commit the result.
Only manifests change for surviving targets; previous compiled payloads remain
byte-identical. This script does not claim to rerun the Lean compiler.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

VALIDATOR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(VALIDATOR))
from verifier.catalog import load_catalog
from verifier.hashing import pretty_json, sha256_bytes
from verifier.task_generator import task_id
from verifier.task_loader import load_task_bundle
from verifier.task_pool import (
    build_task_allowlist, group_task_declarations, load_retired_sources,
    load_selection_audit, load_task_grouping, load_task_targets,
    select_task_declarations,
)
from verifier.task_registry import TaskPoolRegistry, TaskNotAllowed

BASE = 'a0282e5029b6102a64b113aefdfe6f4767136f40'
REVIEW = Path(__file__).resolve().parent

def read(path):
    return json.loads(path.read_text())

def write(path, value):
    path.write_text(pretty_json(value))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('retire', 'rebuild'))
    parser.add_argument('--tasks-root', type=Path, required=True)
    parser.add_argument('--previous-root', type=Path, required=True)
    args = parser.parse_args()
    root, previous = args.tasks_root.resolve(), args.previous_root.resolve()
    assert root != previous
    assert subprocess.check_output(['git','-C',str(previous),'rev-parse','HEAD'],text=True).strip() == BASE
    assert not subprocess.check_output(['git','-C',str(previous),'status','--porcelain'],text=True).strip()
    metadata = root/'tiers/tier-1'
    removals = {x['theorem']:x for x in read(REVIEW/'withdrawals.json')}
    decisions = {x['theorem']:x for x in read(REVIEW/'target-decisions.json')}
    old_bundles = {p.name:load_task_bundle(p) for p in (previous/'pool/tier-1').iterdir() if p.is_dir()}
    old_policy = read(previous/'allowlist.json')
    assert len(old_bundles) == 518
    old_registry = TaskPoolRegistry.load(previous/'allowlist.json')
    for bundle in old_bundles.values():
        old_registry.assert_bundle(bundle)

    if args.phase == 'retire':
        assert subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip() == BASE
        retired = read(metadata/'retired-source-theorems.json')
        for name, bundle in old_bundles.items():
            if bundle.manifest.source_theorem in removals:
                shutil.rmtree(root/'pool/tier-1'/name)
                retired['source_theorems'].append(bundle.manifest.source_theorem)
                retired['source_type_sha256'].append(bundle.manifest.source_type_hash)
        for key in ('source_theorems','source_type_sha256'):
            retired[key] = sorted(set(retired[key]))
        write(metadata/'retired-source-theorems.json', retired)
        for name,key in [('task-targets.json','targets'),('selection-audit.json','selected')]:
            doc = read(metadata/name)
            doc[key] = [x for x in doc[key] if x['theorem'] not in removals]
            if key == 'selected':
                doc['audit_date_utc'] = '2026-09-08'
                doc['github_open_pr_count'] = 359
                doc['source_main_commit'] = '2c817e975be7a95478b72a8429155ca568e1a3de'
                doc['source_status_sources'][0]['revision'] = '5308c57c700559416b9f205df274b136784203e7'
                for row in doc[key]:
                    row['open_prs_touching_source'] = sorted(x['number'] for x in decisions[row['theorem']]['open_prs'])
            write(metadata/name, doc)
        log = (metadata/'RETIREMENTS.md').read_text().splitlines()
        lines = [s for s in log if s.startswith('- ')]
        for name, item in removals.items():
            code = 'SOLVED + NOT_OPEN' if item['decision']=='retire_settled' else item['decision'].upper()
            lines.append(f"- `{name}` — 2026-09-08 — `{code} ({item['reason']})`")
        (metadata/'RETIREMENTS.md').write_text('# Source theorem retirements\n\n'+'\n'.join(sorted(lines))+'\n')
        # Keep historical payloads and commitments unchanged in this deletion commit.
        policy = old_policy
        policy['allowed_source_theorems'] = [x for x in policy['allowed_source_theorems'] if x['theorem'] not in removals]
        policy['allowed_task_bundles'] = [x for x in policy['allowed_task_bundles'] if not set(x['theorems']) & removals.keys()]
        policy['audit_date_utc'] = '2026-09-08'
        tier = policy['tier_policies']['tier-1']
        tier.update(pool_size=502,source_theorem_count=251,reward_target_count=251,minimum_erdos_tasks=227)
        for field,file in [('selection_audit_sha256','selection-audit.json'),('task_targets_sha256','task-targets.json'),('retired_source_theorems_sha256','retired-source-theorems.json')]:
            tier[field] = sha256_bytes((metadata/file).read_bytes())
        write(root/'allowlist.json',policy)
        print('Removed eight targets in both modes; commit now, then regenerate retired display metadata.')
        return

    migrations=[]
    for name, old in old_bundles.items():
        if old.manifest.source_theorem in removals:
            assert not (root/'pool/tier-1'/name).exists()
            continue
        directory = root/'pool/tier-1'/name
        manifest = read(directory/'manifest.json')
        assert manifest['max_submission_bytes'] == 1_000_000
        manifest['max_submission_bytes'] = 10_000_000
        manifest['task_id'] = task_id(manifest['repository_commit'],manifest['source_theorem'],manifest['task_mode'],manifest['adapter_version'],max_submission_bytes=10_000_000)
        assert manifest['task_id'] != old.manifest.task_id
        write(directory/'manifest.json',manifest)
        new = load_task_bundle(directory)
        for file in old.files:
            if file != 'manifest.json':
                assert old.files[file] == new.files[file], (name,file)
        migrations.append(dict(theorem=manifest['source_theorem'],mode=manifest['task_mode'],directory=name,old_task_id=old.manifest.task_id,new_task_id=new.manifest.task_id,old_sha256=old.sha256,new_sha256=new.sha256,old_limit=1_000_000,new_limit=10_000_000,trusted_payloads_unchanged=True))
    catalog = load_catalog(VALIDATOR/'data/catalog.json')
    retired = load_retired_sources(metadata/'retired-source-theorems.json')
    audit = load_selection_audit(metadata/'selection-audit.json')
    targets = load_task_targets(metadata/'task-targets.json')
    grouping = load_task_grouping(metadata/'task-groups.json')
    selected = select_task_declarations(catalog=catalog,retired=retired,selection_audit=audit,task_targets=targets,pool_size=251)
    bundles = [load_task_bundle(p) for p in sorted((root/'pool/tier-1').iterdir()) if p.is_dir()]
    policy = json.loads(build_task_allowlist(catalog=catalog,retired=retired,selection_audit=audit,task_targets=targets,grouping=grouping,selected=group_task_declarations(selected,grouping),bundles=bundles,audit_date_utc='2026-09-08'))
    # The current generic builder omits the display-only digest required by this
    # task-repository release schema; bind it explicitly before registry loading.
    policy['tier_policies']['tier-1']['retired_conjectures_sha256'] = sha256_bytes((metadata/'retired-conjectures.json').read_bytes())
    write(root/'allowlist.json',policy)
    registry = TaskPoolRegistry.load(root/'allowlist.json')
    for bundle in bundles:
        registry.assert_bundle(bundle)
        assert bundle.manifest.max_submission_bytes == 10_000_000
    assert len(bundles)==502 and len(selected)==251
    assert Counter(x.source_path.split('/')[1] for x in selected)=={'ErdosProblems':227,'GreensOpenProblems':24}
    assert len({x.source_path for x in selected})==216
    assert len({x['new_task_id'] for x in migrations})==502
    assert not {x['new_task_id'] for x in migrations} & {b.manifest.task_id for b in old_bundles.values()}
    for bundle in old_bundles.values():
        try:
            registry.assert_bundle(bundle)
        except TaskNotAllowed:
            pass
        else:
            raise AssertionError('Old release task unexpectedly admitted')
    for name in removals:
        assert name in retired.theorems
        assert decisions[name]['type_hash'] in retired.type_hashes
    assert len(re.findall(r'^- `', (metadata/'RETIREMENTS.md').read_text(), re.M)) == 25
    write(REVIEW/'task-id-migration.json', sorted(migrations,key=lambda x:x['old_task_id']))
    write(REVIEW/'release-validation.json',dict(previous_tasks_commit=BASE,old_bundles_loaded=518,new_bundles_loaded_and_allowlisted=502,retained_targets=251,erdos_targets=227,green_targets=24,source_paths=216,removed_targets=8,denied_previous_task_ids=518,retired_names=len(retired.theorems),retired_type_hashes=len(retired.type_hashes),audited_retirement_log_entries=25,retired_display_entries=len(read(metadata/'retired-conjectures.json')['retired']),trusted_payloads_byte_identical=True,source_target_hashes_unchanged=True,fresh_lean_compilation=False,max_proof_bytes=10_000_000,max_zip_bytes=10*1024*1024))
    print('Validated 502 replacement bundles; all trusted payloads unchanged, all previous IDs denied.')

if __name__ == '__main__':
    main()
