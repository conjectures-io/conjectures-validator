# Twenty additional Erdős problems

This batch selects one main statement for each of **151, 159, 173, 295, 311, 322,
345, 388, 389, 393, 422, 423, 430, 461, 462, 467, 472, 478, 483, and 545**.
It adds 20 reward targets to the 306 retained targets, giving 326 targets and
652 proof/counterexample bundles. This document does not establish deployment.

The selection comes from the October 28 intake review. All selected statement
hashes match that review and the complete production catalog. The source remains
`17acb8768de75e168a43c7c828e933071aee63cf`, using Lean 4.35.0-rc2 and the existing
Mathlib and verifier environment. No source repin or theorem changes are needed.

On November 11 we retrieved the primary problem pages, discussions, proof-claim
pages, and current upstream files. Each selected upstream main declaration remains
`research open`. Recent upstream PR titles were searched per problem number;
relevant returned descriptions concern partial, conditional, or different targets.
The review is bounded and does not establish the absence of a solution elsewhere.

## Scope and exclusions

- **326 is excluded:** its discussion contains an unresolved full-resolution and
  Lean-formalization claim by Aron Bhalla. The proof-claims tab has zero entries,
  illustrating why the discussion must also be checked. No assertion of proof
  correctness is made. This corrects the earlier shortlist's intake decision.
- **509 is not selected:** the discussion proposes a stronger partition claim
  implying the target. The replacement is 545, whose sufficiently-large-edge
  formulation excludes the recorded small counterexamples.
- The claims for **151, 295, and 478** respectively concern bounded graph sizes,
  restricted denominators, and a weaker factorial-residue lower bound.
- **388** asks global finiteness across block lengths; fixed-length results do
  not settle it. **389** asks for all starting values, beyond computed examples.
- **393** is unconditional; the Luca theorem and PR 7762 require abc. **423**
  asks for the asymptotic `a(n)=n+o(n)`; PRs 7622 and 7668 concern other variants.
- **462** includes primes in its interval sum, following the displayed website
  formulation. The historical source has an ambiguity concerning their exclusion.
- **467** follows the website's explicit existential residue-class and partition
  formulation; the historical source omits some quantifiers. This interpretation
  is part of the selected target's scope.

The task repository's `erdos-admission-audit.json` binds every selected target to
its exact statement hash, references and scope note. The previous audit remains
in Git history. Prior held and retired targets remain excluded; all immutable
historical bundle bytes and admissions must be preserved by publication.

Validation outcomes are recorded in `validation.json` once the build and checks
finish. The full source catalog is retained. No open-problem solution is claimed
by the compiler or sandbox controls.
