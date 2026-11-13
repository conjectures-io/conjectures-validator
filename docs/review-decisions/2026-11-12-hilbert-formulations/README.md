# Hilbert formalization and independent review packet

Revision 10 contains explicit Lean specifications for Hilbert 6, 12, the
algebraic version of 13, and 16. After four review rounds, Fable 5.2 confirms
acceptance of every precise specification under the declared interpretations.
All blocking conditions are closed; the author agrees. This accepts neither
literal whole-problem completeness for 6 and 12 nor proofs of the targets.
The final response is in `reviews/fable-round-04.txt`; the exact agreement,
source hashes and limitations are recorded in `review-status.json`.

**Compilation, mathematical validity, historical fidelity and completeness are
separate claims.** A target declared with `def … : Prop` is a statement, not an
axiom or a proof. A classification structure specifies the actual required
output; asserting its nonemptiness is not advertised as producing that output.
No open problem is claimed solved. No bounty is activated.

| Problem | Primary specification | Interpretation |
| --- | --- | --- |
| 6 | `Hilbert6Rigid.ClassicalLimitsProgram`, plus probability/mechanics definitions and model witnesses | Selected classical physical models covering the explicit probability, mechanics and limiting-process clauses |
| 12 | `Hilbert12ClassField.ArithmeticOutput`, `Hilbert12Universal.FieldHilbert12For R`, rational and CM benchmarks | Arithmetic classification output and a free contract for proposed field-attached analytic constructions; explicitness is not certified by the contract alone |
| Algebraic 13 | `Hilbert13.AlgebraicHilbert13` | Generic complex septimic resolvent degree exactly three; accepted by Fable round 1 |
| 16 | `Hilbert16Classification.ClassificationOutput`; `Hilbert16.UniformBound` | Actual curve/surface and cycle-arrangement outputs; the cycle bound is the core closed conjecture; computability is optional |

Names in the table have namespace prefix `HilbertDrafts.`. See
`PRIMARY_SOURCE_REVIEW.md`, `FULL_SCOPE.md`, and `primary-source-coverage.json`
for the source interpretation and exact scope. The literal unrestricted
whole-problem claim remains false in the metadata: selected physical models do
not exhaust the programme of #6, and the general recipe contract for #12 does
not certify analytic explicitness. This is not an impossibility claim about
future formalizations.

## Changes requested by Fable

For **6**, kinetic convergence now includes concentration of empirical means.
The continuum model has a local frame-indifferent hyperelastic law, the PDE
`y_tt = (N+1)^2 div DW(∇y)`, zero traction on every box face, and a uniform
initial-energy bound. Energy and momentum balances are conclusions of the
separate `ElasticityToStiff` obligation. The rigidity/compactness limit is a
known-theorem obligation, conditional on smooth solutions existing on the
chosen common time interval. A stationary elastic solution is checked for
every admitted material. A concrete orientation-penalising polynomial material
is specified with an admissibility obligation. Probability consistency and
Newton/momentum equivalence are checked directly; first-order logic is an
optional appendix. Arbitrary-data local Euler/NSF existence replaces mere
nonempty-solution assertions.

The gas statements are explicitly labelled variants. The kinetic reference
asserts its canonical version without proof in Remark 1.5(4); the fluid paper
uses a grand-canonical ensemble. The exact-Maxwellian Euler initial data need
an additional initial-layer argument. None is certified here as an exact
transcription or a newly discovered open problem.

For **12**, ray moduli, prime divisors, principal ray relations, ray class
groups, arithmetic Frobenius, the class-field existence/correspondence theorem,
least conductors and the conductor–discriminant formula are explicit. The
arithmetic output includes exact catalogues for prescribed degree, group and
relative discriminant. These known theorem statements are not proved here.
The exponential is normalized to accept rational inputs; all-rational coverage
and its coprime-power/Frobenius action are specified. The corrected imaginary
quadratic benchmark uses actual Mathlib Weierstrass series, the full integer
lattice, j, and normalized Weber values at all nonidentity torsion classes.
Fable accepted that benchmark in round 2.

The newer recipe interface depends only on the embedded base field, providing
naturality by construction and allowing field-dependent analytic families.
An all-rational-point profile is optional. A checked regression shows that
moving outputs into arguments and using the identity function preserves the
older unrestricted contract. This does not prove that every possible
admissibility criterion fails. General explicitness remains a review obligation;
no unrestricted existential over recipes is called Hilbert 12.

For **16**, pure output structures carry the catalogues, realizability,
exhaustion, irredundancy and sharp maxima. Effective programs are optional
refinements. Primary curves and surfaces are nonsingular; unrestricted singular
loci remain optional. Harnack's sharp bound and the quartic maximum of ten
components are concrete known-theorem benchmarks. Producing a classification
is distinct from proving that some classification program exists in principle.

## Verification

Run `bash verify.sh` from this directory, then `bash verify.sh --clean`.
The scripts use the pinned Docker image, Lean 4.35.0-rc2 and Mathlib revision
`065356127b1dc0016f66b7283ce0ce2c4055aa55`, with networking disabled and warnings
as errors. The audit inventories every explicit declaration and permits only
`propext`, `Classical.choice`, and `Quot.sound`. The clean check creates a fresh
standalone project and uses the pinned dependency cache, without reusing project
artifacts. It does not test bootstrapping an empty dependency cache.

Evidence is in `evidence/validation.json`, `evidence/clean-build.json`, and logs.
Source mappings are checked for declaration existence, not semantic acceptance.
The checksums identify exactly which files the final review and archive cover.
