import Lean

/-!
# Dependency closure of a source statement

The incremental task-version identity is derived from the compiled Lean environment, never
from a caller's claim. For one source theorem this module reports:

* every constant reachable from the theorem's *type*, following definition, opaque and
  recursor bodies and inductive constructors, but never a theorem's proof;
* for each such constant defined in a *local* module (the task source package or the
  validator's own Lean), a complete and unambiguous serialization of its kernel content;
* the transitive import closure of the theorem's module, with the `.olean` each import was
  loaded from.

Constants of pinned external packages (Lean, Mathlib, …) are reported by name only: their
content is pinned by the toolchain and Lake manifest, which are part of the environment
identity, and they cannot depend on a local module. Python hashes and validates the result
(`verifier.task_versions`); nothing here is trusted to decide reuse by itself.
-/

open Lean Meta

namespace FormalConjecturesVerifier.Dependencies

/-- Name components, tagged by kind, as a JSON array: unambiguous for any component text. -/
partial def nameJson : Name → Json
  | .anonymous => Json.arr #[]
  | .str pre component =>
      match nameJson pre with
      | .arr items => Json.arr (items.push (Json.str component))
      | _ => Json.arr #[Json.str component]
  | .num pre component =>
      match nameJson pre with
      | .arr items => Json.arr (items.push (Json.arr #[Json.str "#", toJson component]))
      | _ => Json.arr #[Json.arr #[Json.str "#", toJson component]]

partial def levelJson : Level → Except String Json
  | .zero => pure (Json.str "z")
  | .succ level => return Json.arr #[Json.str "s", ← levelJson level]
  | .max left right => return Json.arr #[Json.str "m", ← levelJson left, ← levelJson right]
  | .imax left right => return Json.arr #[Json.str "i", ← levelJson left, ← levelJson right]
  | .param name => pure (Json.arr #[Json.str "p", nameJson name])
  | .mvar _ => throw "universe metavariable in a closed constant"

def binderJson : BinderInfo → Json
  | .default => Json.str "d"
  | .implicit => Json.str "i"
  | .strictImplicit => Json.str "s"
  | .instImplicit => Json.str "c"

/-- Metadata does not change kernel meaning. Keep it, but never its source syntax: positions
would make an unrelated edit above a definition look like a change to it. -/
def dataValueJson : DataValue → Json
  | .ofString value => Json.arr #[Json.str "s", Json.str value]
  | .ofBool value => Json.arr #[Json.str "b", Json.bool value]
  | .ofName value => Json.arr #[Json.str "n", nameJson value]
  | .ofNat value => Json.arr #[Json.str "k", Json.str (toString value)]
  | .ofInt value => Json.arr #[Json.str "j", Json.str (toString value)]
  | .ofSyntax _ => Json.arr #[Json.str "x"]

/-- Structural identity including binder names and binder info. -/
structure ExprKey where
  expr : Expr

instance : BEq ExprKey := ⟨fun left right => left.expr.equal right.expr⟩
instance : Hashable ExprKey := ⟨fun key => key.expr.hash⟩

structure Serializer where
  nodes : Array Json := #[]
  index : Std.HashMap ExprKey Nat := {}

abbrev SerializeM := StateT Serializer (Except String)

def liftExcept {α : Type} : Except String α → SerializeM α
  | .ok result => pure result
  | .error message => throw message

/-- Post-order DAG serialization: each distinct subterm is emitted once and referenced by its
index, so shared subterms cannot make the output exponential. -/
partial def node (expression : Expr) : SerializeM Nat := do
  if let some existing := (← get).index.get? ⟨expression⟩ then
    return existing
  let encoded ← match expression with
    | .bvar index => pure (Json.arr #[Json.str "B", toJson index])
    | .sort level => do
        let encodedLevel ← liftExcept (levelJson level)
        pure (Json.arr #[Json.str "S", encodedLevel])
    | .const name levels => do
        let encodedLevels ← liftExcept (levels.toArray.mapM levelJson)
        pure (Json.arr #[Json.str "C", nameJson name, Json.arr encodedLevels])
    | .app function argument => do
        let functionIndex ← node function
        let argumentIndex ← node argument
        pure (Json.arr #[Json.str "A", toJson functionIndex, toJson argumentIndex])
    | .lam name domain body info => do
        let domainIndex ← node domain
        let bodyIndex ← node body
        pure (Json.arr #[Json.str "L", nameJson name, binderJson info,
          toJson domainIndex, toJson bodyIndex])
    | .forallE name domain body info => do
        let domainIndex ← node domain
        let bodyIndex ← node body
        pure (Json.arr #[Json.str "P", nameJson name, binderJson info,
          toJson domainIndex, toJson bodyIndex])
    | .letE name type value body nondependent => do
        let typeIndex ← node type
        let valueIndex ← node value
        let bodyIndex ← node body
        pure (Json.arr #[Json.str "T", nameJson name, toJson typeIndex, toJson valueIndex,
          toJson bodyIndex, Json.bool nondependent])
    | .lit (.natVal value) => pure (Json.arr #[Json.str "N", Json.str (toString value)])
    | .lit (.strVal value) => pure (Json.arr #[Json.str "Z", Json.str value])
    | .mdata data inner => do
        let innerIndex ← node inner
        let entries := data.entries.toArray.map fun (key, value) =>
          Json.arr #[nameJson key, dataValueJson value]
        pure (Json.arr #[Json.str "M", Json.arr entries, toJson innerIndex])
    | .proj structName fieldIndex structure_ => do
        let structureIndex ← node structure_
        pure (Json.arr #[Json.str "J", nameJson structName, toJson fieldIndex,
          toJson structureIndex])
    | .fvar _ => throw "free variable in a closed constant"
    | .mvar _ => throw "metavariable in a closed constant"
  let state ← get
  let position := state.nodes.size
  set { state with nodes := state.nodes.push encoded, index := state.index.insert ⟨expression⟩ position }
  return position

def namesJson (names : List Name) : Json :=
  Json.arr (names.toArray.map nameJson)

def safetyString : DefinitionSafety → String
  | .safe => "safe"
  | .unsafe => "unsafe"
  | .partial => "partial"

def hintsJson : ReducibilityHints → Json
  | .opaque => Json.str "opaque"
  | .abbrev => Json.str "abbrev"
  | .regular height => Json.arr #[Json.str "regular", toJson height.toNat]

def quotKindString : QuotKind → String
  | .type => "type"
  | .ctor => "ctor"
  | .lift => "lift"
  | .ind => "ind"

/-- The constants a local constant's meaning depends on. A theorem contributes its statement
only: proofs are irrelevant to what a task asks. -/
def dependencies : ConstantInfo → Array Name
  | .thmInfo info => info.type.getUsedConstants
  | .axiomInfo info => info.type.getUsedConstants
  | .quotInfo info => info.type.getUsedConstants
  | .defnInfo info => info.type.getUsedConstants ++ info.value.getUsedConstants
  | .opaqueInfo info => info.type.getUsedConstants ++ info.value.getUsedConstants
  | .inductInfo info => info.type.getUsedConstants ++ info.all.toArray ++ info.ctors.toArray
  | .ctorInfo info => info.type.getUsedConstants.push info.induct
  | .recInfo info =>
      info.rules.foldl (fun names rule => names ++ rule.rhs.getUsedConstants |>.push rule.ctor)
        (info.type.getUsedConstants ++ info.all.toArray)

/-- The complete kernel content of one local constant. -/
def serializeConstant (info : ConstantInfo) : Except String Json := do
  let typeOnly (kind : String) (extra : List (String × Json)) : Except String Json := do
    let (typeIndex, state) ← (node info.type).run {}
    return Json.mkObj ([("kind", Json.str kind), ("levels", namesJson info.levelParams),
      ("nodes", Json.arr state.nodes), ("type", toJson typeIndex), ("value", Json.null)] ++ extra)
  let withValue (kind : String) (value : Expr) (extra : List (String × Json)) :
      Except String Json := do
    let ((typeIndex, valueIndex), state) ← (do
        let typeIndex ← node info.type
        let valueIndex ← node value
        pure (typeIndex, valueIndex)).run {}
    return Json.mkObj ([("kind", Json.str kind), ("levels", namesJson info.levelParams),
      ("nodes", Json.arr state.nodes), ("type", toJson typeIndex), ("value", toJson valueIndex)]
      ++ extra)
  match info with
  | .thmInfo value => typeOnly "theorem" [("all", namesJson value.all)]
  | .axiomInfo value => typeOnly "axiom" [("unsafe", Json.bool value.isUnsafe)]
  | .quotInfo value => typeOnly "quotient" [("quot_kind", Json.str (quotKindString value.kind))]
  | .defnInfo value =>
      withValue "definition" value.value [("all", namesJson value.all),
        ("safety", Json.str (safetyString value.safety)), ("hints", hintsJson value.hints)]
  | .opaqueInfo value =>
      withValue "opaque" value.value [("all", namesJson value.all),
        ("unsafe", Json.bool value.isUnsafe)]
  | .inductInfo value =>
      typeOnly "inductive" [("all", namesJson value.all), ("ctors", namesJson value.ctors),
        ("num_params", toJson value.numParams), ("num_indices", toJson value.numIndices),
        ("num_nested", toJson value.numNested), ("recursive", Json.bool value.isRec),
        ("unsafe", Json.bool value.isUnsafe), ("reflexive", Json.bool value.isReflexive)]
  | .ctorInfo value =>
      typeOnly "constructor" [("induct", nameJson value.induct), ("cidx", toJson value.cidx),
        ("num_params", toJson value.numParams), ("num_fields", toJson value.numFields),
        ("unsafe", Json.bool value.isUnsafe)]
  | .recInfo value => do
      let ((typeIndex, ruleIndices), state) ← (do
          let typeIndex ← node info.type
          let ruleIndices ← value.rules.toArray.mapM fun (rule : RecursorRule) => do
            let rhs ← node rule.rhs
            pure (Json.arr #[nameJson rule.ctor, toJson rule.nfields, toJson rhs])
          pure (typeIndex, ruleIndices)).run {}
      return Json.mkObj [("kind", Json.str "recursor"), ("levels", namesJson info.levelParams),
        ("nodes", Json.arr state.nodes), ("type", toJson typeIndex), ("value", Json.null),
        ("all", namesJson value.all), ("num_params", toJson value.numParams),
        ("num_indices", toJson value.numIndices), ("num_motives", toJson value.numMotives),
        ("num_minors", toJson value.numMinors), ("k", Json.bool value.k),
        ("unsafe", Json.bool value.isUnsafe), ("rules", Json.arr ruleIndices)]

def moduleOf? (environment : Environment) (name : Name) : Option Name := do
  let index ← environment.getModuleIdxFor? name
  environment.header.moduleNames[index.toNat]?

def isLocalModule (localRoots : Array Name) (module : Name) : Bool :=
  localRoots.contains module.getRoot

structure Closure where
  /-- Local constants, with their module. -/
  localConstants : Array (Name × Name) := #[]
  /-- External constants, with their module. -/
  externalConstants : Array (Name × Name) := #[]

/-- Every constant reachable from `root`'s statement. Fails closed on an unknown constant or a
constant that no imported module defines. -/
partial def constantClosure (environment : Environment) (localRoots : Array Name) (root : Name) :
    Except String Closure := do
  let mut pending : Array Name := #[root]
  let mut seen : Std.HashSet Name := {}
  let mut result : Closure := {}
  while !pending.isEmpty do
    let name := pending.back!
    pending := pending.pop
    if seen.contains name then
      continue
    seen := seen.insert name
    let some info := environment.find? name
      | throw s!"constant is not in the environment: {name}"
    let some module := moduleOf? environment name
      | throw s!"constant has no defining module: {name}"
    if isLocalModule localRoots module then
      result := { result with localConstants := result.localConstants.push (name, module) }
      for dependency in dependencies info do
        unless seen.contains dependency do
          pending := pending.push dependency
    else
      result := { result with externalConstants := result.externalConstants.push (name, module) }
  return result

def closureJson (environment : Environment) (localRoots : Array Name) (theoremName module : Name) :
    IO Json := do
  let closure ← IO.ofExcept (constantClosure environment localRoots theoremName)
  let mut locals : Array Json := #[]
  for (name, owner) in closure.localConstants do
    let some info := environment.find? name
      | throw <| IO.userError s!"constant vanished: {name}"
    let content ← IO.ofExcept (serializeConstant info)
    locals := locals.push (Json.mkObj [("name", nameJson name), ("module", nameJson owner),
      ("content", content)])
  -- External constants are pinned by the toolchain and Lake manifest, and every external
  -- name a local constant uses already appears inside that constant's serialized content.
  let externalModules := closure.externalConstants.foldl
    (fun acc (_, owner) => if acc.contains owner then acc else acc.push owner) #[]
  return Json.mkObj [
    ("theorem", nameJson theoremName),
    ("module", nameJson module),
    ("local_constants", Json.arr locals),
    ("external_constant_count", toJson closure.externalConstants.size),
    ("external_modules", Json.arr (externalModules.map nameJson))
  ]

/-- Every loaded module with its direct imports and the `.olean` Lean resolved for it. The
import closure of any module is computed from this table by the caller. -/
def moduleTableJson (environment : Environment) (localRoots : Array Name) : IO Json := do
  let mut rows : Array Json := #[]
  for module in environment.header.moduleNames, data in environment.header.moduleData do
    let olean ← findOLean module
    rows := rows.push (Json.mkObj [
      ("module", nameJson module),
      ("local", Json.bool (isLocalModule localRoots module)),
      ("olean", Json.str olean.toString),
      ("imports", Json.arr (data.imports.map fun imported => nameJson imported.module))
    ])
  return Json.arr rows

/-- The statement printed with every implicit detail, exactly as the catalog hashes it. -/
def canonicalExpression (expression : Expr) : MetaM String := do
  withOptions (fun options =>
      options
        |>.setBool `pp.all true
        |>.setBool `pp.universes true
        |>.setBool `pp.explicit true
        |>.setBool `pp.proofs true) do
    return toString (← ppExpr expression)

def parseLocalRoots (value : String) : Array Name :=
  (value.splitOn ",").toArray.filter (· ≠ "") |>.map String.toName

end FormalConjecturesVerifier.Dependencies
