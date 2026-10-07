import Lean
import DependencyClosure

/-!
Batch form of `DependencyClosure` for task generation. Reads a request file

    {"local_roots": ["FormalConjectures", ...], "targets": [{"module": "...", "theorem": "..."}]}

imports every requested module once and writes one JSON object to OUTPUT: the loaded module
table and, for each target, its canonical statement and statement closure.
-/

open Lean Meta FormalConjecturesVerifier.Dependencies

def requestError (message : String) : IO α :=
  throw <| IO.userError s!"invalid dependency request: {message}"

unsafe def main (arguments : List String) : IO Unit := do
  let [requestPath, outputPath] := arguments
    | throw <| IO.userError "usage: dependency_extractor REQUEST.json OUTPUT.json"
  let request ← match Json.parse (← IO.FS.readFile requestPath) with
    | .ok value => pure value
    | .error message => requestError message
  let some (.arr rootValues) := (request.getObjVal? "local_roots").toOption
    | requestError "local_roots must be an array"
  let some (.arr targetValues) := (request.getObjVal? "targets").toOption
    | requestError "targets must be an array"
  let mut localRoots : Array Name := #[]
  for value in rootValues do
    let .str root := value | requestError "local roots must be strings"
    localRoots := localRoots.push root.toName
  let mut targets : Array (Name × Name) := #[]
  for value in targetValues do
    let some (.str module) := (value.getObjVal? "module").toOption
      | requestError "target module must be a string"
    let some (.str theoremName) := (value.getObjVal? "theorem").toOption
      | requestError "target theorem must be a string"
    targets := targets.push (module.toName, theoremName.toName)
  if localRoots.isEmpty || targets.isEmpty then
    requestError "local_roots and targets must be non-empty"
  initSearchPath (← findSysroot)
  Lean.enableInitializersExecution
  let modules := targets.foldl
    (fun acc (module, _) => if acc.contains module then acc else acc.push module) #[]
  let environment ← importModules (modules.map fun module => { module }) {} (trustLevel := 1024)
    (loadExts := true)
  let mut results : Array Json := #[]
  for (module, theoremName) in targets do
    let some info := environment.find? theoremName
      | throw <| IO.userError s!"target is not in the environment: {theoremName}"
    let context : Core.Context := { fileName := "", fileMap := default }
    let (canonical, _) ← (MetaM.run' (canonicalExpression info.type)).toIO context
      { env := environment }
    let closure ← closureJson environment localRoots theoremName module
    results := results.push (closure.setObjVal! "statement_canonical" (Json.str canonical))
  let output := Json.mkObj [
    ("schema_version", toJson (1 : Nat)),
    ("lean_githash", Json.str Lean.githash),
    ("local_roots", Json.arr (localRoots.map nameJson)),
    ("modules", ← moduleTableJson environment localRoots),
    ("targets", Json.arr results)
  ]
  IO.FS.writeFile outputPath output.compress
