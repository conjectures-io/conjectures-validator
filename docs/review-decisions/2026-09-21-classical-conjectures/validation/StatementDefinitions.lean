import Lean
import Lean.Util.CollectAxioms

open Lean Meta

def canonical (e : Expr) : MetaM String :=
  withOptions (fun o => o.setBool `pp.all true |>.setBool `pp.proofs true
    |>.setBool `pp.universes true |>.setBool `pp.explicit true) do
    return (← ppExpr e).pretty

unsafe def main (args : List String) : IO Unit := do
  let [input, output] := args | throw (IO.userError "expected input and output paths")
  let j ← IO.ofExcept <| Json.parse (← IO.FS.readFile input)
  let modules ← IO.ofExcept (j.getObjValAs? (Array String) "modules")
  let names ← IO.ofExcept (j.getObjValAs? (Array String) "theorems")
  initSearchPath (← findSysroot)
  Lean.enableInitializersExecution
  let env ← importModules (modules.map fun s => { module := (s.splitOn ".").foldl Name.str Name.anonymous }) {} (trustLevel := 0)
  let action : CoreM Json := do
    let mut owners : Std.HashMap Name Name := {}
    for i in [:env.header.moduleNames.size] do
      let m := env.header.moduleNames[i]!
      if m.toString.startsWith "FormalConjectures" then
        for ci in env.header.moduleData[i]!.constants do
          owners := owners.insert ci.name m
    let mut rows := #[]
    for name in names do
      let target ← getConstInfo name.toName
      let mut queue := target.type.getUsedConstants
      let mut seen : Std.HashSet Name := {}
      let mut definitions : Array Json := #[]
      let mut idx := 0
      while idx < queue.size do
        let n := queue[idx]!
        idx := idx + 1
        if seen.contains n then continue
        seen := seen.insert n
        let some owner := owners[n]? | continue
        let ci ← getConstInfo n
        queue := queue ++ ci.type.getUsedConstants
        let value := match ci with
          | .thmInfo _ => none
          | _ => ci.value?
        if let some v := value then queue := queue ++ v.getUsedConstants
        definitions := definitions.push <| Json.mkObj [
          ("name", toJson n.toString), ("module", toJson owner.toString),
          ("type", toJson (← MetaM.run' (canonical ci.type))),
          ("value", toJson (← value.mapM fun v => MetaM.run' (canonical v)))]
      let mut bad : Array String := #[]
      for n in target.type.getUsedConstants do
        for ax in (← collectAxioms n) do
          if ax != ``propext && ax != ``Quot.sound && ax != ``Classical.choice then
            if !bad.contains ax.toString then bad := bad.push ax.toString
      rows := rows.push <| Json.mkObj [
        ("theorem", toJson name),
        ("type", toJson (← MetaM.run' (canonical target.type))),
        ("type_has_sorry", toJson target.type.hasSorry),
        ("nonstandard_statement_axioms", toJson bad),
        ("definitions", toJson definitions)]
    return toJson rows
  let options := ({} : Options).set `maxHeartbeats (0 : Nat) |>.set `maxRecDepth (100000 : Nat)
  let (result, _) ← Core.CoreM.toIO action { fileName := "", fileMap := default, options } { env := env }
  IO.FS.writeFile output result.pretty
