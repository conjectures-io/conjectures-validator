# Primary sources and revision 9 review map

The current scope interpretation is in FULL_SCOPE.md. Earlier gap claims in
revision 7 were corrected after Fable review: no first-order bridge is required
for the physical models; classification output is a type-level task; no theorem
rules out every admissibility predicate for analytic recipes.

## Scope authority

Hilbert, *Mathematical Problems*, authorized Newson English translation (1902),
AMS 2000 reprint: https://people.seas.harvard.edu/~salil/cs121/fall12/handouts/Hilbert.pdf
Problem 6 is on printed pp. 418–419, Problem 12 on pp. 422–423. The Clark
transcription is a convenient cross-check:
https://mathcs.clarku.edu/~djoyce/hilbert/problems.html

Corry's historical account explains the programmatic character of #6:
https://www.tau.ac.il/~corry/publications/articles/pdf/LC-Hilbert6.pdf
Schappacher documents the correction to the imaginary-quadratic wording of #12:
https://smf.emath.fr/publications/history-hilberts-twelfth-problem

## Problem 6

Deng–Hani–Ma, kinetic theorem and Remark 1.5(4):
https://arxiv.org/html/2408.07818v1
The implemented canonical variant is asserted without proof in that remark;
the proof treats the grand-canonical ensemble. The normalization, collision
kernel, solution class and scaling were checked by Fable in round 1.

Deng–Hani–Ma, fluid limits:
https://arxiv.org/html/2503.01800v1
The implemented canonical ensemble and exact-Maxwellian Euler initial data
are declared variants. The reference's prepared Hilbert-expansion data do not
automatically justify the latter; an initial-layer result remains unverified.

Friesecke–James–Müller, geometric rigidity:
https://dept.aem.umn.edu/~james/research/Publications_Files/PDF/73.pdf
The local hyperelastic equations, free faces and orientation-penalising material
are explicit choices for Hilbert's continuum-to-rigid clause. The bridge to
global balances and the compactness-to-rigid limit are known-theorem obligations.
Their composition is conditional on common-time smooth elastic solutions.
Stationary elasticity and probability model witnesses, and mechanical solution
equivalence, address compatibility directly. First-order logic is optional.

## Problem 12

Milne, *Class Field Theory*, version 4.03:
https://www.jmilne.org/math/CourseNotes/CFT.pdf
V.2 defines moduli and ray class groups; V.3.5–3.6 give reciprocity/existence;
V.3.27–3.28 gives the conductor–discriminant formula. The code uses finitely supported
prime-exponent divisors, local congruences measured by normalized prime absolute
values, real-place positivity, exact Artin kernels and arithmetic Frobenius.
Character conductors are the conductors of their kernel-fixed subextensions.
The discriminant product excludes infinite places. ArithmeticOutput requires
actual degree/group/discriminant catalogues, not only their existence.

Silverman, *The Arithmetic of Elliptic Curves*, second edition, C.11.5(b) and
C.11.6, printed pp. 428–429:
https://www.math.ens.psl.eu/~obenoist/refs/Silverman.pdf
The CM benchmark uses the full integer lattice, j, and normalized Weber values
at nonidentity torsion. The g₃=0 and g₂=0 branches use squares and cubes. This
corrects the modular-only historical claim. Fable accepted it in round 2.
The main CM reciprocity action is not included; it is not asserted to be a
source requirement for every general analytic construction.

The rational benchmark uses exp(2πiz) at rational z, with no target-dependent
selection, and separately specifies the coprime-power/Frobenius action.
The free FieldHilbert12For contract allows analytic families attached only to
the embedded base field. The optional RationalPointContract is a benchmark
profile, not a universal admissibility criterion. The proved identity-reencoding
regression shows a concrete weakness of unrestricted parameters. Stronger
trivialization arguments in Fable's transcript are paper arguments, not Lean
theorems; no universal impossibility conclusion is adopted.

The separate rank-one abelian Stark statement follows Reid, Conjecture 1.1:
https://www.cambridge.org/core/services/aop-cambridge-core/content/view/853876BFEE7F6C3686DB9381CB3BF08F/S0010437X03100371a.pdf/local_stark_conjecture_at_a_real_place.pdf
The place norms, Frobenius convention, exceptional two-place unit condition,
partial zeta derivative and abelian root extension were accepted in review.

## Problems 13 and 16

Farb–Wolfson, Problem 1.5 and Definition 2.3:
https://www.math.uchicago.edu/~farb/papers/RD.pdf
The whole tensor product and finite-tower/root-cover formulation match the
algebraic RD(7)=3 question. Fable accepted this in round 1. No conjecture proof
or historical continuous-version equivalence is claimed.

For #16, see HILBERT16_SPECIFICATION.md. The accepted design is an output type,
with optional computability and a nonsingular primary class. Harnack and the
quartic maximum are concrete known-theorem benchmarks. The cycle bound remains
separate from output construction and from any literature-status claims.

## Review rule

The source-to-declaration ledger checks names and records semantic acceptance
separately. A source citation, declaration count or successful build cannot
establish that every interpretation of a historical research programme has
been exhausted. Final review must state the scope of any agreement explicitly.
