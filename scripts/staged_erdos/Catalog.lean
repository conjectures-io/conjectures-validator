import Lean
import Lean.Util.CollectAxioms
import FormalConjecturesUtil.Attributes.Basic

open Lean Meta ProblemAttributes

def categoryString : Category → String
  | .textbook => "textbook"
  | .research .open => "research open"
  | .research .solved => "research solved"
  | .test => "test"
  | .API => "API"

def proofString : FormalProofKind → String
  | .formalConjecturesProof => "formal_conjectures"
  | .lean4 => "lean4"
  | .otherSystem => "other_system"

def optionalJson {α : Type} [ToJson α] : Option α → Json
  | some value => toJson value
  | none => Json.null

def canonical (expression : Expr) : MetaM String :=
  withOptions (fun options => options
      |>.setBool `pp.all true |>.setBool `pp.universes true
      |>.setBool `pp.explicit true |>.setBool `pp.proofs true) do
    return toString (← ppExpr expression)

unsafe def extract (modules : Array Name) : CoreM Json := do
  let env ← getEnv
  let mut declarations := #[]
  for tag in ← getTags do
    let some info := env.find? tag.declName | continue
    let some index := env.getModuleIdxFor? tag.declName | continue
    let moduleName := env.header.moduleNames[index.toNat]!
    if !modules.contains moduleName then continue
    let kind := match info with
      | .thmInfo _ => "theorem"
      | .defnInfo _ => "definition"
      | _ => "unsupported"
    let question ← getQuestionKind? tag.declName
    let isValue := question == some .value
    let proposition ← MetaM.run' (isProp info.type)
    let axioms ← collectAxioms tag.declName
    let mut typeAxioms : Array Name := #[]
    for dependency in info.type.getUsedConstants do
      for axiomName in ← collectAxioms dependency do
        if !typeAxioms.contains axiomName then typeAxioms := typeAxioms.push axiomName
    let classification := if isValue then "GENERAL_VALUE_ANSWER"
      else if proposition && kind == "theorem" then "DIRECT_PROP" else "UNSUPPORTED"
    let proof ← getFormalProofTag tag.declName
    let subjects := (← getSubjectTags).filter (·.declName == tag.declName)
    let ams := subjects.flatMap fun s => (s.subjects.filterMap (·.toNat?)).toArray
    let components := moduleName.components.map fun
      | .str .anonymous value => value
      | .num .anonymous value => toString value
      | component => component.toString
    let path := (String.intercalate "/" components) ++ ".lean"
    declarations := declarations.push <| Json.mkObj [
      ("theorem", toJson tag.declName.toString),
      ("module", toJson moduleName.toString),
      ("source_path", toJson path),
      ("category", toJson (categoryString tag.category)),
      ("ams_subjects", toJson ams),
      ("formal_proof_kind", optionalJson (proof.map (proofString ·.proofKind))),
      ("formal_proof_link", optionalJson (proof.map (·.proofLink))),
      ("question_kind", optionalJson (question.map toString)),
      ("type_axioms", toJson (typeAxioms.qsort Name.lt |>.map toString)),
      ("declaration_kind", toJson kind),
      ("type_pretty", toJson (← MetaM.run' do return toString (← ppExpr info.type))),
      ("type_canonical", toJson (← MetaM.run' (canonical info.type))),
      ("contains_answer_annotation", toJson isValue),
      ("answer_occurrences", toJson (#[] : Array Json)),
      ("contains_sorry_in_type", toJson (info.type.hasSorry || typeAxioms.contains ``sorryAx)),
      ("contains_sorry_in_value", toJson (info.value?.any (·.hasSorry))),
      ("depends_on_sorry", toJson (axioms.contains ``sorryAx)),
      ("transitive_axioms", toJson (axioms.qsort Name.lt |>.map toString)),
      ("has_parameters", toJson info.type.isForall),
      ("is_prop", toJson proposition),
      ("docstring", optionalJson (← findDocString? env tag.declName)),
      ("supported_modes", toJson (if classification == "DIRECT_PROP" then
        #["formalized", "counterexample"] else #[])),
      ("classification", toJson classification),
      ("pointer_target", Json.null),
      ("finite_constructors", toJson (#[] : Array String))]
  return Json.mkObj [("declarations", toJson declarations)]

unsafe def main (args : List String) : IO Unit := do
  let some directory := args.head? | throw <| IO.userError "expected source directory"
  let mut modules := #[]
  for entry in ← (System.FilePath.mk directory).readDir do
    if entry.path.extension == some "lean" then
      modules := modules.push <| Name.mkStr `FormalConjectures.ErdosProblems entry.path.fileStem.get!
  initSearchPath (← findSysroot)
  Lean.enableInitializersExecution
  let env ← importModules (modules.map fun m => { module := m }) {} (trustLevel := 1024) (loadExts := true)
  let options := ({} : Options).set `maxHeartbeats (0 : Nat)
  let ctx : Core.Context := { fileName := "", fileMap := default, options := options }
  let (result, _) ← Core.CoreM.toIO (extract modules) ctx { env := env }
  IO.println result.compress
