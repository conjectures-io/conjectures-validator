import Lean
import Lean.Util.CollectAxioms

open Lean
unsafe def main (args : List String) : IO Unit := do
  let [input] := args | throw (IO.userError "expected input json")
  let data ← IO.FS.readFile input
  let j ← IO.ofExcept (Json.parse data)
  let modules ← IO.ofExcept (j.getObjValAs? (Array String) "modules")
  let names ← IO.ofExcept (j.getObjValAs? (Array String) "theorems")
  initSearchPath (← findSysroot)
  Lean.enableInitializersExecution
  let env ← importModules (modules.map fun s => { module := s.toName }) {} (trustLevel := 0)
  let action : CoreM Json := do
    let mut rows := #[]
    for s in names do
      let ci ← getConstInfo s.toName
      let mut deps := #[]
      for n in ci.type.getUsedConstants do
        let axs ← collectAxioms n
        if axs.any (fun n => n != ``propext && n != ``Quot.sound && n != ``Classical.choice) then
          deps := deps.push (Json.mkObj [("constant",toJson n.toString),("axioms",toJson (axs.map Name.toString))])
      rows := rows.push (Json.mkObj [("theorem",toJson s),("type_has_sorry",toJson ci.type.hasSorry),("dependencies_with_nonstandard_axioms",toJson deps)])
    return toJson rows
  let (result,_) ← Core.CoreM.toIO action { fileName := "", fileMap := default } { env := env }
  IO.println result.compress
