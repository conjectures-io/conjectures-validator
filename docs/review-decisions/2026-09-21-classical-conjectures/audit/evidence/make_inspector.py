import json
from pathlib import Path
root=Path(__file__).resolve().parent
rows=json.load(open(root.parent/'candidates.json'))['candidates']
def lname(s):return '.'.join('«'+x+'»' if x.isdigit() else x for x in s.split('.'))
imports=sorted(set(lname(r['source_path'][:-5].replace('/','.')) for r in rows))
s='import Lean\nimport Lean.Util.CollectAxioms\n'+''.join('import '+m+'\n' for m in imports)
s+='''
open Lean Meta Elab Command
set_option maxHeartbeats 0
set_option maxRecDepth 100000

def inspectTarget (name : Name) : MetaM Json := do
  let info ← getConstInfo name
  let mut typeAxioms : Array Name := #[]
  for c in info.type.getUsedConstants do
    for ax in (← collectAxioms c) do
      if !typeAxioms.contains ax then typeAxioms := typeAxioms.push ax
  let pretty ← ppExpr info.type
  let canonical ← withOptions (fun o => o.setBool `pp.all true |>.setBool `pp.proofs true |>.setBool `pp.universes true) (ppExpr info.type)
  let bodyAxioms ← collectAxioms name
  return Json.mkObj [
    ("theorem", toJson name.toString),
    ("type_pretty", toJson pretty.pretty),
    ("type_canonical", toJson canonical.pretty),
    ("type_has_sorry", toJson info.type.hasSorry),
    ("type_dependency_axioms", toJson (typeAxioms.map Name.toString)),
    ("declaration_axioms", toJson (bodyAxioms.map Name.toString))]

run_meta do
  let mut rows : Array Json := #[]
'''
for r in rows:
 s+='  rows := rows.push (← inspectTarget `'+r['theorem']+')\n'
s+='  IO.FS.writeFile "/audit/evidence/elaborated-targets.json" (toJson rows).pretty\n'
(root/'Inspect.lean').write_text(s)
