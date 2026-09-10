"""Save the dated review, reproducible outputs, and a portable task release."""
import gzip
import hashlib
import json
import re
import shutil
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path

r = Path('/tmp/add-50-20260908')
v = Path('/root/conjectures-validator')
out = v/'docs/review-decisions/2026-09-08-add-50'
read = lambda p: json.loads((r/p).read_text())
def save(name, data):
    (out/name).write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False)+'\n')
def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()
def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()

selected = read('selection.json')
notes = read('review-notes.json')
names = {x['theorem'] for x in selected}
commit = git(r/'tasks', 'rev-parse', 'HEAD')
assert commit == json.loads((v/'pins.lock.json').read_text())['tasks']['commit']
assert not git(r/'tasks', 'status', '--porcelain')
assert read('admission-gates.json')['status'] == 'passed'
save('selected-targets.json', selected)
for source, dest in [
    ('review-notes.json', 'review-notes.json'),
    ('rejected.json', 'rejected.json'),
    ('generation-final.json', 'generation.json'),
    ('type-dependencies-strict.json', 'type-dependencies.json'),
    ('upstream-source-checks.json', 'upstream-source-checks.json'),
    ('admission-gates.json', 'admission-gates.json'),
    ('publication.json', 'publication.json'),
    ('final-verification.json', 'final-verification.json'),
    ('selected-prs-final.json', 'selected-prs.json'),
    ('tests.xml', 'tests.xml'),
]:
    shutil.copyfile(r/source, out/dest)

fetches = []
for p in sorted(r.glob('*fetches.json')):
    fetches.extend(dict(manifest=p.name, **row) for row in json.loads(p.read_text()))
save('source-fetches.json', fetches)

# Retain search terms and result links, not copies of third-party articles.
literature = read('literature-tool-results.json')
save('literature-searches.json', {
    key: {'queries': value.get('queries', []) if isinstance(value, dict) else [], 'result_urls': sorted(set(
        x.rstrip(').,;') for x in re.findall(r'https?://[^\s<>"\uE200-\uF8FF]+', str(value.get('result', '') if isinstance(value, dict) else value))
    ))} for key, value in literature.items()
})

extra_rejections = [
    {'target': 'Erdos64.erdos_64', 'reason': 'Exact public proof claim dated August 19, 2026; withheld pending adjudication.', 'source': 'https://zenodo.org/records/22019344'},
    {'target': 'Erdos75.erdos_75', 'reason': 'Public Ulam/Specker-graph argument claims the exact n^(1-epsilon) upper bound; the discussion suggests an overlooked classical consequence.', 'source': 'https://www.erdosproblems.com/forum/thread/75#post-5344'},
    {'target': 'Erdos254.erdos_254', 'reason': 'Exact complete formal solution claim on the authors\' public project; withheld independently of tracker status.', 'source': 'https://www.starfleetmath.com/'},
    {'target': 'Erdos260.erdos_260', 'reason': 'Public corrected complete proof manuscript and accompanying formalization work.', 'source': 'https://arxiv.org/abs/2606.24972'},
    {'target': 'Erdos267.erdos_267', 'reason': 'Current upstream marks this solved and the authors\' project claims an exact formal proof, although the tracker snapshot remains open.', 'source': 'https://github.com/google-deepmind/formal-conjectures/blob/2c817e975be7a95478b72a8429155ca568e1a3de/FormalConjectures/ErdosProblems/267.lean'},
    {'target': 'Erdos141, infinite three-term arithmetic progressions of consecutive primes', 'reason': 'Equivalent to the retired balanced-primes question; excluded from new reward admission.', 'source': 'https://www.erdosproblems.com/141'},
    {'target': 'Erdos940, density of r-powerful numbers', 'reason': 'The proposed density-zero target would settle an existing Erdos323 upper-bound reward.', 'source': 'https://www.erdosproblems.com/940'},
    {'target': 'Green52, logarithmic lower-bound variant', 'reason': 'The updated problem document records a Hamming-ball counterexample attributed to Hosseini and Alweiss.', 'source': 'https://people.maths.ox.ac.uk/greenbj/papers/open-problems.pdf'},
    {'target': 'Erdos855, density-one pair variant', 'reason': 'Nested eventual quantifiers permit a bound depending on the first variable, which does not encode the intended uniform sufficiently-large pair assertion.', 'source': 'https://www.erdosproblems.com/855'},
    {'target': 'Erdos996, Fourier approximation target', 'reason': 'The pinned sum uses the wrong coefficient index and its norm does not encode the stated approximation error.', 'source': 'https://www.erdosproblems.com/996'},
    {'target': 'Erdos1093, smooth consecutive composite target', 'reason': 'Strict versus weak smoothness boundary discrepancy is under correction in PRs 4975 and 4986.', 'source': 'https://github.com/google-deepmind/formal-conjectures/pull/4975'},
    {'target': 'Green31, finite-field Sidon formulation', 'reason': 'The chosen Sidon predicate includes diagonal collisions in characteristic two, making the target unsuitable.', 'source': 'https://github.com/google-deepmind/formal-conjectures/blob/2c817e975be7a95478b72a8429155ca568e1a3de/FormalConjectures/GreensOpenProblems/31.lean'},
]
save('additional-screening-exclusions.json', extra_rejections)

scripts = out/'audit-scripts'
scripts.mkdir(exist_ok=True)
for name in ['TypeDependenciesStrict.lean', 'run_dependencies_strict.py', 'generate.py',
             'generate_overlap.py', 'run_battery.py', 'run_battery_overlap.py',
             'finalize_evidence.py', 'validate_slate.py', 'publish.py', 'final_verify.py', 'package_review.py']:
    shutil.copyfile(r/name, scripts/name)

# The archive contains only generated probes, diagnostics, and summaries, never workspaces
# or downloaded article bodies. Both original runs stay intact for provenance.
with tarfile.open(out/'tactic-evidence.tar.gz', 'w:gz') as tar:
    for parent in ['attack-audit', 'attack-audit-overlap']:
        for p in sorted((r/parent).rglob('*')):
            rel = p.relative_to(r)
            if p.is_file() and (p.parent == r/parent or rel.parts[1] in {'raw', 'sources'}):
                tar.add(p, arcname=str(rel), recursive=False)
battery = read('attack-audit/sweep-final.json')
save('tactic-summary.json', {
    'aggregate': dict(battery['aggregate'], shards=10),
    'provenance': battery['provenance'],
    'finished_at_utc': battery['finished_at_utc'],
    'compiled_hits_with_sorryAx': 200,
    'compiled_hits_without_sorryAx': 0,
    'unmapped_errors': sum(len(x['unmapped_errors']) for x in battery['shards']),
    'per_target': [{
        'theorem': name,
        'attempts': len([x for x in battery['attempts'] if x['theorem'] == name]),
        'compiled_hits_with_sorryAx': len([x for x in battery['attempts'] if x['theorem'] == name and x['compiled']]),
    } for name in sorted(names)],
})

revisions = {
    'audit_date_utc': '2026-09-08',
    'tasks_baseline': '8dd81458db1cdcd2ec4fd3e6f866aa1907006858',
    'tasks_release': commit,
    'tasks_branch': 'codex/add-50-reviewed-20260908',
    'bundle_prerequisite': 'c1829b7c28bd54a59a9f1f2dcb9834b1cab53cfd',
    'validator_workspace_head': git(v, 'rev-parse', 'HEAD'),
    'compatible_allowlist_builder_and_lean_environment': 'a8d559db1d4d6ccdd2f7cc07e7d7dd5d45a8afb2',
    'formal_conjectures_pinned': '379fc0298dc146df549e7061c3ede0353a5bb51f',
    'formal_conjectures_reviewed_main': '2c817e975be7a95478b72a8429155ca568e1a3de',
    'erdos_tracker': '5308c57c700559416b9f205df274b136784203e7',
    'lean': 'leanprover/lean4:v4.27.0',
    'catalog_sha256': sha(v/'data/catalog.json'),
    'green_problem_document_url': 'https://people.maths.ox.ac.uk/greenbj/papers/open-problems.pdf',
    'green_problem_document_sha256': sha(r/'green-problems.pdf'),
    'open_issue_records': len(read('open-issues.json')),
    'recently_closed_issue_records': len(read('closed-issues.json')),
    'open_pull_requests_with_file_lists': len(read('open-prs.json')),
    'external_snapshot_sha256': {name: sha(r/name) for name in [
        'open-issues.json', 'closed-issues.json', 'open-prs.json', 'starfleetmath.html',
    ]},
}
save('revisions.json', revisions)

def escape(s):
    return s.replace('|', '\\|').replace('\n', ' ')
lines = [f'''# Review decision: 50 additional theorem targets — 2026-09-08

Admit the 50 exact targets below into the **local proposed release**, after semantic review and a dated search for prior mathematical or formal solutions. They comprise **45 Erdős and 5 Green targets across 48 numbered source entries**. Erdős 323 contributes two distinct lower-bound questions and Erdős 936 contributes separate factorial-plus-one and factorial-minus-one questions. These are 50 reward targets, not 50 distinct problem numbers. Forty source files are new to the active pool; eight were already represented by other parts.

The resulting pool contains **259 theorem targets and 518 immutable bundles**, comprising 234 Erdős and 25 Green targets across 222 source files. Each new target has one proof and one refutation bundle. The previous 209 targets, their 418 bundles, all 2,926 bundle files, and existing allowlist rows are unchanged. No task repository push, validator commit, deployment, or production activation was performed.

The novelty finding is **“no known exact formal or informal solution found in the reviewed sources as of September 8, 2026.”** This is a literature and source review, not a proof that no solution exists. Compilation establishes type fidelity to the pinned Lean statement; correspondence with informal mathematics remains an explicit semantic review judgment. An unresolved credible exact solution claim was sufficient to withhold a candidate.

## Revisions and release

| Input | Revision |
| --- | --- |
| Previous task release, already selected in the user's working tree | `8dd81458db1cdcd2ec4fd3e6f866aa1907006858` — 209 targets |
| New local task release | `{commit}` |
| Local task branch | `codex/add-50-reviewed-20260908` |
| Pinned, patched Formal Conjectures | `379fc0298dc146df549e7061c3ede0353a5bb51f` |
| Current upstream reviewed | `2c817e975be7a95478b72a8429155ca568e1a3de` |
| Erdős Problems tracker reviewed | `5308c57c700559416b9f205df274b136784203e7` |
| Lean environment | `leanprover/lean4:v4.27.0` |

The task checkout is `/tmp/add-50-20260908/tasks`. The validator's `pins.lock.json` points at the new local commit. [tasks-release.bundle](tasks-release.bundle) makes the release durable outside that temporary checkout: it contains the final branch and the preceding unpublished 209-target release, with prerequisite `c1829b7c28bd54a59a9f1f2dcb9834b1cab53cfd`. Git verified the bundle against the existing sibling task repository. After obtaining that prerequisite, it can be imported into a chosen task checkout with:

```sh
git -C /path/to/conjectures-tasks fetch \\
  /root/conjectures-validator/docs/review-decisions/2026-09-08-add-50/tasks-release.bundle \\
  refs/heads/codex/add-50-reviewed-20260908:refs/heads/codex/add-50-reviewed-20260908
```

This report audits the **50 additions only**. It does not refresh the previous 209 novelty decisions. The historical selection-audit header and retained rows preserve their earlier provenance. The validator working tree was already modified for the preceding expansion; this work extends those pool counts and preserves unrelated changes.

## Checks and interpretation

1. Read each exact pinned declaration and the definitions used in its type. Compare its domain, quantifier order, constants, strict inequalities, exceptional cases, infima, density convention, and asymptotic strength with its informal source and relevant variants. Review mathematical overlap with active and retired questions, beyond exact-name and hash checks.
2. Check all 50 exact declarations on current upstream, the Erdős tracker states, problem pages and discussions, Green's updated problem document, relevant primary papers, and public solution projects. Review all **359 open pull-request file lists**, mapping changes to selected source files, plus relevant records among **1,121 open issues and 584 recently updated closed issues**. A file-level PR match does not mean the selected declaration is solved; the exact part and helper changes were examined. Conversely, an open tracker label is insufficient evidence of novelty.
3. Run production eligibility in both modes, rejecting active/retired identities or type hashes, answer annotations in the pinned type, formal-proof metadata, and collisions with cataloged proved types. Independently traverse constants used in every selected theorem's **type** and collect their transitive axioms. All 50 have no `sorry` in the type and no dependency axioms beyond `propext`, `Classical.choice`, and `Quot.sound`. The source conjectures themselves have admitted proof bodies, as expected; those bodies are not evidence that the conjectures are proved.
4. Generate **100 bundles** with compiled `target_validator` checks: the proof target is definitionally equal to the entire pinned proposition, and the refutation target to its negation. The independent inspector and production policy checks pass. Preserve all existing bundles, then verify all **518** against the rebuilt allowlist.
5. Run **42 bounded tactic probes per bundle**, with a 20,000-heartbeat limit: **4,200 probes for the final slate**. None yields an admissible solution. There are 200 successful elaborations from `exact?`/`apply?` that reuse earlier failed probe declarations containing `sorryAx`; all are rejected by the axiom audit. Probes share a shard environment, so they are not isolated proof searches. Diagnostics are fully mapped. The original 4,200-probe run included Green 94; 84 replacement probes cover Erdős 700. The final view includes 4,116 retained probes plus those 84, and both raw runs remain in the evidence archive.
6. Run the relevant validator suite against the final task checkout: **56 tests passed** (`test_task_pool`, `test_task_generator`, `test_task_loader`, `test_catalog`, `test_static_checks`, and `test_api_taskpool`, excluding integration-marked tests). This supplements the 100 compiled target checks; it is not a claim that the full application or deployment suite was run.

The compatible allowlist publisher uses the existing `a8d559db1d4d6ccdd2f7cc07e7d7dd5d45a8afb2` validator checkout, whose builder supports the retained-conjectures policy hash already required by the baseline release. The current workspace's generator and admission checks validated the new targets, and its tests and registry validated the resulting release. No unrelated builder API change was introduced.

## Accepted targets

Every row below passed all mechanical admission checks. The third column records the mathematical distinction that was checked when evaluating nearby solutions or statement changes. [selected-targets.json](selected-targets.json) records full pinned types and canonical hashes; [selected-prs.json](selected-prs.json) records the exact file-level PR matches.

| Exact theorem target | Meaning and formalization check | Prior-solution review and primary reference |
| --- | --- | --- |
''']
for row in selected:
    name = row['theorem']
    semantics, novelty, url = notes[name]
    lines.append(f'| `{name}` | {escape(semantics)} | {escape(novelty)} [Source]({url}). |\n')
lines.append('''
The retained Erdős 14 part ii is a statement about **nonunique-sum counts**. The preceding August review described it as a Sidon-density assertion; that description is corrected here without rewriting the historical report. Part i's `N^(1/2-epsilon)` lower bound is compatible with part ii's proposed `o(sqrt(N))` example.

Erdős 1101 part i needs a polarity note: the pinned source asks positively for a polynomial-growth good sequence, while current upstream expresses the conjectured negative answer. The underlying existence question is unchanged, and the two task modes expose both directions using the pinned proposition. Its separate subexponential-growth part ii has a public solution claim and is excluded.

For Erdős 700 part ii, the known prime-square examples attain equality, while this target requires strict inequality and infinitely many examples. The public families rely on unproved infinitude assertions about special primes or prime pairs. PR 5212 changes the characterization in part i, not the selected minimum or part ii. A public finite census does not establish infinitude.

## Rejected or quarantined candidates

Eleven initial candidates were removed. A later Green 94 candidate was also removed for reward overlap. “Quarantine” records an unresolved claim, not our endorsement or rejection of its proof.

| Candidate | Decision and reason | Evidence |
| --- | --- | --- |
''')
for row in read('rejected.json'):
    refs = f"[Source]({row['source']})"
    if row.get('additional_source'):
        refs += f"; [additional source]({row['additional_source']})"
    lines.append(f"| `{row['theorem']}` | **{row['decision']}**: {escape(row['reason'])} | {refs} |\n")
lines.append('''
Erdős 968 illustrates why a source's open annotation cannot settle freshness. For `u_n = p_n/n`, the inequality `u_(n+1) > u_n` is equivalent to `p_(n+1)-p_n > p_n/n`. Published arbitrarily long chains of large consecutive prime gaps, together with the prime number theorem, imply infinitely many increasing triples. This implication is a review deduction from the cited primary result; it is not an assertion that the authors stated problem 968 by number.

Additional candidates screened while finding replacements were also withheld:

| Candidate | Reason | Source |
| --- | --- | --- |
''')
for row in extra_rejections:
    lines.append(f"| {escape(row['target'])} | {escape(row['reason'])} | [Source]({row['source']}) |\n")
lines.append('''
The StarFleet public project was checked against the selected slate. Its actual solution articles have no exact selected-target match; appearances of problems 14, 25, and 30 in its trailing question catalog are not proof claims. Problems 254 and 267 do have explicit solution claims and were withheld. This distinction prevents treating a catalog link as either a proof or reliable evidence of openness.

## Evidence and limits

- [admission-gates.json](admission-gates.json), [generation.json](generation.json), [type-dependencies.json](type-dependencies.json), [upstream-source-checks.json](upstream-source-checks.json), [publication.json](publication.json), and [final-verification.json](final-verification.json) record the mechanical checks and exact identities.
- [review-notes.json](review-notes.json), [rejected.json](rejected.json), [additional-screening-exclusions.json](additional-screening-exclusions.json), [selected-prs.json](selected-prs.json), [literature-searches.json](literature-searches.json), and [source-fetches.json](source-fetches.json) preserve the decisions, source URLs, query terms, retrieval timestamps, and available snapshot hashes.
- [tactic-summary.json](tactic-summary.json) summarizes the final slate. [tactic-evidence.tar.gz](tactic-evidence.tar.gz) preserves the final filtered view, both original probe runs, generated Lean probe sources, raw diagnostics, and axiom reports. [tests.xml](tests.xml) records the 56 passing tests.
- [revisions.json](revisions.json) pins source and build provenance. [audit-scripts](audit-scripts) contains the scripts as run, including the strict type-dependency auditor. Python scripts retain their historical `/tmp` checkout paths; they require those pinned repositories and toolchains or path adaptation. The publisher expects the clean 209-target baseline and will not overwrite a released pool. The bundled release itself can be restored without rerunning generation.
- [evidence-manifest.json](evidence-manifest.json) commits to all durable evidence files with SHA-256. Full third-party HTML/PDF snapshots and broader exploratory data remain in `/tmp/add-50-20260908`; the durable package retains their available hashes and source links, not complete article copies.

The search is bounded by public indexing, source availability, and the dated snapshots. It does not cover undisclosed manuscripts or guarantee discovery of every public proof. Hash collision checks establish exact syntactic identity; the additional semantic overlap review is not a decision procedure for mathematical equivalence. Tactic failure does not establish difficulty or independence. A new exact solution, credible unresolved claim, or demonstrated formalization mismatch should trigger a fresh admission review before activation or payment.
''')
(out/'REVIEW.md').write_text(''.join(lines))
save('evidence-manifest.json', {
    'created_at_utc': datetime.now(timezone.utc).isoformat(),
    'tasks_release': commit,
    'files': [{'path': str(p.relative_to(out)), 'sha256': sha(p), 'bytes': p.stat().st_size}
              for p in sorted(out.rglob('*')) if p.is_file() and p.name != 'evidence-manifest.json'],
})
print(json.dumps({'report': str(out/'REVIEW.md'), 'files': len(list(out.rglob('*'))),
                  'bytes': sum(p.stat().st_size for p in out.rglob('*') if p.is_file()),
                  'tasks_release': commit}, indent=2))
