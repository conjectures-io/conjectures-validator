import Lean
import Lean.Util.CollectAxioms
import TaskSupport
import DependencyClosure
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
  let target ←
    if classification == "DIRECT_PROP" then
      pure sourceType
    else if classification == "BOOL_ANSWER" || classification == "NAT_ANSWER" ||
        classification == "INT_ANSWER" || classification == "FINITE_ANSWER" then
      let answerInfo ← getConstInfo `Bounty.submittedAnswer
      let replacement := .const `Bounty.submittedAnswer (answerInfo.levelParams.map Level.param)
      let (target, count) ← replaceAnswer sourceType replacement
      unless count == 1 do throwError "expected one answer occurrence"
      pure target
    else
      throwError "unsupported inspector classification: {classification}"
  if mode == "counterexample" then
    return mkApp (.const ``Not []) target
  if mode == "formalized" || mode == "answer" then
    return target
  throwError "unsupported inspector task mode: {mode}"

unsafe def runInspector (arguments : List String) : IO Unit := do
  -- The optional LOCAL_ROOTS and CLOSURE_OUTPUT derive the source statement's dependency
  -- closure from this very environment, for version-2 task identities.
  let (challengeModule, targetName, sourceModule, sourceName, classification, mode, closureRequest) ←
    match arguments with
    | [challenge, target, sourceModule, source, classification, mode] =>
        pure (challenge, target, sourceModule, source, classification, mode, none)
    | [challenge, target, sourceModule, source, classification, mode, roots, output] =>
        pure (challenge, target, sourceModule, source, classification, mode, some (roots, output))
    | _ => throw <| IO.userError <|
        "usage: TaskInspector CHALLENGE TARGET SOURCE_MODULE SOURCE CLASSIFICATION MODE " ++
          "[LOCAL_ROOTS CLOSURE_OUTPUT]"
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
  if let some (roots, output) := closureRequest then
    let localRoots := Dependencies.parseLocalRoots roots
    if localRoots.isEmpty then
      throw <| IO.userError "LOCAL_ROOTS must name at least one module root"
    let closure ← Dependencies.closureJson environment localRoots sourceName.toName
      sourceModule.toName
    IO.FS.writeFile output (Json.mkObj [
      ("schema_version", toJson (1 : Nat)),
      ("lean_githash", Json.str Lean.githash),
      ("local_roots", Json.arr (localRoots.map Dependencies.nameJson)),
      ("modules", ← Dependencies.moduleTableJson environment localRoots),
      ("targets", Json.arr #[closure])
    ]).compress
  IO.println result.compress

unsafe def main (arguments : List String) : IO Unit :=
  runInspector arguments
