import Lean
import Lean.Util.CollectAxioms
import TaskSupport
import FormalConjecturesUtil.Attributes.Basic

open Lean Meta FormalConjecturesVerifier ProblemAttributes

def categoryString : Category → String
  | .textbook => "textbook"
  | .research .open => "research open"
  | .research .solved => "research solved"
  | .test => "test"
  | .API => "API"

def canonicalExpression (expression : Expr) : MetaM String := do
  withOptions (fun options =>
      options
        |>.setBool `pp.all true
        |>.setBool `pp.universes true
        |>.setBool `pp.explicit true
        |>.setBool `pp.proofs true) do
    return toString (← ppExpr expression)

def intendedType
    (sourceType : Expr)
    (classification mode : String) : MetaM Expr := do
  let positive ←
    if classification == "DIRECT_PROP" then
      pure sourceType
    else if classification == "PROP_ANSWER_WRAPPER" then
      extractPropAnswerTarget sourceType
    else if classification == "BOOL_ANSWER" || classification == "NAT_ANSWER" ||
        classification == "INT_ANSWER" || classification == "FINITE_ANSWER" then
      let answerInfo ← getConstInfo `Bounty.submittedAnswer
      let replacement := .const `Bounty.submittedAnswer (answerInfo.levelParams.map Level.param)
      let (target, count) ← replaceAnswer sourceType replacement
      unless count == 1 do throwError "expected one answer occurrence"
      pure target
    else
      throwError "unsupported inspector classification: {classification}"
  if mode == "negative" then
    return mkApp (.const ``Not []) positive
  return positive

unsafe def runInspector (arguments : List String) : IO Unit := do
  let [challengeModule, targetName, sourceModule, sourceName, classification, mode] := arguments
    | throw <| IO.userError "usage: TaskInspector CHALLENGE TARGET SOURCE_MODULE SOURCE CLASSIFICATION MODE"
  initSearchPath (← findSysroot)
  Lean.enableInitializersExecution
  let imports := #[
    { module := challengeModule.toName },
    { module := sourceModule.toName }
  ]
  let environment ← importModules imports {} (trustLevel := 0) (loadExts := true)
  let context : Core.Context := { fileName := "", fileMap := default }
  let action : CoreM Json := do
    let targetInfo ← getConstInfo targetName.toName
    let sourceInfo ← getConstInfo sourceName.toName
    let sourceAxioms ← collectAxioms sourceName.toName
    let categoryTags ← getTags
    let formalProofTags ← getFormalProofTags
    let category? := categoryTags.find? (fun tag => tag.declName == sourceName.toName)
    let hasFormalProof := formalProofTags.any (fun tag => tag.declName == sourceName.toName)
    let declarationKind := match sourceInfo with
      | .thmInfo _ => "theorem"
      | .defnInfo _ => "definition"
      | .opaqueInfo _ => "opaque"
      | .axiomInfo _ => "axiom"
      | .quotInfo _ => "quotient"
      | .inductInfo _ => "inductive"
      | .ctorInfo _ => "constructor"
      | .recInfo _ => "recursor"
    MetaM.run' do
      let intended ← intendedType sourceInfo.type classification mode
      let sourceCanonical ← canonicalExpression sourceInfo.type
      let targetCanonical ← canonicalExpression targetInfo.type
      let isMatch ← isDefEq targetInfo.type intended
      return Json.mkObj [
        ("source_type_canonical", toJson sourceCanonical),
        ("target_type_canonical", toJson targetCanonical),
        ("matches_intended_target", toJson isMatch),
        ("target_contains_sorry", toJson targetInfo.type.hasSorry),
        ("source_transitive_axioms", toJson (sourceAxioms.qsort Name.lt |>.toList.map (·.toString))),
        ("source_depends_on_sorry", toJson (sourceAxioms.contains ``sorryAx)),
        ("source_category", match category? with
          | some tag => toJson (categoryString tag.category)
          | none => Json.null),
        ("source_has_formal_proof", toJson hasFormalProof),
        ("source_declaration_kind", toJson declarationKind)
      ]
  let (result, _) ← Lean.Core.CoreM.toIO action context { env := environment }
  IO.println result.compress

unsafe def main (arguments : List String) : IO Unit :=
  runInspector arguments
