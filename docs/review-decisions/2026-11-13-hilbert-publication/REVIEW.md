# Hilbert publication and the Hilbert 16 proof claim

Reviewed November 13, 2026.

## Public coverage

The website publishes entries for all four prepared problems, Hilbert 6, 12,
algebraic 13 and 16. The previous release placed the complete four-problem
packet in the repositories while exposing only algebraic 13 and rank-one
abelian Stark in the website's task catalog. This update adds searchable
public pages for the remaining formulations and their declared scope.

| Problem | Public interpretation |
| --- | --- |
| 6 | Selected probability, mechanics, kinetic, fluid, elasticity and rigidity specifications |
| 12 | Arithmetic classification output, rational and imaginary-quadratic benchmarks, and a field-attached analytic recipe contract |
| Algebraic 13 | The existing complex resolvent-degree target RD(7) = 3 |
| 16 | Curve/surface and cycle-arrangement classification output, together with the general uniform-bound proposition |

The published Lean excerpts are exact slices of the Fable-reviewed modules.
Each source link pins the task-repository commit, and the API carries the
complete module's SHA-256. Tests compare the excerpts and hashes with the
accepted source files. Fable's four-round acceptance concerns the precise
interpretations recorded in that packet. The selected-model scope of Hilbert
6 and the explicitness limitation of general Hilbert 12 remain visible on
their pages. The existing algebraic 13 and Stark submission targets retain
their executable contracts.
[Reviewed package](https://github.com/conjectures-io/conjectures-tasks/tree/ab3ecc93d19428ced0f14cdc467a6a075e3dd852/staged/hilbert-formulations-20261113)

## Hilbert 16 manuscript

OpenAI's September 24 manuscript states that for every degree d at least one,
a finite B(d) bounds the number of limit cycles of every real planar polynomial
vector field of degree at most d. It allows arbitrary coefficients and cycle
locations, counts isolated nonconstant periodic-orbit images, and gives an
existence assertion for the bound. Its final argument combines finite passage
templates, component bounds, rotation perturbations and spatial rescaling.
Exact degree-by-degree maxima and arrangement classification remain outside
the stated result. The repository path history shows the initial October 6
publication and no later change through the inspected October 30 repository
revision `4eaee87548250b2682affda7d05d947c05554bae`.
[Manuscript and source](https://github.com/openai/math/tree/4eaee87548250b2682affda7d05d947c05554bae/preprints/uniform-bounds-for-planar-polynomial-limit-cycles-September-24-2026)

Our `HilbertDrafts.Hilbert16.UniformBound` uses the same geometric isolation
criterion and degree-only quantifier order. It bounds every finite injective
family of cycle images, which also rules out an infinite set of cycles.
The manuscript's positive-degree statement covers degree-zero fields by
including them among fields of degree at most one. This is a statement-level
scope comparison. Establishing the analytic arguments throughout the
160-page manuscript remains a substantial proof-review task.
[Reviewed Lean statement](https://github.com/conjectures-io/conjectures-tasks/blob/ab3ecc93d19428ced0f14cdc467a6a075e3dd852/staged/hilbert-formulations-20261113/draft/HilbertDrafts/Hilbert16.lean)

## Released formal verification

The inspected family-143 scope note and formalization catalogue identify
`OAI.QuinticLienard.main`. Its comparator statement concerns the systems
x′ = y − F(x), y′ = −x with degree F at most five. It asserts at most two
isolated periodic-orbit images and existence of a polynomial attaining two.
The released formalization scope therefore establishes a restricted companion
result. The general B(d) theorem has no listed formal proof in these artifacts.
Comparator challenge files contain the target placeholder by design, so
reading that file checks the intended statement. This audit did not rerun
the companion proof or its comparator.
[Family scope](https://github.com/openai/math/blob/4eaee87548250b2682affda7d05d947c05554bae/lean/docs/143.md)
[Comparator target](https://github.com/openai/math/blob/4eaee87548250b2682affda7d05d947c05554bae/lean/ComparatorChallenges/QuinticLienard.lean)
[Formalization catalogue](https://github.com/openai/math/blob/4eaee87548250b2682affda7d05d947c05554bae/lean/formalization.yaml)

## Subsequent mathematical literature

Chen, Xiao and Zhang's October 23 preprint describes the general uniform bound
as a claim requiring careful independent verification. Their work develops
uniqueness criteria for Rayleigh–Liénard systems. This provides a directly
attributable assessment in subsequent mathematical literature.
[Chen, Xiao and Zhang, introduction](https://arxiv.org/html/2610.33114v1#S1)

Qin and Sun's October 8 preprint acknowledges OpenAI's claimed finiteness and
claims the complementary lower bound H(d) = Ω(d² log² d). That lower-bound
result supplies separate information about how large the maximum can be. It
does not verify the upper-bound argument.
[Qin and Sun](https://arxiv.org/html/2610.12218v1)

The bounded search recorded in `evidence.json` found no published independent
verification of the full uniform-bound argument and no specific refutation
of that manuscript. The public Hilbert 16 page therefore records **Proof
claimed** and cites the sources above. General uniform boundedness, its
restricted Lean companion, and the remaining classification requirements each
have their own stated coverage. The research performed here is a literature
and statement audit. Full mathematical validity of the general proof remains
unverified in this review.
