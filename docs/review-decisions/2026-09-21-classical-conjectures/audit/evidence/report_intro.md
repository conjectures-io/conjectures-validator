# Audit of the original 40 historical conjecture candidates

Audited on **2026-09-21**. This supersedes the first-pass assessment. **The original claim that all 40 are open is not defensible.** The disposition is **36 retained provisionally, 3 held for review of recent full-proof claims, and 1 removed as resolved**. “Retain” means suitable to continue considering, not certified admission-ready or guaranteed unsolved. Evidence is stronger for some rows than others; the individual notes identify older surveys, partial results, current status pages, and unvalidated claims.

The formalization result is more encouraging for the **new upstream snapshot**: all 40 target statements compile, their statement dependencies contain no `sorryAx` or nonstandard axioms, and no remaining mathematical mismatch was confirmed in those exact statements. However, the **verifier's older source pin contains two confirmed defects** relevant to this list. Matching Lean versions does not make the sources interchangeable.

## Decisions that change the shortlist

| Action | Candidate | Reason |
|---|---|---|
| Remove from open list | #35 Köthe | September 7 counterexample, September 14 corroborating construction, and public Lean matrix-form disproof. |
| Hold | #16 Catalan constant irrationality | September 3 complete-proof preprint; acceptance and correctness not established here. |
| Hold | #40 Scholz | August 17 full-proof claim; not independently validated here. |
| Hold | #4 Perfect cuboid | January/February 2026 nonexistence claim; not independently validated here. |
| Require corrected source | #27 Normality of π | Older verifier helper states simple normality only; new helper quantifies over every finite block. |
| Require corrected source | #32 Hardy–Littlewood I | Older verifier helper has big-O instead of asymptotic equivalence and omits distinctness of offsets. |
| Correct displayed question | #17 Brocard–Ramanujan | Formal True means the known three solutions are complete, while source prose asks whether there are others. |

The three holds are editorial caution, **not declarations that those claims are correct**. Likewise, the unvalidated claims logged for other famous problems are not silently being counted as accepted solutions. This audit cannot referee every purported proof on the internet. Köthe has materially stronger corroboration than a lone uploaded manuscript. No tasks were admitted, source pins changed, or live production catalog queried as part of this audit.

## What was actually checked

- Read all 40 exact selected statements, relevant custom definitions, quantifiers, domains, boundary cases, and answer polarity.
- Searched for current resolutions and distinguished full results from finite computations, conditional theorems, asymptotics, and special cases. Each entry has its own sources; an upstream `research open` label was not treated as independent evidence.
- Retrieved all 187 issues returned by the upstream `misformalization` search, inspected relevant reports, and retained 14 relevant issue records in the evidence directory. A closed issue was checked against the source rather than assumed fixed by its status alone.
- Compared 239 Formal Conjectures modules in the selected import closure against the verifier image. 79 source files differ or are new. Differences include module syntax and metadata as well as mathematical changes, so this is not a count of defects.
- Recompiled all 37 distinct selected source modules and affected dependencies: **87 module compilations, all exit code zero**. Unchanged dependencies and Mathlib came from the pinned verifier image. This is a source-overlay elaboration check, not a full repository CI run or an end-to-end submission test.
- Used Lean's elaborated constant information and transitive axiom collector on each **statement type**, separately from its admitted proof body. **40/40 types have no direct sorry and use only a subset of `propext`, `Classical.choice`, `Quot.sound`.** All 40 declaration bodies still depend on `sorryAx`, as expected for the repository's conjecture placeholders; none was proved by this audit.
- Kernel-checked the finite-dimensionality bridge for inverse Galois and the exact twin-prime singular-series local-factor identity. Both use only standard axioms. Other equivalence arguments noted below were reviewed mathematically, not all formalized.

## Exact environment and reproduction

| Component | Audited value |
|---|---|
| Upstream Formal Conjectures | `2f82a24192f64ac678557247e496b2381d3889a1` |
| Verifier Docker image | `sha256:b0b4929274e9f62d92749c814215ef21e546c9f882f20ba5c41d704996f5e5b3` |
| Lean, upstream and verifier | **4.33.1** |
| Lean commit | `819816b2e0a3bf405af45ae5c7af2491d8f5bee6` |
| Mathlib, upstream and verifier | `0df444a360eaa60ab8c11dca51a86af692955474` |
| Verifier Formal Conjectures base | `7d1a8c9912747679d0093f6d1216420c33ee5ffa` |
| Verifier patched Formal Conjectures | `8432eac998110a563e03df65a28c117e97c8c142` |

The earlier 4.27 version warning was wrong: it came from an older checkout. The image actually used here is 4.33.1. The build preserves the upstream Lean options and sets `weak.google.answer=always_true` to elaborate unanswered yes/no placeholders. Therefore a printed `True ↔ P` records the positive answer branch, **not evidence that P is true**. Admission must support and validate the intended resolution polarity.

Reproduction scripts, source hashes, successful build logs, the exact elaborated types, axiom lists, and selected source diffs are under [evidence](evidence/). Start with [reproduce.sh](evidence/reproduce.sh). The build overlay contains symlinks into the verifier container and must be used with that image; it is not a standalone Lean installation. Audit harness errors encountered while configuring options and the inspector were corrected before the recorded successful runs; they were not failures of the mathematical statements.

## Confirmed source-pin defects and issue triage

**Normality (#27):** the pinned `IsNormalInBase` quantifies over one digit `d<b`, with frequency `1/b`. That is simple normality. The corrected definition quantifies over `k` and `w : Fin k → ℕ`, requiring frequency `1/b^k` for every block. This matters even though the theorem's surface name is unchanged. See [issue 5710](https://github.com/google-deepmind/formal-conjectures/issues/5710) and [saved diff](evidence/definition-changes.diff).

**Hardy–Littlewood I (#32):** the pin's formula is `π_P =O ...`, not `π_P ~ ...`; it also lacks the corrected `m 0 = 0 → Function.Injective m →` premises. Duplicated offsets change the singular series relative to the tuple size. Use the fixed definition with its helper closure. See [issue 4996](https://github.com/google-deepmind/formal-conjectures/issues/4996) and the same saved diff.

**The remaining Hardy–Littlewood product complaint:** [open issue 5907](https://github.com/google-deepmind/formal-conjectures/issues/5907) alleges conditional convergence and hence misuse of Lean's unordered infinite product. My mathematical assessment is that this objection is not substantiated for the corrected distinct linear offsets. If r is the number of offsets, all sufficiently large primes distinguish their residues, and the factor is

`(1-r/q)/(1-1/q)^r = 1 + O_r(1/q²)`.

The first-order terms cancel. Thus the tail logarithms are absolutely summable. For admissible offsets the finitely many exceptional factors are positive; for nonadmissible offsets a factor is zero and the repository's admissible-tuple count is identically zero. At r=1 the formula reduces to the prime number theorem. The r=2 identity `factor = 1-1/(q-1)²` was kernel-checked. The full arbitrary-r convergence bridge was not formalized here, so this is an argued issue assessment, not an upstream issue closure.

**Borsuk (#38):** [issue 5657](https://github.com/google-deepmind/formal-conjectures/issues/5657) records the earlier real-diameter problem: unbounded sets can receive the default value zero from `Metric.diam`. The selected new file uses extended diameter plus bounded/nontrivial hypotheses. It is absent from the older image pin. Do not substitute the old Erdős 505 statement without its repairs.

Other relevant reports concern adjacent variants or helpers: regular-prime Bernoulli indices (#5711), Artin's conditional GRH hypothesis (#5701), smooth inscribed-square variants (#5704), inverse Galois over function fields (#5705), Mahler-measure variants (#5706), a no-three-in-line neighboring theorem (#5684), Fermat squarefreeness polarity (#5702), Polignac (#4997), and extended RH (#3931). Their existence does not automatically invalidate the particular 40 targets. [Recorded issue details](evidence/relevant-upstream-issues.json) preserve the distinctions. Köthe issue #5002 concerns a neighboring radical formulation, not the selected sum-of-ideals proposition.

## Why Köthe must come off the open list

The [September 7 paper](https://arxiv.org/abs/2609.07996) constructs a counterexample. The [September 14 paper](https://arxiv.org/abs/2609.15080) gives a short point-module construction refuting the conjecture. The [public Lean repository](https://github.com/tadamcz/koethe) targets an equivalent matrix formulation. I inspected its stated target and verification claims but **did not independently replay that external proof** under either its original toolchain or our 4.33.1 verifier.

The connection to our selected statement is not merely a shared name. Given a nil two-sided ideal I for which M₂(I) is not nil, take the two left ideals in M₂(R) consisting of matrices supported in one column with entries in I. A column-j matrix with entries a_i has powers with column entries `a_i * a_j^(k-1)`, hence is nilpotent because a_j is. Both column ideals are nil, while their sum M₂(I) contains the nonnilpotent matrix. This manually checked bridge refutes the selected sum-of-two-nil-left-ideals formulation. The stale `research open` attribute should not justify a new open-problem reward.

## Per-candidate audit

All entries below refer to the exact upstream commit above, not a moving main branch. **Compile and statement-axiom checks passed for every row.** “No confirmed mismatch” is bounded by the checks performed; it is not a proof of equivalence to every historical formulation.

