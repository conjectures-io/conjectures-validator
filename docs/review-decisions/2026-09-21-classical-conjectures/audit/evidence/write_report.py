"""Assemble the audited shortlist without modifying the live task catalog."""
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

evidence = Path(__file__).resolve().parent
root = evidence.parent
data = json.loads((root / 'candidates.json').read_text())
notes = {r['number']: r for r in json.loads((evidence / 'audit_notes.json').read_text())}
types = {r['theorem']: r for r in json.loads((evidence / 'elaborated-targets.json').read_text())}
build = json.loads((evidence / 'build-results.json').read_text())
allowed = {'propext', 'Classical.choice', 'Quot.sound'}
assert len(notes) == len(types) == len(data['candidates']) == 40
assert len(build) == 87 and all(r['exit_code'] == 0 for r in build)
assert all(not r['type_has_sorry'] and set(r['type_dependency_axioms']) <= allowed for r in types.values())
assert all('sorryAx' in r['declaration_axioms'] for r in types.values())

report = (evidence / 'report_intro.md').read_text()
type_report = '# Elaborated target types\n\nLean 4.33.1; audited upstream snapshot; answer placeholders elaborated with `always_true`. These are statements, not proved results.\n\n'
for row in data['candidates']:
    n = row['number']
    note = notes[n]
    typ = types[row['theorem']]
    disposition = 'remove_resolved' if n == 35 else 'hold_recent_claim' if n in {4, 16, 40} else 'retain_provisionally_open'
    row.update(
        audit_date='2026-09-21', disposition=disposition,
        independent_status_review=note['status_note'],
        external_sources=note['sources'],
        formalization_review='audited upstream: compile pass; statement axiom closure clean; semantic review with noted limits',
        audit_note=note['semantic_note'],
        admission_ready=False,
        source_compile_pass=True,
        statement_has_sorry=typ['type_has_sorry'],
        statement_dependency_axioms=typ['type_dependency_axioms'],
        declaration_axioms=typ['declaration_axioms'],
        elaborated_type_pretty_sha256=hashlib.sha256(typ['type_pretty'].encode()).hexdigest(),
        requires_source_pin_fix=n in {27, 32, 38},
    )
    label = {'remove_resolved': 'REMOVE — resolved', 'hold_recent_claim': 'HOLD — proof claim review', 'retain_provisionally_open': 'RETAIN — provisionally open'}[disposition]
    sources = ', '.join(f'[Source {i}]({url})' for i, url in enumerate(note['sources'], 1))
    report += f"### {n}. {row['name']} — {label}\n\n"
    report += f"[Exact Lean source]({row['url']}) · `{row['theorem']}`\n\n"
    report += f"**Status:** {note['status_note']} {sources}.\n\n"
    report += f"**Formalization:** {note['semantic_note']}\n\n"
    type_report += f"## {n}. {row['name']}\n\n```lean\n{typ['type_pretty']}\n```\n\nStatement axioms: `{', '.join(typ['type_dependency_axioms'])}`.\n\n"

report += '''## Admission recommendation and limits

Continue with the 36 retained entries using the audited source revision, remove Köthe from the open list, and leave the three claim-review holds out of an unqualified open catalog. Before admission, generate each actual task package and confirm its statement/helper closure matches this audit, its answer polarity is correct, and a proposed proof cannot import an unresolved `sorry` theorem. This audit does not assert that any of the 36 will be easy to solve.

The clean statement-type closure is narrower than a blanket clean import environment: these files contain many deliberately admitted neighboring theorems. The verifier must still reject `sorryAx` and unapproved axioms in submitted proof dependencies. No successful proof submissions were exercised here, and the external Köthe proof was not replayed.

Historical “oldest” dates remain approximate editorial metadata. They were not re-established from original manuscripts. Exact source IDs were checked against the local 259-entry allowlist during the original screen; the live production catalog and semantic duplicates beyond the original exclusions were not exhaustively audited. This report is a dated assessment, not a claim that mathematical status cannot change tomorrow.
'''
old = root / 'FIRST_PASS_AUDIT.md'
if not old.exists():
    old.write_text('> SUPERSEDED by AUDIT.md. This first pass contains stale status assessments, including Köthe.\n\n' + (root / 'AUDIT.md').read_text())
(root / 'AUDIT.md').write_text(report)
(evidence / 'ELABORATED_TYPES.md').write_text(type_report)
data['audit_summary'] = dict(Counter(r['disposition'] for r in data['candidates']))
data['audit_summary'].update(compiled_targets=40, statement_axiom_checks_passed=40, lean_version='4.33.1', admission_ready=0)
(root / 'candidates.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
with (root / 'candidates.csv').open('w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=list(data['candidates'][0]))
    writer.writeheader()
    for row in data['candidates']:
        writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, list) else v for k, v in row.items()})
readme = root / 'README.md'
text = readme.read_text()
table_start = text.index('| # | Date or historical origin |')
text = '''# Original forty historical conjecture candidates — audited

**The original list is not 40 confirmed open problems.** The September 21 audit recommends **36 provisional retains, 3 holds for recent proof claims, and removal of Köthe as resolved**. See [the complete per-candidate audit](AUDIT.md), [machine-readable decisions](candidates.json), and [CSV](candidates.csv).

All 40 exact statements compiled in the actual **Lean 4.33.1** verifier image, and all 40 statement dependency checks passed. The older verifier source pin nevertheless contains defective formulations of normality of π and Hardy–Littlewood I; the audit used the newer corrected snapshot. None has been admitted by this work.

The following table preserves the **original shortlist and historical ordering**, including removed/held entries for traceability. It must not be copied as an unqualified open-problem catalog. Dates marked * are historical roots, not verified dates for the exact proposition; Green 72 and the Catalan–Mersenne all-terms question especially require attribution care.

Audited upstream: `2f82a24192f64ac678557247e496b2381d3889a1`. Original local allowlist screen: 259 declarations, no exact matches among these 40. Collatz and Littlewood were excluded as already represented; Mersenne-prime infinitude and even-perfect-number infinitude were counted once. Live production state was not queried.

''' + text[table_start:]
readme.write_text(text)
print(json.dumps(data['audit_summary'], indent=2))
