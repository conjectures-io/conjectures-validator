# Wikipedia-guided formulations of Hilbert 6 and 12

Historical revision-6 record. The 2026-11-13 instruction selects the original
source as authority; current scope and additions are in PRIMARY_SOURCE_REVIEW.md.
The modern-physics checklist below is superseded by that original-text audit.

The user's instruction on 2026-11-12 is to use Wikipedia to guide the meaning
and then formalize. These are the pinned article revisions used:

- [Hilbert 6, revision 1374839407](https://en.wikipedia.org/w/index.php?title=Hilbert%27s_sixth_problem&oldid=1374839407).
- [Hilbert 12, revision 1382222556](https://en.wikipedia.org/w/index.php?title=Hilbert%27s_twelfth_problem&oldid=1382222556).

This revision adds mathematical content; it does not just rebuild the old
kinetic/Stark statements. All additions are in the existing `Hilbert6.lean`
and `Hilbert12.lean` modules. No proposed conjecture has been assumed as a Lean
axiom, and compiling a definition does not prove its proposition.

## 6: source-to-code map

The article's opening identifies axiomatization of physics, emphasizing
probability and mechanics. Its further explanation adds mean values and limits
from particles to continua. Its history discusses fluid limits; its status
section also discusses quantum field theory and general relativity. The latter
are not implemented here and cannot be counted as covered by a classical gas
model. Those observations are scope guidance, not adoption of every scientific
or historical claim on the page.

| Requirement | Implementation | Status |
| --- | --- | --- |
| Probability axioms | `Hilbert6Probability.ProbabilityAxioms` | Explicit sigma-algebra, nonnegative event values, countable additivity, total mass one |
| Mathematical interpretation of these axioms | `toMeasure`, `isProbabilityMeasure`, `unique_measure` | Proved construction and uniqueness on measurable events |
| Mean values | `MeanValueLaw`, `meanValueLaw` | Fully quantified almost-sure strong law, proved using Mathlib |
| Finite classical mechanics | `Hilbert6Mechanics.System`, `Newtonian`, `MomentumEquations` | Positive masses and actual differential equations |
| Relation between presentations | `newtonian_iff_momentum`, `freeMotion` | Proved equivalence under p = mv and a class of actual models |
| Kinetic limit | `Hilbert6Kinetic.NewtonBoltzmannLimit` | Existing closed Euclidean hard-sphere statement |
| Periodic particle dynamics | `Hilbert6Fluid.TorusNonoverlap`, `TorusCollision`, `TorusPath`, `IsTorusFlow` | Explicit periodic-image contact, lifted positions, elastic collision histories |
| Macroscopic equations | `SmoothEuler`, `SmoothNSF` | Explicit conservation laws and incompressible velocity/heat equations |
| Particle-to-fluid convergence | `ParticleFluidLimit`, `FluidLimitProgram` | Closed proposed formulation, including Euler and NSF regimes |
| Selected classical gas program | `ClassicalGasProgram` | Closed conjunction of means, kinetic limit, and fluid limits |
| General modern physics | No definition supplied | Still missing; no opaque adequacy predicate used |

### Precise choices for the fluid candidate

Space has dimension three. Periodic functions are functions on R³ invariant
under every integer translation. Particle positions have continuous lifts;
physical separation is checked against every integer translate of every other
particle. Initial positions lie in the half-open unit cube. This allows ordinary
Euclidean derivatives and integrals while retaining periodic contacts across
opposite faces. Diameters are positive and less than half the period.

There are N+1 particles, with diameter sqrt(1/(κ(N+1))). Lean proves the exact
identity (N+1) ε² = 1/κ. Their initial law is a product density conditioned on
periodic nonoverlap, divided by its actual partition function. Later laws are
pushforwards by the specified collision dynamics. Normalization, integrability,
and positivity of the partition function are requirements of the conclusion.

Euler uses smooth periodic positive density and temperature, unit total mass,
and the monatomic energy density ρ(|u|²+3θ)/2. The equations are conservation
of mass, three components of momentum, and energy. Initial particles have the
corresponding local Maxwell distribution. Observation time is t.

NSF uses divergence-free velocity, pressure, and a mean-zero temperature
fluctuation, with density fluctuation -θ. The initial local Maxwell distribution
has density 1-κθ, temperature 1+κθ, and bulk velocity κu. Observation time is t/κ.
The limiting perturbation is M(v)[u·v + θ(|v|²-5)/2]. Positive viscosity and
thermal diffusivity are chosen once for the entire task, before quantifying
over solutions; they cannot be adjusted separately for each trajectory.

For every continuous periodic test with bounded velocity support, expectations
and the actual random empirical averages must converge, the latter in
probability. Bounds hold uniformly in macroscopic time. The quantifier order
is tolerance, sufficiently small κ, then sufficiently many particles. It is
an iterated limit, not every possible simultaneous scaling. Microscopic flow
existence and nonempty smooth fluid solution classes are explicit requirements.

[The paper linked by Wikipedia](https://arxiv.org/html/2503.01800v1) provides the
periodic hard-sphere and fluid-limit reference, particularly Definition 1.1,
Section 1.3, and Theorems 2–3. Our canonical ensemble, smooth solution classes,
test formulation, and existential universal transport coefficients are choices
for this proposal. We have not proved equivalence to its quantitative,
grand-canonical statements. That comparison is a substantive Fable review item.
This is not a new-open-problem eligibility claim.

## 12: source-to-code map

The article's generation interpretation concerns every number field K and its
maximal abelian extension. It allows subfields of fields generated by special
values. Its rational benchmark is exponential values at rational multiples of
2πi. For imaginary quadratic fields it describes modular and elliptic values,
together with roots of unity; modular values alone do not suffice. The phrase
“suitable analogue” leaves the admissible analytic constructions unspecified.

| Requirement | Implementation | Status |
| --- | --- | --- |
| One analytic family | `Hilbert12Universal.AnalyticFamily` | Countably many functions, finite complex arities, open domains, genuine analyticity |
| Evaluation instructions | `Parameters`, `Parameters.evaluate` | Valid arguments in those domains |
| Base-field dependence | `Recipe` | Instructions use the base field and embedding; no target extension is an input |
| Actual special values | `specialValues`, `stage` | Algebraic preimages of evaluations, not independently supplied desired outputs |
| Every evaluation algebraic | First clause of `GenerationContract` | Required for all instruction indices |
| Finite abelian construction stages | Second clause of `GenerationContract` | Every finite stage must be finite Galois with commuting automorphisms |
| Every finite abelian extension covered | Third clause of `GenerationContract` | Containment in a stage using finitely many evaluations |
| Every number field | `Hilbert12For R` | Universal recipe-dependent task over number fields and complex embeddings |
| Exactly the maximal abelian extension | `generated_eq_maximalAbelian` | Proved consequence of the contract |
| Unique algebraic labels | `evaluation_unique` | Proved from injectivity of the embedding |
| Rational benchmark | `exponentialFamily`, `cyclotomicParameters`, `RationalBenchmark` | Fully specified exponential family and closed generation statement |
| Primitive roots in the benchmark | `cyclotomic_evaluation_primitive` | Proved for every n+1 |
| General analytic admissibility | No closed condition supplied | Remaining specification gap, not a proof gap |

### What the new universal contract establishes

Let S be the set of algebraic preimages of a recipe's evaluations. Its contract
requires that every evaluation is algebraic, each finite stage is abelian, and
every finite abelian extension is contained in some finite stage. The checked
lemma proves K(S) = K^ab, where K^ab is defined by the supremum of all finite
abelian intermediate fields of a fixed algebraic closure. Stage monotonicity,
finite-stage containment in K(S), and implication to the older coverage
predicate are also proved.

This fixes the scope defect in a Stark-only target: the new task explicitly
ranges over every number field and every finite abelian extension. It does
**not** by itself fix the admissibility defect in unrestricted analytic
presentations. A Lean function can conceal arbitrary constants or an oracle,
even if its type never mentions a target extension. Consequently `Hilbert12For`
takes a fixed recipe; the packet does not advertise `∃ R, Hilbert12For R` as
Hilbert's problem. Restricting to computable recipes alone would not establish
historical adequacy either.

Fable should review a mathematical specification for admissible new functions
and evaluation rules before a closed general-existence target is accepted.
Constructing generators or proving the conjecture is not required in order to
state such a target. This distinction is why the missing admissibility rule is
recorded honestly, instead of being replaced by an arbitrary predicate or by
an assertion that formalization is impossible.

The rational benchmark exercises a concrete admissible family: every function
is exp and the n-th instruction evaluates it at 2πi/(n+1). Its primitive-root
property is proved. `RationalBenchmark` states the remaining generation
obligations; it is not presented as a new proof of Kronecker–Weber. The exact
Stark statement and the earlier constant-oracle counterexample remain in the
file for comparison.

## Review and completion status

The new classical fluid candidate and universal field-generation task are
available for manual review. They materially extend the previous packet.
Entire modern #6 and a closed adequate general #12 existence specification
remain incomplete. The separate #13 and #16 proposals are retained unchanged.
`coverage.json` records this independently of the compiler and axiom audit.
