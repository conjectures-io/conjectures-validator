# Hilbert intake — 2026-11-12

One addition qualifies in this review: **Dirichlet generalized Riemann hypothesis
(GRH)**, in the generalized-zeta theme of Hilbert #8. This is a reinstatement of
an earlier pool entry. RH, Goldbach, and twin primes are already active. No
additional numbered Hilbert problem has passed admission here.

The candidate release contains 307 reward targets and 614 proof/counterexample
bundles, against a production baseline of 306 targets. This document is not
evidence of deployment; see `validation.json` for completed checks and release state.

## Decisions

| Problem or scope | Formal statement found | Decision |
| --- | --- | --- |
| #8: Riemann hypothesis | `RiemannHypothesis.riemannHypothesis` | Already active; no duplicate |
| #8: Goldbach | `GoldbachConjecture.goldbach` | Already active; no duplicate |
| #8: twin primes | `TwinPrimes.twin_primes` | Already active; no duplicate |
| #8: Dirichlet GRH | `GRH.generalized_riemann_hypothesis` | Re-admit the reviewed exact proposition |
| #8: full number-field ERH | No correct statement in the pinned RH module | Exclude: the former naive Dedekind-series version was removed upstream |
| #5 extension: Hilbert–Smith, including the p-adic formulation | Two pinned `research open` statements | Hold: missing manifold hypothesis in pinned types, plus recent full-proof claims |
| #6: axiomatization of physics | No complete problem-level statement found in the checked collection | Do not substitute a single equation or partial physics result for the program |
| #12: explicit class field theory | No complete Lean target found | Needs a precise, audited formal statement |
| #13: algebraic/resolvent-degree form | No complete Lean target found | Needs a precise, audited formal statement; the continuous version is a different question |
| #16: general real curves and limit cycles | No complete open Lean target found | The published Lean scope located covers quintic Liénard systems only |
| #10 over the rationals, an extension of the original integer problem | No eligible pinned statement found | Also has a recent full-solution claim; not an addition |
| Original #5 and #17 | Present in Formal Conjectures, marked `research solved` | Exclude solved statements even when their Lean proofs are placeholders |

This is a bounded intake review, not a claim to have searched every repository or
proof assistant. Current Formal Conjectures contains only `5.lean` and `17.lean`
in `HilbertProblems/`; cross-listed #8 statements are in other directories.
Broad historical programs and related generalizations are distinguished from
separate original numbered problems. “Formalized” here means a faithful theorem
statement suitable for the existing Lean task pipeline; it does not require a
proof of the open conjecture.

## GRH statement and literature review

The target quantifies over positive moduli, primitive complex Dirichlet
characters, and zeros of Mathlib's analytically continued `LFunction`. Its
`trivialZeros` definition handles conductor one, odd characters, and nonprincipal
even characters separately. The upstream `implies_riemannHypothesis` sanity
theorem proves agreement with RH at conductor one. The target does not use the
uncorrected Dedekind `LSeries` outside its domain of convergence.

The pinned statement hash is
`sha256:19f4c1959e1bdd118d84801a2fbdc6d73ba2a83678e3c90744dba7ee919bb57c`.
The current [upstream statement](https://github.com/google-deepmind/formal-conjectures/blob/a425ca3eb797fe927cb88c950565fdcbbbbada70/FormalConjectures/Millennium/RiemannHypothesis.lean)
still labels it open. The recent [quasi-RH formalization scope](https://github.com/openai/math/blob/4eaee87548250b2682affda7d05d947c05554bae/lean/docs/003.md)
gives a zero-free half-plane \(\Re s>7/8\), which does not establish the requested
critical line \(\Re s=1/2\). The bounded search identified no accepted complete
solution. An open annotation or a failed search alone is not proof of unsolvedness.
The [Clay Mathematics Institute](https://www.claymath.org/millennium/riemann-hypothesis/)
also still lists the classical RH as unsolved on the date of this review.

Hilbert's [original problem 8](https://mathcs.clarku.edu/~djoyce/hilbert/problems.html)
discusses RH, Goldbach, twin primes, and zeta functions over number fields. The
Dirichlet target is a specific modern component of that generalized-zeta theme;
it is not presented as the entire number-field ERH.

GRH's exact theorem and type were on the retirement list. The source of that
retirement is validator commit
`c2f662d12815bf8026984d2997c48129ca154319`, July 24: it replaced the previous
64-task pool with 100 Erdős tasks and retired previous pool entries for
freshness. The migration's README and diff are retained in
`evidence/old-pool-retirement.diff`. No corresponding solved-problem retirement
entry or modern version-registry record was found. This intake removes only
GRH's theorem and exact type from that old exclusion list, following the user's
request to include formalized open Hilbert problems. Every other retirement is
preserved.

## Hilbert–Smith hold

The two production-pinned theorem types omit `ChartedSpace` and the finite
manifold dimension. A section variable in the source was not included in the
elaborated proposition. The pinned Lie-group presentation additionally requires
second countability without a corresponding hypothesis on the acting group.
Current [upstream #5](https://github.com/google-deepmind/formal-conjectures/blob/a425ca3eb797fe927cb88c950565fdcbbbbada70/FormalConjectures/HilbertProblems/5.lean)
has repairs, but those repairs are not part of the current production pin.

There is also a direct [author proof/formalization repository](https://github.com/adbrw/HS_proof),
dated October 8, which claims the second-countable Hilbert–Smith theorem and
p-adic exclusion, with a Mathlib-only challenge and matching Lean solutions.
Its `FCChallenge.lean` explicitly distinguishes its second-countability and
smoothness hypotheses from upstream's variants. The September 23
[OpenAI manuscript](https://github.com/openai/math/blob/4eaee87548250b2682affda7d05d947c05554bae/preprints/The-Hilbert-Smith-conjecture-in-every-finite-dimension-September-23-2026/paper.pdf)
also claims a full theorem. These are claims requiring review, not independently
verified solutions in this intake. Neither justifies advertising Hilbert–Smith
as having no solution claims. No Hilbert–Smith target is admitted.

## Other formalization leads

The checked ProofAtlas pages for
[#12](https://www.proofatlas.ai/collaboration/hilberts-twelfth-problem/),
[#13](https://www.proofatlas.ai/collaboration/hilberts-13th-problem-algebraic-form/),
[#16](https://www.proofatlas.ai/collaboration/hilberts-sixteenth-problem/), and
[#10 over Q](https://www.proofatlas.ai/collaboration/hilberts-tenth-problem-over-q/)
describe research investigations and future formalization work. Their line
counts are not Lean source or evidence of compilable complete statements.
These pages were discovery leads, not authoritative substitutes for theorem
source. None yielded another admission-ready complete target.

For #16, the primary [published Lean scope](https://github.com/openai/math/blob/4eaee87548250b2682affda7d05d947c05554bae/lean/docs/143.md)
is the sharp bound of two limit cycles for quintic Liénard systems. It does not
formalize the claimed general degree-uniform bound, arrangement classification,
or the first part about real algebraic curves. A formalized solved special case
does not satisfy this request for open problems.

## Source and release boundaries

- Production source: `17acb8768de75e168a43c7c828e933071aee63cf`.
- Production tasks baseline: `4a10571b0e196007207b02b585fad5e46f16abd6`.
- Candidate tasks: [`527e0da31d709af1b451c5f7cfe8932afe767706`](https://github.com/conjectures-io/conjectures-tasks/commit/527e0da31d709af1b451c5f7cfe8932afe767706),
  on `codex/hilbert-grh-20261112`.
- Upstream inventory checked: `a425ca3eb797fe927cb88c950565fdcbbbbada70`.
- Lean: `4.35.0-rc2`; Mathlib: `065356127b1dc0016f66b7283ce0ce2c4055aa55`.
- Existing source, proof definitions, and compiled environment are retained.
- Existing reviews keep their original dates. Only GRH receives a new November
  12 status review; the inherited global source/PR inventory is not relabeled
  as freshly reviewed.
- The held November 11 Erdős batch is separate and is not activated here.
  Task `main` currently contains that batch, including solved Erdős 173. This
  candidate therefore pins the isolated production-baseline branch plus GRH;
  it must not be rebased onto task `main` without resolving that held batch.

The narrow source identity exception admits only the exact GRH theorem in its
canonical file. Schema-3 admission now supports named classical targets with
both an exact reviewed type hash and the existing classical-status evidence.
It does not waive the retirement, proof-collision, or source-fidelity checks.
