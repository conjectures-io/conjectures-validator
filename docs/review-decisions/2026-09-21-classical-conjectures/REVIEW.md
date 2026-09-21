# September 21 classical conjecture admissions

Adds **36 targets**, each with proof and refutation tasks, to the 259-target release at tasks commit `0a60fecdc7d11f1a69ef959f49ec5d5de54c7bc3`. The resulting pool has **295 reward targets and 590 bundles**: 238 Erdős, 24 Green, 32 Wikipedia and one Millennium target, across 257 source files.

The [preceding 40-candidate audit](audit/AUDIT.md) gives the individual status sources, mathematical formulations and caveats. Köthe is excluded as resolved. Perfect cuboid, Catalan constant irrationality and Scholz remain on hold for recent full-proof claims. Retention is a dated assessment of the evidence, not certification that a problem is unsolved or likely to be easy.

## Source and compatibility changes

Lean remains **4.33.1** and Mathlib remains `0df444a360eaa60ab8c11dca51a86af692955474`. The verifier source becomes `6a786f997e18e8f095762a2830d191b7e25e505e`, reconstructed from base `7d1a8c9912747679d0093f6d1216420c33ee5ffa` plus the tasks repository's SHA-256-pinned source patch (`82b0f491dd18c331f12efa722761e46b728e9bb8d643466e9ef8b40dde903023`). The new target sources come from upstream `2f82a24192f64ac678557247e496b2381d3889a1`.

The patch preserves the earlier audited repairs. It imports the selected source modules and the corrected normal-number helper, rather than updating unrelated helper definitions. Normality of π quantifies over every finite block. Hardy–Littlewood I has asymptotic equivalence and distinct offsets. Green 72 uses eventual nonattainment. Borsuk's dimension-four statement uses extended diameter and its boundedness hypotheses. The Brocard–Ramanujan question text now agrees with the completeness proposition.

The old misspelled `Millenium.RiemannHypothesis` import forwards to the new `Millennium` module so the catalog cannot acquire duplicate declarations. The overlapping `FormalConjecturesAnswerPostpone` Lake library is removed from this verifier's source patch: rebuilt files otherwise acquired its options despite requesting the default library. This was detected as unresolved answer placeholders in two new statements. After the fix, the normal verifier `always_true` elaboration produces the exact audited types. `True ↔ P` selects the positive proposition; it does not prove P. Both P and its refutation are separately committed tasks. Workspace creation accepts the already-sanitized source configuration and rejects ambiguous library ownership or a remaining postpone-mode option.

## Admission and retirement rules

Named source families are admitted through explicit canonical file/namespace identities. They must have a matching retained entry in `classical-status-audit.json`, whose exact bytes are hash-pinned in the selection audit. Arbitrary named source files, crossed families, missing evidence, changed evidence and held entries are rejected.

Four historical denylist names are explicitly re-admitted: corrected Green 72, Artin primitive roots part i, Lehmer totient and Lehmer Mahler measure. The latter three were on the original broad historical denylist; the dated audit found no established resolution of these exact targets. Only their matching current type hashes are released. Distinct older types remain denied. Green 44 stays retired and Erdős 96 stays active with its existing reward identity.

The retained selection audit keeps its September 8 review boundary; this work does not claim to have repeated the literature review for the other 259 targets. The separately committed classical evidence records the September 21 screen for the additions.

## Upstream PR review

The [scoped PR review](validation/pr-review.json) screened 482 open upstream PRs. PR 6435's Hardy–Littlewood proposal starts from the obsolete big-O formulation and assumes conditional convergence; for corrected distinct linear offsets the tail factors are `1 + O(q^-2)`. The mathematical objection was reviewed and the proposal was not adopted. This is an explicit review decision, not an upstream issue closure or a claim that no touching PR exists. Hadamard PRs cover finite orders 668 and 12, not the universal target. Other touching PRs concern references and docstrings.

## Mechanical validation

- The base plus source patch independently reproduces the exact pinned commit using a temporary Git index.
- Full source build: **10,099 jobs, success**, followed by the catalog extractor, task inspector and task support build.
- **36/36** new target types and the recursively used Formal Conjectures definitions match the audited snapshot. No direct statement `sorryAx` or nonstandard transitive statement axiom remains.
- **259/259** retained target types and their recursively used Formal Conjectures definitions match the previous verifier. Mathlib is unchanged. Proof bodies are deliberately excluded from this definition comparison; conjecture proof bodies remain admitted placeholders.
- All **590** bundles were independently compiled and inspected with the production target validator, then checked against the generated allowlist. Each target has both resolution modes.
- All 259 existing reward identities survive. Every task ID is new because the source revision changes. Held/resolved candidates are absent. The public catalog loads 295 active targets and 21 retired targets without slug collisions, and all three repinned fixtures load. Fixture repinning now computes task IDs with each fixture’s own size limit, instead of the changed global default.

The [definition comparison](validation/definition-comparison.json), [mechanical results](validation/mechanical-validation.json), [per-bundle results](validation/generation.json), and input/output snapshots are retained alongside the Lean inspection program. These checks establish statement identity and bundle integrity; they do not solve the conjectures, certify mathematical equivalence to every informal source, or replay external claimed proofs. Python and API validation results are recorded in `validation/python-tests.txt`.

## Release boundary

This is a source-pin rotation and pool release. Existing published bundles remain recoverable from their pinned historical tasks commit; their commitments must not be rewritten in historical submission records. Follow `POOL.md`: pause admissions, drain queued/running/retryable/review/reward work for the prior pin, then activate the matching tasks checkout, validator and image together. This change has not been deployed by the admission work.

## Added targets

| Original audit row | Problem | Exact theorem |
|---|---|---|
| 1 | Odd perfect numbers | `PerfectNumbers.odd_perfect_number_conjecture` |
| 2 | Infinitely many Mersenne primes | `Mersenne.infinitely_many_mersenne_primes` |
| 3 | Infinitely many Fermat primes | `Fermat.infinite_fermat_primes` |
| 5 | Euler–Mascheroni constant irrationality | `Irrational.irrational_eulerMascheroniConstant` |
| 6 | Goldbach conjecture | `GoldbachConjecture.goldbach` |
| 7 | Euler's idoneal-number completeness | `Idoneal.idoneal_numbers_completeness` |
| 8 | Legendre conjecture | `LegendreConjecture.legendre_conjecture` |
| 9 | Gauss real quadratic class-number-one problem | `ClassNumberProblem.class_number_problem` |
| 10 | Twin prime conjecture | `TwinPrimes.twin_primes` |
| 11 | Kummer–Vandiver conjecture | `KummerVandiver.kummer_vandiver` |
| 12 | Infinitely many regular primes | `RegularPrimes.regularprime_conjecture` |
| 13 | Pollock's tetrahedral-number conjecture | `PollocksConjecture.pollock_tetrahedral` |
| 14 | Bunyakovsky conjecture | `Bunyakovsky.bunyakovsky_conjecture` |
| 15 | Riemann hypothesis | `RiemannHypothesis.riemannHypothesis` |
| 17 | Brocard–Ramanujan factorial problem | `Erdos398.erdos_398` |
| 18 | Catalan–Mersenne primality question | `Mersenne.catalans_mersenne_conjecture` |
| 19 | Oppermann conjecture | `Oppermann.oppermann_conjecture` |
| 20 | Inverse Galois problem | `InverseGalois.inverse_galois_problem` |
| 21 | Hadamard matrix existence | `Hadamard.HadamardConjecture` |
| 22 | Lemoine conjecture | `Lemoine.lemoine_conjecture` |
| 23 | No-three-in-line problem | `Green72.green_72` |
| 24 | Dickson conjecture | `Dickson.dickson_conjecture` |
| 25 | Brocard's prime-square conjecture | `Brocard.brocard_conjecture` |
| 26 | Carmichael totient conjecture | `CarmichaelTotient.charmichaelTotient` |
| 27 | Normality of π in base 10 | `NormalNumber.pi_normal_base_ten` |
| 28 | Inscribed square problem | `InscribedSquare.inscribed_square_problem` |
| 29 | Infinitely many primes n²+1 | `PrimesAndPerfectSquares.infinite_prime_sq_add_one` |
| 30 | Gauss circle error bound | `GaussCircleProblem.error_isBigO` |
| 31 | Goormaghtigh conjecture | `Goormaghtigh.goormaghtigh_conjecture` |
| 32 | First Hardy–Littlewood conjecture | `HardyLittlewood.first_hardy_littlewood_conjecture` |
| 33 | Second Hardy–Littlewood conjecture | `HardyLittlewood.second_hardy_littlewood_conjecture` |
| 34 | Artin primitive-root conjecture | `ArtinPrimitiveRootsConjecture.artin_primitive_roots.parts.i` |
| 36 | Lehmer totient problem | `LehmerTotient.lehmer_totient` |
| 37 | Lehmer Mahler-measure problem | `LehmerMahlerMeasureProblem.lehmer_mahler_measure_problem` |
| 38 | Borsuk problem in dimension four | `Borsuk.borsuk_conjecture.four` |
| 39 | Erdős–Szekeres convex-polygon conjecture | `Erdos107.erdos_107` |
