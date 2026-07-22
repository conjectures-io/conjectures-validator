import Lean
import FormalConjecturesUtil.Attributes.Basic
import FormalConjecturesUtil.Answer

open Lean Meta ProblemAttributes Google

def categoryString : Category → String
  | .textbook => "textbook"
  | .research .open => "research open"
  | .research .solved => "research solved"
  | .test => "test"
  | .API => "API"

def formalProofString : FormalProofKind → String
  | .formalConjecturesProof => "formal_conjectures"
  | .lean4 => "lean4"
  | .otherSystem => "other_system"

partial def leanFiles (directory : System.FilePath) : IO (Array System.FilePath) := do
  if !(← directory.isDir) then return #[]
  let mut files := #[]
  for entry in ← directory.readDir do
    if ← entry.path.isDir then
      files := files ++ (← leanFiles entry.path)
    else if entry.path.extension == some "lean" then
      files := files.push entry.path
  return files

def moduleNameFor (file : System.FilePath) : IO Name := do
  let components := file.withExtension "" |>.components
  let mut result := Name.anonymous
  let mut inside := false
  for component in components do
    if component == "FormalConjectures" then inside := true
    if inside then result := Name.mkStr result component
  if result.isAnonymous then
    throw <| IO.userError s!"not under FormalConjectures: {file}"
  return result

def canonicalExpression (expression : Expr) : MetaM String := do
  withOptions (fun options =>
      options
        |>.setBool `pp.all true
        |>.setBool `pp.universes true
        |>.setBool `pp.explicit true
        |>.setBool `pp.proofs true) do
    return toString (← ppExpr expression)

def prettyExpression (expression : Expr) : MetaM String := do
  return toString (← ppExpr expression)

partial def hasForall : Expr → Bool
  | .forallE .. => true
  | _ => false

partial def stripForalls : Expr → Expr
  | .forallE _ _ body _ => stripForalls body
  | expression => expression

def answerIsIffSide (expression : Expr) : Bool :=
  let body := stripForalls expression
  let function := body.getAppFn
  let arguments := body.getAppArgs
  if !function.isConstOf ``Iff || arguments.size != 2 then false
  else
    let leftAnswers := findAnswerExprs arguments[0]!
    let rightAnswers := findAnswerExprs arguments[1]!
    let metadataPresent := !(findAnswerExprs expression).isEmpty
    let leftHas := if metadataPresent then !leftAnswers.isEmpty else arguments[0]!.isConstOf ``True
    let rightHas := if metadataPresent then !rightAnswers.isEmpty else arguments[1]!.isConstOf ``True
    leftHas != rightHas

partial def forallCount : Expr → Nat
  | .forallE _ _ body _ => 1 + forallCount body
  | _ => 0

def nullaryFiniteConstructors (answerType : Expr) : MetaM (Array Name) := do
  let some typeName := answerType.getAppFn.constName?
    | return #[]
  let .inductInfo inductiveInfo ← getConstInfo typeName
    | return #[]
  let mut constructors := #[]
  for constructorName in inductiveInfo.ctors do
    let constructor ← getConstInfo constructorName
    if forallCount constructor.type == inductiveInfo.numParams then
      constructors := constructors.push constructorName
    else
      return #[]
  return constructors

def answerDescription (expression : Expr) : MetaM Json := do
  let answerType ← inferType expression
  let kind := if ← isProp expression then "Prop" else "value"
  return Json.mkObj [
    ("kind", toJson kind),
    ("expression_pretty", toJson (← prettyExpression expression)),
    ("type_pretty", toJson (← prettyExpression answerType))
  ]

def classify (declarationKind : String) (type : Expr) (value? : Option Expr) : MetaM (String × Array Name) := do
  if declarationKind == "definition" || declarationKind == "opaque" then
    return (if value?.any (·.hasSorry) then "DEFINITION_HOLE" else "UNSUPPORTED", #[])
  let answers := findAnswerExprs type
  if answers.size > 1 then return ("MULTIPLE_ANSWER_HOLES", #[])
  if answers.isEmpty then
    return (if ← isProp type then "DIRECT_PROP" else "UNSUPPORTED", #[])
  let answer := answers[0]!
  if ← isProp answer then
    return (if answerIsIffSide type then "PROP_ANSWER_WRAPPER" else "UNSUPPORTED", #[])
  let answerType ← inferType answer >>= whnf
  if answerType.isConstOf ``Bool then return ("BOOL_ANSWER", #[``Bool.false, ``Bool.true])
  if answerType.isConstOf ``Nat then return ("NAT_ANSWER", #[])
  if answerType.isConstOf ``Int then return ("INT_ANSWER", #[])
  let constructors ← nullaryFiniteConstructors answerType
  if !constructors.isEmpty then return ("FINITE_ANSWER", constructors)
  return ("GENERAL_VALUE_ANSWER", #[])

partial def unwrapProof : Expr → Expr
  | .mdata _ inner => unwrapProof inner
  | .lam _ _ body _ => unwrapProof body
  | expression => expression

def pointerTarget? (environment : Environment) (self : Name) (value? : Option Expr) : Option Name := do
  let value ← value?
  if value.hasSorry then none else
  let body := unwrapProof value
  let target ← body.getAppFn.constName?
  if target == self then none else
  match environment.find? target with
  | some (.thmInfo _) => some target
  | _ => none

def supportedModes (classification : String) : List String :=
  if classification == "DIRECT_PROP" || classification == "PROP_ANSWER_WRAPPER" then
    ["positive", "negative"]
  else if classification == "BOOL_ANSWER" || classification == "NAT_ANSWER" ||
      classification == "INT_ANSWER" || classification == "FINITE_ANSWER" then
    ["answer"]
  else []

def sourcePath (moduleName : Name) : String :=
  let components := moduleName.components.map fun
    | .str .anonymous value => value
    | .num .anonymous value => toString value
    | component => component.toString
  String.intercalate "/" components ++ ".lean"

def optionalJson {α : Type} [ToJson α] (value : Option α) : Json :=
  match value with
  | some item => toJson item
  | none => Json.null

unsafe def withImports (modules : Array Name) (action : CoreM α) : IO α := do
  initSearchPath (← findSysroot)
  Lean.enableInitializersExecution
  let imports := modules.map fun moduleName => { module := moduleName }
  let environment ← importModules imports {} (trustLevel := 1024) (loadExts := true)
  let context : Core.Context := { fileName := "", fileMap := default }
  let (result, _) ← Lean.Core.CoreM.toIO action context { env := environment }
  return result

unsafe def extractCatalog (modules : Array Name) : CoreM Json := do
  let environment ← getEnv
  let categoryTags ← getTags
  let subjectTags ← getSubjectTags
  let formalProofTags ← getFormalProofTags
  let mut categories : Std.HashMap Name CategoryTag := {}
  for tag in categoryTags do categories := categories.insert tag.declName tag
  let mut subjects : Std.HashMap Name (List Nat) := {}
  for tag in subjectTags do
    subjects := subjects.insert tag.declName (tag.subjects.filterMap (·.toNat?))
  let mut formalProofs : Std.HashMap Name FormalProofTag := {}
  for tag in formalProofTags do formalProofs := formalProofs.insert tag.declName tag
  let mut declarations : Array Json := #[]
  for moduleName in modules do
    let some moduleIndex := environment.header.moduleNames.findIdx? (· == moduleName)
      | continue
    let moduleData := environment.header.moduleData[moduleIndex]!
    for info in moduleData.constants do
      let name := info.name
      let some category := categories.get? name
        | continue
      let declarationKind := match info with
        | .thmInfo _ => "theorem"
        | .defnInfo _ => "definition"
        | .opaqueInfo _ => "opaque"
        | .axiomInfo _ => "axiom"
        | .quotInfo _ => "quotient"
        | .inductInfo _ => "inductive"
        | .ctorInfo _ => "constructor"
        | .recInfo _ => "recursor"
      let pointer := if declarationKind == "theorem" then pointerTarget? environment name info.value? else none
      let (initialClassification, constructors) ← MetaM.run' (classify declarationKind info.type info.value?)
      let classification := if pointer.isSome then "POINTER_DECLARATION" else initialClassification
      let answerExpressions := findAnswerExprs info.type
      let answerJson ← MetaM.run' <| answerExpressions.mapM answerDescription
      let formalProof := formalProofs.get? name
      let typePretty ← MetaM.run' (prettyExpression info.type)
      let typeCanonical ← MetaM.run' (canonicalExpression info.type)
      let proposition ← MetaM.run' (isProp info.type)
      let docstring ← findDocString? environment name
      declarations := declarations.push <| Json.mkObj [
        ("theorem", toJson name.toString),
        ("module", toJson moduleName.toString),
        ("source_path", toJson (sourcePath moduleName)),
        ("category", toJson (categoryString category.category)),
        ("ams_subjects", toJson (subjects.getD name [])),
        ("formal_proof_kind", optionalJson (formalProof.map (formalProofString ·.proofKind))),
        ("formal_proof_link", optionalJson (formalProof.map (·.proofLink))),
        ("declaration_kind", toJson declarationKind),
        ("type_pretty", toJson typePretty),
        ("type_canonical", toJson typeCanonical),
        ("contains_answer_annotation", toJson (!answerExpressions.isEmpty)),
        ("answer_occurrences", toJson answerJson),
        ("contains_sorry_in_type", toJson info.type.hasSorry),
        ("has_parameters", toJson (hasForall info.type)),
        ("is_prop", toJson proposition),
        ("docstring", optionalJson docstring),
        ("supported_modes", toJson (supportedModes classification)),
        ("classification", toJson classification),
        ("pointer_target", optionalJson (pointer.map (·.toString))),
        ("finite_constructors", toJson (constructors.toList.map (·.toString)))
      ]
  return Json.mkObj [("declarations", toJson declarations)]

unsafe def main (arguments : List String) : IO Unit := do
  let directory := System.FilePath.mk (arguments.head?.getD "FormalConjectures")
  let files ← leanFiles directory
  let mut modules := #[]
  for file in files do modules := modules.push (← moduleNameFor file)
  let result ← withImports modules (extractCatalog modules)
  IO.println result.compress
