# Twenty Erdős problems, including the full context for #12

Prepared batch: 5, 7, 12, 13, 19, 21, 49, 59, 60, 65, 70, 78, 80, 86, 87,
94, 102, 103, 104, and 111. One reviewed reward target per number is selected.
The task baseline is `280d71ce9f92ea3c24c67f5bd40a1347239e6144`, including the
retirement of Erdős 456(iii). The intended pool has 309 reward targets: 289
retained and 20 additions. This document alone is not evidence of deployment.

## Erdős 12: disposition

Parts (i) and (ii) are already solved, with answers yes and no respectively.
The current upstream Formal Conjectures file still marks part (iii) open.
The public listing includes all three questions and their statuses; only the
remaining reciprocal-summability question is the active reward target.

The July 30 Libertas claim is not a valid unconditional proof of part (iii).
The currently published `lemma_block_decomposition.lean` declares
`axiom growth_ineq (A : Set ℕ) (hA : IsGood A) (i : ℕ) : P i ≤ a_seq A hA.1 (S_seq i)`.
`block_lower_bound` uses that axiom; `sum_bound` uses `block_lower_bound`;
`block_decomposition_proved` uses `sum_bound`; `summable_of_isGood` uses that
decomposition. The final theorem therefore depends on an unproved assumption.
Finite numerical tests cannot discharge the universally quantified axiom.

There is also a direct counterexample to this axiom as written. Let A consist
of the squares of primes congruent to 3 modulo 4. There are infinitely many
such primes. If p² divides q²+r² for distinct larger elements, then modulo p
we get q²+r²=0. Since -1 is not a square modulo p, both q and r would be
divisible by p, contradicting their being larger primes. Thus A is good.
Its least element is 9.

Their definitions give S_seq(0)=0, a_seq(A,0)=9, M(0)=1, F(0)≥3,
Y(0)=3^8000 and P(0)=10 F(0)Y(0)+C(0)≥30·3^8000>9. Applying growth_ineq
at i=0 contradicts these facts. This refutes the extra assumption, not the
open reciprocal-summability conjecture. The original quarantine is resolved
by rejection of the claimed proof, rather than acceptance of it.

The claim repository was inspected at commit `7cd9c8a08fc7937e51e59df4159aeb566bfe722a`
(September 10, 2026). The downloaded Lean files are retained under `evidence/`. This is a source
and mathematical dependency review; it does not claim to have compiled the
external project's incomplete build layout or kernel-checked this informal
counterexample. The external `ErdosProblems/12.lean` also contains `sorry`
for parts (i) and (ii); it is not a self-contained proof of all of #12.

Sources:

- https://github.com/libertas-technology-group/Libertas-Erdos-Solutions/blob/7cd9c8a08fc7937e51e59df4159aeb566bfe722a/erdos/12/lemma_block_decomposition.lean
- https://github.com/libertas-technology-group/Libertas-Erdos-Solutions/blob/7cd9c8a08fc7937e51e59df4159aeb566bfe722a/erdos/12/lemma_part_iii.lean
- https://github.com/google-deepmind/formal-conjectures/blob/main/FormalConjectures/ErdosProblems/12.lean
- https://www.erdosproblems.com/forum/proof-claims/172/comments

The other nineteen targets inherit the bounded October 28 intake review. Their
upstream open labels were rechecked November 4; the retrieved files and blob
identifiers are retained in `evidence/upstream-status-refresh.json`.
This does not assert an exhaustive literature search or certify every claim
in external discussions. Build and publication validation are recorded separately.

## Full source build

The complete `FormalConjectures` build passes (10,625 jobs). The earlier intake
had built only the candidate and retained-target modules; full extraction is
required here to preserve the website's repository-wide catalog. Three
compatibility repairs were needed outside the selected reward targets:

- GraphConjecture146's sanity proof uses `edist`, matching its graph-square definition.
- Erdős 775 uses `UniformHypergraph (Fin n) 3` and its maximal-clique sizes.
- Erdős 794 uses `Finset.IsUniform 3` in place of the obsolete three-uniform name.

The latter two specialize the current uniform-hypergraph definitions to three,
preserving their intended statements. No new placeholder proof or axiom was
introduced. All three changed modules pass `lake --wfail build`. The final
source commit is `17acb8768de75e168a43c7c828e933071aee63cf`; its patch digest is
`37bde332c8a0951fd954b0317aee440b88940fa82cf01c65bcbd073af3b0f881`.

## Validation and release boundary

The final catalog contains 6,062 declarations in 1,387 modules. All 309 selected
statement hashes match the intake build, and the final source repairs change
none of their dependency constants. Publication initially builds 554 bundles
and reuses 64; publication against the final source pin reuses all 618. The
bundle audit verifies every active digest and preserves all 1,681 historical
version records, their immutable bytes, and their existing admissions.

The non-integration regression suite passes 1,490 tests (882 skipped by that
suite's scope); all 64 catalog API tests pass against a separate disposable
Postgres instance. The full task-pool and admission checks are recorded under
`validation/`. Sandbox controls exercise #12 and #5 in both attack directions
and a known valid counterexample fixture. They are rejection/acceptance
controls, not proofs of the open problems.

The sandbox validation image is a local compiled-cache image with current
verifier code. It is not a production release image. A release must build the
normal Dockerfile from the committed validator and task pins, install it
through the documented worker workflow, and pass the deployment smoke checks.
No live service, production database, or production worker was changed.
The task companion is https://github.com/conjectures-io/conjectures-tasks/pull/23.
