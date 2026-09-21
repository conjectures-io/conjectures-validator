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

### 1. Odd perfect numbers — RETAIN — provisionally open

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Wikipedia/PerfectNumbers.lean#L83) · `PerfectNumbers.odd_perfect_number_conjecture`

**Status:** Retain as provisionally open. Classical research sources treat existence as unresolved. Located a 2021 nonexistence claim, but did not establish its acceptance or validate its proof; this is not a certified absence-of-proof finding. [Source 1](https://abel.math.harvard.edu/~knill/seminars/perfect/perfect.pdf), [Source 2](https://arxiv.org/abs/2101.07176).

**Formalization:** Nat.Perfect excludes zero. The selected question concerns odd positive perfect numbers, not the solved classification of even perfect numbers.

### 2. Infinitely many Mersenne primes — RETAIN — provisionally open

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Wikipedia/Mersenne.lean#L135) · `Mersenne.infinitely_many_mersenne_primes`

**Status:** Retain as provisionally open. A 2025 preprints.org infinitude claim was found; no accepted resolution or independently checked proof was established. [Source 1](https://durham-repository.worktribe.com/OutputFile/1495393), [Source 2](https://www.preprints.org/manuscript/202510.0673).

**Formalization:** Infinitely many exponents producing Mersenne primes is equivalent to infinitely many distinct Mersenne primes. It also duplicates infinitude of even perfect numbers, so do not reward those separately.

### 3. Infinitely many Fermat primes — RETAIN — provisionally open

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Wikipedia/Fermat.lean#L41) · `Fermat.infinite_fermat_primes`

**Status:** Retain as open in the sources examined. Infinitude is a question, not the prevailing expectation; a proof of finitude would resolve it. [Source 1](https://math.dartmouth.edu/~carlp/PDF/paper62.pdf).

**Formalization:** Uses the actual Fermat sequence 2^(2^n)+1. The historical date records the sequence's origins, not a verified first formulation of this infinitude question.

### 4. Perfect cuboid — HOLD — proof claim review

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Wikipedia/EulerBrick.lean#L55) · `EulerBrick.perfect_euler_brick_existence`

**Status:** HOLD: a January 2026 complete obstruction claim, revised February 9, directly addresses perfect cuboids. Acceptance and correctness were not established in this audit. Do not advertise the question as unqualifiedly open pending claim review. [Source 1](https://arxiv.org/abs/2602.00239), [Source 2](https://arxiv.org/abs/1704.00165).

**Formalization:** Positive natural edges exclude zero-length degeneracies; all three face diagonals and the body diagonal are required to be integral. The 2017 error analysis concerns an older Wyss claim, not a refutation of the 2026 claim.

### 5. Euler–Mascheroni constant irrationality — RETAIN — provisionally open

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Wikipedia/Irrational.lean#L95) · `Irrational.irrational_eulerMascheroniConstant`

**Status:** Retain as open in the sources examined. A search hit about generalized Euler numbers is not a result on the Euler–Mascheroni constant. [Source 1](https://webusers.imj-prg.fr/~michel.waldschmidt/articles/pdf/EulerConstantVI.pdf), [Source 2](https://arxiv.org/abs/2509.13354).

**Formalization:** Direct Irrational predicate on the Euler–Mascheroni constant. Distinguish gamma from Euler's number e and from generalized factorial-series constants.

### 6. Goldbach conjecture — RETAIN — provisionally open

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Wikipedia/GoldbachConjecture.lean#L36) · `GoldbachConjecture.goldbach`

**Status:** Retain as open. Helfgott's theorem concerns three primes and does not prove the binary conjecture selected here. [Source 1](https://www.maths.ox.ac.uk/outreach/oxford-online-maths-club/episode-7), [Source 2](https://arxiv.org/abs/1501.05438).

**Formalization:** Every even natural number greater than two must be a sum of two primes. Repeated primes are allowed, as required for four.

### 7. Euler's idoneal-number completeness — RETAIN — provisionally open

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Wikipedia/IdonealCompleteness.lean#L102) · `Idoneal.idoneal_numbers_completeness`

**Status:** Retain as open unconditionally. Kani's survey gives completeness of the known 65 under GRH and an unconditional bound of at most 67; neither is unconditional completeness. [Source 1](https://mast.queensu.ca/~kani/papers/2011K1.pdf), [Source 2](https://users.fmf.uni-lj.si/skreko/Old/MyPapers/01157.pdf), [Source 3](https://www.sciencedirect.com/science/article/pii/S0022404925001136).

**Formalization:** The definition excluding representations ab+bc+ac with 0<a<b<c has a published equivalent characterization. The target compares the entire set with the explicit 65-element list. Finite checking of that list does not prove completeness.

### 8. Legendre conjecture — RETAIN — provisionally open

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Wikipedia/LegendreConjecture.lean#L38) · `LegendreConjecture.legendre_conjecture`

**Status:** Retain as provisionally open. A 2026 paper explicitly studies weaker Legendre bounds. Ferreira's short-interval preprint supplies a recent claim behind neighboring eventual results; no accepted resolution of this full universal target was established. [Source 1](https://arxiv.org/abs/2602.22502), [Source 2](https://arxiv.org/abs/2307.08725).

**Formalization:** Strictly between n² and (n+1)² for every n≥1. An unspecified sufficiently-large-n theorem does not discharge all small cases. Audit proof dependencies separately from nearby research-solved labels.

### 9. Gauss real quadratic class-number-one problem — RETAIN — provisionally open

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Wikipedia/ClassNumberProblem.lean#L41) · `ClassNumberProblem.class_number_problem`

**Status:** Retain as open. A 2026 University of Padua research abstract still identifies infinitude of real quadratic fields of class number one as unresolved. [Source 1](https://www.math.unipd.it/news/simultaneous-divisibility-of-class-numbers-of-quadratic-fields/).

**Formalization:** Squarefree integers d>1 parameterize real quadratic fields. AdjoinRoot uses an explicit irreducibility witness. The target concerns the ordinary class number of the full ring of integers, not the narrow class number or imaginary fields.

### 10. Twin prime conjecture — RETAIN — provisionally open

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Wikipedia/TwinPrimes.lean#L37) · `TwinPrimes.twin_primes`

**Status:** Retain as open in the sources examined. Bounded prime gaps do not establish infinitely many gaps equal to two. [Source 1](https://terrytao.wordpress.com/2014/02/07/new-equidistribution-estimates-of-zhang-type-and-bounded-gaps-between-primes-and-a-retrospective/).

**Formalization:** Infinitude of prime p with p+2 prime. Correct exact-gap target; finite searches and any larger bounded-gap theorem are insufficient.

### 11. Kummer–Vandiver conjecture — RETAIN — provisionally open

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Wikipedia/KummerVandiver.lean#L39) · `KummerVandiver.kummer_vandiver`

**Status:** Retain as open. The 2026 partial-regularity result does not establish the full Kummer–Vandiver conjecture. [Source 1](https://arxiv.org/abs/2602.05090).

**Formalization:** For prime positive p, p does not divide the class number of the maximal real subfield of the p-th cyclotomic field. This is distinct from regularity of p itself.

### 12. Infinitely many regular primes — RETAIN — provisionally open

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Wikipedia/RegularPrimes.lean#L79) · `RegularPrimes.regularprime_conjecture`

**Status:** Retain as open. The 2026 density-one partial-regularity result controls only a restricted range of Bernoulli indices and does not prove infinitely many fully regular primes. [Source 1](https://arxiv.org/abs/2602.05090), [Source 2](https://oeis.org/A000928/internal).

**Formalization:** Main definition uses coprimality with cyclotomic class-group cardinality. Upstream issue 5711 corrected even Bernoulli indices in an auxiliary characterization; the selected main definition avoids that former indexing error.

### 13. Pollock's tetrahedral-number conjecture — RETAIN — provisionally open

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Wikipedia/PollocksConjecture.lean#L59) · `PollocksConjecture.pollock_tetrahedral`

**Status:** Retain as open. The research paper on sums of two generalized tetrahedral numbers treats a different problem; allowing negative indices changes the set of summands. [Source 1](https://vadim.sdsu.edu/lp2.pdf).

**Formalization:** Five ordinary tetrahedral numbers with natural indices. Zero is allowed, making exactly five slots equivalent to at most five summands. Natural division by six is exact for n(n+1)(n+2).

### 14. Bunyakovsky conjecture — RETAIN — provisionally open

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Wikipedia/Bunyakovsky.lean#L37) · `Bunyakovsky.bunyakovsky_conjecture`

**Status:** Retain as open; the 2025 research lectures formulate the polynomial-prime conjecture with its necessary conditions. [Source 1](https://stnb.cat/media/publicacions/publicacions/AFBoix_STNB2025_Lectures_on_schinzel_for_polynomials_summarybis.pdf).

**Formalization:** Shared helpers require nonconstant irreducible integer polynomial, positive leading coefficient, and no fixed prime divisor. Using natAbs for prime values is harmless for infinitude because the polynomial is eventually positive.

### 15. Riemann hypothesis — RETAIN — provisionally open

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Millennium/RiemannHypothesis.lean#L61) · `RiemannHypothesis.riemannHypothesis`

**Status:** Retain as open. The Clay Mathematics Institute's current page explicitly lists the Riemann hypothesis as unsolved. [Source 1](https://www.claymath.org/millennium/riemann-hypothesis/).

**Formalization:** Direct use of Mathlib's RiemannHypothesis. It is not the defective naive Dedekind-zeta-series extension discussed in upstream issue 3931. Preserve this exact target.

### 16. Catalan's constant irrationality — HOLD — proof claim review

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Wikipedia/Irrational.lean#L103) · `Irrational.irrational_catalanConstant`

**Status:** HOLD: Zhi-Wei Sun's September 3, 2026 preprint claims a complete proof of irrationality. This audit did not establish acceptance or independently validate the full argument; public objections are not themselves a certified disproof. [Source 1](https://arxiv.org/abs/2609.04176).

**Formalization:** Direct Irrational predicate on Catalan's constant, so the new claim addresses the selected mathematical problem exactly. The formal statement itself showed no identified mismatch.

### 17. Brocard–Ramanujan factorial problem — RETAIN — provisionally open

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/ErdosProblems/398.lean#L39) · `Erdos398.erdos_398`

**Status:** Retain as open. The live Erdős Problems entry marks the exact factorial equation problem open; conditional finiteness is weaker than determining every solution. [Source 1](https://www.erdosproblems.com/398).

**Formalization:** The formal proposition says that the n-values are exactly {4,5,7}. Its prose asks whether there are other solutions, reversing the yes/no polarity. Fix the displayed question: True for this formal target means there are NO other solutions.

### 18. Catalan–Mersenne primality question — RETAIN — provisionally open

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Wikipedia/Mersenne.lean#L145) · `Mersenne.catalans_mersenne_conjecture`

**Status:** Retain as unresolved with historical-attribution caution. The sequence reference does not determine primality of every iterate; Catalan's original bounded wording is not the modern all-terms statement. [Source 1](https://oeis.org/A007013).

**Formalization:** Starts at two and iterates x↦2^x−1; selected target requires all terms prime. Do not call the exact all-terms proposition a verbatim 1876 formulation or confuse it with ordinary Mersenne-prime infinitude.

### 19. Oppermann conjecture — RETAIN — provisionally open

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Wikipedia/Oppermann.lean#L57) · `Oppermann.oppermann_conjecture`

**Status:** Retain as provisionally open. Strong-Andrica implications and finite verification do not settle the universal statement. The Ferreira preprint behind neighboring eventual variants requires separate claim review. [Source 1](https://arxiv.org/abs/1812.02762), [Source 2](https://arxiv.org/abs/2307.08725).

**Formalization:** Requires a prime in each of the two strict intervals around n², with n≥2. The n=1 failure is correctly excluded. An eventual variant is not the selected full statement.

### 20. Inverse Galois problem — RETAIN — provisionally open

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Wikipedia/InverseGalois.lean#L53) · `InverseGalois.inverse_galois_problem`

**Status:** Retain as open. Recent solvable-group/Grunwald progress does not realize every finite group over the rationals. [Source 1](https://arxiv.org/abs/2302.13719), [Source 2](https://arxiv.org/abs/2604.18099).

**Formalization:** GaloisRealization omits an explicit FiniteDimensional field, but it follows from IsGalois and finiteness of the automorphism group. This implication was kernel-checked in evidence/Bridges.lean with only standard axioms; this omission is not a loophole.

### 21. Hadamard matrix existence — RETAIN — provisionally open

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Wikipedia/Hadamard.lean#L90) · `Hadamard.HadamardConjecture`

**Status:** Retain as open in the sources examined. New matrix orders, including any reported construction at a previously missing order, do not establish existence at every multiple of four. [Source 1](https://arxiv.org/abs/2605.16722).

**Formalization:** The determinant-extremal ±1-matrix formulation is equivalent to orthogonal Hadamard rows. The order-zero case is the legitimate empty matrix. Do not conflate a solved finite order with the universal conjecture.

### 22. Lemoine conjecture — RETAIN — provisionally open

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Wikipedia/Lemoine.lean#L39) · `Lemoine.lemoine_conjecture`

**Status:** Retain as provisionally open. Computational verification is finite; located asymptotic claims do not by themselves certify the universal statement. [Source 1](https://arxiv.org/abs/2304.00024), [Source 2](https://arxiv.org/abs/1709.05335), [Source 3](https://arxiv.org/abs/2012.01329).

**Formalization:** Odd n>6 must equal p+2q with both p and q prime. Repeated primes are permitted. Correct ordinary Lemoine formulation.

### 23. No-three-in-line problem — RETAIN — provisionally open

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/GreensOpenProblems/72.lean#L103) · `Green72.green_72`

**Status:** Retain as open. The 2026 no-(k+1)-in-line result addresses k≥3, excluding this no-three-in-line case; newer heuristic corrections are not a resolution. [Source 1](https://people.maths.ox.ac.uk/greenbj/papers/open-problems.pdf), [Source 2](https://arxiv.org/abs/2607.05255), [Source 3](https://arxiv.org/abs/2603.00215).

**Formalization:** Green 72 asks eventual NONattainment of 2N points, not existence of 2N for every N. The finite grid bounds the supremum. Neighboring eventual-attainment conjectures are not the logical negation. Its 1900/1917 dates belong to the source puzzle.

### 24. Dickson conjecture — RETAIN — provisionally open

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Wikipedia/Dickson.lean#L43) · `Dickson.dickson_conjecture`

**Status:** Retain as open; current research exposition treats simultaneous prime values of admissible linear forms as conjectural. [Source 1](https://stnb.cat/media/publicacions/publicacions/AFBoix_STNB2025_Lectures_on_schinzel_for_polynomials_summarybis.pdf).

**Formalization:** Degree-one, positive-leading-coefficient forms and the shared admissibility predicate match Dickson. Upstream issue 4997 concerns a neighboring Polignac variant, not this selected target.

### 25. Brocard's prime-square conjecture — RETAIN — provisionally open

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Wikipedia/BrocardConjecture.lean#L40) · `Brocard.brocard_conjecture`

**Status:** Retain as provisionally open. Conditional implications and finite verification do not settle every consecutive-prime pair; eventual short-interval claims require separate checking. [Source 1](https://arxiv.org/abs/1812.02762), [Source 2](https://arxiv.org/abs/2307.08725).

**Formalization:** At least four primes between consecutive prime squares. Nat.nth uses zero-based indexing, so n≥1 correctly starts at primes 3 and 5 rather than the exceptional pair 2 and 3.

### 26. Carmichael totient conjecture — RETAIN — provisionally open

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Wikipedia/CarmichaelTotient.lean#L59) · `CarmichaelTotient.charmichaelTotient`

**Status:** Retain as open in the sources examined. Evidence here is older research literature; no accepted resolution was located, which is weaker than a fresh authoritative status confirmation. [Source 1](https://math.dartmouth.edu/~carlp/PDF/carmichaelconjecture.pdf).

**Formalization:** Every positive n has a different natural m with the same totient. Since totient(n)>0 for n>0, allowing m=0 does not create a spurious witness. Preserve the actual source identifier charmichaelTotient.

### 27. Normality of π in base 10 — RETAIN — provisionally open

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Wikipedia/NormalityOfPi.lean#L43) · `NormalNumber.pi_normal_base_ten`

**Status:** Retain as open. Empirical digit-frequency tests do not establish limiting frequencies for every block length. [Source 1](https://www.davidhbailey.com/dhbpapers/normality-digits-pi.pdf), [Source 2](https://webusers.imj-prg.fr/~michel.waldschmidt/articles/pdf/AWSLecture5.pdf).

**Formalization:** NEW snapshot checks all finite digit blocks with limiting frequency 10^(-k). The verifier's pinned helper only checks individual digits (simple normality): a confirmed weaker formulation. Import the corrected helper, not only the unchanged-looking theorem name. Upstream issue 5710.

### 28. Inscribed square problem — RETAIN — provisionally open

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Wikipedia/InscribedSquare.lean#L57) · `InscribedSquare.inscribed_square_problem`

**Status:** Retain as open for arbitrary Jordan curves. The June 2026 peer-reviewed result for curves between two graphs does not cover every continuous embedded circle. [Source 1](https://ems.press/journals/cmh/articles/14299791).

**Formalization:** Embedding of Circle into the Euclidean plane enforces a Jordan curve. Midpoint/diagonal conditions and side inequalities specify a nondegenerate rectangle, with ratio one giving a square. The selected statement imposes no smoothness shortcut.

### 29. Infinitely many primes n²+1 — RETAIN — provisionally open

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Wikipedia/PrimesAndPerfectSquares.lean#L35) · `PrimesAndPerfectSquares.infinite_prime_sq_add_one`

**Status:** Retain as open. Large-prime-factor results for n²+h do not show that n²+1 itself is prime infinitely often. [Source 1](https://arxiv.org/abs/2505.00493), [Source 2](https://dms.umontreal.ca/~andrew/PDF/ItalySurvey.pdf).

**Formalization:** Infinitely many natural n with n²+1 prime, equivalent to infinitely many distinct such prime values. This is Landau's polynomial-prime problem, not a bounded or almost-prime variant.

### 30. Gauss circle error bound — RETAIN — provisionally open

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Wikipedia/GaussCircleProblem.lean#L92) · `GaussCircleProblem.error_isBigO`

**Status:** Retain as open. A September 2026 research preprint explicitly describes the sharp Gauss-circle bound as unresolved and reports numerical experiments. [Source 1](https://arxiv.org/abs/2609.04725).

**Formalization:** Counts integer lattice points in a closed radius-r disk and subtracts πr². Existence of an o(1) exponent is mathematically equivalent to O(r^(1/2+ε)) for every ε>0 by a diagonal-threshold argument. This bridge was reasoned manually, not formalized here.

### 31. Goormaghtigh conjecture — RETAIN — provisionally open

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Wikipedia/Goormaghtigh.lean#L50) · `Goormaghtigh.goormaghtigh_conjecture`

**Status:** Retain as open. The large finite bound for Goormaghtigh primes concerns prime common repunit values and does not classify all composite values or the unbounded problem. [Source 1](https://arxiv.org/abs/2410.03677), [Source 2](https://arxiv.org/abs/2505.08160).

**Formalization:** Bases are distinct and at least two; both repunit lengths are at least three. The selected statement classifies common values as 31 and 8191, including composite values in its quantification.

### 32. First Hardy–Littlewood conjecture — RETAIN — provisionally open

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Wikipedia/HardyLittlewood.lean#L94) · `HardyLittlewood.first_hardy_littlewood_conjecture`

**Status:** Retain as open. This is the full prime-tuples asymptotic, not a bounded-gap theorem. An open upstream product-convergence complaint was examined but is not substantiated for the current distinct-offset formulation; see the detailed analysis in AUDIT.md. [Source 1](https://terrytao.wordpress.com/2014/02/07/new-equidistribution-estimates-of-zhang-type-and-bounded-gaps-between-primes-and-a-retrospective/), [Source 2](https://github.com/google-deepmind/formal-conjectures/issues/5907), [Source 3](https://github.com/google-deepmind/formal-conjectures/issues/4996).

**Formalization:** NEW snapshot uses asymptotic equivalence and injective offsets. The verifier pin instead uses big-O and permits duplicate offsets: confirmed misformalization. For distinct r offsets, large-prime factors are 1+O(1/q²), so the alleged general conditional-product problem does not apply automatically.

### 33. Second Hardy–Littlewood conjecture — RETAIN — provisionally open

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Wikipedia/HardyLittlewood.lean#L112) · `HardyLittlewood.second_hardy_littlewood_conjecture`

**Status:** Retain as unresolved. A 2026 Acta Arithmetica paper proves restricted ranges, not the full inequality. Expected falsity is compatible with open status. [Source 1](https://arxiv.org/abs/2503.02766), [Source 2](https://www.impan.pl/en/publishing-house/journals-and-series/acta-arithmetica/online/116375/on-the-second-hardy-littlewood-conjecture).

**Formalization:** π(x+y)≤π(x)+π(y) for natural x,y≥2. This conjecture is incompatible with Hardy–Littlewood I; do not assume both as trusted lemmas or present both as necessarily true.

### 34. Artin primitive-root conjecture — RETAIN — provisionally open

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Wikipedia/ArtinPrimitiveRootsConjecture.lean#L120) · `ArtinPrimitiveRootsConjecture.artin_primitive_roots.parts.i`

**Status:** Retain as open unconditionally. Conditional proofs and density formulae under suitable GRH hypotheses do not prove the selected unconditional statement. [Source 1](https://guests.mpim-bonn.mpg.de/moree/artinsurveysmallfont.pdf), [Source 2](https://arxiv.org/abs/1112.4816).

**Formalization:** Eligible integer a is neither a square nor -1. The target asks positive RELATIVE density among primes, not positive density among all integers. orderOf in ZMod expresses primitive roots. A repaired neighboring GRH formulation is not an assumption of this selected theorem.

### 35. Köthe conjecture — REMOVE — resolved

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Wikipedia/Koethe.lean#L53) · `Koethe.KotheConjecture`

**Status:** REMOVE from an open-problem list: September 2026 counterexample papers refute Köthe, with a public Lean disproof of the equivalent matrix formulation. Strong corroboration exceeds a lone proof claim, but this audit did not independently replay that external proof repository. [Source 1](https://arxiv.org/abs/2609.07996), [Source 2](https://arxiv.org/abs/2609.15080), [Source 3](https://github.com/tadamcz/koethe).

**Formalization:** The sum of two nil LEFT ideals in an arbitrary unital ring is the intended statement. The matrix counterexample bridges to it using two column left ideals. The source research-open label is stale; no formalization defect is needed to explain the resolution.

### 36. Lehmer totient problem — RETAIN — provisionally open

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Wikipedia/LehmerTotient.lean#L35) · `LehmerTotient.lehmer_totient`

**Status:** Retain as open. The January 2026 purported short proof is explicitly withdrawn; a February 2026 INTEGERS paper still investigates the unresolved problem. [Source 1](https://www.preprints.org/manuscript/202601.1141), [Source 2](https://math.colgate.edu/~integers/aa33/aa33.pdf).

**Formalization:** Asks whether there exists composite n>1 with φ(n) dividing n−1. True means a counterexample to the usual nonexistence conjecture. Label the yes/no task by existence rather than reversing its expected answer.

### 37. Lehmer Mahler-measure problem — RETAIN — provisionally open

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Wikipedia/LehmerMahlerMeasureProblem.lean#L47) · `LehmerMahlerMeasureProblem.lehmer_mahler_measure_problem`

**Status:** Retain as open in the sources examined. The status basis includes an older specialist survey; no established resolution was located in the current search. [Source 1](https://arxiv.org/abs/math/0701397), [Source 2](https://www.maths.ed.ac.uk/~chris/papers/Smyth240707.pdf).

**Formalization:** Mahler measure uses complex roots with multiplicity and the leading-coefficient norm. The selected problem is existence of a uniform gap above one for integer polynomials, not the stronger claim that Lehmer's specific polynomial attains the optimum.

### 38. Borsuk problem in dimension four — RETAIN — provisionally open

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Wikipedia/BorsukConjecture.lean#L111) · `Borsuk.borsuk_conjecture.four`

**Status:** Retain the dimension-four case as open. A May 2026 preprint improves an upper bound to eight; the selected target asks five. High-dimensional counterexamples do not settle dimension four. [Source 1](https://arxiv.org/abs/2605.19068).

**Formalization:** NEW Borsuk definition requires a bounded nontrivial set and uses extended diameter. This avoids the old Metric.diam convention assigning zero to unbounded sets. Cover by at most five smaller sets converts to a partition by intersections and differences; old Erdős 505 code is not a safe substitute.

### 39. Erdős–Szekeres convex-polygon conjecture — RETAIN — provisionally open

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/ErdosProblems/107.lean#L50) · `Erdos107.erdos_107`

**Status:** Retain as open. The current Erdős Problems entry distinguishes the exact 2^(n−2)+1 threshold from known asymptotic results. [Source 1](https://www.erdosproblems.com/107).

**Formalization:** No-three-collinear general position and convex-independent subsets express the intended planar problem. n≥3 is correct. Natural sInf is meaningful using the classical existence of a finite Erdős–Szekeres threshold; that existence bridge was not re-proved here.

### 40. Scholz addition-chain conjecture — HOLD — proof claim review

[Exact Lean source](https://github.com/google-deepmind/formal-conjectures/blob/2f82a24192f64ac678557247e496b2381d3889a1/FormalConjectures/Wikipedia/ScholzConjecture.lean#L44) · `ScholzConjecture.scholz_conjecture`

**Status:** HOLD: an August 17, 2026 author-uploaded paper claims the full Scholz inequality. Earlier infinite-subfamily results do not settle it. This audit did not establish acceptance or validate the complete new proof. [Source 1](https://www.researchgate.net/publication/412298449_THE_SCHOLZ_CONJECTURE_VIA_A_MULTI-STEP_OPTIMIZATION), [Source 2](https://arxiv.org/abs/2210.13812), [Source 3](https://arxiv.org/abs/2302.02143).

**Formalization:** Addition chains begin at one and increase strictly. Summands are drawn from the list, but positivity forces both to precede the element they sum to. Positive n avoids the zero edge case; chains exist via 1,2,…,n, justifying the minimum-length interpretation.

## Admission recommendation and limits

Continue with the 36 retained entries using the audited source revision, remove Köthe from the open list, and leave the three claim-review holds out of an unqualified open catalog. Before admission, generate each actual task package and confirm its statement/helper closure matches this audit, its answer polarity is correct, and a proposed proof cannot import an unresolved `sorry` theorem. This audit does not assert that any of the 36 will be easy to solve.

The clean statement-type closure is narrower than a blanket clean import environment: these files contain many deliberately admitted neighboring theorems. The verifier must still reject `sorryAx` and unapproved axioms in submitted proof dependencies. No successful proof submissions were exercised here, and the external Köthe proof was not replayed.

Historical “oldest” dates remain approximate editorial metadata. They were not re-established from original manuscripts. Exact source IDs were checked against the local 259-entry allowlist during the original screen; the live production catalog and semantic duplicates beyond the original exclusions were not exhaustively audited. This report is a dated assessment, not a claim that mathematical status cannot change tomorrow.
