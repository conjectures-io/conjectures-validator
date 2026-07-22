import Lean
import FormalConjecturesUtil.Answer

open Lean Elab Meta Term Google

namespace FormalConjecturesVerifier

def constantType (source : TSyntax `str) : TermElabM Expr := do
  let sourceInfo ← getConstInfo source.getString.toName
  return sourceInfo.type

def answerType (source : TSyntax `str) : TermElabM Expr := do
  let answers := Google.findAnswerExprs (← constantType source)
  unless answers.size == 1 do
    throwError "expected exactly one answer occurrence, found {answers.size}"
  inferType answers[0]!

partial def answerCount (expression : Expr) : Nat :=
  Google.findAnswerExprs expression |>.size

partial def extractPropAnswerTarget (expression : Expr) : MetaM Expr := do
  match expression with
  | .forallE name domain body binderInfo =>
      unless answerCount domain == 0 do
        throwError "answer annotation in a binder domain is not a proposition wrapper"
      return .forallE name domain (← extractPropAnswerTarget body) binderInfo
  | _ =>
      let function := expression.getAppFn
      let arguments := expression.getAppArgs
      if function.isConstOf ``Iff && arguments.size == 2 then
        let left := arguments[0]!
        let right := arguments[1]!
        let leftCount := answerCount left
        let rightCount := answerCount right
        let metadataPresent := leftCount > 0 || rightCount > 0
        let leftIsAnswer := if metadataPresent then leftCount > 0 else left.isConstOf ``True
        let rightIsAnswer := if metadataPresent then rightCount > 0 else right.isConstOf ``True
        if leftIsAnswer && !rightIsAnswer then
          return right
        if rightIsAnswer && !leftIsAnswer then
          return left
      throwError "expected exactly one answer-annotated side of an iff"

partial def replaceAnswer (expression replacement : Expr) : MetaM (Expr × Nat) := do
  match expression with
  | .mdata metadata inner =>
      if metadata.contains `answer then
        let expectedType ← inferType inner
        let replacementType ← inferType replacement
        unless ← isDefEq expectedType replacementType do
          throwError "submitted answer type does not match the source answer type"
        return (replacement, 1)
      let (newInner, count) ← replaceAnswer inner replacement
      return (.mdata metadata newInner, count)
  | .app function argument =>
      let (newFunction, functionCount) ← replaceAnswer function replacement
      let (newArgument, argumentCount) ← replaceAnswer argument replacement
      return (.app newFunction newArgument, functionCount + argumentCount)
  | .lam name domain body binderInfo =>
      let (newDomain, domainCount) ← replaceAnswer domain replacement
      let (newBody, bodyCount) ← replaceAnswer body replacement
      return (.lam name newDomain newBody binderInfo, domainCount + bodyCount)
  | .forallE name domain body binderInfo =>
      let (newDomain, domainCount) ← replaceAnswer domain replacement
      let (newBody, bodyCount) ← replaceAnswer body replacement
      return (.forallE name newDomain newBody binderInfo, domainCount + bodyCount)
  | .letE name type value body nondep =>
      let (newType, typeCount) ← replaceAnswer type replacement
      let (newValue, valueCount) ← replaceAnswer value replacement
      let (newBody, bodyCount) ← replaceAnswer body replacement
      return (.letE name newType newValue newBody nondep, typeCount + valueCount + bodyCount)
  | .proj typeName index sourceStructure =>
      let (newStructure, count) ← replaceAnswer sourceStructure replacement
      return (.proj typeName index newStructure, count)
  | other => return (other, 0)

syntax (name := fcTypeOfName) "fcTypeOfName% " str : term

@[term_elab fcTypeOfName]
unsafe def elaborateTypeOfName : TermElab := fun stx _expectedType => do
  match stx with
  | `(fcTypeOfName% $source:str) => constantType source
  | _ => throwUnsupportedSyntax

syntax (name := fcAnswerType) "fcAnswerType% " str : term

@[term_elab fcAnswerType]
unsafe def elaborateAnswerType : TermElab := fun stx _expectedType => do
  match stx with
  | `(fcAnswerType% $source:str) => answerType source
  | _ => throwUnsupportedSyntax

syntax (name := fcPropAnswerTargetName) "fcPropAnswerTargetName% " str : term

@[term_elab fcPropAnswerTargetName]
unsafe def elaboratePropAnswerTargetName : TermElab := fun stx _expectedType => do
  match stx with
  | `(fcPropAnswerTargetName% $source:str) =>
      extractPropAnswerTarget (← constantType source)
  | _ => throwUnsupportedSyntax

syntax (name := fcValueAnswerTargetName)
  "fcValueAnswerTargetName% " str " using " term : term

@[term_elab fcValueAnswerTargetName]
unsafe def elaborateValueAnswerTargetName : TermElab := fun stx _expectedType => do
  match stx with
  | `(fcValueAnswerTargetName% $source:str using $replacement:term) =>
      let replacementExpression ← elabTerm replacement none
      let (target, count) ← replaceAnswer (← constantType source) replacementExpression
      unless count == 1 do
        throwError "expected exactly one answer occurrence, found {count}"
      return target
  | _ => throwUnsupportedSyntax

syntax (name := fcPropAnswerTarget) "fcPropAnswerTarget% " term : term

@[term_elab fcPropAnswerTarget]
unsafe def elaboratePropAnswerTarget : TermElab := fun stx _expectedType => do
  match stx with
  | `(fcPropAnswerTarget% $source:term) =>
      let sourceExpression ← elabTerm source none
      let sourceType ← inferType sourceExpression
      extractPropAnswerTarget sourceType
  | _ => throwUnsupportedSyntax

syntax (name := fcValueAnswerTarget) "fcValueAnswerTarget% " term " using " term : term

@[term_elab fcValueAnswerTarget]
unsafe def elaborateValueAnswerTarget : TermElab := fun stx _expectedType => do
  match stx with
  | `(fcValueAnswerTarget% $source:term using $replacement:term) =>
      let sourceExpression ← elabTerm source none
      let replacementExpression ← elabTerm replacement none
      let sourceType ← inferType sourceExpression
      let (target, count) ← replaceAnswer sourceType replacementExpression
      unless count == 1 do
        throwError "expected exactly one answer occurrence, found {count}"
      return target
  | _ => throwUnsupportedSyntax

end FormalConjecturesVerifier
