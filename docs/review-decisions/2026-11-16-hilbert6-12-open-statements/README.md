# Reward statements for Hilbert 6 and Hilbert 12

Reviewed November 16, 2026. The November 15 record left Hilbert 6 and the
general Hilbert 12 problem without a reward target. The owner asked for that to
be fixed. This record admits three statements and supersedes the "no reward
target" findings of the November 15 record for these two problems.

| Problem | Statement admitted | Source of the statement |
| --- | --- | --- |
| 12 | `Hilbert12LambdaGeometric`: for every number field, the maximal abelian extension is Λ-geometric | Universal form of Question 4 of Borger and de Smit |
| 12 | `BorgerDeSmitQuestion4`: some number field other than Q and the imaginary quadratic fields has this property | Question 4 of Borger and de Smit, verbatim |
| 6 | `VlasovPoissonMeanFieldLimit`: mean-field limit from Newtonian point charges to the 3D Vlasov–Poisson equation | A recognised open problem of the particle-to-continuum programme |

**A statement that compiles is not thereby faithful, open or proved.** The Lean
checks establish compilation, permitted axioms and consistency. Fidelity rests
on the reviews. Open status rests on bounded literature searches.

## What changed since November 15

The November 15 record tried explicitness criteria of its own for Hilbert 12
and found each either trivially satisfiable or a change of problem. That
finding stands for those criteria. The fix is to use a criterion from the
literature. For Hilbert 6 the reviewed statements follow results that already
have proof claims. The fix is to state a different, recognised open problem
from the same programme.

## Hilbert 12

Borger and de Smit (arXiv:1809.02295, an unrefereed preprint) study schemes
over the ring of integers of a number field that carry commuting Frobenius
lifts at every prime. They prove that the periodic points of such a scheme of
finite type generate abelian extensions (Theorem 8.4). They call an abelian
extension Λ-geometric when one such scheme generates it (Section 12.1). Their
abstract describes the framework as "allowing for some precise questions in the
spirit of Hilbert's 12th Problem".

Finite type is the explicitness requirement. Without it the ray class algebras
themselves qualify (their Remark 9.11), which is the trivial solution the
November 15 record could not exclude. With it, the rational case is the
multiplicative group with its power maps, and the imaginary quadratic case is
the Lattès scheme of complex multiplication, which rests on Gurney's thesis
(arXiv:1710.08674). No other number field is known to have the property and no
obstruction is known for any field. The authors state no expected answer.

Two statements are admitted.

- **Question 4, verbatim.** "Are there number fields other than Q and imaginary
  quadratic fields for which the maximal abelian extension is Λ-geometric?"
  The target is the affirmative answer.
- **The universal form.** Every number field has the property. The paper does
  not pose this. It is this platform's reading of Hilbert's request for all
  number fields within their framework. One number field with no such scheme
  refutes it.

The two are coupled. A proof of the universal form proves Question 4, and a
refutation of Question 4 refutes the universal form; both implications are
short. In every consistent outcome exactly two of the four proof and refutation
rewards are payable.

This is an algebraic-geometric formulation, not Hilbert's analytic one, and not
the only modern reading. It is independent of the Stark route, which remains a
separate active target.

## Hilbert 6

`VlasovPoissonMeanFieldLimit` concerns point particles in three-space under the
repulsive Coulomb force scaled by one over their number. Their initial states
are independent samples of a smooth compactly supported density. The statement
asserts that empirical averages at each fixed time converge in probability to
those of the classical Vlasov–Poisson solution. The force is not truncated and
no noise is added.

The statement has two named parts. `VlasovPoissonExistence` is a known theorem
of Pfaffelmoser, Schaeffer, and Lions and Perthame. It is included so the
statement cannot be empty, and a proof must still establish it.
`MeanFieldConvergence` is the open part. It is expected to be true, so a
refutation is not a realistic outcome.

The known results do not decide it: the limit with the force truncated at
radius about N^(-5/12), the three-dimensional limit with velocity noise, the
two-dimensional limit for strictly positive data, and the monokinetic limit.
Surveys through September 2026 state the three-dimensional cutoff-free case as
open. The field is moving quickly, and a claim may appear soon.

Two disclosures apply.

- **Label.** This is a modern instance of the programme, going back to Hilbert's
  sixth problem, of deriving continuum equations from particle dynamics. It is
  not a question Hilbert posed and not the hard-sphere, Boltzmann and fluid
  derivation of Deng, Hani and Ma, whose first paper is accepted.
- **Setting.** A theorem for another class of initial data, for the torus or
  for short times would not by itself settle this statement. The published
  two-dimensional theorem needs strictly positive data and does not cover
  compactly supported densities.

## Reviews

Four fresh-context reviewers examined the statements. Their briefs and reports
are reproduced verbatim under `reviews/`.

- Both Lean-semantics adversaries report no defect. Their attack files are in
  `reviews/attacks-hilbert12/` and `reviews/attacks-hilbert6/` and compile in
  `verify.sh`. For Hilbert 12 they machine-check that the fibre predicate picks
  the right fibre, that the equivalence of ideals reproduces the rational
  examples of the paper, that every ring-level Frobenius lift satisfies the
  scheme-level definition, and that the side conditions of Question 4 exclude
  exactly the rational field and the fields of degree two with no real place.
  For Hilbert 6 they machine-check that the initial law is the product law of
  mass one, that the equation clause is the stated directional derivative, and
  that coincident initial positions are a null set.
- Both fidelity reviewers accept the mathematics unchanged and require
  labelling changes, all applied. The Hilbert 12 reviewer compared the
  definitions with the paper clause by clause and confirmed both known cases.

The consistency modules prove that the identity is not a Frobenius lift on the
affine line, that it is one on the base, that the universal form implies
Question 4, and that an admissible initial density exists.

## Verification

Run `bash verify.sh` from this directory. It uses the pinned image, Lean
4.35.0-rc2 and Mathlib `065356127b1dc0016f66b7283ce0ce2c4055aa55`, with
networking disabled and warnings as errors. `Hilbert6.lean` and `Hilbert12.lean`
are byte-identical to the modules accepted on November 13. Every audited
declaration depends only on `propext`, `Classical.choice` and `Quot.sound`.

## Limits

- All reviewers and literature searches ran on the same model family as the
  author, in separate contexts. No cross-model or human mathematician review
  was done.
- The Hilbert 12 source is an unrefereed preprint. The redundant clause that
  periodic coordinates are abelian rests on its Theorem 8.4.
- The full statement for the rational field and for imaginary quadratic fields
  is argued from the paper, not machine-checked. Kronecker–Weber is not in
  Mathlib.
- For Hilbert 6, that the true solution lies in the formal solution class, and
  global existence and uniqueness of the particle trajectories, are standard
  results argued on paper.
- The literature searches were bounded. They do not certify that no solution or
  claim exists.
